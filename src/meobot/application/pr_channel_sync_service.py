"""Fetching a channel's numbers from its platform, and writing down what happened.

Step 1F.2.4b, extended by 1F.2.4c. The orchestration half: claim a connection,
get a token, ask the provider, normalize, append, record health.

It contains no HTTP, no endpoint and no provider field name - those live under
:mod:`meobot.integrations`, and the separation is what lets a test drive this
whole file with a fake provider and no network. Step 1F.2.4c is the evidence
that the separation was real: adding Facebook and Instagram changed nothing in
this file except the platform name in a sentence.

The claim is the lock
---------------------

Two workers, or a worker and somebody pressing "Đồng bộ ngay", must not sync one
channel at once - they would burn double the quota and race to insert the same
reading. The lock is a **conditional UPDATE** on the connection row:

    UPDATE ... SET sync_status = 'SYNCING'
    WHERE id = ? AND sync_status <> 'SYNCING'

One writer wins, the other sees zero rows affected and gives up. Not a Python
flag - there are four worker processes and an API container, and a boolean in
one of them is a lock over nothing. Not an advisory lock either, because the
claim has to *survive* the transaction: a worker that dies mid-sync leaves the
row ``SYNCING``, which is exactly what
:meth:`release_stale` exists to collect, and an advisory lock would have
evaporated with the connection.

Idempotency sits underneath it
-------------------------------

The claim stops two syncs running together; it does nothing about the same
reading being fetched twice an hour apart, or a Celery retry re-running a job
that already succeeded. That is
:func:`~meobot.domain.pr.channel_connections.reading_fingerprint` and a partial
unique index: a re-fetch of an already-recorded period with the same numbers is
refused by the database and reported as ``duplicate``, not as a failure.

The two together are why "retry the job" is always safe.

What a failed sync does not do
-------------------------------

It does not write a snapshot. There is no such thing as a partial reading worth
recording *as a reading*: a row of nulls looks exactly like a channel that lost
all its metrics. The previous numbers stay current, the failure lands on the
connection's health columns, and the timeline is untouched.

It also does not delete the stored credential. Only ``AUTH_REQUIRED`` - the one
code that means the grant is actually gone - moves a connection to
``ACTION_REQUIRED``. A timeout must never cost somebody their connection.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_connection_service import PrChannelConnectionService
from meobot.application.pr_support import record_pr_event
from meobot.core.config import Settings
from meobot.core.errors import IntegrationError
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.pr import PrChannel
from meobot.db.models.pr_channel_connection import PrChannelConnection
from meobot.db.models.pr_reporting import PrChannelMetricSnapshot
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.channel_connections import (
    ChannelSyncProviderError,
    PrChannelConnectionState,
    PrChannelSyncErrorCode,
    PrChannelSyncStatus,
    PrChannelSyncTrigger,
    backoff_seconds,
    is_due,
    reading_fingerprint,
)
from meobot.domain.pr.channel_metrics import (
    MAX_METRIC_VALUE,
    ChannelMetricsProvider,
    ChannelMetricsReading,
    PrChannelPlatform,
)
from meobot.domain.pr.errors import PrNotFoundError, PrValidationError
from meobot.domain.pr.labels import channel_platform_label
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrMetricSource

logger = get_logger(__name__)

#: How long a connection rests after its first failure, doubling per consecutive
#: failure up to :data:`BACKOFF_CAP_SECONDS`. Only ever *delays* a connection
#: the sweeper would otherwise pick up, so the floor is one sweep interval and
#: there is no tight loop to be had.
BACKOFF_BASE_SECONDS = 3600
BACKOFF_CAP_SECONDS = 86400


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What one sync attempt did. Returned, audited, never rendered raw."""

    channel_id: uuid.UUID
    ok: bool
    #: The appended snapshot, or ``None`` when the reading was a duplicate or
    #: the attempt failed.
    snapshot_id: uuid.UUID | None = None
    #: True when the provider answered but the reading was already recorded.
    #: Success, not failure: the timeline is correct and nothing needed adding.
    duplicate: bool = False
    error_code: PrChannelSyncErrorCode | None = None
    #: A short Vietnamese sentence **MeoBot** wrote. Never provider prose.
    error_message: str | None = None
    observed_at: datetime | None = None
    #: True when the attempt never started because somebody else held the claim.
    skipped_locked: bool = False


#: What a person is told for each failure class, with the platform's own name
#: written into it. One table, so that a screen never has to interpret an error
#: code and a provider's own words never reach a user.
#:
#: Step 1F.2.4b wrote "YouTube" into every one of these, which was accurate while
#: YouTube was the only connector and became wrong the moment a Facebook Page
#: could fail. The platform name is a parameter now, and it is the same
#: Vietnamese label the badge uses - a person reading "Không kết nối được tới
#: Facebook" should see the same word they saw on the card.
ERROR_MESSAGE_TEMPLATES: dict[PrChannelSyncErrorCode, str] = {
    PrChannelSyncErrorCode.AUTH_REQUIRED: "Kết nối {platform} cần xác thực lại.",
    PrChannelSyncErrorCode.RATE_LIMITED: (
        "{platform} đang giới hạn truy vấn. Hệ thống sẽ thử lại sau."
    ),
    PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE: (
        "Không kết nối được tới {platform}. Hệ thống sẽ thử lại sau."
    ),
    PrChannelSyncErrorCode.INVALID_ACCOUNT: (
        "Tài khoản {platform} đã cấp quyền không khớp với kênh này."
    ),
    PrChannelSyncErrorCode.INSUFFICIENT_SCOPE: (
        "Quyền đã cấp cho {platform} không đủ để đọc số liệu. Hãy kết nối lại."
    ),
    PrChannelSyncErrorCode.BAD_RESPONSE: "{platform} trả về dữ liệu không đọc được.",
    PrChannelSyncErrorCode.UNKNOWN: "Đồng bộ thất bại vì lỗi chưa xác định.",
}


def error_message(error_code: PrChannelSyncErrorCode, platform: PrChannelPlatform) -> str:
    """The sentence a person sees for one failure on one platform."""
    template = ERROR_MESSAGE_TEMPLATES.get(
        error_code, ERROR_MESSAGE_TEMPLATES[PrChannelSyncErrorCode.UNKNOWN]
    )
    return template.format(platform=channel_platform_label(platform))


class PrChannelSyncService:
    """Runs one sync, and finds the connections that are due for one.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Gate for the manual trigger. Reading metrics is open;
            asking the platform for new ones is ``PR_CHANNEL_MANAGE``.
        connections: Owns the credential. This service never decrypts anything
            itself - it asks for an access token and gets one.
        settings: Cadence, batch size and staleness thresholds.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        connections: PrChannelConnectionService,
        settings: Settings,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._connections = connections
        self._settings = settings

    # --- Selecting work ---------------------------------------------------
    async def due_connections(self, *, limit: int | None = None) -> Sequence[PrChannelConnection]:
        """Connections the scheduler should sync now.

        Narrow by design, and narrow **in SQL**: connected, auto-sync on, not
        already claimed, and not resting after a run of failures. The sweeper
        must never load every channel and filter in Python - that is the query
        that gets slow quietly, long after anybody remembers writing it.

        Being *due* is measured from the last **success**, not the last attempt:
        a connection failing every cycle must still come round again, or a
        transient outage would freeze it for ever.
        """
        now = utcnow()
        statement = (
            select(PrChannelConnection)
            .where(
                PrChannelConnection.status == PrChannelConnectionState.CONNECTED,
                PrChannelConnection.auto_sync_enabled.is_(True),
                PrChannelConnection.sync_status != PrChannelSyncStatus.SYNCING,
            )
            .order_by(PrChannelConnection.last_sync_succeeded_at.asc().nullsfirst())
            .limit(limit or self._settings.pr_channel_sync_batch_size)
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        return [row for row in rows if self._is_eligible(row, now=now)]

    def _is_eligible(self, connection: PrChannelConnection, *, now: datetime) -> bool:
        """Due by cadence, and past any backoff its failures earned."""
        # Every stored timestamp goes through ``ensure_utc``: SQLite returns a
        # naive datetime where PostgreSQL returns an aware one, and the offline
        # suite builds its schema on SQLite.
        succeeded = (
            ensure_utc(connection.last_sync_succeeded_at)
            if connection.last_sync_succeeded_at is not None
            else None
        )
        if not is_due(
            last_succeeded_at=succeeded,
            now=now,
            min_interval_seconds=self._settings.pr_channel_sync_min_interval_seconds,
        ):
            return False
        if connection.consecutive_failures <= 0 or connection.last_sync_failed_at is None:
            return True
        rest = backoff_seconds(
            connection.consecutive_failures,
            base_seconds=BACKOFF_BASE_SECONDS,
            cap_seconds=BACKOFF_CAP_SECONDS,
        )
        return (now - ensure_utc(connection.last_sync_failed_at)).total_seconds() >= rest

    async def release_stale(self) -> int:
        """Free connections left ``SYNCING`` by a worker that died.

        Without this a crashed worker would hold a channel's claim for ever and
        that channel would silently stop syncing - the quietest possible
        failure. Bounded by a threshold comfortably longer than the Celery task
        time limit, so a slow-but-alive sync is never stolen from itself.
        """
        moment = utcnow() - timedelta(seconds=self._settings.pr_channel_sync_stale_after_seconds)
        result = await self._session.execute(
            update(PrChannelConnection)
            .where(
                PrChannelConnection.sync_status == PrChannelSyncStatus.SYNCING,
                PrChannelConnection.last_sync_started_at < moment,
            )
            .values(sync_status=PrChannelSyncStatus.FAILED, last_sync_error_code=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    # --- Running one ------------------------------------------------------
    async def request_sync(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        channel_id: uuid.UUID,
    ) -> PrChannelConnection:
        """Authorize a manual "Đồng bộ ngay" and record who asked.

        Only the *request*. The fetch itself happens on the worker, which is
        why this returns immediately and the route answers ``202``: a Google
        round trip inside an HTTP request would hold a connection open for
        seconds and time out on a bad day, for no benefit - the panel refetches
        the connection state anyway.

        The audit event here is the answer to "who triggered this", which the
        metric row deliberately cannot answer: an API reading has no human
        author, and stamping the clicker onto it as ``recorded_by_user_id``
        would say a person typed numbers they never saw.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)
        channel = await self._require_channel(channel_id)
        connection = await self._require_live_connection(channel_id)
        if connection.status is not PrChannelConnectionState.CONNECTED:
            raise PrValidationError(
                "Kết nối YouTube cần xác thực lại trước khi đồng bộ.",
                details={"reason": "connection_action_required"},
            )

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_SYNC_REQUESTED,
            entity_type="pr_channel_connection",
            entity_id=connection.id,
            after={
                "channel_id": str(channel.id),
                "channel_code": channel.code,
                "provider": connection.provider.value,
                "trigger": PrChannelSyncTrigger.MANUAL.value,
            },
        )
        return connection

    async def claim(self, connection_id: uuid.UUID) -> bool:
        """Take the sync lock for one connection, or report that somebody else has it.

        A conditional UPDATE, which is the whole mechanism: the database decides
        the winner, and the loser learns it lost from ``rowcount``. The caller
        **must commit** before doing any provider work - a claim that is still
        in a transaction is not a claim anybody else can see.
        """
        result = await self._session.execute(
            update(PrChannelConnection)
            .where(
                PrChannelConnection.id == connection_id,
                PrChannelConnection.sync_status != PrChannelSyncStatus.SYNCING,
                PrChannelConnection.status == PrChannelConnectionState.CONNECTED,
            )
            .values(sync_status=PrChannelSyncStatus.SYNCING, last_sync_started_at=utcnow())
        )
        return bool(getattr(result, "rowcount", 0))

    async def run_sync(
        self,
        *,
        connection_id: uuid.UUID,
        trigger: PrChannelSyncTrigger,
        request_id: uuid.UUID,
        actor: Actor,
        provider_client: ChannelMetricsProvider | None = None,
    ) -> SyncOutcome:
        """One attempt, from claimed connection to settled health.

        Assumes the claim is already held - :meth:`claim` is separate so it can
        commit on its own, before a provider call that may take seconds.

        Never raises for a provider failure. A sync that could not reach Google
        is an outcome, not an exception: the caller is a Celery task whose job
        is to record what happened and move to the next channel.
        """
        connection = await self._session.get(PrChannelConnection, connection_id)
        if connection is None:
            raise PrNotFoundError(
                "No channel connection with that id",
                details={"connection_id": str(connection_id)},
            )
        channel = await self._require_channel(connection.channel_id)

        try:
            access_token = await self._connections.access_token_for(
                connection, provider_client=provider_client
            )
            client = provider_client or self._provider_for(connection)
            reading = await client.fetch_channel_metrics(
                access_token=access_token,
                account_id=connection.provider_account_id,
                now=utcnow(),
            )
            # Refresh the display name while we have an answer, so a renamed
            # account does not sit in the panel under its old title for ever.
            #
            # **Best effort, and deliberately after the reading.** It is
            # cosmetic: the identity that matters was already verified inside
            # ``fetch_channel_metrics``, which refuses to return a reading for an
            # account other than the bound one. And not every provider can
            # answer it from a token alone - an Instagram account is reached
            # through a Page, so a Page token identifies the Page rather than
            # the account being measured. Losing a display name must not fail a
            # sync that fetched every number correctly.
            try:
                identity = await client.fetch_channel_identity(access_token=access_token)
            except ChannelSyncProviderError:
                pass
            else:
                connection.provider_account_name = identity.title
                connection.provider_account_handle = identity.handle
        except ChannelSyncProviderError as exc:
            return await self._fail(
                connection,
                channel,
                error_code=exc.error_code,
                trigger=trigger,
                request_id=request_id,
                actor=actor,
            )
        except PrValidationError:
            # No usable credential. The one failure that is definitely the
            # grant's fault rather than the network's.
            return await self._fail(
                connection,
                channel,
                error_code=PrChannelSyncErrorCode.AUTH_REQUIRED,
                trigger=trigger,
                request_id=request_id,
                actor=actor,
            )
        except IntegrationError:
            return await self._fail(
                connection,
                channel,
                error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE,
                trigger=trigger,
                request_id=request_id,
                actor=actor,
            )

        snapshot, duplicate = await self._append(connection, reading)
        await self._succeed(connection)
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_SYNC_SUCCEEDED,
            entity_type="pr_channel_connection",
            entity_id=connection.id,
            after={
                "channel_id": str(channel.id),
                "channel_code": channel.code,
                "provider": connection.provider.value,
                "snapshot_id": str(snapshot.id) if snapshot is not None else None,
                "observed_at": reading.observed_at.isoformat(),
                "duplicate": duplicate,
                "trigger": trigger.value,
            },
        )
        return SyncOutcome(
            channel_id=channel.id,
            ok=True,
            snapshot_id=snapshot.id if snapshot is not None else None,
            duplicate=duplicate,
            observed_at=reading.observed_at,
        )

    # --- Writing ----------------------------------------------------------
    async def _append(
        self, connection: PrChannelConnection, reading: ChannelMetricsReading
    ) -> tuple[PrChannelMetricSnapshot | None, bool]:
        """Append one API reading, or recognise it as one already recorded.

        The write that makes this whole step worth having, and its three rules
        are all Step 1F.2.4a's, kept:

        * ``source = API``, always. This endpoint records what a platform said;
        * ``recorded_by_user_id = None``, **always**. Somebody pressing "Đồng bộ
          ngay" triggered a fetch - they did not observe a number, and naming
          them as the recorder would make the history claim they typed it. Who
          asked is in the audit trail, where it belongs;
        * append-only. No historical row is touched, manual or API.
        """
        fingerprint = reading_fingerprint(
            provider_account_id=connection.provider_account_id,
            period_start=reading.period_start,
            period_end=reading.period_end,
            metrics=reading.canonical(),
        )
        existing = await self._session.execute(
            select(PrChannelMetricSnapshot.id)
            .where(
                PrChannelMetricSnapshot.channel_id == connection.channel_id,
                PrChannelMetricSnapshot.provider_reading_key == fingerprint,
            )
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return None, True

        snapshot = PrChannelMetricSnapshot(
            channel_id=connection.channel_id,
            observed_at=reading.observed_at,
            source=PrMetricSource.API,
            recorded_by_user_id=None,
            provider_reading_key=fingerprint,
            extra_metrics=dict(reading.extra_metrics) if reading.extra_metrics else None,
            **self._validated(reading),
        )
        self._session.add(snapshot)
        try:
            await self._session.flush()
        except IntegrityError:
            # Lost a race to another writer that inserted the same fingerprint,
            # or the same instant. Either way the reading is recorded and this
            # is a duplicate rather than a failure.
            await self._session.rollback()
            return None, True
        return snapshot, False

    @staticmethod
    def _validated(reading: ChannelMetricsReading) -> dict[str, int | None]:
        """Canonical metrics, with anything implausible dropped to ``None``.

        A provider response is untrusted input and is checked here as well as at
        the transport boundary - the database's non-negative constraint would
        otherwise turn one bad remote field into a failed sync that discards a
        dozen good ones. Dropping the field keeps the reading and says "not
        recorded" about the part that was unreadable, which is true.
        """

        def usable(value: object) -> bool:
            return (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= MAX_METRIC_VALUE
            )

        return {
            name: (value if usable(value) else None) for name, value in reading.canonical().items()
        }

    # --- Health -----------------------------------------------------------
    async def _succeed(self, connection: PrChannelConnection) -> None:
        moment = utcnow()
        connection.sync_status = PrChannelSyncStatus.SUCCESS
        connection.last_sync_succeeded_at = moment
        connection.last_sync_error_code = None
        connection.last_sync_error_message = None
        connection.consecutive_failures = 0
        # A success proves the credential works, which clears an auth failure a
        # reconnect may have already fixed.
        connection.status = PrChannelConnectionState.CONNECTED
        await self._session.flush()

    async def _fail(
        self,
        connection: PrChannelConnection,
        channel: PrChannel,
        *,
        error_code: PrChannelSyncErrorCode,
        trigger: PrChannelSyncTrigger,
        request_id: uuid.UUID,
        actor: Actor,
    ) -> SyncOutcome:
        """Record a failure. **No snapshot is written and no token is dropped.**

        Only ``AUTH_REQUIRED`` moves the connection out of ``CONNECTED``: that
        is the one code meaning the grant itself is gone. A timeout, a quota
        limit or a malformed body says nothing about the credential, and
        clearing it would turn a five-minute Google outage into a morning of
        people reconnecting channels by hand.
        """
        moment = utcnow()
        connection.sync_status = PrChannelSyncStatus.FAILED
        connection.last_sync_failed_at = moment
        connection.last_sync_error_code = error_code
        connection.last_sync_error_message = error_message(error_code, connection.provider)
        connection.consecutive_failures = min(connection.consecutive_failures + 1, 1000)
        if error_code is PrChannelSyncErrorCode.AUTH_REQUIRED:
            connection.status = PrChannelConnectionState.ACTION_REQUIRED
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_SYNC_FAILED,
            entity_type="pr_channel_connection",
            entity_id=connection.id,
            after={
                "channel_id": str(channel.id),
                "channel_code": channel.code,
                "provider": connection.provider.value,
                # The safe class, never the provider's own words.
                "error_code": error_code.value,
                "trigger": trigger.value,
                "consecutive_failures": connection.consecutive_failures,
            },
        )
        logger.warning(
            "pr_channel_sync_failed",
            extra={
                "pr_channel_code": channel.code,
                "provider": connection.provider.value,
                "error_code": error_code.value,
            },
        )
        return SyncOutcome(
            channel_id=channel.id,
            ok=False,
            error_code=error_code,
            error_message=connection.last_sync_error_message,
        )

    # --- Internals --------------------------------------------------------
    def _provider_for(self, connection: PrChannelConnection) -> ChannelMetricsProvider:
        from meobot.application.pr_channel_providers import build_provider

        return build_provider(connection.provider, self._settings)

    async def _require_channel(self, channel_id: uuid.UUID) -> PrChannel:
        channel = await self._session.get(PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No PR channel with that id", details={"channel_id": str(channel_id)}
            )
        return channel

    async def _require_live_connection(self, channel_id: uuid.UUID) -> PrChannelConnection:
        result = await self._session.execute(
            select(PrChannelConnection)
            .where(
                PrChannelConnection.channel_id == channel_id,
                PrChannelConnection.status != PrChannelConnectionState.DISCONNECTED,
            )
            .limit(1)
        )
        connection = result.scalar_one_or_none()
        if connection is None:
            raise PrNotFoundError(
                "Kênh này chưa kết nối với nền tảng nào.",
                details={"channel_id": str(channel_id)},
            )
        return connection


__all__: list[str] = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "ERROR_MESSAGE_TEMPLATES",
    "PrChannelSyncService",
    "SyncOutcome",
    "error_message",
]
