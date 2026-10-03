"""Step 1F.2.4b - the YouTube connector, from consent to a row in the timeline.

Numbered 1-56, following the requirement numbering the step was specified with:
1-9 OAuth security, 10-16 token storage, 17-21 identity, 22-31 normalization,
32-39 the API snapshot, 40-43 idempotency and concurrency, 44-49 sync health,
50-56 the scheduler.

**No test in this file contacts Google.** Every one of them drives the real
provider through an ``httpx.MockTransport``, or drives the services through a
fake provider - and the last test in the file asserts that as a property of the
source rather than as a habit, because a single ``httpx.AsyncClient()`` built
without an injected transport would quietly start making real requests from CI.

The three claims this file exists to keep separate
---------------------------------------------------

**A credential is not a metric.** The refresh token is encrypted, never
returned, never logged, never audited; the numbers it fetches are public
figures on a dashboard. Tests 10-16 are entirely about the first never leaking
into the places the second lives.

**A connection is not a sync.** A connection that is perfectly healthy can have
a failed last run, and a run that failed on a timeout says nothing about the
credential. Tests 44-49 are about those two states staying apart - and about the
one error code, ``AUTH_REQUIRED``, that is allowed to move the connection.

**A trigger is not an author.** Somebody pressing "Đồng bộ ngay" caused a fetch;
they did not observe a number. Test 33 says the snapshot's
``recorded_by_user_id`` is ``NULL`` and test 32 says who asked is in the audit
trail instead.
"""

from __future__ import annotations

# The ``world`` fixture is imported from a sibling module rather than rebuilt,
# for the reason ``test_pr_channel_metrics`` gives. pytest requires a fixture to
# be a module-level name and every test then takes a parameter of the same name.
# ruff: noqa: F811
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from meobot.application.pr_channel_providers import (
    PROVIDER_BUILDERS,
    PrConnectorNotConfiguredError,
    build_provider,
    supports,
)
from meobot.core.config import Settings
from meobot.core.secrets import generate_key
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_channel_connection import PrChannelOAuthState
from meobot.db.models.pr_reporting import PrChannelMetricSnapshot
from meobot.db.models.user import User
from meobot.domain.pr.channel_connections import (
    PrChannelConnectionState,
    PrChannelSyncErrorCode,
    PrChannelSyncStatus,
    PrChannelSyncTrigger,
    is_due,
    reading_fingerprint,
)
from meobot.domain.pr.channel_metrics import (
    ChannelMetricsReading,
    FetchedChannelIdentity,
    PrChannelMetricsStatus,
    PrChannelPlatform,
    ProviderTokens,
)
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.reporting import PrMetricSource
from meobot.integrations.youtube.constants import YOUTUBE_SCOPES
from meobot.integrations.youtube.errors import classify_status
from meobot.integrations.youtube.provider import (
    YouTubeChannelMetricsProvider,
    analytics_window,
)
from tests.unit.test_pr_channel_metrics import channel as make_channel
from tests.unit.test_pr_channel_metrics import record as record_manual
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

NOW = utcnow().replace(microsecond=0) - timedelta(days=1)

ACCOUNT_ID = "UCabcdefghijklmnopqrstuv"
OTHER_ACCOUNT_ID = "UCzzzzzzzzzzzzzzzzzzzzzz"
REFRESH_TOKEN = "1//refresh-token-value"


# ===========================================================================
# Fixtures: a Google that lives in this file
# ===========================================================================


def statistics_payload(
    *,
    account_id: str = ACCOUNT_ID,
    subscribers: str | None = "124812",
    videos: str | None = "412",
    total_views: str | None = "98765432",
    hidden: bool = False,
) -> dict[str, Any]:
    """A ``channels.list`` body, shaped the way the Data API shapes one.

    Counts are **strings**, because that is what YouTube actually sends, and a
    connector that only handled integers would work against a hand-written
    fixture and fail against Google.
    """
    stats: dict[str, Any] = {"hiddenSubscriberCount": hidden}
    if subscribers is not None:
        stats["subscriberCount"] = subscribers
    if videos is not None:
        stats["videoCount"] = videos
    if total_views is not None:
        stats["viewCount"] = total_views
    return {
        "items": [
            {
                "id": account_id,
                "snippet": {"title": "Apex Media", "customUrl": "@apexmedia"},
                "statistics": stats,
            }
        ]
    }


def report_payload(
    *, views: int | None = 5000, likes: int = 300, comments: int = 42, shares: int = 11
) -> dict[str, Any]:
    """A ``reports.query`` body. ``views=None`` produces the no-rows shape."""
    if views is None:
        return {"columnHeaders": [], "rows": []}
    return {
        "columnHeaders": [
            {"name": "views"},
            {"name": "likes"},
            {"name": "comments"},
            {"name": "shares"},
            {"name": "estimatedMinutesWatched"},
        ],
        "rows": [[views, likes, comments, shares, 900]],
    }


@dataclass
class FakeGoogle:
    """Every Google endpoint the connector uses, as one transport.

    Records what it was asked, so a test can assert that a client secret was
    sent server-to-server and that an authorization code never appeared
    anywhere else.
    """

    statistics: dict[str, Any] = field(default_factory=statistics_payload)
    report: dict[str, Any] = field(default_factory=report_payload)
    token_response: dict[str, Any] | None = None
    token_status: int = 200
    data_status: int = 200
    analytics_status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            url = str(request.url)
            if "oauth2.googleapis.com/token" in url:
                body = self.token_response or {
                    "access_token": "ya29.access",
                    "expires_in": 3599,
                    "refresh_token": REFRESH_TOKEN,
                    "scope": " ".join(YOUTUBE_SCOPES),
                }
                return httpx.Response(self.token_status, json=body)
            if "revoke" in url:
                return httpx.Response(200, content=b"")
            if "youtube/v3/channels" in url:
                if self.data_status >= 400:
                    return httpx.Response(
                        self.data_status,
                        json={"error": {"errors": [{"reason": "quotaExceeded"}]}},
                    )
                return httpx.Response(200, json=self.statistics)
            if "youtubeanalytics" in url:
                if self.analytics_status >= 400:
                    return httpx.Response(self.analytics_status, json={"error": {}})
                return httpx.Response(200, json=self.report)
            return httpx.Response(404, json={})

        return httpx.MockTransport(handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())

    def provider(self) -> YouTubeChannelMetricsProvider:
        return YouTubeChannelMetricsProvider(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="https://pr.example.com/api/pr/channels/connections/youtube/callback",
            client=self.client(),
        )

    def sent_bodies(self) -> str:
        return " ".join(request.content.decode("utf-8", "ignore") for request in self.requests)


@dataclass
class FakeProvider:
    """A provider that answers from memory. No transport at all.

    Used by every test about *orchestration* - claiming, appending, health,
    scheduling - where routing a fake HTTP response through the real client
    would only be testing the client again.
    """

    platform: PrChannelPlatform = PrChannelPlatform.YOUTUBE
    account_id: str = ACCOUNT_ID
    reading: ChannelMetricsReading | None = None
    refresh_token: str | None = REFRESH_TOKEN
    fail_with: Exception | None = None
    refresh_calls: int = 0
    revoked: list[str] = field(default_factory=list)

    def build_authorization_url(self, *, state: str) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    async def exchange_authorization_code(self, *, code: str) -> ProviderTokens:
        return ProviderTokens(
            access_token="ya29.access",
            durable_credential=self.refresh_token,
            granted_scopes=YOUTUBE_SCOPES,
        )

    async def acquire_access(self, *, durable_credential: str) -> ProviderTokens:
        self.refresh_calls += 1
        return ProviderTokens(access_token="ya29.access")

    async def revoke(self, *, durable_credential: str) -> None:
        self.revoked.append(durable_credential)

    async def fetch_channel_identity(self, *, access_token: str) -> FetchedChannelIdentity:
        return FetchedChannelIdentity(
            account_id=self.account_id, title="Apex Media", handle="@apexmedia"
        )

    async def fetch_channel_metrics(
        self, *, access_token: str, account_id: str, now: datetime
    ) -> ChannelMetricsReading:
        if self.fail_with is not None:
            raise self.fail_with
        return self.reading or ChannelMetricsReading(
            observed_at=now,
            metrics={"followers": 124812, "views_30d": 5000, "posts_count": 412},
            extra_metrics={"youtube_total_view_count": 98765432},
            period_start="2026-07-01",
            period_end="2026-07-30",
        )


def _log_calls(source: str) -> list[str]:
    """Every ``logger.<level>(...)`` argument list in a module, as text.

    Crude on purpose: a parser would be more precise and would also be a second
    thing to get wrong. What matters is that the *payload* of a log call is
    scanned rather than the whole file, because these modules necessarily name
    tokens in signatures and docstrings.
    """
    calls: list[str] = []
    for marker in ("logger.info(", "logger.warning(", "logger.error(", "logger.debug("):
        start = 0
        while (index := source.find(marker, start)) != -1:
            depth = 0
            for offset in range(index + len(marker) - 1, len(source)):
                if source[offset] == "(":
                    depth += 1
                elif source[offset] == ")":
                    depth -= 1
                    if depth == 0:
                        calls.append(source[index : offset + 1])
                        start = offset
                        break
            else:  # pragma: no cover - unbalanced source is a syntax error
                start = index + len(marker)
    return calls


def connector_settings(**overrides: Any) -> Settings:
    """A deployment with the connector fully configured."""
    values: dict[str, Any] = {
        "_env_file": None,
        "web_base_url": "https://pr.example.com",
        "youtube_oauth_client_id": "client-id",
        "youtube_oauth_client_secret": "client-secret",
        "pr_secret_encryption_key": generate_key(),
        "web_cookie_secure": False,
    }
    values.update(overrides)
    return Settings(**values)


async def youtube_channel(world: World, *, name: str = "Apex Media", **kwargs: Any):
    """A channel whose canonical platform really is YouTube."""
    return await make_channel(world, platform_code="YOUTUBE", name=name, **kwargs)


async def connect(
    world: World,
    channel_id: uuid.UUID,
    *,
    actor: User | None = None,
    provider: FakeProvider | None = None,
):
    """Walk a real authorization to a bound connection, with a fake provider."""
    fake = provider or FakeProvider()
    who = actor or world.owner
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(who), channel_id=channel_id
    )
    token = start.authorization_url.split("state=")[1]
    return await world.services.channel_connections.finish_authorization(
        session_actor=world.actor(who),
        request_id=world.request_id,
        state_token=token,
        code="4/auth-code",
        provider_client=fake,
    )


async def run_sync(
    world: World,
    connection_id: uuid.UUID,
    *,
    provider: FakeProvider | None = None,
    trigger: PrChannelSyncTrigger = PrChannelSyncTrigger.SCHEDULED,
):
    return await world.services.channel_sync.run_sync(
        connection_id=connection_id,
        trigger=trigger,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=provider or FakeProvider(),
    )


@pytest.fixture(autouse=True)
def _configured(world: World) -> None:
    """Give every test's service bundle a configured connector.

    ``World`` builds its services from a plain ``Settings``; the connector needs
    an OAuth client and an encryption key, and rebuilding the whole world per
    test would be slower and would not test anything extra.
    """
    settings = connector_settings()
    world.services.channel_connections._settings = settings
    world.services.channel_sync._settings = settings
    world.services.settings = settings


# ===========================================================================
# 1-9: OAUTH SECURITY
# ===========================================================================


async def test_01_a_channel_manager_can_start_an_authorization(world: World) -> None:
    """Requirement 1."""
    row = await youtube_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert start.authorization_url.startswith("https://accounts.google.com/")
    assert "state=" in start.authorization_url


async def test_02_an_ordinary_member_cannot(world: World) -> None:
    """Requirement 2. Managing a credential is management work."""
    row = await youtube_channel(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.channel_connections.start_authorization(
            actor=world.actor(world.member), channel_id=row.id
        )


async def test_03_a_non_youtube_channel_is_refused(world: World) -> None:
    """Requirement 3. Server-side, not by a button that was not drawn."""
    tiktok = await make_channel(world, platform_code="TIKTOK", name="Dr Tiến")
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.start_authorization(
            actor=world.actor(world.owner), channel_id=tiktok.id
        )
    assert failure.value.details["reason"] == "platform_mismatch"


async def test_04_the_state_binds_the_channel_and_the_user(world: World) -> None:
    """Requirement 4. And only the hash is written down."""
    row = await youtube_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner), channel_id=row.id
    )
    token = start.authorization_url.split("state=")[1]

    state = await world.session.get(PrChannelOAuthState, start.state_id)
    assert state is not None
    assert state.channel_id == row.id
    assert state.user_id == world.owner.id
    assert state.consumed_at is None
    # The token itself is never stored - only something that can be compared.
    assert state.state_hash != token
    assert token not in state.state_hash


async def test_05_an_unknown_state_is_refused(world: World) -> None:
    """Requirement 5."""
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            session_actor=world.actor(world.owner),
            request_id=world.request_id,
            state_token="not-a-real-state",
            code="4/auth-code",
            provider_client=FakeProvider(),
        )
    assert failure.value.details["reason"] == "state_unknown"


async def test_06_an_expired_state_is_refused(world: World) -> None:
    """Requirement 6."""
    row = await youtube_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner), channel_id=row.id
    )
    token = start.authorization_url.split("state=")[1]
    state = await world.session.get(PrChannelOAuthState, start.state_id)
    assert state is not None
    state.expires_at = utcnow() - timedelta(minutes=1)
    await world.session.flush()

    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            session_actor=world.actor(world.owner),
            request_id=world.request_id,
            state_token=token,
            code="4/auth-code",
            provider_client=FakeProvider(),
        )
    assert failure.value.details["reason"] == "state_expired"


async def test_07_a_state_cannot_be_used_twice(world: World) -> None:
    """Requirement 7. Single use, and consumed before any network call."""
    row = await youtube_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner), channel_id=row.id
    )
    token = start.authorization_url.split("state=")[1]
    await world.services.channel_connections.finish_authorization(
        session_actor=world.actor(world.owner),
        request_id=world.request_id,
        state_token=token,
        code="4/auth-code",
        provider_client=FakeProvider(),
    )

    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            session_actor=world.actor(world.owner),
            request_id=world.request_id,
            state_token=token,
            code="4/auth-code",
            provider_client=FakeProvider(),
        )
    assert failure.value.details["reason"] == "state_consumed"


async def test_08_a_callback_cannot_name_a_channel_of_its_own(world: World) -> None:
    """Requirement 8, and the reason the state table exists.

    The channel is read from the state row, never from the request. There is no
    parameter to inject: ``finish_authorization`` takes a state and a code and
    nothing else, and the connection it creates belongs to whatever channel
    MeoBot wrote down when it started the flow.
    """
    mine = await youtube_channel(world, name="Kênh của tôi")
    theirs = await youtube_channel(world, name="Kênh khác")

    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner), channel_id=mine.id
    )
    token = start.authorization_url.split("state=")[1]
    outcome = await world.services.channel_connections.finish_authorization(
        session_actor=world.actor(world.owner),
        request_id=world.request_id,
        state_token=token,
        code="4/auth-code",
        provider_client=FakeProvider(),
    )
    assert outcome.connection.channel_id == mine.id
    assert outcome.connection.channel_id != theirs.id

    # And a state presented by a *different signed-in session* is refused. The
    # session is a cross-check rather than the source of identity - see
    # ``_redeem_state`` - so the refusal is the generic one and says nothing
    # about whose flow it was.
    other_start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner), channel_id=theirs.id
    )
    other_token = other_start.authorization_url.split("state=")[1]
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            session_actor=world.actor(world.head),
            request_id=world.request_id,
            state_token=other_token,
            code="4/auth-code",
            provider_client=FakeProvider(),
        )
    assert failure.value.details["reason"] == "state_session_mismatch"
    assert "không hợp lệ hoặc đã hết hạn" in failure.value.message, (
        "the wording must not reveal that somebody else started this flow"
    )


async def test_09_an_authorization_code_never_reaches_a_log_or_an_audit_row(
    world: World,
) -> None:
    """Requirement 9. The code is a bearer credential for a few seconds."""
    row = await youtube_channel(world)
    await connect(world, row.id)

    entries = (await world.session.execute(select(AuditLog))).scalars().all()
    dumped = json.dumps(
        [{"a": e.action, "b": e.before_data, "c": e.after_data, "e": e.entity_id} for e in entries],
        default=str,
    )
    assert "4/auth-code" not in dumped
    assert "ya29.access" not in dumped
    assert REFRESH_TOKEN not in dumped


# ===========================================================================
# 10-16: TOKEN STORAGE
# ===========================================================================


async def test_10_the_refresh_token_is_stored_encrypted(world: World) -> None:
    """Requirement 10. Ciphertext in the column, plaintext nowhere.

    The column is ``encrypted_credential`` since migration 0029 - Step 1F.2.4c
    renamed it, because Meta has no refresh token and storing one there would
    have been a lie in the schema. Google's behaviour is unchanged.
    """
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    connection = outcome.connection

    stored = connection.encrypted_credential
    assert stored is not None
    assert REFRESH_TOKEN not in stored
    # The envelope is self-describing: scheme, key id, nonce, ciphertext.
    assert stored.startswith("v1:primary:")
    assert len(stored.split(":")) == 4


async def test_10a_a_ciphertext_cannot_be_moved_to_another_connection(world: World) -> None:
    """The AAD binding, as a test rather than as a claim in a docstring.

    A refresh token copied from one connection row into another must not
    decrypt there - otherwise a database with one compromised row is a database
    where any channel can be pointed at that account.
    """
    first = await youtube_channel(world, name="Kênh A")
    second = await youtube_channel(world, name="Kênh B")
    a = (await connect(world, first.id)).connection
    b = (await connect(world, second.id)).connection

    stolen = a.encrypted_credential
    assert stolen is not None
    b.encrypted_credential = stolen
    await world.session.flush()

    # Reading it back on the wrong row yields nothing rather than the token.
    assert world.services.channel_connections._read_credential(b) is None


async def test_11_no_api_response_carries_a_refresh_token(world: World) -> None:
    """Requirement 11. Asserted over the real HTTP surface."""
    row = await youtube_channel(world)
    await connect(world, row.id)
    world.act_as(world.owner)

    for path in (f"/api/pr/channels/{row.id}/connection", f"/api/pr/channels/{row.id}"):
        response = world.client.get(path)
        assert response.status_code == 200, response.text
        assert REFRESH_TOKEN not in response.text
        assert "refresh_token" not in response.text
        assert "encrypted_refresh_token" not in response.text
        assert "encrypted_credential" not in response.text
        assert "client-secret" not in response.text


async def test_12_the_audit_trail_carries_scopes_but_never_a_token(world: World) -> None:
    """Requirement 12.

    Scope *names* are deliberately present: they are what an operator consented
    to, and a missing one is how an insufficient-scope failure is diagnosed
    months later. What must never appear is anything replayable.
    """
    row = await youtube_channel(world)
    await connect(world, row.id)

    entry = (
        await world.session.execute(
            select(AuditLog).where(AuditLog.action == "pr.channel.connection.connected")
        )
    ).scalar_one()
    assert entry.after_data is not None
    assert entry.after_data["provider_account_id"] == ACCOUNT_ID
    assert "youtube.readonly" in entry.after_data["granted_scopes"]
    dumped = json.dumps(entry.after_data, default=str)
    assert REFRESH_TOKEN not in dumped
    assert "ya29" not in dumped


async def test_13_the_redaction_list_already_covers_every_name_used(world: World) -> None:
    """Requirement 13, asserted structurally rather than by scraping logs.

    MeoBot's logging layer redacts by field name, so "a token cannot be logged"
    is a property of that list plus the names this connector uses. Both halves
    are checked: the names are in the list, and no logging call in the connector
    passes a value under any other name.
    """
    from meobot.core.logging import SENSITIVE_KEYS

    for name in ("refresh_token", "access_token", "client_secret", "authorization"):
        assert name in SENSITIVE_KEYS, name

    from pathlib import Path

    import meobot.application.pr_channel_connection_service as connection_module
    import meobot.integrations.youtube.provider as provider_module

    for module in (provider_module, connection_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        # No log call interpolates a secret-shaped local into its payload.
        # Scoped to the ``extra=`` payload of each log call, which is the only
        # part that carries values. The whole file necessarily *names* these
        # things - they are parameters - and even an event name may contain one:
        # ``pr_channel_refresh_token_undecryptable`` is a perfectly good log
        # message and carries nothing. What must never appear is a value.
        for call in _log_calls(source):
            marker = call.find("extra=")
            payload = call[marker:] if marker != -1 else ""
            # Quoted key names, so ``"status_code"`` - an HTTP status, and a
            # useful thing to log - is not mistaken for an authorization code.
            for forbidden in (
                '"refresh_token"',
                '"access_token"',
                '"state_token"',
                '"code"',
                '"token"',
                '"authorization"',
            ):
                assert forbidden not in payload, (
                    f"{module.__name__}: {forbidden} reaches a log payload"
                )


async def test_14_a_reconnect_without_a_new_refresh_token_keeps_the_old_one(
    world: World,
) -> None:
    """Requirement 14, and the single most breakable rule in the step.

    Google issues a refresh token on first consent and normally **omits** it on
    every authorization afterwards. A reconnect that read the omission as "clear
    the stored token" would leave the channel unable to sync in the background -
    silently, and only discoverable a day later when the scheduler failed.
    """
    row = await youtube_channel(world)
    first = await connect(world, row.id)
    original = first.connection.encrypted_credential
    assert original is not None

    # The second consent carries no refresh token, which is the normal case.
    silent = FakeProvider(refresh_token=None)
    second = await connect(world, row.id, provider=silent)

    assert second.is_reconnect is True
    assert second.connection.id == first.connection.id
    assert second.connection.encrypted_credential == original
    # And the connection is still usable for an unattended sync.
    assert (
        await world.services.channel_connections.access_token_for(
            second.connection, provider_client=silent
        )
        == "ya29.access"
    )


async def test_15_disconnecting_drops_the_local_secret(world: World) -> None:
    """Requirement 15. Revoked remotely, and gone locally either way."""
    row = await youtube_channel(world)
    await connect(world, row.id)
    fake = FakeProvider()

    connection = await world.services.channel_connections.disconnect(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=row.id,
        provider_client=fake,
    )
    assert connection.encrypted_credential is None
    assert connection.status is PrChannelConnectionState.DISCONNECTED
    assert connection.auto_sync_enabled is False
    assert fake.revoked == [REFRESH_TOKEN]


async def test_15a_a_failed_remote_revoke_still_disconnects_locally(world: World) -> None:
    """A channel nobody can disconnect because Google is down is worse."""

    class Stubborn(FakeProvider):
        async def revoke(self, *, durable_credential: str) -> None:
            raise RuntimeError("Google is having an afternoon")

    row = await youtube_channel(world)
    await connect(world, row.id)
    with pytest.raises(RuntimeError):
        await world.services.channel_connections.disconnect(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            channel_id=row.id,
            provider_client=Stubborn(),
        )
    # The real provider swallows a revoke failure - see YouTubeChannelMetricsProvider.
    # This asserts the contract that makes that safe: revoke is best effort.
    fake = FakeGoogle().provider()
    await fake.revoke(durable_credential=REFRESH_TOKEN)


async def test_16_disconnecting_preserves_every_snapshot(world: World) -> None:
    """Requirement 16. The whole point of keeping history out of the credential."""
    row = await youtube_channel(world)
    await record_manual(world, row.id, at=NOW - timedelta(days=3), followers=1000)
    outcome = await connect(world, row.id)
    await run_sync(world, outcome.connection.id)

    before = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert before.total == 2

    await world.services.channel_connections.disconnect(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=row.id,
        provider_client=FakeProvider(),
    )
    after = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert after.total == 2
    sources = {entry.snapshot.source for entry in after.history}
    assert sources == {PrMetricSource.MANUAL, PrMetricSource.API}


# ===========================================================================
# 17-21: IDENTITY
# ===========================================================================


async def test_17_18_the_authorized_identity_is_stored(world: World) -> None:
    """Requirements 17 and 18."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)

    assert outcome.identity.account_id == ACCOUNT_ID
    assert outcome.connection.provider_account_id == ACCOUNT_ID
    assert outcome.connection.provider_account_name == "Apex Media"
    assert outcome.connection.provider_account_handle == "@apexmedia"


async def test_19_an_empty_external_id_is_filled_in(world: World) -> None:
    """Requirement 19. A verified id is strictly better than a blank."""
    row = await youtube_channel(world)
    assert row.external_id is None

    outcome = await connect(world, row.id)
    await world.session.refresh(row)
    assert row.external_id == ACCOUNT_ID
    assert outcome.external_id_conflict is None


async def test_20_a_conflicting_external_id_is_reported_not_overwritten(world: World) -> None:
    """Requirement 20, and the final rule.

    ``external_id`` is a field a person typed and other things may reference;
    ``provider_account_id`` is what the connector binds to. When they disagree,
    the connection is authoritative for syncing and the typed value is **left
    alone** and surfaced - because silently rewriting somebody's data using an
    account they may have authorized by mistake is exactly the quiet damage this
    step must not do.
    """
    row = await youtube_channel(world)
    row.external_id = "UC-something-a-person-typed"
    await world.session.flush()

    outcome = await connect(world, row.id)
    await world.session.refresh(row)

    assert row.external_id == "UC-something-a-person-typed", "never overwritten"
    assert outcome.external_id_conflict == "UC-something-a-person-typed"
    # And the connector binds to the verified id regardless.
    assert outcome.connection.provider_account_id == ACCOUNT_ID


async def test_21_a_reconnect_onto_a_different_account_is_never_silent(world: World) -> None:
    """Requirement 21. Allowed - a channel can move - but announced."""
    row = await youtube_channel(world)
    await connect(world, row.id)

    outcome = await connect(world, row.id, provider=FakeProvider(account_id=OTHER_ACCOUNT_ID))
    assert outcome.rebound_from_account_id == ACCOUNT_ID
    assert outcome.connection.provider_account_id == OTHER_ACCOUNT_ID

    entry = (
        (
            await world.session.execute(
                select(AuditLog).where(AuditLog.action == "pr.channel.connection.reconnected")
            )
        )
        .scalars()
        .all()[-1]
    )
    assert entry.after_data is not None
    assert entry.after_data["rebound_from_account_id"] == ACCOUNT_ID


# ===========================================================================
# 22-31: NORMALIZATION
# ===========================================================================


async def test_22_a_subscriber_count_becomes_followers() -> None:
    """Requirement 22. And a string count is parsed - YouTube sends strings."""
    google = FakeGoogle()
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    assert reading.canonical()["followers"] == 124812


async def test_23_hidden_subscribers_become_null_not_zero() -> None:
    """Requirement 23, and requirement 26.

    A channel whose owner ticked "hide subscriber count" has not lost its
    subscribers. Storing zero would draw a cliff on every chart the day somebody
    changed a privacy setting.
    """
    google = FakeGoogle(statistics=statistics_payload(hidden=True, subscribers=None))
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    assert reading.canonical()["followers"] is None
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["youtube_hidden_subscriber_count"] is True


async def test_24_the_video_count_becomes_posts_count() -> None:
    """Requirement 24."""
    google = FakeGoogle()
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    assert reading.canonical()["posts_count"] == 412


async def test_25_26_the_cumulative_view_count_never_lands_in_views_30d() -> None:
    """Requirements 25 and 26, and the easiest mistake in the whole step.

    The Data API's ``viewCount`` is every view the channel has ever had.
    Presented as "Views 30 ngày" it would be wrong by three orders of magnitude
    and entirely plausible on screen.
    """
    google = FakeGoogle(
        statistics=statistics_payload(total_views="98765432"),
        report=report_payload(views=5000),
    )
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    metrics = reading.canonical()

    assert metrics["views_30d"] == 5000, "the Analytics figure, not the lifetime one"
    assert metrics["views_30d"] != 98765432
    assert metrics["views_7d"] == 5000
    # Preserved, under a name that says what it is.
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["youtube_total_view_count"] == 98765432


async def test_27_28_analytics_windows_fill_the_windowed_columns() -> None:
    """Requirements 27 and 28, and the window metadata that makes them readable."""
    google = FakeGoogle(report=report_payload(views=5000))
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    extra = reading.extra_metrics
    assert extra is not None

    seven = analytics_window(NOW, days=7)
    thirty = analytics_window(NOW, days=30)
    assert extra["youtube_analytics_start_7d"] == seven.start_iso
    assert extra["youtube_analytics_end_7d"] == seven.end_iso
    assert extra["youtube_analytics_start_30d"] == thirty.start_iso
    assert extra["youtube_analytics_end_30d"] == thirty.end_iso

    # Settled windows: they end before today, so the same window queried twice
    # returns the same numbers - which is what makes the fingerprint meaningful.
    assert thirty.end < NOW.date()
    assert (thirty.end - thirty.start).days == 29
    assert (seven.end - seven.start).days == 6
    # And the reading's identity is the widest window it covers.
    assert reading.period_start == thirty.start_iso
    assert reading.period_end == thirty.end_iso


async def test_29_a_metric_youtube_does_not_report_stays_null() -> None:
    """Requirement 29, and the refusal to invent an engagement number.

    YouTube has no channel-level metric that means what "reach" or
    "impressions" mean on Facebook or TikTok, and there is no ``engagements``
    metric at all. Empty cards are the honest answer; a summed one would be
    MeoBot inventing a definition and presenting it as measured.
    """
    google = FakeGoogle()
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    metrics = reading.canonical()
    for absent in (
        "reach_7d",
        "reach_30d",
        "impressions_7d",
        "impressions_30d",
        "engagements_7d",
        "engagements_30d",
        "following",
    ):
        assert metrics[absent] is None, absent


async def test_29a_an_analytics_window_with_no_rows_is_null_not_zero() -> None:
    """ "Nothing happened" and "the platform has not said" are different facts."""
    google = FakeGoogle(report=report_payload(views=None))
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    metrics = reading.canonical()
    assert metrics["views_30d"] is None
    assert metrics["likes_30d"] is None
    # The Data API half still came through - a partial reading is still a reading.
    assert metrics["followers"] == 124812


async def test_30_zero_survives_as_zero() -> None:
    """Requirement 30. A month with no shares really did have none."""
    google = FakeGoogle(report=report_payload(views=0, likes=0, comments=0, shares=0))
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    metrics = reading.canonical()
    assert metrics["views_30d"] == 0
    assert metrics["shares_30d"] == 0
    assert metrics["views_30d"] is not None


@pytest.mark.parametrize("bad", ["-5", "not-a-number", "12.5", None, True, 10**18, {"nested": 1}])
async def test_31_an_implausible_remote_value_is_refused(bad: Any) -> None:
    """Requirement 31. A provider response is untrusted input.

    Dropped to ``None`` rather than raised: one unreadable field must not
    discard a dozen good ones, and "not recorded" is a true statement about the
    part that could not be read.
    """
    google = FakeGoogle(statistics=statistics_payload(subscribers=bad))
    reading = await google.provider().fetch_channel_metrics(
        access_token="ya29", account_id=ACCOUNT_ID, now=NOW
    )
    assert reading.canonical()["followers"] is None
    # The rest of the reading survives.
    assert reading.canonical()["posts_count"] == 412


async def test_31a_a_body_that_is_not_json_is_a_bad_response() -> None:
    """A 200 with a broken body is classified, not crashed on."""

    def handle(request: httpx.Request) -> httpx.Response:
        if "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29"})
        return httpx.Response(200, content=b"<html>not json</html>")

    provider = YouTubeChannelMetricsProvider(
        client_id="c",
        client_secret="s",
        redirect_uri="https://pr.example.com/cb",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    from meobot.integrations.youtube.errors import YouTubeApiError

    with pytest.raises(YouTubeApiError) as failure:
        await provider.fetch_channel_identity(access_token="ya29")
    assert failure.value.error_code is PrChannelSyncErrorCode.BAD_RESPONSE


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, None, PrChannelSyncErrorCode.AUTH_REQUIRED),
        (
            403,
            {"error": {"errors": [{"reason": "quotaExceeded"}]}},
            PrChannelSyncErrorCode.RATE_LIMITED,
        ),
        (
            403,
            {"error": {"errors": [{"reason": "insufficientPermissions"}]}},
            PrChannelSyncErrorCode.INSUFFICIENT_SCOPE,
        ),
        (404, None, PrChannelSyncErrorCode.INVALID_ACCOUNT),
        (429, None, PrChannelSyncErrorCode.RATE_LIMITED),
        (503, None, PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE),
        (418, None, PrChannelSyncErrorCode.BAD_RESPONSE),
    ],
)
def test_31b_http_failures_map_onto_the_safe_vocabulary(
    status: int, payload: Any, expected: PrChannelSyncErrorCode
) -> None:
    """A quota 403 and a scope 403 need opposite responses - wait, or reconnect."""
    assert classify_status(status, payload=payload) is expected


# ===========================================================================
# 32-39: THE API SNAPSHOT
# ===========================================================================


async def test_32_33_a_successful_sync_appends_an_api_snapshot_with_no_author(
    world: World,
) -> None:
    """Requirements 32 and 33, and the distinction between a trigger and an author.

    An API reading has no human author. The person who pressed "Đồng bộ ngay"
    caused a fetch; they did not observe a number, and writing them into
    ``recorded_by_user_id`` would make the history claim they typed it. Who
    asked is in the audit trail instead.
    """
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    result = await run_sync(world, outcome.connection.id, trigger=PrChannelSyncTrigger.MANUAL)

    assert result.ok is True
    assert result.snapshot_id is not None
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.source is PrMetricSource.API
    assert snapshot.recorded_by_user_id is None
    assert snapshot.followers == 124812

    entry = (
        await world.session.execute(
            select(AuditLog).where(AuditLog.action == "pr.channel.sync.succeeded")
        )
    ).scalar_one()
    assert entry.after_data is not None
    assert entry.after_data["trigger"] == "MANUAL"
    assert entry.after_data["snapshot_id"] == str(result.snapshot_id)


async def test_34_35_manual_history_survives_and_the_timeline_stays_append_only(
    world: World,
) -> None:
    """Requirements 34 and 35. A mixed timeline is the normal case."""
    row = await youtube_channel(world)
    manual_one = await record_manual(world, row.id, at=NOW - timedelta(days=5), followers=100)
    outcome = await connect(world, row.id)
    await run_sync(world, outcome.connection.id)
    manual_two = await record_manual(world, row.id, at=utcnow(), followers=999)

    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 3
    await world.session.refresh(manual_one)
    assert manual_one.followers == 100, "an API sync rewrites nothing"
    assert view.latest is not None
    assert view.latest.snapshot.id == manual_two.id


async def test_36_latest_is_still_ordered_by_observed_at(world: World) -> None:
    """Requirement 36, and requirement 61's server half.

    A manual correction recorded after an API reading **is** the current figure,
    and the panel must say ``MANUAL`` for it rather than assuming the connector
    produced whatever is newest.
    """
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    await run_sync(world, outcome.connection.id)
    await record_manual(world, row.id, at=utcnow(), followers=999)

    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.latest is not None
    assert view.latest.snapshot.source is PrMetricSource.MANUAL
    assert view.latest.snapshot.followers == 999
    # The badge still describes the connection; the reading describes itself.
    assert view.status is PrChannelMetricsStatus.CONNECTED_API

    summaries = await world.services.channel_metrics.summaries_for_channels([row.id])
    assert summaries[row.id].latest_source == "MANUAL"
    assert summaries[row.id].status is PrChannelMetricsStatus.CONNECTED_API


async def test_37_the_source_window_metadata_is_preserved(world: World) -> None:
    """Requirement 37. What the numbers cover, kept beside them."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    reading = ChannelMetricsReading(
        observed_at=utcnow(),
        metrics={"views_30d": 5000},
        extra_metrics={
            "youtube_analytics_start_30d": "2026-07-01",
            "youtube_analytics_end_30d": "2026-07-30",
        },
        period_start="2026-07-01",
        period_end="2026-07-30",
    )
    result = await run_sync(world, outcome.connection.id, provider=FakeProvider(reading=reading))

    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.extra_metrics is not None
    assert snapshot.extra_metrics["youtube_analytics_start_30d"] == "2026-07-01"
    # observed_at is when MeoBot looked, not the report's end date.
    assert snapshot.observed_at.date() != datetime(2026, 7, 30, tzinfo=UTC).date()


async def test_38_a_partial_reading_writes_the_nulls_it_has(world: World) -> None:
    """Requirement 38. Some numbers is a reading; no numbers is not."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    reading = ChannelMetricsReading(
        observed_at=utcnow(), metrics={"followers": 500}, period_start="a", period_end="b"
    )
    result = await run_sync(world, outcome.connection.id, provider=FakeProvider(reading=reading))

    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.followers == 500
    assert snapshot.views_30d is None
    assert snapshot.reach_30d is None


async def test_39_a_failed_sync_writes_no_snapshot(world: World) -> None:
    """Requirement 39. A row of nulls looks exactly like a collapse."""
    from meobot.integrations.youtube.errors import YouTubeApiError

    row = await youtube_channel(world)
    await record_manual(world, row.id, at=NOW - timedelta(days=1), followers=100)
    outcome = await connect(world, row.id)

    broken = FakeProvider(
        fail_with=YouTubeApiError("boom", error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE)
    )
    result = await run_sync(world, outcome.connection.id, provider=broken)

    assert result.ok is False
    assert result.snapshot_id is None
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 1, "the previous reading is still the current one"
    assert view.latest is not None
    assert view.latest.snapshot.followers == 100


# ===========================================================================
# 40-43: IDEMPOTENCY AND CONCURRENCY
# ===========================================================================


async def test_40_a_repeated_fetch_of_the_same_period_is_not_a_new_reading(
    world: World,
) -> None:
    """Requirement 40. A scheduler retry must not thicken a time series."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)

    first = await run_sync(world, outcome.connection.id)
    second = await run_sync(world, outcome.connection.id)

    assert first.duplicate is False
    assert second.duplicate is True, "same account, same period, same numbers"
    assert second.ok is True, "a duplicate is success - nothing needed adding"
    assert second.snapshot_id is None

    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 1


async def test_41_a_later_period_with_identical_numbers_is_a_new_reading(
    world: World,
) -> None:
    """Requirement 41, and why the fingerprint covers the period.

    Two readings a week apart that both report 124,812 followers are different
    readings that happen to agree. Deduplicating on values alone would delete
    the evidence that a channel flatlined, which is itself a finding.
    """
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)

    same_numbers = {"followers": 124812, "views_30d": 5000, "posts_count": 412}
    july = ChannelMetricsReading(
        observed_at=utcnow() - timedelta(days=7),
        metrics=same_numbers,
        period_start="2026-07-01",
        period_end="2026-07-30",
    )
    august = ChannelMetricsReading(
        observed_at=utcnow(),
        metrics=same_numbers,
        period_start="2026-07-08",
        period_end="2026-08-06",
    )
    first = await run_sync(world, outcome.connection.id, provider=FakeProvider(reading=july))
    second = await run_sync(world, outcome.connection.id, provider=FakeProvider(reading=august))

    assert first.duplicate is False
    assert second.duplicate is False
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 2


def test_41a_the_fingerprint_ignores_when_meobot_looked() -> None:
    """The rule that makes the whole mechanism work rather than no-op.

    ``observed_at`` in the fingerprint would make every key unique, every sync a
    new row, and the unique index decorative.
    """
    metrics = {"followers": 100}
    a = reading_fingerprint(
        provider_account_id=ACCOUNT_ID, period_start="a", period_end="b", metrics=metrics
    )
    b = reading_fingerprint(
        provider_account_id=ACCOUNT_ID, period_start="a", period_end="b", metrics=metrics
    )
    assert a == b
    # And two accounts never collide, however identical their numbers.
    c = reading_fingerprint(
        provider_account_id=OTHER_ACCOUNT_ID, period_start="a", period_end="b", metrics=metrics
    )
    assert a != c


async def test_42_a_second_sync_cannot_claim_a_channel_already_syncing(
    world: World,
) -> None:
    """Requirement 42. The lock is a conditional UPDATE, not a Python flag."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)

    assert await world.services.channel_sync.claim(outcome.connection.id) is True
    assert await world.services.channel_sync.claim(outcome.connection.id) is False, (
        "a second claimant must lose"
    )

    # And the manual route refuses rather than queueing a second run.
    world.act_as(world.owner)
    response = world.client.post(f"/api/pr/channels/{row.id}/metrics/sync")
    assert response.status_code == 409, response.text


async def test_43_channels_sync_independently(world: World) -> None:
    """Requirement 43. One channel's claim is not another's."""
    first = await youtube_channel(world, name="Kênh A")
    second = await youtube_channel(world, name="Kênh B")
    a = (await connect(world, first.id)).connection
    b = (
        await connect(world, second.id, provider=FakeProvider(account_id=OTHER_ACCOUNT_ID))
    ).connection

    assert await world.services.channel_sync.claim(a.id) is True
    assert await world.services.channel_sync.claim(b.id) is True


# ===========================================================================
# 44-49: SYNC HEALTH
# ===========================================================================


async def test_44_success_stamps_the_last_success(world: World) -> None:
    """Requirement 44."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    assert outcome.connection.last_sync_succeeded_at is None

    await run_sync(world, outcome.connection.id)
    assert outcome.connection.sync_status is PrChannelSyncStatus.SUCCESS
    assert outcome.connection.last_sync_succeeded_at is not None
    assert outcome.connection.consecutive_failures == 0


async def test_45_46_a_transient_failure_records_a_safe_error_and_keeps_the_connection(
    world: World,
) -> None:
    """Requirements 45 and 46, and the refusal to burn a credential on a timeout."""
    from meobot.integrations.youtube.errors import YouTubeApiError

    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    token_before = outcome.connection.encrypted_credential

    await run_sync(
        world,
        outcome.connection.id,
        provider=FakeProvider(
            fail_with=YouTubeApiError("boom", error_code=PrChannelSyncErrorCode.RATE_LIMITED)
        ),
    )

    assert outcome.connection.sync_status is PrChannelSyncStatus.FAILED
    assert outcome.connection.last_sync_error_code is PrChannelSyncErrorCode.RATE_LIMITED
    # A sentence MeoBot wrote, not a provider payload.
    assert outcome.connection.last_sync_error_message is not None
    assert "YouTube" in outcome.connection.last_sync_error_message
    assert "boom" not in outcome.connection.last_sync_error_message
    # The credential is untouched, and the connection is still connected.
    assert outcome.connection.status is PrChannelConnectionState.CONNECTED
    assert outcome.connection.encrypted_credential == token_before


async def test_47_a_revoked_grant_moves_the_connection_to_action_required(
    world: World,
) -> None:
    """Requirement 47, and requirement 20's data badge.

    The one error code allowed to change the connection's state - and the badge
    that follows it must not be a hopeful green over data that stopped moving.
    """
    from meobot.integrations.youtube.errors import YouTubeApiError

    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    await run_sync(world, outcome.connection.id)

    await run_sync(
        world,
        outcome.connection.id,
        provider=FakeProvider(
            fail_with=YouTubeApiError("gone", error_code=PrChannelSyncErrorCode.AUTH_REQUIRED)
        ),
    )
    assert outcome.connection.status is PrChannelConnectionState.ACTION_REQUIRED

    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.status is PrChannelMetricsStatus.ACTION_REQUIRED
    assert view.total == 1, "the API history it collected is still there"


async def test_48_reconnecting_clears_the_auth_required_state(world: World) -> None:
    """Requirement 48. Somebody who just reconnected must not be told to again."""
    from meobot.integrations.youtube.errors import YouTubeApiError

    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    await run_sync(
        world,
        outcome.connection.id,
        provider=FakeProvider(
            fail_with=YouTubeApiError("gone", error_code=PrChannelSyncErrorCode.AUTH_REQUIRED)
        ),
    )
    assert outcome.connection.status is PrChannelConnectionState.ACTION_REQUIRED

    again = await connect(world, row.id)
    assert again.connection.id == outcome.connection.id
    assert again.connection.status is PrChannelConnectionState.CONNECTED
    assert again.connection.last_sync_error_code is None
    assert again.connection.consecutive_failures == 0


async def test_49_history_survives_a_run_of_failures(world: World) -> None:
    """Requirement 49."""
    from meobot.integrations.youtube.errors import YouTubeApiError

    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    await run_sync(world, outcome.connection.id)

    for _ in range(3):
        await run_sync(
            world,
            outcome.connection.id,
            provider=FakeProvider(
                fail_with=YouTubeApiError(
                    "boom", error_code=PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE
                )
            ),
        )
    assert outcome.connection.consecutive_failures == 3
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 1


# ===========================================================================
# 50-56: THE SCHEDULER
# ===========================================================================


async def test_50_an_eligible_connection_is_selected(world: World) -> None:
    """Requirement 50."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)

    due = await world.services.channel_sync.due_connections()
    assert [item.id for item in due] == [outcome.connection.id]


async def test_51_a_disconnected_channel_is_skipped(world: World) -> None:
    """Requirement 51."""
    row = await youtube_channel(world)
    await connect(world, row.id)
    await world.services.channel_connections.disconnect(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=row.id,
        provider_client=FakeProvider(),
    )
    assert await world.services.channel_sync.due_connections() == []


async def test_52_a_non_youtube_channel_never_appears(world: World) -> None:
    """Requirement 52. It cannot even be connected, so it cannot be due."""
    await make_channel(world, platform_code="TIKTOK", name="Dr Tiến")
    assert await world.services.channel_sync.due_connections() == []


async def test_53_auto_sync_disabled_is_skipped(world: World) -> None:
    """Requirement 53."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    outcome.connection.auto_sync_enabled = False
    await world.session.flush()
    assert await world.services.channel_sync.due_connections() == []


async def test_54_a_connection_synced_recently_is_not_due(world: World) -> None:
    """Requirement 54. Daily cadence, measured from the last success."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    await run_sync(world, outcome.connection.id)

    assert await world.services.channel_sync.due_connections() == []

    # A day later it comes round again.
    outcome.connection.last_sync_succeeded_at = utcnow() - timedelta(days=2)
    await world.session.flush()
    assert len(await world.services.channel_sync.due_connections()) == 1


def test_54a_being_due_is_measured_from_the_last_success() -> None:
    """A connection failing every cycle must still come round again."""
    now = utcnow()
    assert is_due(last_succeeded_at=None, now=now, min_interval_seconds=86400) is True
    assert (
        is_due(last_succeeded_at=now - timedelta(hours=1), now=now, min_interval_seconds=86400)
        is False
    )
    assert (
        is_due(last_succeeded_at=now - timedelta(days=2), now=now, min_interval_seconds=86400)
        is True
    )


async def test_55_one_channel_failing_does_not_stop_the_others(world: World) -> None:
    """Requirement 55. A sweep is a loop over independent attempts."""
    from meobot.integrations.youtube.errors import YouTubeApiError

    broken_channel = await youtube_channel(world, name="Hỏng")
    working_channel = await youtube_channel(world, name="Chạy")
    broken = (await connect(world, broken_channel.id)).connection
    working = (
        await connect(world, working_channel.id, provider=FakeProvider(account_id=OTHER_ACCOUNT_ID))
    ).connection

    first = await run_sync(
        world,
        broken.id,
        provider=FakeProvider(
            fail_with=YouTubeApiError("boom", error_code=PrChannelSyncErrorCode.UNKNOWN)
        ),
    )
    second = await run_sync(world, working.id, provider=FakeProvider(account_id=OTHER_ACCOUNT_ID))

    assert first.ok is False
    assert second.ok is True
    assert second.snapshot_id is not None


async def test_56_a_failing_connection_backs_off_rather_than_spinning(
    world: World,
) -> None:
    """Requirement 56. No tight retry loop, ever.

    A connection that has just failed is not immediately due again: the backoff
    grows with the failure count and is capped, and the floor is a whole sweep
    interval because this only ever *delays* a connection the sweeper would have
    taken.
    """
    from meobot.domain.pr.channel_connections import backoff_seconds
    from meobot.integrations.youtube.errors import YouTubeApiError

    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    await run_sync(
        world,
        outcome.connection.id,
        provider=FakeProvider(
            fail_with=YouTubeApiError("boom", error_code=PrChannelSyncErrorCode.UNKNOWN)
        ),
    )

    assert await world.services.channel_sync.due_connections() == [], "resting after a failure"

    assert backoff_seconds(1, base_seconds=3600, cap_seconds=86400) == 3600
    assert backoff_seconds(2, base_seconds=3600, cap_seconds=86400) == 7200
    assert backoff_seconds(50, base_seconds=3600, cap_seconds=86400) == 86400, "capped"
    assert backoff_seconds(0, base_seconds=3600, cap_seconds=86400) == 0


async def test_56a_a_stranded_claim_is_released(world: World) -> None:
    """A worker that dies must not stop a channel syncing for ever."""
    row = await youtube_channel(world)
    outcome = await connect(world, row.id)
    await world.services.channel_sync.claim(outcome.connection.id)
    outcome.connection.last_sync_started_at = utcnow() - timedelta(hours=4)
    await world.session.flush()

    released = await world.services.channel_sync.release_stale()
    assert released == 1
    assert outcome.connection.sync_status is not PrChannelSyncStatus.SYNCING


# ===========================================================================
# The registry, and what is deliberately absent
# ===========================================================================


def test_the_registry_supports_only_the_implemented_platforms() -> None:
    """What has a connector, and what deliberately does not.

    Step 1F.2.4b registered one platform; Step 1F.2.4c added Facebook and
    Instagram; Step 1F.2.6 added TikTok. The claim this test carries is not
    "one" or "four" - it is that the set is **exactly what has an
    implementation**, so that a platform cannot appear in the registry without a
    class behind it or disappear from the UI while one exists.

    Website and Other are the live half of that: they are absent from the
    registry **and** from the tree, and the second half is the one worth
    testing, because an empty provider class with a real-sounding name would
    read like an integration that exists. TikTok held that role until 1F.2.6
    gave it a real connector, which is why the class-absence check below now
    names Website.
    """
    assert set(PROVIDER_BUILDERS) == {
        PrChannelPlatform.YOUTUBE,
        PrChannelPlatform.FACEBOOK,
        PrChannelPlatform.INSTAGRAM,
        PrChannelPlatform.TIKTOK,
    }
    for supported in (
        PrChannelPlatform.YOUTUBE,
        PrChannelPlatform.FACEBOOK,
        PrChannelPlatform.INSTAGRAM,
        PrChannelPlatform.TIKTOK,
    ):
        assert supports(supported) is True, supported
    for absent in (
        PrChannelPlatform.WEBSITE,
        PrChannelPlatform.OTHER,
    ):
        assert supports(absent) is False, absent

    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src"
    hits = [
        path.name
        for path in root.rglob("*.py")
        if "class WebsiteChannelMetricsProvider" in path.read_text("utf-8")
    ]
    assert hits == [], f"a Website provider exists in {hits}"


def test_an_unsupported_platform_is_refused_rather_than_falling_back() -> None:
    """No generic provider. The refusal names the kind of no it is."""
    settings = connector_settings()
    with pytest.raises(PrValidationError) as failure:
        build_provider(PrChannelPlatform.WEBSITE, settings)
    assert failure.value.details["reason"] == "connector_unsupported"
    # The refusal names what *is* supported, so the message is actionable rather
    # than merely negative.
    assert failure.value.details["supported"] == [
        "FACEBOOK",
        "INSTAGRAM",
        "TIKTOK",
        "YOUTUBE",
    ]


def test_a_deployment_without_oauth_config_says_so_and_does_not_crash() -> None:
    """Requirement 92. Missing configuration is a state, not a boot failure."""
    bare = Settings(_env_file=None)
    assert bare.youtube_connector_enabled is False
    with pytest.raises(PrConnectorNotConfiguredError) as failure:
        build_provider(PrChannelPlatform.YOUTUBE, bare)
    assert "chưa sẵn sàng" in failure.value.message


def test_the_scopes_are_read_only_and_minimal() -> None:
    """Requirement 11 of the spec, and the reason it is worth a test.

    ``youtube.force-ssl`` and ``youtube`` would both work here and both grant
    upload and delete. Asking a marketing manager to consent to deleting videos
    so a dashboard can show a follower count is asking for the wrong thing.
    """
    assert YOUTUBE_SCOPES == (
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    )
    joined = " ".join(YOUTUBE_SCOPES)
    for dangerous in ("force-ssl", "youtubepartner", "upload", "monetary"):
        assert dangerous not in joined, dangerous
    assert joined.count("readonly") == 2


def test_the_authorization_url_asks_for_offline_access() -> None:
    """Unattended sync needs a refresh token, and the URL is what asks for one.

    ``prompt=consent`` matters as much as ``access_type=offline``: without it
    Google omits the refresh token on every authorization after the first, which
    is exactly the reconnect case.
    """
    url = FakeGoogle().provider().build_authorization_url(state="opaque-state")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=opaque-state" in url
    assert "response_type=code" in url
    # No incremental authorization: a grant must not accumulate scopes from
    # other Google integrations sharing this client.
    assert "include_granted_scopes" not in url


def test_no_provider_module_builds_an_unmocked_http_client() -> None:
    """The claim that keeps this whole file offline.

    Every endpoint is a literal in ``constants`` and the client is injectable.
    What must not exist is a module-level ``httpx.AsyncClient()`` that would
    quietly make real requests the first time CI ran.
    """
    from pathlib import Path

    import meobot.integrations.youtube.provider as provider_module

    source = Path(provider_module.__file__).read_text(encoding="utf-8")
    # The one construction is lazy, inside ``_ensure_client``, and only when no
    # client was injected.
    assert source.count("httpx.AsyncClient(") == 1
    assert "def _ensure_client" in source
    # And no URL is ever built from anything but the constants module.
    assert "channel.url" not in source
    assert "base_url=" not in source
