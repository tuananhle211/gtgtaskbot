"""A channel's numbers: what they are now, what they were, and who wrote them.

Step 1F.2.4a. The table this service writes to is Step 1B's
``pr_channel_metric_snapshots``, unchanged in its rules and widened in its
columns by migration 0027. Nothing here is a new kind of record; what is new is
that a person can finally create one, and that a screen can finally read one.

Append-only, and what a correction is
--------------------------------------

There is no update method and no delete method, and that is the product
decision rather than an omission. A snapshot is a claim about a moment - *"at
09:00 on the 20th the app said 124,812 followers"* - and a claim that can be
edited afterwards answers nothing. So a wrong entry is corrected by **recording
a new one**, and both stay: the mistake, and the fix, in the order they
happened.

The one thing that cannot be appended is a second reading at the *same* instant
from the same source. 0013's unique index refuses it, and this service turns
that into a sentence a person can act on rather than a database error. Somebody
who typed 09:00 and meant 19:00 records 19:00; somebody who typed the wrong
number at 09:00 records the right one now, and the panel shows the newer reading
as current because that is what it is.

Deleting history has no endpoint at all. Metric history is the only evidence of
what a channel was doing before somebody changed something, and an admin screen
that can quietly remove it is worth less than one that cannot.

"Latest" has to be defined, not assumed
----------------------------------------

:data:`LATEST_FIRST` orders by ``observed_at DESC, id DESC``. The tiebreak
matters: two readings can share an instant across different sources, and
``created_at`` is not the answer - a backfilled reading is created today and
observed in March, so ordering by insertion would make March the present. The
same ordering is used for the current figure, for the one it is compared
against, and for the history list, so the three can never disagree about which
row is which.

Never N+1
---------

Three query shapes, and none of them is per-row:

* :meth:`summaries_for_channels` reads the latest snapshot for **every** channel
  in one statement, using a window function rather than a correlated subquery
  per channel. The channel list draws its badges from it;
* :meth:`describe` reads one bounded page of history, its total, and the
  recorder names for that page - a fixed number of statements regardless of how
  many snapshots or how many distinct recorders the page contains;
* the recorder names are resolved by joining ``users``, not by asking
  ``/people`` per row. A person who has since been deactivated still has a name
  here, because the join does not filter on status - who typed it does not
  change when they leave.

No platform ever contacted
---------------------------

This module imports no HTTP client and performs no network call. Step 1F.2.4a
ships the *port* a connector will implement
(:class:`~meobot.domain.pr.channel_metrics.ChannelMetricsProvider`) and no
provider, and ``has_api_connection`` is passed ``False`` everywhere, so
:class:`~meobot.domain.pr.channel_metrics.PrChannelMetricsStatus.CONNECTED_API`
is unreachable. A status claiming an integration that does not exist would be
the single most misleading thing this screen could say.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_support import record_pr_event
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrChannel
from meobot.db.models.pr_channel_connection import PrChannelConnection
from meobot.db.models.pr_reporting import PrChannelMetricSnapshot
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.channel_analytics import (
    ANALYTICS_LOOKBACK_DAYS,
    ChannelAnalytics,
    MetricObservation,
    compute_channel_analytics,
)
from meobot.domain.pr.channel_connections import (
    PrChannelConnectionState,
    PrChannelSyncStatus,
    metrics_status_for,
)
from meobot.domain.pr.channel_metrics import (
    MANUAL_METRIC_FIELDS,
    MAX_METRIC_VALUE,
    ChannelFollowerTrend,
    PrChannelMetricsStatus,
    follower_trend,
    is_future_capture,
    normalize_capture_time,
    stale_days,
)
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrValidationError,
)
from meobot.domain.pr.policy import PR_READ_PERMISSION, PrCapability, require_permission
from meobot.domain.pr.reporting import PrMetricSource

logger = get_logger(__name__)

#: The default page of history, and the most a caller may ask for. Thirty is
#: about a year of a team that records monthly and a month of one that records
#: daily; either way it is a page somebody can read rather than every reading
#: ever taken sent down a phone connection.
DEFAULT_SNAPSHOT_PAGE = 30
MAX_SNAPSHOT_PAGE = 100

#: Deterministic "most recent first". See the module docstring for why
#: ``created_at`` is not in it.
LATEST_FIRST = (PrChannelMetricSnapshot.observed_at.desc(), PrChannelMetricSnapshot.id.desc())

#: The most readings one analytics computation will load.
#:
#: Step 1F.2.4d. The window is already bounded by
#: :data:`~meobot.domain.pr.channel_analytics.ANALYTICS_LOOKBACK_DAYS`, so on a
#: channel syncing daily this is never reached - thirty-seven days is
#: thirty-seven rows. It exists for the channel somebody has been re-syncing by
#: hand every few minutes, where the same window holds hundreds of rows and none
#: of the extra ones can change the answer: the baseline is chosen by nearness
#: to a date, and the newest few hundred readings already bracket every date the
#: derivations ask about.
ANALYTICS_ROW_LIMIT = 400

#: Step 1F.2.4a built no connector, so this was a constant ``False``. Step
#: 1F.2.4b made it a query: the data badge is now derived from the channel's
#: live connection through
#: :func:`~meobot.domain.pr.channel_connections.metrics_status_for`, which is
#: also what made ``ACTION_REQUIRED`` reachable.


@dataclass(frozen=True, slots=True)
class RecordChannelMetricsCommand:
    """One reading somebody typed in.

    **There is no ``source`` field and no ``recorded_by_user_id`` field.** The
    only method that consumes this command writes ``MANUAL`` and the
    authenticated actor, so a client cannot label its own typing as an API
    reading or attribute it to somebody else - see the tests in
    ``tests/unit/test_pr_channel_metrics``.

    Every metric is optional and every one of them may be ``0``. ``None`` means
    *the platform does not report this, or nobody looked*; ``0`` means the
    number is zero. They are different facts and the form keeps them apart.
    """

    channel_id: uuid.UUID
    #: The instant the reading describes - not when the row is written. A figure
    #: read yesterday and typed today is dated yesterday.
    captured_at: datetime

    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    views_7d: int | None = None
    views_30d: int | None = None
    reach_7d: int | None = None
    reach_30d: int | None = None
    impressions_7d: int | None = None
    impressions_30d: int | None = None
    engagements_7d: int | None = None
    engagements_30d: int | None = None
    likes_30d: int | None = None
    comments_30d: int | None = None
    shares_30d: int | None = None
    # --- Step 1F.2.4d -----------------------------------------------------
    #: Page likes. A different number from ``followers`` on every Meta Page, and
    #: the manual form offers both because the person filling it in is reading
    #: both off the same screen.
    fans: int | None = None
    posts_count_7d: int | None = None
    posts_count_30d: int | None = None
    reactions_30d: int | None = None
    video_views_7d: int | None = None
    video_views_30d: int | None = None

    #: Platform-specific numbers with no canonical column - ``subscribers_hidden``
    #: on YouTube, ``profile_views`` on TikTok. Stored, never parsed, and never
    #: what the summary cards read: a metric worth a card gets a column.
    extra_metrics: dict[str, Any] | None = None

    def metrics(self) -> dict[str, int | None]:
        """The canonical metrics, by column name, including the blanks."""
        return {name: getattr(self, name) for name in MANUAL_METRIC_FIELDS}


@dataclass(frozen=True, slots=True)
class ChannelMetricsSummary:
    """What a channel card needs, without loading its history.

    Deliberately small. Anything more and the channel list would be fetching a
    detail page per row for a badge nobody has clicked on yet.

    Step 1F.2.4b added the connection fields, and they are still two queries for
    a whole page rather than two per card - see
    :meth:`PrChannelMetricsService.summaries_for_channels`.
    """

    channel_id: uuid.UUID
    status: PrChannelMetricsStatus
    latest_captured_at: datetime | None = None
    followers: int | None = None
    #: Whole days since the latest reading, computed server-side so no browser
    #: does date arithmetic on it. ``None`` when there is no reading.
    days_since_capture: int | None = None
    #: Step 1F.2.4b. The source of the **latest** reading. A connected channel
    #: whose newest snapshot was typed by hand says so - the badge describes the
    #: connection, this describes the number on the card, and they can differ.
    latest_source: str | None = None
    #: The connector's own state, or ``None`` for a channel that has never had
    #: one. Kept separate from :attr:`status` because a failing sync and a dead
    #: credential are different problems with different fixes.
    connection_state: PrChannelConnectionState | None = None
    sync_status: PrChannelSyncStatus | None = None
    last_sync_succeeded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SnapshotView:
    """One stored reading, with its recorder resolved to a name."""

    snapshot: PrChannelMetricSnapshot
    #: The recorder's display name, or ``None`` for a reading no person entered.
    #: Resolved by a join, and not filtered by account status - somebody who has
    #: left still typed what they typed.
    recorded_by_name: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelMetricsView:
    """Everything the channel metrics panel draws, in one answer.

    ``latest`` and ``previous`` are the first two rows of the same ordering the
    history uses, so "current" on the cards and the top row of the table are
    always the same reading.
    """

    channel: PrChannel
    status: PrChannelMetricsStatus
    latest: SnapshotView | None = None
    previous: SnapshotView | None = None
    trend: ChannelFollowerTrend | None = None
    history: tuple[SnapshotView, ...] = ()
    total: int = 0
    limit: int = DEFAULT_SNAPSHOT_PAGE
    offset: int = 0
    days_since_capture: int | None = None
    #: Step 1F.2.4d. Growth, growth rate, engagement rate and the month's best
    #: post - all derived from this channel's own stored readings and none of
    #: them fetched. ``None`` for a channel with no readings at all, which is
    #: different from a channel whose every derivation came out ``None``: the
    #: first has nothing to describe, the second has a reading that answered
    #: nothing yet.
    analytics: ChannelAnalytics | None = None
    #: What this actor may do here, decided by the same capability the writes
    #: require. Not a hint: the write re-asks.
    can_record_metrics: bool = False
    can_edit_channel: bool = False
    can_manage_assignments: bool = False
    #: Whether appending to this channel's history is a thing that has happened,
    #: which is what makes changing its platform worth warning about.
    has_history: bool = field(default=False)


class PrChannelMetricsService:
    """Manual channel readings, and the current picture derived from them.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants. The
            same service the channel writes consult, so "may record metrics"
            cannot drift from "may manage channels".
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities

    # --- Reading ----------------------------------------------------------
    async def describe(
        self,
        *,
        actor: Actor,
        channel_id: uuid.UUID,
        limit: int = DEFAULT_SNAPSHOT_PAGE,
        offset: int = 0,
    ) -> ChannelMetricsView:
        """The current figures, the trend, and one page of history.

        Whoever may read the PR module may read this. Step 1F.2.4a widened
        nobody's view of channels: the metrics sit behind the same
        ``PR_READ_PERMISSION`` the channel itself does, and *writing* them sits
        behind ``PR_CHANNEL_MANAGE``.
        """
        require_permission(actor, PR_READ_PERMISSION)
        channel = await self._require_channel(channel_id)

        bounded = max(1, min(limit, MAX_SNAPSHOT_PAGE))
        start = max(0, offset)

        total = await self._count(channel_id)
        page = await self._page(channel_id, limit=bounded, offset=start)
        # The current pair is the head of the ordering, not the head of the
        # page: a caller asking for page 2 still has to be told what "now" is,
        # and so does one asking for a page of 1. Reused from the page only when
        # the page really does start at the top and really does contain both -
        # anything looser would silently drop the trend at ``limit=1``.
        head = (
            list(page[:2])
            if start == 0 and bounded >= 2
            else list(await self._page(channel_id, limit=2, offset=0))
        )
        latest = head[0] if head else None
        previous = head[1] if len(head) > 1 else None

        # One extra bounded statement for the whole panel - not one per
        # derivation, and not the history page reused. The page is what the
        # caller asked to *see* and may start at offset 90; the derivations need
        # the readings around two specific dates, which is a different question
        # with a different bound.
        observations = await self._observations(channel_id)

        names = await self._recorder_names([*page, *head])
        may_manage = await self._capabilities.allows(actor, PrCapability.PR_CHANNEL_MANAGE)
        connection = (await self._live_connections([channel_id])).get(channel_id)
        now = utcnow()

        return ChannelMetricsView(
            channel=channel,
            status=metrics_status_for(
                connection_state=connection.status if connection is not None else None,
                has_snapshot=total > 0,
            ),
            latest=self._view(latest, names),
            previous=self._view(previous, names),
            trend=follower_trend(
                latest.followers if latest is not None else None,
                previous.followers if previous is not None else None,
            ),
            history=tuple(self._as_view(row, names) for row in page),
            total=total,
            limit=bounded,
            offset=start,
            days_since_capture=stale_days(
                latest.observed_at if latest is not None else None, now=now
            ),
            analytics=compute_channel_analytics(observations),
            can_record_metrics=may_manage,
            can_edit_channel=may_manage,
            can_manage_assignments=may_manage,
            has_history=total > 0,
        )

    async def summaries_for_channels(
        self, channel_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, ChannelMetricsSummary]:
        """The latest reading for every one of these channels, in one query.

        A window function partitioned by channel, filtered to rank 1. The
        alternative a list screen reaches for - a query per card - is the
        failure this method exists to make impossible, and a test asserts the
        statement count for a page of channels is constant.

        Takes no actor: it is a projection over rows the caller has already been
        authorized to list, and adding a second permission check here would
        make the list's own check look optional.
        """
        unique = list(dict.fromkeys(channel_ids))
        if not unique:
            return {}

        ranked = (
            select(
                PrChannelMetricSnapshot.channel_id,
                PrChannelMetricSnapshot.observed_at,
                PrChannelMetricSnapshot.followers,
                PrChannelMetricSnapshot.source,
                func.row_number()
                .over(
                    partition_by=PrChannelMetricSnapshot.channel_id,
                    order_by=LATEST_FIRST,
                )
                .label("rank"),
            )
            .where(PrChannelMetricSnapshot.channel_id.in_(unique))
            .subquery()
        )
        result = await self._session.execute(
            select(
                ranked.c.channel_id,
                ranked.c.observed_at,
                ranked.c.followers,
                ranked.c.source,
            ).where(ranked.c.rank == 1)
        )
        latest = {row[0]: row for row in result.all()}

        # Step 1F.2.4b. One more statement for the whole page, not one per card.
        connections = await self._live_connections(unique)

        now = utcnow()
        summaries: dict[uuid.UUID, ChannelMetricsSummary] = {}
        for channel_id in unique:
            row = latest.get(channel_id)
            connection = connections.get(channel_id)
            observed_at = row[1] if row is not None else None
            source: PrMetricSource | None = row[3] if row is not None else None
            summaries[channel_id] = ChannelMetricsSummary(
                channel_id=channel_id,
                status=metrics_status_for(
                    connection_state=connection.status if connection is not None else None,
                    has_snapshot=row is not None,
                ),
                latest_captured_at=observed_at,
                followers=row[2] if row is not None else None,
                days_since_capture=stale_days(observed_at, now=now),
                latest_source=source.value if source is not None else None,
                connection_state=connection.status if connection is not None else None,
                sync_status=connection.sync_status if connection is not None else None,
                last_sync_succeeded_at=(
                    connection.last_sync_succeeded_at if connection is not None else None
                ),
            )
        return summaries

    async def _live_connections(
        self, channel_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, PrChannelConnection]:
        """Live connections for a whole page, in one statement.

        Read here rather than through ``PrChannelConnectionService`` on purpose:
        this method must never touch a credential, and going through the service
        that owns decryption for a badge would put the secret one attribute
        access away from a list response.
        """
        result = await self._session.execute(
            select(PrChannelConnection).where(
                PrChannelConnection.channel_id.in_(channel_ids),
                PrChannelConnection.status != PrChannelConnectionState.DISCONNECTED,
            )
        )
        return {row.channel_id: row for row in result.scalars().all()}

    async def _observations(self, channel_id: uuid.UUID) -> list[MetricObservation]:
        """This channel's recent readings, newest first, for the derivations.

        Bounded twice - by
        :data:`~meobot.domain.pr.channel_analytics.ANALYTICS_LOOKBACK_DAYS` and
        by :data:`ANALYTICS_ROW_LIMIT` - and bounded **in SQL**. A panel that
        loaded a channel's whole metric history to subtract two numbers would be
        the query that gets slow quietly, which is the failure
        :meth:`due_connections` next door is written the way it is to avoid.

        The window is measured from *now* rather than from the newest reading.
        That is deliberate: a channel whose last sync was two months ago has no
        recent readings, and its growth cards go blank rather than reporting a
        two-month-old delta as this month's - which is the same judgement
        ``days_since_capture`` already makes visible beside them.
        """
        since = utcnow() - timedelta(days=ANALYTICS_LOOKBACK_DAYS)
        result = await self._session.execute(
            self._base(channel_id)
            .where(PrChannelMetricSnapshot.observed_at >= since)
            .order_by(*LATEST_FIRST)
            .limit(ANALYTICS_ROW_LIMIT)
        )
        return [
            MetricObservation(
                observed_at=row.observed_at,
                metrics={name: getattr(row, name) for name in MANUAL_METRIC_FIELDS},
                extra_metrics=row.extra_metrics,
            )
            for row in result.scalars().all()
        ]

    async def has_history(self, channel_id: uuid.UUID) -> bool:
        """Whether anything has ever been recorded for this channel.

        What the channel form asks before warning that changing a platform
        leaves readings behind that were taken under the old one.
        """
        return await self._count(channel_id) > 0

    # --- Writing ----------------------------------------------------------
    async def record_manual_snapshot(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        command: RecordChannelMetricsCommand,
    ) -> PrChannelMetricSnapshot:
        """Append one hand-entered reading. ``PR_CHANNEL_MANAGE``.

        The capability is the existing one, not a new ``PR_METRICS_RECORD``.
        Whoever registers the channels is whoever writes down what they are
        doing, and a grant nobody would ever hold on its own is a row that
        quietly means nothing - the same reasoning ``PrPlatformService``
        recorded when it declined to invent ``PR_PLATFORM_MANAGE``.

        Raises:
            PrNotFoundError: No channel with that id.
            PrValidationError: No metric given, a negative or absurd count, or a
                capture time in the future.
            PrConflictError: A manual reading already exists for this channel at
                this exact instant.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)
        channel = await self._require_channel(command.channel_id)

        captured_at = self._require_capture_time(command.captured_at)
        metrics = self._require_metrics(command)
        await self._require_free_instant(command.channel_id, captured_at)

        snapshot = PrChannelMetricSnapshot(
            channel_id=channel.id,
            observed_at=captured_at,
            # Written here, never taken from the command. This endpoint records
            # what a person typed, and only that.
            source=PrMetricSource.MANUAL,
            recorded_by_user_id=actor.user_id,
            extra_metrics=command.extra_metrics or None,
            **metrics,
        )
        self._session.add(snapshot)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_METRICS_RECORDED,
            entity_type="pr_channel_metric_snapshot",
            entity_id=snapshot.id,
            after={
                "channel_id": str(channel.id),
                "channel_code": channel.code,
                "snapshot_id": str(snapshot.id),
                "captured_at": captured_at.isoformat(),
                "source": PrMetricSource.MANUAL.value,
            },
        )
        logger.info(
            "pr_channel_metrics_recorded",
            extra={
                "pr_channel_code": channel.code,
                "pr_snapshot_id": str(snapshot.id),
                "captured_at": captured_at.isoformat(),
            },
        )
        return snapshot

    # --- Internals --------------------------------------------------------
    async def _require_channel(self, channel_id: uuid.UUID) -> PrChannel:
        channel = await self._session.get(PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No PR channel with that id", details={"channel_id": str(channel_id)}
            )
        return channel

    @staticmethod
    def _require_capture_time(moment: datetime) -> datetime:
        """UTC, and not from the future."""
        captured_at = normalize_capture_time(moment)
        if is_future_capture(captured_at, now=utcnow()):
            raise PrValidationError(
                "Thời điểm ghi nhận không thể ở tương lai.",
                details={"field": "captured_at", "captured_at": captured_at.isoformat()},
            )
        return captured_at

    @staticmethod
    def _require_metrics(command: RecordChannelMetricsCommand) -> dict[str, int | None]:
        """At least one number, and every number a plausible count.

        A reading with nothing in it records that somebody opened a form. Zero
        is accepted everywhere - a channel really can have zero shares in a
        month - which is the whole reason "at least one" is a count of *given*
        fields rather than of truthy ones.
        """
        metrics = command.metrics()
        if all(value is None for value in metrics.values()):
            raise PrValidationError(
                "Hãy nhập ít nhất một chỉ số.",
                details={"fields": list(MANUAL_METRIC_FIELDS)},
            )
        for name, value in metrics.items():
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise PrValidationError(
                    "Chỉ số phải là số nguyên.", details={"field": name, "value": str(value)}
                )
            if value < 0:
                raise PrValidationError(
                    "Chỉ số không thể là số âm.", details={"field": name, "value": value}
                )
            if value > MAX_METRIC_VALUE:
                raise PrValidationError(
                    "Chỉ số lớn bất thường, hãy kiểm tra lại.",
                    details={"field": name, "value": value, "max": MAX_METRIC_VALUE},
                )
        return metrics

    async def _require_free_instant(self, channel_id: uuid.UUID, captured_at: datetime) -> None:
        """Refuse a second manual reading at an instant that already has one.

        0013's unique index would refuse it anyway; this turns the refusal into
        a sentence naming the clash instead of an integrity error at flush time,
        and it is why the correction path is "record the right number now"
        rather than "overwrite 09:00".
        """
        existing = await self._session.execute(
            select(PrChannelMetricSnapshot.id)
            .where(
                PrChannelMetricSnapshot.channel_id == channel_id,
                PrChannelMetricSnapshot.observed_at == captured_at,
                PrChannelMetricSnapshot.source == PrMetricSource.MANUAL,
            )
            .limit(1)
        )
        clash = existing.scalar_one_or_none()
        if clash is not None:
            raise PrConflictError(
                "Kênh này đã có một lần ghi nhận thủ công tại đúng thời điểm đó. "
                "Hãy ghi nhận ở một thời điểm khác - lịch sử chỉ số chỉ ghi thêm, "
                "không sửa đè.",
                details={
                    "channel_id": str(channel_id),
                    "captured_at": captured_at.isoformat(),
                    "existing_snapshot_id": str(clash),
                },
            )

    @staticmethod
    def _base(channel_id: uuid.UUID) -> Select[tuple[PrChannelMetricSnapshot]]:
        return select(PrChannelMetricSnapshot).where(
            PrChannelMetricSnapshot.channel_id == channel_id
        )

    async def _count(self, channel_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(PrChannelMetricSnapshot)
            .where(PrChannelMetricSnapshot.channel_id == channel_id)
        )
        return int(result.scalar_one())

    async def _page(
        self, channel_id: uuid.UUID, *, limit: int, offset: int
    ) -> Sequence[PrChannelMetricSnapshot]:
        result = await self._session.execute(
            self._base(channel_id).order_by(*LATEST_FIRST).limit(limit).offset(offset)
        )
        return result.scalars().all()

    async def _recorder_names(
        self, snapshots: Iterable[PrChannelMetricSnapshot]
    ) -> dict[uuid.UUID, str]:
        """``user_id`` -> display name, for a whole page, in one query.

        No status filter. A reading entered by somebody who has since been
        suspended still says who entered it - hiding the name would make the
        history less attributable the longer it survived.
        """
        wanted = {
            snapshot.recorded_by_user_id
            for snapshot in snapshots
            if snapshot.recorded_by_user_id is not None
        }
        if not wanted:
            return {}
        result = await self._session.execute(
            select(User.id, User.full_name).where(User.id.in_(wanted))
        )
        return {row[0]: row[1] for row in result.all()}

    @staticmethod
    def _as_view(snapshot: PrChannelMetricSnapshot, names: dict[uuid.UUID, str]) -> SnapshotView:
        return SnapshotView(
            snapshot=snapshot,
            recorded_by_name=(
                names.get(snapshot.recorded_by_user_id)
                if snapshot.recorded_by_user_id is not None
                else None
            ),
        )

    @classmethod
    def _view(
        cls, snapshot: PrChannelMetricSnapshot | None, names: dict[uuid.UUID, str]
    ) -> SnapshotView | None:
        return None if snapshot is None else cls._as_view(snapshot, names)


__all__: list[str] = [
    "ANALYTICS_ROW_LIMIT",
    "DEFAULT_SNAPSHOT_PAGE",
    "LATEST_FIRST",
    "MAX_SNAPSHOT_PAGE",
    "ChannelMetricsSummary",
    "ChannelMetricsView",
    "PrChannelMetricsService",
    "RecordChannelMetricsCommand",
    "SnapshotView",
]
