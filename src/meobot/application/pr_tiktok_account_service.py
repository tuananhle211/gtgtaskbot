"""What a person sees when they open a connected TikTok channel.

Step 1F.2.9. A **read**, on top of everything Step 1F.2.6 already built. It adds
no table, no credential store, no second sync and no second registry: it asks
:class:`~meobot.application.pr_channel_connection_service.PrChannelConnectionService`
for an access token exactly as the sync does, hands it to the **same**
:class:`~meobot.integrations.tiktok.provider.TikTokChannelMetricsProvider` the
sweeper uses, and returns what TikTok answered.

Why this is a read and not a sync
----------------------------------

The two answer different questions and must not be collapsed.

A **sync** produces a *reading*: four numbers, fingerprinted, appended to a time
series, compared against its predecessors, and worth retrying on a worker
because nobody is waiting for it. It has been running on a daily cadence since
Step 1F.2.4b and nothing here changes it.

This produces a *picture*: an avatar, a handle, a bio, the four lifetime
counters and a page of recent videos. It is stored nowhere, it has no
fingerprint, and it is worth exactly one HTTP round trip because somebody is
looking at the screen right now. A cover image URL that TikTok expires within
hours is the clearest evidence that this data has no business in a database.

So the panel does both, from one button: ``refresh`` re-reads the picture *and*
asks the existing sync infrastructure for a fresh reading. Two mechanisms, one
press, and neither one reimplemented.

The wrong-account check, again
-------------------------------

``/v2/user/info/`` has no id parameter - it answers for whoever the token
belongs to - so the ``open_id`` that comes back is authoritative. It is compared
against the connection's ``provider_account_id`` here for the same reason
:meth:`~meobot.integrations.tiktok.provider.TikTokChannelMetricsProvider.fetch_channel_metrics`
compares it: a panel that showed one account's followers under another account's
channel name would be worse than a panel that showed nothing.

What never leaves this module
------------------------------

The access token. It is a local in :meth:`_read`, it is passed to the provider
as an argument, and there is nowhere in
:class:`TikTokAccountView` to put it - the dataclass has no such field and
``slots=True`` means nothing can attach one. The refresh token is never even
decrypted here; ``access_token_for`` owns that, and owns writing back the
rotated one TikTok hands out on every refresh.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_connection_service import PrChannelConnectionService
from meobot.application.pr_channel_providers import build_provider
from meobot.application.pr_channel_sync_service import PrChannelSyncService, error_message
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrChannel, PrPlatform
from meobot.db.models.pr_channel_connection import PrChannelConnection
from meobot.domain.identity.models import Actor
from meobot.domain.pr.channel_connections import (
    ChannelSyncProviderError,
    PrChannelConnectionState,
    PrChannelSyncErrorCode,
)
from meobot.domain.pr.channel_metrics import PrChannelPlatform, platform_from_code
from meobot.domain.pr.errors import PrNotFoundError, PrValidationError
from meobot.domain.pr.policy import PR_READ_PERMISSION, PrCapability, require_permission
from meobot.integrations.tiktok.constants import MAX_VIDEO_PAGES, RECENT_VIDEO_PAGE
from meobot.integrations.tiktok.provider import (
    TikTokAccountOverview,
    TikTokChannelMetricsProvider,
    TikTokRecentVideos,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TikTokAccountView:
    """One live look at a connected TikTok account.

    **Holds no credential and cannot be made to.** There is no token field, no
    encrypted envelope and no client secret; ``slots=True`` means nothing can
    quietly attach one at runtime either. The API schema built from this is an
    explicit field list on top of a dataclass that has nothing dangerous in it,
    which is two independent reasons a token cannot reach a browser.
    """

    channel: PrChannel
    connection: PrChannelConnection
    account: TikTokAccountOverview
    videos: TikTokRecentVideos
    #: The scopes **TikTok granted**, as stored on the connection at consent -
    #: which may be fewer than were asked for. Scope names are not secrets: they
    #: are the list a person saw and agreed to, and printing them is the point.
    granted_scopes: tuple[str, ...]
    #: When MeoBot asked TikTok, not when TikTok's numbers were true. The
    #: Display API serves lifetime counters with no reporting window at all, so
    #: there is no second timestamp to conflate this with.
    fetched_at: datetime
    can_manage_connection: bool
    #: How many times a caller may follow TikTok's cursor. The connector's own
    #: ceiling, sent to the browser so the bound on "Xem thêm" is the server's
    #: rather than a number a page decided for itself.
    max_video_pages: int = MAX_VIDEO_PAGES
    #: Step 1F.2.9. True when this call also handed the connection to the
    #: existing sync infrastructure. False on a plain read, and false on a
    #: refresh that found a sync already running - which is not a failure, and
    #: must not be reported as one: the picture on screen was still refreshed.
    sync_requested: bool = False


class PrTikTokAccountService:
    """Reads a connected TikTok account for display. Writes nothing itself.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        capabilities: Resolves PR capabilities. Reading is the ordinary PR read
            permission; asking TikTok for fresh numbers is
            ``PR_CHANNEL_MANAGE``, the same capability the existing manual sync
            requires.
        connections: Owns the credential. This service never decrypts anything -
            it asks for an access token and gets one, and the rotated refresh
            token TikTok returns is written back by that service, not this one.
        sync: The existing sync orchestration, used unchanged. ``refresh`` calls
            its ``request_sync`` and ``claim`` rather than reimplementing the
            audit event, the state check or the lock.
        settings: Deployment configuration. The TikTok app credentials live
            here, never on a channel row.
    """

    provider = PrChannelPlatform.TIKTOK

    def __init__(
        self,
        session: AsyncSession,
        capabilities: PrCapabilityService,
        connections: PrChannelConnectionService,
        sync: PrChannelSyncService,
        settings: Settings,
    ) -> None:
        self._session = session
        self._capabilities = capabilities
        self._connections = connections
        self._sync = sync
        self._settings = settings

    # --- Reading ----------------------------------------------------------
    async def describe(
        self,
        *,
        actor: Actor,
        channel_id: uuid.UUID,
        video_limit: int = RECENT_VIDEO_PAGE,
        cursor: int | None = None,
        provider_client: TikTokChannelMetricsProvider | None = None,
    ) -> TikTokAccountView:
        """The account, its counters and one bounded page of its videos.

        Whoever may read the PR module may read this, which is the same rule
        :meth:`~meobot.application.pr_channel_metrics_service.PrChannelMetricsService.describe`
        applies to a channel's stored numbers. It is a live call to TikTok, so
        it is bounded: one ``user/info`` request and one ``video/list`` page.

        Raises:
            PrNotFoundError: No such channel.
            PrValidationError: The channel is not on TikTok, has no live TikTok
                connection, or the connection is not usable. Also every provider
                failure, carrying MeoBot's own Vietnamese sentence and the safe
                error class - never TikTok's prose and never its payload.
        """
        require_permission(actor, PR_READ_PERMISSION)
        can_manage = await self._capabilities.allows(actor, PrCapability.PR_CHANNEL_MANAGE)
        channel, connection = await self._require_connected(channel_id)
        return await self._read(
            channel=channel,
            connection=connection,
            video_limit=video_limit,
            cursor=cursor,
            provider_client=provider_client,
            can_manage=can_manage,
        )

    async def refresh(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        channel_id: uuid.UUID,
        video_limit: int = RECENT_VIDEO_PAGE,
        provider_client: TikTokChannelMetricsProvider | None = None,
    ) -> TikTokAccountView:
        """ "Đồng bộ lại": re-read the picture **and** ask for a fresh reading.

        Two things, deliberately, because a person pressing one button means
        both and would otherwise have to press two:

        1. the existing sync infrastructure is asked for a metric snapshot -
           :meth:`~meobot.application.pr_channel_sync_service.PrChannelSyncService.request_sync`
           for the capability check and the audit event, then its ``claim`` for
           the lock. **No second sync system**, no second audit action, no
           second lock;
        2. the account and its videos are read live, here, in this request, and
           returned - which is what makes the press visibly do something. The
           snapshot lands on the worker whenever it lands.

        A claim that fails means a sync is already running. That is **not an
        error** and does not raise: the live read still happened, the screen
        still updated, and :attr:`TikTokAccountView.sync_requested` says the
        snapshot was left to the run already in flight. Refusing the whole call
        over it would make a harmless double-click look like a breakage.

        The caller enqueues the worker task when ``sync_requested`` is true -
        deliberately not done here, because an application service that imported
        a Celery task would be an application service that could dispatch one,
        and the PR services are kept free of both by an architecture test.

        Raises:
            PrPermissionDeniedError: Not a channel manager.
            PrNotFoundError: No such channel.
            PrValidationError: As :meth:`describe`, plus a connection that is
                not ``CONNECTED`` - which ``request_sync`` refuses before
                anything is claimed or fetched.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)
        channel, connection = await self._require_connected(channel_id)

        # The existing manual-sync path, unchanged: it re-checks the capability,
        # re-checks that the connection is CONNECTED, and writes the one audit
        # event that answers "who asked for this".
        await self._sync.request_sync(actor=actor, request_id=request_id, channel_id=channel_id)
        claimed = await self._sync.claim(connection.id)
        if not claimed:
            logger.info(
                "tiktok_refresh_sync_already_running",
                extra={"pr_connection_id": str(connection.id)},
            )

        view = await self._read(
            channel=channel,
            connection=connection,
            video_limit=video_limit,
            cursor=None,
            provider_client=provider_client,
            can_manage=True,
        )
        return replace(view, sync_requested=claimed)

    # --- Internals --------------------------------------------------------
    async def _read(
        self,
        *,
        channel: PrChannel,
        connection: PrChannelConnection,
        video_limit: int,
        cursor: int | None,
        provider_client: TikTokChannelMetricsProvider | None,
        can_manage: bool,
    ) -> TikTokAccountView:
        """One access token, two bounded Display API calls, one view.

        Every provider failure is caught here and turned into MeoBot's own
        sentence. Nothing from TikTok's response body travels further than this
        method: not the error prose, not the ``log_id``, not the status line.
        """
        client = provider_client or self._provider()
        try:
            # The one call that decrypts a stored credential - and the one that
            # persists TikTok's rotated refresh token, which is why it has to
            # happen inside the caller's transaction.
            token = await self._connections.access_token_for(connection, provider_client=client)
            account = await client.fetch_account_overview(access_token=token)
            videos = await client.fetch_recent_videos(
                access_token=token, limit=video_limit, cursor=cursor
            )
        except ChannelSyncProviderError as exc:
            raise self._provider_failure(exc.error_code) from exc

        if account.open_id != connection.provider_account_id:
            # The same wrong-account check the sync makes, for the same reason:
            # showing one account's numbers under another account's channel name
            # is worse than showing none.
            raise self._provider_failure(PrChannelSyncErrorCode.INVALID_ACCOUNT)

        return TikTokAccountView(
            channel=channel,
            connection=connection,
            account=account,
            videos=videos,
            granted_scopes=tuple((connection.granted_scopes or "").split()),
            fetched_at=utcnow(),
            can_manage_connection=can_manage,
        )

    def _provider(self) -> TikTokChannelMetricsProvider:
        """The registry's TikTok provider, refused if this deployment has none.

        Through ``build_provider`` rather than constructed here, so the
        "supported" and "configured" refusals stay in the one place that owns
        them - and so a deployment with no TikTok app gets
        ``PrConnectorNotConfiguredError`` with its "set these three environment
        variables" sentence instead of a confusing empty panel.
        """
        client = build_provider(self.provider, self._settings)
        # The registry is typed to the port, which has no account overview on
        # it. This narrowing is the one place the TikTok-specific reads are
        # named, and it cannot be reached for another platform: the channel's
        # own platform was checked before we got here.
        assert isinstance(client, TikTokChannelMetricsProvider)
        return client

    def _provider_failure(self, error_code: PrChannelSyncErrorCode) -> PrValidationError:
        """A provider failure as something a Vietnamese screen can say.

        Reuses
        :func:`~meobot.application.pr_channel_sync_service.error_message` rather
        than composing new prose, so the sentence a person reads under the
        account panel is word for word the one the connection panel already
        shows for the same failure. The safe error *class* travels in
        ``details`` for a client that wants to branch; TikTok's own words never
        travel at all.
        """
        return PrValidationError(
            error_message(error_code, self.provider),
            details={"reason": "provider_error", "error_code": error_code.value},
        )

    async def _require_connected(
        self, channel_id: uuid.UUID
    ) -> tuple[PrChannel, PrChannelConnection]:
        """The channel and its live TikTok connection, or a refusal saying which.

        Three separate refusals rather than one, because they need three
        different actions: register the channel, put it on TikTok, or press
        "Kết nối TikTok".
        """
        channel = await self._session.get(PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No PR channel with that id", details={"channel_id": str(channel_id)}
            )
        platform_row = await self._session.get(PrPlatform, channel.platform_id)
        if platform_from_code(platform_row.code if platform_row else None) is not self.provider:
            raise PrValidationError(
                "Kênh này không phải kênh TikTok.",
                details={"reason": "not_tiktok", "channel_id": str(channel_id)},
            )
        connection = await self._connections.get_live_connection(
            channel_id, provider=PrChannelPlatform.TIKTOK
        )
        if connection is None or connection.status is not PrChannelConnectionState.CONNECTED:
            raise PrValidationError(
                "Kênh này chưa kết nối TikTok.",
                details={"reason": "not_connected", "channel_id": str(channel_id)},
            )
        return channel, connection


__all__: list[str] = [
    "PrTikTokAccountService",
    "TikTokAccountView",
]
