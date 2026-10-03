"""Connecting a PR channel to a platform account, and taking it down again.

Step 1F.2.4b. Everything about the **credential**: starting an authorization,
finishing one safely, keeping the refresh token, handing it back to a sync, and
disconnecting. Measurement is
:mod:`meobot.application.pr_channel_sync_service`; transport is
:mod:`meobot.integrations.youtube`.

The state token is the whole security model of the callback
------------------------------------------------------------

An OAuth callback is an unauthenticated GET that anybody on the internet can
make with any query string they like. Nothing in it may be trusted - and in
particular **the channel is never read from the callback**. It comes from the
state row, which MeoBot wrote when it started the flow.

The token is minted with :func:`secrets.token_urlsafe`, only its SHA-256 is
stored, and redeeming it checks four things in order: it exists, it has not
expired, it has not been consumed, and the session completing it belongs to the
user who started it. A row is marked consumed **before** any Google call, so two
callbacks racing on one state cannot both establish a connection.

That last check is why a stolen link is not enough: an attacker who obtains a
state token still has to be signed in as the person who started that
authorization, in a browser holding that person's session cookie.

Why no PKCE
-----------

PKCE protects a *public* client - one that cannot keep a secret, where the
authorization code is redeemed from a device an attacker may share. This is a
confidential server-side client: the code is redeemed by the API container using
a client secret that never leaves it, over a channel the browser is not part of.
PKCE would add a verifier to store and prove nothing extra, because the client
secret already does what the verifier would.

The interception PKCE exists to stop is prevented here by a different property:
the code arrives on a redirect URI registered on the OAuth client, is bound to a
single-use state, and is exchanged server-to-server.

The refresh token is the only thing worth stealing
---------------------------------------------------

So it is the only thing encrypted, it is never returned by any API, never
audited, never logged, and never held in a variable longer than one call needs.
:meth:`access_token_for` is the single place it is decrypted, and what it hands
back is an access token that expires within the hour.

**An omitted refresh token is not a revocation.** Google issues one on first
consent and normally omits it afterwards; a reconnect that read that omission as
"clear the stored token" would silently break unattended sync for every channel
anybody ever reconnected. :meth:`finish_authorization` keeps what it has.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_providers import build_provider, supports
from meobot.application.pr_support import record_pr_event
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.secrets import SecretBox, SecretDecryptionError, build_secret_box
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.pr import PrChannel, PrPlatform
from meobot.db.models.pr_channel_connection import PrChannelConnection, PrChannelOAuthState
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.channel_connections import (
    PrChannelConnectionState,
    PrChannelSyncStatus,
)
from meobot.domain.pr.channel_metrics import (
    AccountSelectingProvider,
    ChannelMetricsProvider,
    DiscoveredProviderAccount,
    FetchedChannelIdentity,
    PrChannelPlatform,
    ProviderAccountChoices,
    ProviderTokens,
    platform_from_code,
)
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.policy import PrCapability

logger = get_logger(__name__)

#: Bytes of entropy in a state token. 32 bytes through ``token_urlsafe`` is 43
#: characters; guessing one inside its ten-minute life is not a threat model.
STATE_TOKEN_BYTES = 32


def hash_state(token: str) -> str:
    """SHA-256 of a state token, hex. The only form ever written down.

    Plain SHA-256 rather than a password hash on purpose, and for the same
    reason ``web_auth_service.hash_token`` gives: this is a
    high-entropy random token, not a human-chosen secret, so there is nothing
    for a slow hash to defend against and a fast comparison is what an index
    can use.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizationStart:
    """Where to send the browser, and what MeoBot wrote down about it."""

    authorization_url: str
    state_id: uuid.UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ConnectionOutcome:
    """The result of finishing an authorization.

    ``rebound_from_account_id`` is set when a reconnect landed on a **different**
    YouTube account than the one this channel was bound to. It is not an error -
    somebody may genuinely be moving a channel - but it must never be silent, so
    it travels back to the UI and into the audit trail.
    """

    connection: PrChannelConnection
    identity: FetchedChannelIdentity
    is_reconnect: bool
    rebound_from_account_id: str | None = None
    external_id_conflict: str | None = None
    #: Step 1F.2.4c. Set when consent succeeded and **nothing is bound yet** -
    #: the person has several Pages or Instagram accounts and must pick one.
    #: The connection is ``PENDING_SELECTION`` and cannot sync until they do.
    pending_accounts: tuple[DiscoveredProviderAccount, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountChoices:
    """What the account picker draws after a Meta authorization."""

    channel_id: uuid.UUID
    provider: PrChannelPlatform
    #: The domain's type, not a second one - it already exists precisely so a
    #: discovered account can travel without a credential attached.
    accounts: tuple[DiscoveredProviderAccount, ...] = ()


class PrChannelConnectionService:
    """The credential half of a channel connector.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities. Managing a connection is
            ``PR_CHANNEL_MANAGE`` - the capability that already means "may
            change the publishing registry" - rather than a new grant nobody
            would hold independently.
        settings: Deployment configuration. The OAuth client and the encryption
            key live here, never on a channel row.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        settings: Settings,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._settings = settings
        self._box: SecretBox | None = None

    # --- Reading ----------------------------------------------------------
    async def get_live_connection(
        self, channel_id: uuid.UUID, *, provider: PrChannelPlatform
    ) -> PrChannelConnection | None:
        """This channel's live connection for ``provider``, if it has one."""
        result = await self._session.execute(
            select(PrChannelConnection)
            .where(
                PrChannelConnection.channel_id == channel_id,
                PrChannelConnection.provider == provider,
                PrChannelConnection.status != PrChannelConnectionState.DISCONNECTED,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def connections_for_channels(
        self, channel_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, PrChannelConnection]:
        """Live connections for a whole page of channels, in one query.

        What the channel list uses for its badge. The alternative - asking per
        card - is the N+1 Step 1F.2.4a went to some trouble to avoid, and adding
        a connector is not a reason to reintroduce it.
        """
        unique = list(dict.fromkeys(channel_ids))
        if not unique:
            return {}
        result = await self._session.execute(
            select(PrChannelConnection).where(
                PrChannelConnection.channel_id.in_(unique),
                PrChannelConnection.status != PrChannelConnectionState.DISCONNECTED,
            )
        )
        return {row.channel_id: row for row in result.scalars().all()}

    # --- Authorizing ------------------------------------------------------
    async def start_authorization(
        self,
        *,
        actor: Actor,
        channel_id: uuid.UUID,
        provider: PrChannelPlatform = PrChannelPlatform.YOUTUBE,
    ) -> AuthorizationStart:
        """Mint a state token and build the consent URL.

        Raises:
            PrPermissionDeniedError: Not a channel manager.
            PrNotFoundError: No such channel.
            PrValidationError: The channel's platform has no connector - a
                TikTok channel cannot be pointed at YouTube's OAuth, and the
                refusal is here on the server rather than in a hidden button.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)
        channel = await self._require_channel(channel_id)
        await self._require_provider_platform(channel, provider)
        if actor.user_id is None:
            raise PrPermissionDeniedError(
                "Chỉ tài khoản đã đăng nhập mới kết nối được kênh.",
                details={"channel_id": str(channel_id)},
            )

        client = build_provider(provider, self._settings)
        existing = await self.get_live_connection(channel_id, provider=provider)

        token = secrets.token_urlsafe(STATE_TOKEN_BYTES)
        expires_at = utcnow() + timedelta(seconds=self._settings.youtube_oauth_state_ttl_seconds)
        state = PrChannelOAuthState(
            state_hash=hash_state(token),
            channel_id=channel_id,
            provider=provider,
            user_id=actor.user_id,
            is_reconnect=existing is not None,
            expires_at=expires_at,
        )
        self._session.add(state)
        await self._session.flush()

        return AuthorizationStart(
            authorization_url=client.build_authorization_url(state=token),
            state_id=state.id,
            expires_at=expires_at,
        )

    async def finish_authorization(
        self,
        *,
        request_id: uuid.UUID,
        state_token: str,
        code: str,
        session_actor: Actor | None = None,
        provider_client: ChannelMetricsProvider | None = None,
    ) -> ConnectionOutcome:
        """Redeem a state, exchange the code, and bind the account.

        **Takes no required actor.** The person, the channel and the provider all
        come from the state row - see :meth:`_redeem_state`. A browser returning
        from Google or Meta may or may not carry a MeoBot session cookie
        depending on cross-site cookie policy, and a flow whose security rests
        on a single-use bound state must not also rest on that.

        ``session_actor`` is the session **if one happened to arrive**, used as a
        cross-check and never as the source of identity.

        The order matters and is not negotiable: the state is validated and
        **consumed before** anything is sent to the provider. Two callbacks
        racing on one state therefore cannot both reach the exchange, and a
        replay finds a row that is already consumed.

        ``code`` is never logged. It is a bearer credential for a few seconds,
        and a log line holding one can be replayed into somebody's account.

        Raises:
            PrValidationError: The state is missing, unknown, expired, already
                used, presented by a different signed-in session, or names a
                user who can no longer act. All indistinguishable by design.
            PrPermissionDeniedError: The person who started the flow may no
                longer manage channels.
        """
        state = await self._redeem_state(state_token=state_token, session_actor=session_actor)
        # From here on, ``actor`` is the person the state names - never the
        # caller, who may be an unauthenticated redirect.
        actor = await self._actor_for_state(state)
        channel = await self._require_channel(state.channel_id)
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)

        client = provider_client or build_provider(state.provider, self._settings)
        tokens = await client.exchange_authorization_code(code=code)

        if isinstance(client, AccountSelectingProvider):
            # A provider whose consent reaches several accounts - Meta, where
            # one person routinely manages a dozen Pages. Binding the first
            # result would be a coin flip with somebody's brand, so the flow
            # pauses here and a person chooses. See :meth:`select_account`.
            return await self._begin_selection(
                actor=actor,
                request_id=request_id,
                state=state,
                channel=channel,
                client=client,
                tokens=tokens,
            )

        identity = await client.fetch_channel_identity(access_token=tokens.access_token)

        existing = await self.get_live_connection(state.channel_id, provider=state.provider)
        rebound_from = None
        if existing is not None and existing.provider_account_id != identity.account_id:
            # A reconnect that landed somewhere else. Allowed - a channel can
            # genuinely move - but never silent: it is returned to the UI and
            # written into the audit trail on both sides.
            rebound_from = existing.provider_account_id

        connection = existing or PrChannelConnection(
            channel_id=state.channel_id,
            provider=state.provider,
            provider_account_id=identity.account_id,
        )
        connection.provider_account_id = identity.account_id
        connection.provider_account_name = identity.title
        connection.provider_account_handle = identity.handle
        connection.status = PrChannelConnectionState.CONNECTED
        connection.connected_by_user_id = actor.user_id
        connection.connected_at = utcnow()
        connection.disconnected_at = None
        # A fresh consent clears a previous auth failure. Leaving the old error
        # in place would keep the panel telling somebody to reconnect after they
        # just did.
        connection.last_sync_error_code = None
        connection.last_sync_error_message = None
        connection.consecutive_failures = 0
        if connection.sync_status is PrChannelSyncStatus.SYNCING:
            connection.sync_status = PrChannelSyncStatus.NEVER_SYNCED
        if tokens.granted_scopes:
            connection.granted_scopes = " ".join(tokens.granted_scopes)
        if existing is None:
            self._session.add(connection)
        await self._session.flush()

        self._store_credential(connection, tokens)

        conflict = self._reconcile_external_id(channel, identity)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=(
                AuditAction.PR_CHANNEL_CONNECTION_RECONNECTED
                if existing is not None
                else AuditAction.PR_CHANNEL_CONNECTION_CONNECTED
            ),
            entity_type="pr_channel_connection",
            entity_id=connection.id,
            # Scope *names* are safe and useful - a missing one is how
            # INSUFFICIENT_SCOPE gets diagnosed. No token, ever.
            after={
                "channel_id": str(channel.id),
                "channel_code": channel.code,
                "provider": state.provider.value,
                "provider_account_id": identity.account_id,
                "provider_account_name": identity.title,
                "granted_scopes": connection.granted_scopes,
                "connected_by": str(actor.user_id) if actor.user_id else None,
                "rebound_from_account_id": rebound_from,
            },
        )
        logger.info(
            "pr_channel_connection_established",
            extra={
                "pr_channel_code": channel.code,
                "provider": state.provider.value,
                "provider_account_id": identity.account_id,
                "is_reconnect": existing is not None,
            },
        )
        return ConnectionOutcome(
            connection=connection,
            identity=identity,
            is_reconnect=existing is not None,
            rebound_from_account_id=rebound_from,
            external_id_conflict=conflict,
        )

    async def _begin_selection(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        state: PrChannelOAuthState,
        channel: PrChannel,
        client: AccountSelectingProvider,
        tokens: ProviderTokens,
    ) -> ConnectionOutcome:
        """Consent is done; discover what could be bound and pause for a choice.

        Three outcomes, and the middle one is the interesting one:

        * **nothing eligible** - the person authorized an account with no
          Facebook Page, or no Page with a linked Instagram professional
          account. No connection is created at all. A row that could never sync
          is worse than no row, because the panel would show it as connected;
        * **exactly one** - bound immediately, and the panel shows *which*
          account it bound. Making somebody choose from a list of one is
          ceremony, but hiding what was chosen is not acceptable either;
        * **several** - the connection is parked ``PENDING_SELECTION`` holding
          the long-lived user token, and the eligible accounts go back to the
          browser as ids and names. No token reaches it.
        """
        choices = await client.discover_accounts(access_token=tokens.access_token)
        if not choices.accounts:
            raise PrValidationError(
                self._nothing_eligible_message(state.provider),
                details={"reason": "no_eligible_account", "provider": state.provider.value},
            )

        connection = await self._upsert_pending(
            actor=actor, state=state, choices=choices, tokens=tokens
        )

        if len(choices.accounts) == 1:
            return await self._finalize_selection(
                actor=actor,
                request_id=request_id,
                channel=channel,
                connection=connection,
                client=client,
                access_token=tokens.access_token,
                account_id=choices.accounts[0].account_id,
            )

        return ConnectionOutcome(
            connection=connection,
            identity=FetchedChannelIdentity(
                account_id=choices.owner_account_id, title=choices.owner_name
            ),
            is_reconnect=False,
            pending_accounts=choices.accounts,
        )

    async def _upsert_pending(
        self,
        *,
        actor: Actor,
        state: PrChannelOAuthState,
        choices: ProviderAccountChoices,
        tokens: ProviderTokens,
    ) -> PrChannelConnection:
        """Park the connection holding the discovery credential.

        ``provider_account_id`` is set to the **authorizing account** - a real
        provider id, and a true statement: this connection holds a credential
        for that Meta user and has bound nothing yet. It is not a placeholder,
        which matters because the column is ``NOT NULL`` with a non-empty check
        and a sentinel would have been a lie the database accepted.
        """
        existing = await self.get_live_connection(state.channel_id, provider=state.provider)
        connection = existing or PrChannelConnection(
            channel_id=state.channel_id,
            provider=state.provider,
            provider_account_id=choices.owner_account_id,
        )
        if existing is None:
            connection.provider_account_id = choices.owner_account_id
            self._session.add(connection)
        connection.status = PrChannelConnectionState.PENDING_SELECTION
        connection.connected_by_user_id = actor.user_id
        connection.connected_at = utcnow()
        connection.disconnected_at = None
        if tokens.granted_scopes:
            connection.granted_scopes = " ".join(tokens.granted_scopes)
        await self._session.flush()
        # The discovery credential. Replaced by the chosen account's own token
        # the moment somebody picks, and dropped entirely if they never do -
        # see :meth:`expire_pending_selections`.
        self._store_credential(connection, tokens)
        await self._session.flush()
        return connection

    async def list_account_choices(
        self,
        *,
        actor: Actor,
        channel_id: uuid.UUID,
        provider_client: ChannelMetricsProvider | None = None,
    ) -> AccountChoices:
        """The eligible accounts for a connection waiting on a choice.

        Recomputed from the platform rather than replayed from anything the
        browser holds, so the list a person sees is what the authorization can
        reach *now*.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)
        connection = await self._require_pending(channel_id)
        client = provider_client or build_provider(connection.provider, self._settings)
        if not isinstance(client, AccountSelectingProvider):
            raise PrValidationError(
                "Nền tảng này không cần chọn tài khoản.",
                details={"reason": "selection_not_applicable"},
            )
        access_token = await self.access_token_for(connection, provider_client=client)
        choices = await client.discover_accounts(access_token=access_token)
        return AccountChoices(
            channel_id=channel_id,
            provider=connection.provider,
            accounts=choices.accounts,
        )

    async def select_account(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        channel_id: uuid.UUID,
        account_id: str,
        provider_client: ChannelMetricsProvider | None = None,
    ) -> ConnectionOutcome:
        """Bind the account a person chose, after proving they could reach it.

        **The submitted id is never trusted.** It is checked against a discovery
        result computed *here*, from the stored credential, at this moment - not
        against a list the browser was given earlier and not against the id
        alone. So a caller who posts an arbitrary Page id gets a refusal rather
        than a binding, and one who posts a Page they lost access to five
        minutes ago gets the same.

        Raises:
            PrValidationError: Nothing is awaiting selection, the flow expired,
                or the account is not reachable through this authorization.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)
        channel = await self._require_channel(channel_id)
        connection = await self._require_pending(channel_id)
        client = provider_client or build_provider(connection.provider, self._settings)
        if not isinstance(client, AccountSelectingProvider):
            raise PrValidationError(
                "Nền tảng này không cần chọn tài khoản.",
                details={"reason": "selection_not_applicable"},
            )

        access_token = await self.access_token_for(connection, provider_client=client)
        choices = await client.discover_accounts(access_token=access_token)
        allowed = {item.account_id for item in choices.accounts}
        if account_id not in allowed:
            raise PrValidationError(
                "Tài khoản này không thuộc quyền quản lý của tài khoản đã cấp quyền.",
                details={"reason": "account_not_accessible"},
            )

        return await self._finalize_selection(
            actor=actor,
            request_id=request_id,
            channel=channel,
            connection=connection,
            client=client,
            access_token=access_token,
            account_id=account_id,
        )

    async def _finalize_selection(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        channel: PrChannel,
        connection: PrChannelConnection,
        client: AccountSelectingProvider,
        access_token: str,
        account_id: str,
    ) -> ConnectionOutcome:
        """Swap the discovery credential for the bound account's own, and connect.

        The last step, and the one that decides what is stored: the user token
        that discovery ran on is replaced by the chosen Page's token, which does
        not expire and grants nothing beyond that Page. The wider credential
        never survives the flow.
        """
        previous_account = connection.provider_account_id
        bound = await client.bind_account(access_token=access_token, account_id=account_id)
        identity = bound.identity

        rebound_from = (
            previous_account
            if previous_account not in {"", identity.account_id, None}
            and connection.status is not PrChannelConnectionState.PENDING_SELECTION
            else None
        )

        connection.provider_account_id = identity.account_id
        connection.provider_account_name = identity.title
        connection.provider_account_handle = identity.handle
        connection.status = PrChannelConnectionState.CONNECTED
        connection.last_sync_error_code = None
        connection.last_sync_error_message = None
        connection.consecutive_failures = 0
        if connection.sync_status is PrChannelSyncStatus.SYNCING:
            connection.sync_status = PrChannelSyncStatus.NEVER_SYNCED
        await self._session.flush()
        self.store_credential_value(connection, bound.durable_credential)

        conflict = self._reconcile_external_id(channel, identity)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_CONNECTION_CONNECTED,
            entity_type="pr_channel_connection",
            entity_id=connection.id,
            after={
                "channel_id": str(channel.id),
                "channel_code": channel.code,
                "provider": connection.provider.value,
                "provider_account_id": identity.account_id,
                "provider_account_name": identity.title,
                "granted_scopes": connection.granted_scopes,
                "connected_by": str(actor.user_id) if actor.user_id else None,
                "rebound_from_account_id": rebound_from,
            },
        )
        logger.info(
            "pr_channel_connection_established",
            extra={
                "pr_channel_code": channel.code,
                "provider": connection.provider.value,
                "provider_account_id": identity.account_id,
                "is_reconnect": rebound_from is not None,
            },
        )
        return ConnectionOutcome(
            connection=connection,
            identity=identity,
            is_reconnect=rebound_from is not None,
            rebound_from_account_id=rebound_from,
            external_id_conflict=conflict,
        )

    async def _require_pending(self, channel_id: uuid.UUID) -> PrChannelConnection:
        """A connection awaiting a choice, and not one that has gone stale."""
        result = await self._session.execute(
            select(PrChannelConnection)
            .where(
                PrChannelConnection.channel_id == channel_id,
                PrChannelConnection.status == PrChannelConnectionState.PENDING_SELECTION,
            )
            .limit(1)
        )
        connection = result.scalar_one_or_none()
        if connection is None:
            raise PrValidationError(
                "Không có kết nối nào đang chờ chọn tài khoản.",
                details={"reason": "no_pending_selection"},
            )
        if self._selection_expired(connection):
            raise PrValidationError(
                "Phiên chọn tài khoản đã hết hạn. Hãy kết nối lại.",
                details={"reason": "selection_expired"},
            )
        return connection

    def _selection_expired(self, connection: PrChannelConnection) -> bool:
        started = connection.connected_at
        if started is None:
            return True
        ttl = self._settings.meta_account_selection_ttl_seconds
        return (utcnow() - ensure_utc(started)).total_seconds() > ttl

    async def expire_pending_selections(self) -> int:
        """Drop the credential from selection flows nobody finished.

        A parked connection is holding a long-lived **user** token - the widest
        credential in the whole flow - purely so a chooser can list accounts.
        Leaving one behind because somebody closed the tab is exactly the stale
        secret this step is required not to leave lying around, so the existing
        stale-sync sweeper collects them too rather than a new job being added.
        """
        result = await self._session.execute(
            select(PrChannelConnection).where(
                PrChannelConnection.status == PrChannelConnectionState.PENDING_SELECTION
            )
        )
        cleared = 0
        for connection in result.scalars().all():
            if not self._selection_expired(connection):
                continue
            connection.encrypted_credential = None
            connection.status = PrChannelConnectionState.DISCONNECTED
            connection.disconnected_at = utcnow()
            cleared += 1
        if cleared:
            await self._session.flush()
            logger.info("pr_channel_pending_selections_expired", extra={"count": cleared})
        return cleared

    @staticmethod
    def _nothing_eligible_message(provider: PrChannelPlatform) -> str:
        """What to say when consent worked and there is nothing to bind."""
        if provider is PrChannelPlatform.INSTAGRAM:
            return "Không tìm thấy tài khoản Instagram Professional phù hợp."
        if provider is PrChannelPlatform.FACEBOOK:
            return "Không tìm thấy Trang Facebook phù hợp."
        return "Không tìm thấy tài khoản phù hợp để kết nối."

    async def disconnect(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        channel_id: uuid.UUID,
        provider: PrChannelPlatform = PrChannelPlatform.YOUTUBE,
        provider_client: ChannelMetricsProvider | None = None,
    ) -> PrChannelConnection:
        """Revoke remotely where possible, drop the secret, keep the history.

        Three things happen and the order is chosen so the important one cannot
        be skipped: revoke at the platform (best effort), **drop the stored
        secret**, mark the row disconnected. A revoke that fails does not stop
        the other two - a channel nobody can disconnect because Google is having
        an afternoon would be worse than a grant that lingers on Google's side.

        **No metric snapshot is touched.** Every API reading this connection
        ever produced stays exactly where it is; so does every manual one. The
        connection row itself is kept, disconnected, because "this was connected
        and then was not" is history somebody may need to read.
        """
        await self._capabilities.require(actor, PrCapability.PR_CHANNEL_MANAGE)
        channel = await self._require_channel(channel_id)
        connection = await self.get_live_connection(channel_id, provider=provider)
        if connection is None:
            raise PrNotFoundError(
                "Kênh này chưa có kết nối nào để ngắt.",
                details={"channel_id": str(channel_id), "provider": provider.value},
            )

        credential = self._read_credential(connection)
        if credential is not None:
            client = provider_client or build_provider(provider, self._settings)
            await client.revoke(durable_credential=credential)

        connection.encrypted_credential = None
        connection.status = PrChannelConnectionState.DISCONNECTED
        connection.disconnected_at = utcnow()
        connection.auto_sync_enabled = False
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CHANNEL_CONNECTION_DISCONNECTED,
            entity_type="pr_channel_connection",
            entity_id=connection.id,
            after={
                "channel_id": str(channel.id),
                "channel_code": channel.code,
                "provider": provider.value,
                "provider_account_id": connection.provider_account_id,
            },
        )
        return connection

    # --- Tokens -----------------------------------------------------------
    async def access_token_for(
        self,
        connection: PrChannelConnection,
        *,
        provider_client: ChannelMetricsProvider | None = None,
    ) -> str:
        """Mint an access token from the stored refresh token.

        The **only** place a durable credential is decrypted, and what it
        returns is never written down. That is the answer to "should the access
        token be cached": no, because caching it would put a second secret at
        rest to save one round trip a day - and for Meta there is nothing to
        cache anyway, since the durable credential *is* the access token.

        Raises:
            PrValidationError: There is no usable credential. The caller turns
                this into ``ACTION_REQUIRED`` - see ``PrChannelSyncService``.
        """
        credential = self._read_credential(connection)
        if credential is None:
            raise PrValidationError(
                "Kết nối này cần xác thực lại.",
                details={"reason": "missing_credential"},
            )
        client = provider_client or build_provider(connection.provider, self._settings)
        tokens = await client.acquire_access(durable_credential=credential)
        # A provider may hand back a *rotated* durable credential. Storing it is
        # required when it happens, and the absence of one is normal - for Meta
        # it is always absent, because the credential it was given is the one it
        # returns.
        if tokens.durable_credential:
            self._store_credential(connection, tokens)
        return tokens.access_token

    def _store_credential(self, connection: PrChannelConnection, tokens: ProviderTokens) -> None:
        """Encrypt and keep a durable credential - or keep the one already held.

        The load-bearing ``if``. Google issues a refresh token on first consent
        and normally omits it on every authorization after that, so reading an
        omission as "clear the stored one" would break unattended sync for every
        channel anybody ever reconnected. It is a one-line rule with its own
        test for exactly that reason.
        """
        if not tokens.durable_credential:
            return
        self.store_credential_value(connection, tokens.durable_credential)

    def store_credential_value(self, connection: PrChannelConnection, credential: str) -> None:
        """Encrypt one credential onto a connection.

        Public because Meta's flow replaces the credential *after* the
        authorization finished: the callback stores a long-lived **user** token
        to discover Pages with, and selecting a Page swaps it for that Page's
        own token. Both go through here, so there is exactly one place in the
        codebase that writes ciphertext into a connection row.
        """
        box = self._secret_box()
        connection.encrypted_credential = box.encrypt(credential, aad=str(connection.id))

    def read_credential(self, connection: PrChannelConnection) -> str | None:
        """The decrypted durable credential, or ``None``. See :meth:`_read_credential`."""
        return self._read_credential(connection)

    def _read_credential(self, connection: PrChannelConnection) -> str | None:
        """Decrypt, or ``None`` when there is nothing usable.

        A value that will not decrypt - a rotated-away key, a row restored from
        a backup taken under a different key - is treated as "no credential"
        rather than as a crash. The connection becomes ``ACTION_REQUIRED`` and
        somebody reconnects, which is the only real remedy anyway.
        """
        envelope = connection.encrypted_credential
        if not envelope:
            return None
        try:
            return self._secret_box().decrypt(envelope, aad=str(connection.id))
        except SecretDecryptionError:
            logger.warning(
                "pr_channel_credential_undecryptable",
                extra={"pr_connection_id": str(connection.id)},
            )
            return None

    def _secret_box(self) -> SecretBox:
        if self._box is None:
            self._box = build_secret_box(self._settings)
        return self._box

    # --- Internals --------------------------------------------------------
    async def _redeem_state(
        self, *, state_token: str, session_actor: Actor | None
    ) -> PrChannelOAuthState:
        """Validate a state token and consume it, in that order.

        **The state row is the authority on who this flow belongs to**, not the
        caller. That is the whole reason it stores a ``user_id``: an OAuth
        callback arrives as a redirect from Google or Meta, and requiring a
        session cookie to *receive* it makes a security-critical flow depend on
        cross-site cookie policy rather than on the mechanism designed to carry
        identity across the round trip. A production 401 on a perfectly valid
        Meta callback is what that mistake looks like.

        ``session_actor`` is therefore **optional and advisory**. When a session
        did arrive it is cross-checked against the state's user as
        defence-in-depth, and a mismatch is refused; when none arrived the flow
        proceeds on the state alone, which is single-use, expiring, hashed at
        rest and bound to a channel and a provider.

        On failure wording, two different rules apply and it is worth being
        exact rather than uniform:

        * **unknown** and **wrong session** share one deliberately unhelpful
          sentence. Both are reachable by somebody who did not start the flow,
          and distinguishing them would confirm that a state exists and that
          *another person* owns it;
        * **expired** and **already used** keep their own wording, which tells a
          person what to do about it. That discloses nothing, because it is only
          ever *displayed* on the authenticated panel path - the unauthenticated
          redirect callback turns every failure, without exception, into the
          same ``connection=failed`` page.
        """
        if not state_token or not state_token.strip():
            raise PrValidationError(
                "Yêu cầu kết nối không hợp lệ.", details={"reason": "state_missing"}
            )
        result = await self._session.execute(
            select(PrChannelOAuthState)
            .where(PrChannelOAuthState.state_hash == hash_state(state_token.strip()))
            .limit(1)
            .with_for_update()
        )
        state = result.scalar_one_or_none()
        if state is None:
            raise PrValidationError(
                "Yêu cầu kết nối không hợp lệ hoặc đã hết hạn.",
                details={"reason": "state_unknown"},
            )
        if state.consumed_at is not None:
            raise PrValidationError(
                "Yêu cầu kết nối này đã được dùng rồi.", details={"reason": "state_consumed"}
            )
        # ``ensure_utc`` because SQLite hands back a naive datetime for a column
        # PostgreSQL returns aware - the same reason ``WebSession`` coerces its
        # own expiry, and the offline suite builds its schema on SQLite.
        if ensure_utc(state.expires_at) <= utcnow():
            raise PrValidationError(
                "Yêu cầu kết nối đã hết hạn. Hãy bấm Kết nối lại.",
                details={"reason": "state_expired"},
            )
        if session_actor is not None and session_actor.user_id != state.user_id:
            # Defence in depth, not the primary control. A browser that *did*
            # send a session and is signed in as somebody else is refused with
            # the same words as an unknown token - see the docstring on why the
            # reason is not disclosed.
            raise PrValidationError(
                "Yêu cầu kết nối không hợp lệ hoặc đã hết hạn.",
                details={"reason": "state_session_mismatch"},
            )
        # Consumed before any network call: two callbacks racing on one state
        # cannot both get as far as exchanging a code.
        state.consumed_at = utcnow()
        await self._session.flush()
        return state

    async def _actor_for_state(self, state: PrChannelOAuthState) -> Actor:
        """The person who started this flow, as an :class:`Actor`.

        Built from the ``users`` row the state names, so the capability check
        that follows is made against **their** role - the same check the same
        person passed when they started the authorization a minute earlier.
        Nothing here trusts the request.

        A user who has since been deactivated, or deleted, cannot finish a flow
        they started. That is not a formality: the window between authorizing
        and returning is where somebody gets suspended, and completing the bind
        would leave a live credential attributed to an account that is no longer
        allowed to hold one.
        """
        user = await self._session.get(User, state.user_id)
        if user is None or not user.active:
            raise PrValidationError(
                "Yêu cầu kết nối không hợp lệ hoặc đã hết hạn.",
                details={"reason": "state_user_unavailable"},
            )
        return Actor(
            user_id=user.id,
            full_name=user.full_name,
            role=user.role,
            active=user.active,
        )

    async def _require_channel(self, channel_id: uuid.UUID) -> PrChannel:
        channel = await self._session.get(PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No PR channel with that id", details={"channel_id": str(channel_id)}
            )
        return channel

    async def _require_provider_platform(
        self, channel: PrChannel, provider: PrChannelPlatform
    ) -> PrChannelPlatform | None:
        """Refuse a channel whose platform is not the provider's.

        The server-side half of "only YouTube channels show YouTube controls".
        A hidden button is not authorization, and pointing a TikTok channel at
        YouTube's OAuth would bind a connection that could never mean anything.
        """
        platform_row = await self._session.get(PrPlatform, channel.platform_id)
        platform = platform_from_code(platform_row.code if platform_row else None)
        if platform is not provider:
            raise PrValidationError(
                "Kênh này không phải kênh YouTube nên không kết nối YouTube được.",
                details={
                    "field": "platform",
                    "reason": "platform_mismatch",
                    "channel_platform": platform.value if platform else None,
                    "provider": provider.value,
                },
            )
        if not supports(platform):
            raise PrValidationError(
                "Đồng bộ API tự động chưa được hỗ trợ cho nền tảng này.",
                details={"reason": "connector_unsupported"},
            )
        return platform

    @staticmethod
    def _reconcile_external_id(channel: PrChannel, identity: FetchedChannelIdentity) -> str | None:
        """Fill ``external_id`` when it is empty; never overwrite it.

        The source-of-truth rule, and it is deliberately timid. ``external_id``
        is a field a person typed and other things may already reference;
        ``connection.provider_account_id`` is what the connector actually binds
        to and is authoritative for syncing. So:

        * empty -> set it, because a verified id is strictly better than a blank;
        * equal -> nothing to do;
        * different -> **leave it alone** and return it, so the panel can show
          both and a person decides. Silently rewriting a field somebody
          maintains, using a value from an account they may have authorized by
          mistake, is exactly the kind of quiet damage this step is meant to
          avoid.
        """
        current = (channel.external_id or "").strip()
        if not current:
            channel.external_id = identity.account_id
            return None
        if current == identity.account_id:
            return None
        return current

    async def resolve_actor_user(self, actor: Actor) -> User | None:
        """The ``users`` row behind an actor, for attribution."""
        if actor.user_id is None:
            return None
        return await self._session.get(User, actor.user_id)


__all__: list[str] = [
    "STATE_TOKEN_BYTES",
    "AuthorizationStart",
    "ConnectionOutcome",
    "PrChannelConnectionService",
    "hash_state",
]
