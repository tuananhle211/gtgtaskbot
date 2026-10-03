"""Step 1F.2.6 - TikTok Login Kit consent, the Display API, and the probe.

Numbered 1-52, following the requirement numbering the step was specified with:
1-6 the registry and configuration, 7-19 OAuth, 20-27 the token lifecycle,
28-35 account identity, 36-42 measurement and degradation, 43-52 the probe.

**No test in this file contacts TikTok.** Every one drives the real provider,
the real client and the real probe through an ``httpx.MockTransport``.

What this file is really testing
---------------------------------

Three things TikTok does that no connector in this repository had met before,
each of which is a silent data-corruption bug if got wrong:

**A 200 is not a success.** TikTok answers a missing scope, a bad field name and
a rate limit with ``HTTP 200`` and an ``error`` object. A client that classified
on the status line would hand back an empty ``data`` as though it were a
reading, and the sync would write a snapshot of nulls and record it as a
success - which on a dashboard is indistinguishable from an account that lost
all its followers. Tests 12, 37 and 39.

**The refresh token rotates.** Every refresh returns a new one and retires the
old. Dropping it produces a connector that works perfectly for one day and then
fails ``AUTH_REQUIRED`` forever. Tests 21-24.

**A lifetime total is not a windowed one.** ``likes_count`` is every like the
account has ever received; ``likes_30d`` is a month's worth. They differ by
three orders of magnitude on a real account, and the first must never be written
into the second. Test 40.
"""

from __future__ import annotations

# The ``world`` fixture and the channel helper are imported from sibling modules
# rather than rebuilt, for the reason ``test_pr_meta_connector`` gives. ruff
# sees a redefinition on every signature that takes the fixture.
# ruff: noqa: F811
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from meobot.application.pr_channel_providers import (
    PROVIDER_BUILDERS,
    PrConnectorNotConfiguredError,
    build_provider,
    supports,
)
from meobot.cli.tiktok_probe import render
from meobot.core.config import Settings
from meobot.core.secrets import generate_key
from meobot.core.time import utcnow
from meobot.db.models.pr_channel_connection import PrChannelConnection, PrChannelOAuthState
from meobot.db.models.pr_reporting import PrChannelMetricSnapshot
from meobot.db.models.user import User
from meobot.domain.pr.channel_connections import (
    CONNECTABLE_PLATFORMS,
    PrChannelConnectionState,
    PrChannelSyncErrorCode,
    PrChannelSyncStatus,
    PrChannelSyncTrigger,
    is_connectable,
)
from meobot.domain.pr.channel_metrics import (
    AccountSelectingProvider,
    PrChannelPlatform,
)
from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.reporting import PrMetricSource
from meobot.integrations.tiktok.client import TikTokApiClient
from meobot.integrations.tiktok.constants import (
    MAX_VIDEO_PAGES,
    TIKTOK_SCOPES,
    VIDEO_PAGE_SIZE,
    TikTokEndpoints,
)
from meobot.integrations.tiktok.errors import (
    TikTokApiError,
    classify_tiktok_error,
    is_ok,
    oauth_error_code,
)
from meobot.integrations.tiktok.probe import (
    PROBE_MAX_PAGES,
    ProbeVerdict,
    TikTokCapabilityProbe,
)
from meobot.integrations.tiktok.provider import (
    TIKTOK_API_PRODUCT_KEY,
    TIKTOK_FIELDS_KEY,
    TIKTOK_TOTAL_LIKES_KEY,
    TIKTOK_VIDEO_COUNT_KEY,
    TikTokChannelMetricsProvider,
)
from tests.unit.test_pr_channel_metrics import channel as make_channel
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

NOW = utcnow().replace(microsecond=0)

OPEN_ID = "_000AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
UNION_ID = "_111ZyXwVuTsRqPoNmLkJiHgFeDcBa0987654321"

#: Both secrets, as distinctive strings. Every "no token in the output" test
#: searches for these exact values, so they must not resemble ordinary words.
ACCESS_TOKEN = "act.SECRETACCESSTOKENVALUE0001"
REFRESH_TOKEN = "rft.SECRETREFRESHTOKENVALUE0001"
ROTATED_REFRESH = "rft.SECRETROTATEDVALUE0002"
CLIENT_SECRET = "cs.SECRETCLIENTSECRET0003"


# ===========================================================================
# Fixtures: a TikTok that lives in this file
# ===========================================================================


def video_row(video_id: str, *, created: int, **counters: Any) -> dict[str, Any]:
    """One ``video/list`` row, shaped the way TikTok shapes one."""
    row: dict[str, Any] = {"id": video_id, "create_time": created, "title": f"Video {video_id}"}
    row.update(counters)
    return row


@dataclass
class FakeTikTok:
    """Every TikTok endpoint the connector uses, as one transport.

    Reproduces the three shapes that matter and that a friendlier fake would
    hide: an error delivered on a **200**, a field list refused **whole** rather
    than partially, and a refresh that **rotates** the refresh token.
    """

    user: dict[str, Any] = field(
        default_factory=lambda: {
            "open_id": OPEN_ID,
            "union_id": UNION_ID,
            "avatar_url": "https://p16.tiktokcdn.com/avatar.jpeg",
            "display_name": "Tâm sự cùng bs Vũ Trọng Tiến",
            "username": "bsvutrongtien",
            "profile_deep_link": "https://www.tiktok.com/@bsvutrongtien",
            "bio_description": "Bác sĩ thẩm mỹ",
            "is_verified": False,
            "follower_count": 128400,
            "following_count": 312,
            "likes_count": 4_500_000,
            "video_count": 382,
        }
    )
    #: Fields whose **scope** this grant does not cover. Asking for one refuses
    #: the whole request with ``scope_not_authorized`` on a 200 - TikTok's real
    #: shape, and the reason a connector must degrade by group.
    denied_fields: frozenset[str] = frozenset()
    #: Fields TikTok does not recognise at all. ``invalid_params``, also on a
    #: 200, and it means something different: stop asking rather than reconnect.
    unknown_fields: frozenset[str] = frozenset()
    #: Fields accepted and then simply **omitted** from the response. The third
    #: failure mode, and the one the probe's ``NOT_RETURNED`` verdict exists for.
    silent_fields: frozenset[str] = frozenset()
    videos: list[dict[str, Any]] = field(default_factory=list)
    #: A failure scoped to ``video/list``, leaving user info healthy.
    video_error: str | None = None
    #: A failure scoped to ``/oauth/token/``, in OAuth's own dialect.
    token_error: str | None = None
    token_status: int = 200
    granted_scope: str = ",".join(TIKTOK_SCOPES)
    #: Every refresh hands back a new refresh token, as TikTok does.
    rotate_to: str = ROTATED_REFRESH
    requests: list[httpx.Request] = field(default_factory=list)

    # --- the transport ----------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            path = urlsplit(str(request.url)).path
            if path.endswith("/oauth/token/"):
                return self._token(request)
            if path.endswith("/oauth/revoke/"):
                return httpx.Response(200, json={"error": {"code": "ok", "message": ""}})
            if path.endswith("/user/info/"):
                return self._user_info(str(request.url))
            if path.endswith("/video/list/"):
                return self._video_list(request)
            return httpx.Response(404, json=_error("invalid_params", "no such endpoint"))

        return httpx.MockTransport(handle)

    def _token(self, request: httpx.Request) -> httpx.Response:
        if self.token_error is not None:
            return httpx.Response(
                self.token_status,
                json={"error": self.token_error, "error_description": "nope", "log_id": "L1"},
            )
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS_TOKEN,
                "expires_in": 86400,
                "open_id": OPEN_ID,
                "refresh_token": self.rotate_to,
                "refresh_expires_in": 31536000,
                "scope": self.granted_scope,
                "token_type": "Bearer",
            },
        )

    def _requested(self, url: str) -> list[str]:
        raw = parse_qs(urlsplit(url).query).get("fields", [""])[0]
        return [name for name in raw.split(",") if name]

    def _refuse(self, asked: list[str]) -> httpx.Response | None:
        """TikTok's real refusal shape: **200**, and the whole request dies."""
        if any(name in self.unknown_fields for name in asked):
            return httpx.Response(200, json=_error("invalid_params", "field is invalid"))
        if any(name in self.denied_fields for name in asked):
            return httpx.Response(200, json=_error("scope_not_authorized", "scope missing"))
        return None

    def _user_info(self, url: str) -> httpx.Response:
        asked = self._requested(url)
        refusal = self._refuse(asked)
        if refusal is not None:
            return refusal
        user = {
            name: self.user[name]
            for name in asked
            if name in self.user and name not in self.silent_fields
        }
        return httpx.Response(
            200,
            json={"data": {"user": user}, "error": {"code": "ok", "message": "", "log_id": "L"}},
        )

    def _video_list(self, request: httpx.Request) -> httpx.Response:
        if self.video_error is not None:
            return httpx.Response(200, json=_error(self.video_error, "refused"))
        asked = self._requested(str(request.url))
        refusal = self._refuse(asked)
        if refusal is not None:
            return refusal
        import json as _json

        body = _json.loads(request.content or b"{}")
        limit = int(body.get("max_count", VIDEO_PAGE_SIZE))
        start = int(body.get("cursor", 0))
        window = self.videos[start : start + limit]
        rows = [
            {
                key: value
                for key, value in row.items()
                # ``id`` always comes back; everything else only if asked for
                # and not on the silent list.
                if (key == "id" or key in asked) and key not in self.silent_fields
            }
            for row in window
        ]
        end = start + len(window)
        return httpx.Response(
            200,
            json={
                "data": {"videos": rows, "cursor": end, "has_more": end < len(self.videos)},
                "error": {"code": "ok", "message": "", "log_id": "L"},
            },
        )

    # --- builders ---------------------------------------------------------
    def client(self) -> TikTokApiClient:
        return TikTokApiClient(
            client_key="client-key",
            client_secret=CLIENT_SECRET,
            redirect_uri="https://pr.example.com/api/pr/channels/connections/tiktok/callback",
            endpoints=TikTokEndpoints(),
            client=httpx.AsyncClient(transport=self.transport()),
        )

    def provider(self) -> TikTokChannelMetricsProvider:
        return TikTokChannelMetricsProvider(client=self.client())

    def sent(self) -> str:
        return " ".join(str(request.url) for request in self.requests)

    def bodies(self) -> str:
        return " ".join((request.content or b"").decode() for request in self.requests)


def _error(code: str, message: str) -> dict[str, Any]:
    """An Open API error body. Note the ``data`` beside it: TikTok sends both."""
    return {"data": {}, "error": {"code": code, "message": message, "log_id": "L-ERR"}}


def connector_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "web_base_url": "https://pr.example.com",
        "tiktok_client_key": "client-key",
        "tiktok_client_secret": CLIENT_SECRET,
        "pr_secret_encryption_key": generate_key(),
        "web_cookie_secure": False,
    }
    values.update(overrides)
    return Settings(**values)


async def tiktok_channel(world: World, *, name: str = "Tâm sự cùng bs Vũ Trọng Tiến"):
    return await make_channel(world, platform_code="TIKTOK", name=name)


async def authorize_and_finish(
    world: World,
    channel_id: uuid.UUID,
    *,
    tiktok: FakeTikTok,
    actor: User | None = None,
    code: str = "tiktok-auth-code",
):
    """Walk a real authorization through the real provider over a fake TikTok."""
    who = actor or world.owner
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(who), channel_id=channel_id, provider=PrChannelPlatform.TIKTOK
    )
    token = start.authorization_url.split("state=")[1]
    return await world.services.channel_connections.finish_authorization(
        session_actor=world.actor(who),
        request_id=world.request_id,
        state_token=token,
        code=code,
        provider_client=tiktok.provider(),
    )


async def sync_now(world: World, connection, *, tiktok: FakeTikTok):
    """One sync attempt through the real orchestration, over the fake TikTok."""
    return await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.MANUAL,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=tiktok.provider(),
    )


@pytest.fixture(autouse=True)
def _configured(world: World) -> None:
    settings = connector_settings()
    world.services.channel_connections._settings = settings
    world.services.channel_sync._settings = settings
    world.services.settings = settings


@pytest.fixture
def tiktok() -> FakeTikTok:
    return FakeTikTok()


# ===========================================================================
# 1-6: THE REGISTRY AND CONFIGURATION
# ===========================================================================


def test_01_the_registry_now_supports_tiktok() -> None:
    """Requirement 1."""
    assert supports(PrChannelPlatform.TIKTOK) is True
    assert PrChannelPlatform.TIKTOK in PROVIDER_BUILDERS
    assert is_connectable(PrChannelPlatform.TIKTOK) is True
    assert PrChannelPlatform.TIKTOK in CONNECTABLE_PLATFORMS


def test_02_adding_tiktok_did_not_disturb_the_other_three() -> None:
    """Requirement 2. The port's promise: a platform is a registry entry."""
    assert set(PROVIDER_BUILDERS) == {
        PrChannelPlatform.YOUTUBE,
        PrChannelPlatform.FACEBOOK,
        PrChannelPlatform.INSTAGRAM,
        PrChannelPlatform.TIKTOK,
    }


@pytest.mark.parametrize("platform", [PrChannelPlatform.WEBSITE, PrChannelPlatform.OTHER])
def test_03_website_and_other_stay_unsupported(platform: PrChannelPlatform) -> None:
    """Requirement 3. Adding one connector must not soften the refusal."""
    assert supports(platform) is False
    with pytest.raises(PrValidationError) as failure:
        build_provider(platform, connector_settings())
    assert failure.value.details["reason"] == "connector_unsupported"


def test_04_unconfigured_is_a_different_refusal_from_unsupported() -> None:
    """Requirement 4.

    An operator with two environment variables to fill in must not be told the
    platform is unsupported - that sends them somewhere no amount of work helps.
    """
    settings = connector_settings(tiktok_client_key=None, tiktok_client_secret=None)
    with pytest.raises(PrConnectorNotConfiguredError) as failure:
        build_provider(PrChannelPlatform.TIKTOK, settings)
    assert failure.value.details["provider"] == "TIKTOK"
    assert "TIKTOK_CLIENT_KEY" in str(failure.value)


def test_05_the_connector_needs_an_encryption_key_too() -> None:
    """Requirement 5.

    Checked together with the app credentials because a missing key fails only
    *after* somebody has already consented in TikTok's UI, which is the most
    expensive possible moment to discover it.
    """
    assert connector_settings().tiktok_connector_enabled is True
    assert connector_settings(pr_secret_encryption_key=None).tiktok_connector_enabled is False


def test_06_the_redirect_uri_derives_from_the_web_base_url() -> None:
    """Requirement 6. One setting, so the two halves cannot disagree."""
    assert connector_settings().tiktok_redirect_uri == (
        "https://pr.example.com/api/pr/channels/connections/tiktok/callback"
    )
    override = connector_settings(tiktok_oauth_redirect_uri="https://alt.example.com/cb")
    assert override.tiktok_redirect_uri == "https://alt.example.com/cb"


# ===========================================================================
# 7-19: OAUTH
# ===========================================================================


def test_07_the_authorization_url_asks_for_exactly_the_four_scopes(tiktok: FakeTikTok) -> None:
    """Requirement 7."""
    url = tiktok.provider().build_authorization_url(state="opaque-state")
    scopes = parse_qs(urlsplit(url).query)["scope"][0]
    assert scopes.split(",") == list(TIKTOK_SCOPES)
    assert set(TIKTOK_SCOPES) == {
        "user.info.basic",
        "user.info.profile",
        "user.info.stats",
        "video.list",
    }


def test_08_no_publishing_or_advertising_scope_is_ever_requested() -> None:
    """Requirement 8.

    MeoBot reads numbers. A consent screen asking a marketing manager to grant
    posting rights so a dashboard can show a follower count is asking for the
    wrong thing, and this test is what stops one being added by habit.
    """
    forbidden = {
        "video.upload",
        "video.publish",
        "user.account.type",
        "biz.creator.info",
        "ad.read",
    }
    assert forbidden.isdisjoint(TIKTOK_SCOPES)


def test_09_scopes_are_comma_separated_not_space_separated(tiktok: FakeTikTok) -> None:
    """Requirement 9.

    TikTok's own separator, and getting it wrong is silent: a space-joined list
    is read as one scope name that does not exist, and the token that comes back
    carries none of them.
    """
    url = tiktok.provider().build_authorization_url(state="s")
    raw = urlsplit(url).query
    assert "scope=user.info.basic%2Cuser.info.profile" in raw


def test_10_the_app_credential_parameter_is_client_key(tiktok: FakeTikTok) -> None:
    """Requirement 10.

    TikTok calls it ``client_key``. A request carrying ``client_id`` is refused
    with an error that names nothing, which is an afternoon to diagnose.
    """
    query = parse_qs(urlsplit(tiktok.provider().build_authorization_url(state="s")).query)
    assert query["client_key"] == ["client-key"]
    assert "client_id" not in query


def test_11_the_authorization_url_carries_the_opaque_state(tiktok: FakeTikTok) -> None:
    """Requirement 11."""
    query = parse_qs(urlsplit(tiktok.provider().build_authorization_url(state="abc123")).query)
    assert query["state"] == ["abc123"]
    assert query["response_type"] == ["code"]


@pytest.mark.asyncio
async def test_12_a_two_hundred_carrying_an_error_is_a_failure(tiktok: FakeTikTok) -> None:
    """Requirement 12. **The load-bearing test of this file.**

    TikTok answers a missing scope with ``HTTP 200`` and an ``error`` object
    beside an empty ``data``. A client that classified on the status line would
    return that as a reading, and the sync would write a snapshot of nulls and
    record it as a success - which on a dashboard is indistinguishable from an
    account that lost all its followers.
    """
    tiktok.denied_fields = frozenset({"follower_count"})
    with pytest.raises(TikTokApiError) as failure:
        await tiktok.client().user_info(
            access_token=ACCESS_TOKEN, fields=("open_id", "follower_count")
        )
    assert failure.value.error_code is PrChannelSyncErrorCode.INSUFFICIENT_SCOPE
    assert failure.value.status_code == 200


def test_13_is_ok_reads_the_error_object_and_not_the_status() -> None:
    """Requirement 13."""
    assert is_ok({"data": {}, "error": {"code": "ok"}}) is True
    assert is_ok({"data": {}}) is True
    assert is_ok({"data": {}, "error": {"code": "scope_not_authorized"}}) is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("access_token_invalid", PrChannelSyncErrorCode.AUTH_REQUIRED),
        ("scope_not_authorized", PrChannelSyncErrorCode.INSUFFICIENT_SCOPE),
        ("scope_permission_missed", PrChannelSyncErrorCode.INSUFFICIENT_SCOPE),
        ("rate_limit_exceeded", PrChannelSyncErrorCode.RATE_LIMITED),
        ("internal_error", PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE),
        ("user_not_found", PrChannelSyncErrorCode.INVALID_ACCOUNT),
        ("invalid_params", PrChannelSyncErrorCode.BAD_RESPONSE),
    ],
)
def test_14_every_open_api_error_maps_to_something_actionable(
    code: str, expected: PrChannelSyncErrorCode
) -> None:
    """Requirement 14. Seven codes a person and a retry policy can both act on."""
    assert classify_tiktok_error(200, _error(code, "x")) is expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("invalid_grant", PrChannelSyncErrorCode.AUTH_REQUIRED),
        ("access_denied", PrChannelSyncErrorCode.AUTH_REQUIRED),
        ("invalid_client", PrChannelSyncErrorCode.BAD_RESPONSE),
        ("invalid_request", PrChannelSyncErrorCode.BAD_RESPONSE),
    ],
)
def test_15_the_token_endpoint_speaks_a_different_dialect(
    error: str, expected: PrChannelSyncErrorCode
) -> None:
    """Requirement 15.

    ``/oauth/token/`` uses OAuth 2.0's flat ``error`` string, not the Open API's
    ``error`` object. One lenient parser reading both would mis-classify in
    whichever direction it guessed - and ``invalid_grant``, which means
    "reconnect", does not exist in the Open API vocabulary at all.
    """
    assert oauth_error_code(error, status_code=400) is expected


def test_16_a_client_fault_never_tells_somebody_to_reconnect() -> None:
    """Requirement 16.

    A misconfigured client key is the operator's problem. Reporting it as
    ``AUTH_REQUIRED`` would send a marketing manager to reauthorize, wasting
    their time and rotating a working credential for nothing.
    """
    verdict = oauth_error_code("invalid_client", status_code=401)
    assert verdict is PrChannelSyncErrorCode.BAD_RESPONSE


@pytest.mark.asyncio
async def test_17_a_full_authorization_stores_an_encrypted_credential(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 17. The whole flow, over the real service."""
    channel = await tiktok_channel(world)
    outcome = await authorize_and_finish(world, channel.id, tiktok=tiktok)

    assert outcome.connection.status is PrChannelConnectionState.CONNECTED
    assert outcome.connection.provider is PrChannelPlatform.TIKTOK
    assert outcome.connection.provider_account_id == OPEN_ID
    stored = outcome.connection.encrypted_credential
    assert stored is not None
    # Ciphertext, not the token. The whole point of the column.
    assert ROTATED_REFRESH not in stored
    assert ACCESS_TOKEN not in stored


@pytest.mark.asyncio
async def test_18_the_state_is_single_use(world: World, tiktok: FakeTikTok) -> None:
    """Requirement 18. A state token that works twice is not a state token."""
    channel = await tiktok_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=channel.id,
        provider=PrChannelPlatform.TIKTOK,
    )
    token = start.authorization_url.split("state=")[1]
    kwargs = {
        "session_actor": world.actor(world.owner),
        "request_id": world.request_id,
        "state_token": token,
        "code": "c",
    }
    await world.services.channel_connections.finish_authorization(
        provider_client=tiktok.provider(), **kwargs
    )
    with pytest.raises(PrValidationError):
        await world.services.channel_connections.finish_authorization(
            provider_client=tiktok.provider(), **kwargs
        )

    row = (
        await world.session.execute(
            select(PrChannelOAuthState).where(PrChannelOAuthState.channel_id == channel.id)
        )
    ).scalar_one()
    assert row.consumed_at is not None


@pytest.mark.asyncio
async def test_19_an_unknown_state_is_refused(world: World, tiktok: FakeTikTok) -> None:
    """Requirement 19. The CSRF device, doing its job."""
    with pytest.raises(PrValidationError):
        await world.services.channel_connections.finish_authorization(
            session_actor=world.actor(world.owner),
            request_id=world.request_id,
            state_token="not-a-state-anybody-minted",
            code="c",
            provider_client=tiktok.provider(),
        )


# ===========================================================================
# 20-27: THE TOKEN LIFECYCLE
# ===========================================================================


@pytest.mark.asyncio
async def test_20_the_durable_credential_is_the_refresh_token(tiktok: FakeTikTok) -> None:
    """Requirement 20. TikTok is Google-shaped here, not Meta-shaped."""
    tokens = await tiktok.provider().exchange_authorization_code(code="c")
    assert tokens.access_token == ACCESS_TOKEN
    assert tokens.durable_credential == ROTATED_REFRESH
    assert tokens.expires_at is not None


@pytest.mark.asyncio
async def test_21_acquire_access_exchanges_the_refresh_token(tiktok: FakeTikTok) -> None:
    """Requirement 21."""
    tokens = await tiktok.provider().acquire_access(durable_credential=REFRESH_TOKEN)
    assert tokens.access_token == ACCESS_TOKEN
    assert "grant_type=refresh_token" in tiktok.bodies()


@pytest.mark.asyncio
async def test_22_the_rotated_refresh_token_is_returned(tiktok: FakeTikTok) -> None:
    """Requirement 22. **The one that breaks the connector on day two.**

    TikTok rotates the refresh token on every refresh and retires the old one.
    A provider that returned no durable credential here - as Meta's correctly
    does, and as Google's usually does - would leave the connection holding a
    token TikTok has already invalidated.
    """
    tokens = await tiktok.provider().acquire_access(durable_credential=REFRESH_TOKEN)
    assert tokens.durable_credential == ROTATED_REFRESH
    assert tokens.durable_credential != REFRESH_TOKEN


@pytest.mark.asyncio
async def test_23_the_rotated_token_is_persisted_by_the_service(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 23. Rotation is only correct if it survives the process."""
    channel = await tiktok_channel(world)
    outcome = await authorize_and_finish(world, channel.id, tiktok=tiktok)
    connection = outcome.connection
    before = connection.encrypted_credential

    tiktok.rotate_to = "rft.SECONDROTATION0004"
    token = await world.services.channel_connections.access_token_for(
        connection, provider_client=tiktok.provider()
    )
    assert token == ACCESS_TOKEN
    assert connection.encrypted_credential != before
    assert (
        world.services.channel_connections.read_credential(connection) == "rft.SECONDROTATION0004"
    )


@pytest.mark.asyncio
async def test_24_a_dead_refresh_token_becomes_auth_required(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 24.

    The 365-day ceiling TikTok anchors to the *first* authorization and does not
    extend on refresh arrives here, as ``invalid_grant``. There is no column for
    that date and this step adds none: the right thing already happens, and the
    panel already says "kết nối lại".
    """
    channel = await tiktok_channel(world)
    connected = await authorize_and_finish(world, channel.id, tiktok=tiktok)

    tiktok.token_error = "invalid_grant"
    tiktok.token_status = 400
    outcome = await sync_now(world, connected.connection, tiktok=tiktok)
    assert outcome.ok is False
    assert outcome.error_code is PrChannelSyncErrorCode.AUTH_REQUIRED

    connection = (
        await world.session.execute(
            select(PrChannelConnection).where(PrChannelConnection.channel_id == channel.id)
        )
    ).scalar_one()
    assert connection.status is PrChannelConnectionState.ACTION_REQUIRED


@pytest.mark.asyncio
async def test_25_a_rate_limit_never_costs_somebody_their_connection(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 25. Only ``AUTH_REQUIRED`` moves a connection out of health."""
    channel = await tiktok_channel(world)
    connected = await authorize_and_finish(world, channel.id, tiktok=tiktok)

    tiktok.video_error = None
    tiktok.denied_fields = frozenset()
    tiktok.token_error = "rate_limit_exceeded"
    tiktok.token_status = 429
    outcome = await sync_now(world, connected.connection, tiktok=tiktok)
    assert outcome.error_code is PrChannelSyncErrorCode.RATE_LIMITED

    connection = (
        await world.session.execute(
            select(PrChannelConnection).where(PrChannelConnection.channel_id == channel.id)
        )
    ).scalar_one()
    assert connection.status is PrChannelConnectionState.CONNECTED
    assert connection.encrypted_credential is not None


@pytest.mark.asyncio
async def test_26_granted_scopes_are_read_back_not_assumed(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 26.

    A person can decline a scope at the consent screen, and an app not approved
    for ``user.info.stats`` gets a token without it. Recording the *request*
    instead of the *grant* would make the panel claim a permission the token
    does not carry.
    """
    tiktok.granted_scope = "user.info.basic,user.info.profile"
    channel = await tiktok_channel(world)
    outcome = await authorize_and_finish(world, channel.id, tiktok=tiktok)
    assert outcome.connection.granted_scopes == "user.info.basic user.info.profile"
    assert "user.info.stats" not in (outcome.connection.granted_scopes or "")


@pytest.mark.asyncio
async def test_27_disconnect_revokes_and_drops_the_credential(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 27.

    Unlike Meta - where the only available revoke drops the whole app grant for
    that person across every channel - TikTok's revoke is scoped to one token,
    so it really is called.
    """
    channel = await tiktok_channel(world)
    await authorize_and_finish(world, channel.id, tiktok=tiktok)
    await world.services.channel_connections.disconnect(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=channel.id,
        provider=PrChannelPlatform.TIKTOK,
        provider_client=tiktok.provider(),
    )
    connection = (
        await world.session.execute(
            select(PrChannelConnection).where(PrChannelConnection.channel_id == channel.id)
        )
    ).scalar_one()
    assert connection.status is PrChannelConnectionState.DISCONNECTED
    assert connection.encrypted_credential is None
    assert "/oauth/revoke/" in tiktok.sent()


# ===========================================================================
# 28-35: ACCOUNT IDENTITY
# ===========================================================================


@pytest.mark.asyncio
async def test_28_identity_binds_to_open_id(tiktok: FakeTikTok) -> None:
    """Requirement 28."""
    identity = await tiktok.provider().fetch_channel_identity(access_token=ACCESS_TOKEN)
    assert identity.account_id == OPEN_ID
    assert identity.title == "Tâm sự cùng bs Vũ Trọng Tiến"
    assert identity.handle == "@bsvutrongtien"
    assert identity.profile_url == "https://www.tiktok.com/@bsvutrongtien"


@pytest.mark.asyncio
async def test_29_identity_never_binds_to_username(world: World, tiktok: FakeTikTok) -> None:
    """Requirement 29.

    A username changes at will and a display name is not even unique - two
    clinics with the same name would bind to each other's numbers. ``open_id``
    is TikTok's stable per-app identifier and is the only thing bound to.
    """
    channel = await tiktok_channel(world)
    outcome = await authorize_and_finish(world, channel.id, tiktok=tiktok)
    assert outcome.connection.provider_account_id == OPEN_ID
    assert outcome.connection.provider_account_id != "bsvutrongtien"


@pytest.mark.asyncio
async def test_30_the_profile_url_is_tiktoks_own_deep_link(tiktok: FakeTikTok) -> None:
    """Requirement 30.

    Composed from a username it would be wrong the moment somebody renamed
    themselves, and would be a URL MeoBot invented rather than one the platform
    confirmed.
    """
    tiktok.user["profile_deep_link"] = "https://www.tiktok.com/@renamed"
    identity = await tiktok.provider().fetch_channel_identity(access_token=ACCESS_TOKEN)
    assert identity.profile_url == "https://www.tiktok.com/@renamed"


@pytest.mark.asyncio
async def test_31_a_token_resolving_to_no_account_is_invalid(tiktok: FakeTikTok) -> None:
    """Requirement 31."""
    tiktok.silent_fields = frozenset({"open_id"})
    with pytest.raises(TikTokApiError) as failure:
        await tiktok.provider().fetch_channel_identity(access_token=ACCESS_TOKEN)
    assert failure.value.error_code is PrChannelSyncErrorCode.INVALID_ACCOUNT


@pytest.mark.asyncio
async def test_32_a_sync_against_the_wrong_binding_is_refused(tiktok: FakeTikTok) -> None:
    """Requirement 32.

    The wrong-account check. Somebody reconnecting while signed into a different
    TikTok account must not silently start writing that account's numbers into
    this channel's history.
    """
    with pytest.raises(TikTokApiError) as failure:
        await tiktok.provider().fetch_channel_metrics(
            access_token=ACCESS_TOKEN, account_id="somebody-elses-open-id", now=NOW
        )
    assert failure.value.error_code is PrChannelSyncErrorCode.INVALID_ACCOUNT


def test_33_tiktok_is_not_an_account_selecting_provider(tiktok: FakeTikTok) -> None:
    """Requirement 33.

    TikTok consent authorizes exactly one account, so there is nothing to
    choose. Implementing the optional Protocol would put a chooser in front of a
    person listing one item - and would make ``PENDING_SELECTION`` a state a
    TikTok connection could get stuck in for no reason.
    """
    assert not isinstance(tiktok.provider(), AccountSelectingProvider)


@pytest.mark.asyncio
async def test_34_identity_survives_an_app_approved_for_basic_alone(
    tiktok: FakeTikTok,
) -> None:
    """Requirement 34.

    An app awaiting review for ``user.info.profile`` and ``user.info.stats``
    still has a usable identity. Without group degradation the whole request
    would die and a connection that could show a display name would show nothing.
    """
    tiktok.denied_fields = frozenset(
        {
            "profile_deep_link",
            "bio_description",
            "is_verified",
            "username",
            "follower_count",
            "following_count",
            "likes_count",
            "video_count",
        }
    )
    identity = await tiktok.provider().fetch_channel_identity(access_token=ACCESS_TOKEN)
    assert identity.account_id == OPEN_ID
    assert identity.title == "Tâm sự cùng bs Vũ Trọng Tiến"
    assert identity.handle is None


@pytest.mark.asyncio
async def test_35_a_refused_basic_group_still_fails_loudly(tiktok: FakeTikTok) -> None:
    """Requirement 35.

    Degradation has a floor. Without ``open_id`` there is no account identity
    and no connection worth having, so that refusal propagates rather than
    producing a connection bound to nothing.
    """
    tiktok.denied_fields = frozenset({"open_id"})
    with pytest.raises(TikTokApiError) as failure:
        await tiktok.provider().fetch_channel_identity(access_token=ACCESS_TOKEN)
    assert failure.value.error_code is PrChannelSyncErrorCode.INSUFFICIENT_SCOPE


# ===========================================================================
# 36-42: MEASUREMENT AND DEGRADATION
# ===========================================================================


@pytest.mark.asyncio
async def test_36_a_reading_carries_the_counts_the_display_api_serves(
    tiktok: FakeTikTok,
) -> None:
    """Requirement 36."""
    reading = await tiktok.provider().fetch_channel_metrics(
        access_token=ACCESS_TOKEN, account_id=OPEN_ID, now=NOW
    )
    assert reading.metrics["followers"] == 128400
    assert reading.metrics["following"] == 312
    assert reading.metrics["posts_count"] == 382
    assert reading.extra_metrics is not None
    assert reading.extra_metrics[TIKTOK_TOTAL_LIKES_KEY] == 4_500_000
    assert reading.extra_metrics[TIKTOK_VIDEO_COUNT_KEY] == 382
    assert reading.extra_metrics[TIKTOK_API_PRODUCT_KEY] == "DISPLAY_API"


@pytest.mark.asyncio
async def test_37_a_refused_stats_group_leaves_nulls_and_still_reads(
    tiktok: FakeTikTok,
) -> None:
    """Requirement 37.

    An optional field group failing must not fail the whole reading - and the
    counts it would have carried must be ``NULL``, never ``0``. A zero is a
    measurement and a blank is not.
    """
    tiktok.denied_fields = frozenset(
        {"follower_count", "following_count", "likes_count", "video_count"}
    )
    reading = await tiktok.provider().fetch_channel_metrics(
        access_token=ACCESS_TOKEN, account_id=OPEN_ID, now=NOW
    )
    assert reading.metrics["followers"] is None
    assert reading.metrics["following"] is None
    assert reading.metrics["posts_count"] is None
    assert reading.extra_metrics is not None
    assert reading.extra_metrics[TIKTOK_FIELDS_KEY]["stats"] == "not_permitted"
    assert reading.extra_metrics[TIKTOK_FIELDS_KEY]["basic"] == "available"


@pytest.mark.asyncio
async def test_38_an_unknown_field_is_recorded_as_unsupported(tiktok: FakeTikTok) -> None:
    """Requirement 38.

    ``invalid_params`` means the *name* is wrong, not the caller - a different
    word, because the two need opposite responses months later when somebody
    reads the snapshot and asks why a column is blank.
    """
    tiktok.unknown_fields = frozenset({"bio_description"})
    reading = await tiktok.provider().fetch_channel_metrics(
        access_token=ACCESS_TOKEN, account_id=OPEN_ID, now=NOW
    )
    assert reading.extra_metrics is not None
    assert reading.extra_metrics[TIKTOK_FIELDS_KEY]["profile"] == "unsupported"
    # And the groups beside it survived.
    assert reading.metrics["followers"] == 128400


@pytest.mark.asyncio
async def test_39_an_auth_failure_is_never_swallowed_as_degradation(
    tiktok: FakeTikTok,
) -> None:
    """Requirement 39.

    A revoked token must still fail the sync loudly. Swallowing it here would
    write a snapshot of nulls and record it as a success, which is the failure
    every rule in this connector is arranged to prevent.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_error("access_token_invalid", "gone"))

    client = TikTokApiClient(
        client_key="k",
        client_secret=CLIENT_SECRET,
        redirect_uri="https://pr.example.com/cb",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(TikTokApiError) as failure:
        await TikTokChannelMetricsProvider(client=client).fetch_channel_metrics(
            access_token=ACCESS_TOKEN, account_id=OPEN_ID, now=NOW
        )
    assert failure.value.error_code is PrChannelSyncErrorCode.AUTH_REQUIRED


@pytest.mark.asyncio
async def test_40_lifetime_likes_never_land_in_a_windowed_column(tiktok: FakeTikTok) -> None:
    """Requirement 40. **The mapping mistake that would be hardest to spot.**

    ``likes_count`` is every like the account has ever received - 4.5 million
    here. ``likes_30d`` is a month's worth. Filing the first in the second would
    put a number three orders of magnitude too large on a management card, and
    it would look plausible enough to be quoted to a client.
    """
    reading = await tiktok.provider().fetch_channel_metrics(
        access_token=ACCESS_TOKEN, account_id=OPEN_ID, now=NOW
    )
    canonical = reading.canonical()
    for windowed in (
        "likes_30d",
        "comments_30d",
        "shares_30d",
        "views_7d",
        "views_30d",
        "engagements_7d",
        "engagements_30d",
        "posts_count_7d",
        "posts_count_30d",
        "reach_30d",
        "impressions_30d",
        "video_views_30d",
    ):
        assert canonical[windowed] is None, windowed
    # And no reporting window is claimed for a reading that covers none.
    assert reading.period_start is None
    assert reading.period_end is None


@pytest.mark.asyncio
async def test_41_a_sync_appends_a_snapshot_and_is_idempotent(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 41.

    The existing pipeline, unchanged: claim, fetch, fingerprint, append. The
    second run of an account whose counts have not moved is a *duplicate*, which
    is success rather than failure - a reading that says the same thing about
    the same account is the same reading.
    """
    channel = await tiktok_channel(world)
    connected = await authorize_and_finish(world, channel.id, tiktok=tiktok)

    first = await sync_now(world, connected.connection, tiktok=tiktok)
    assert first.ok is True
    assert first.duplicate is False

    second = await sync_now(world, connected.connection, tiktok=tiktok)
    assert second.ok is True
    assert second.duplicate is True

    rows = (
        (
            await world.session.execute(
                select(PrChannelMetricSnapshot).where(
                    PrChannelMetricSnapshot.channel_id == channel.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].source is PrMetricSource.API
    assert rows[0].followers == 128400
    # An automatic reading names nobody as its recorder. Who asked is in the
    # audit trail; nobody observed this number.
    assert rows[0].recorded_by_user_id is None


@pytest.mark.asyncio
async def test_42_a_successful_sync_marks_the_connection_healthy(
    world: World, tiktok: FakeTikTok
) -> None:
    """Requirement 42."""
    channel = await tiktok_channel(world)
    connected = await authorize_and_finish(world, channel.id, tiktok=tiktok)
    await sync_now(world, connected.connection, tiktok=tiktok)
    connection = (
        await world.session.execute(
            select(PrChannelConnection).where(PrChannelConnection.channel_id == channel.id)
        )
    ).scalar_one()
    assert connection.sync_status is PrChannelSyncStatus.SUCCESS
    assert connection.consecutive_failures == 0
    assert connection.last_sync_error_code is None


# ===========================================================================
# 43-52: THE PROBE
# ===========================================================================


@pytest.mark.asyncio
async def test_43_the_probe_reports_available_fields(tiktok: FakeTikTok) -> None:
    """Requirement 43."""
    tiktok.videos = [video_row("v1", created=1_700_000_000, view_count=9000)]
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(
        access_token=ACCESS_TOKEN, granted_scopes=TIKTOK_SCOPES
    )
    assert report.open_id == OPEN_ID
    assert "follower_count" in report.available()
    assert "view_count" in report.available()


@pytest.mark.asyncio
async def test_44_a_permission_denied_field_is_not_permitted(tiktok: FakeTikTok) -> None:
    """Requirement 44."""
    tiktok.denied_fields = frozenset({"follower_count"})
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    result = next(r for r in report.results if r.field == "follower_count")
    assert result.verdict is ProbeVerdict.NOT_PERMITTED
    # And it did not take the three fields beside it down with it.
    assert "following_count" in report.available()


@pytest.mark.asyncio
async def test_45_an_unknown_field_is_unsupported(tiktok: FakeTikTok) -> None:
    """Requirement 45. A different word from 44, and a different remedy."""
    tiktok.unknown_fields = frozenset({"is_verified"})
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    result = next(r for r in report.results if r.field == "is_verified")
    assert result.verdict is ProbeVerdict.UNSUPPORTED


@pytest.mark.asyncio
async def test_46_a_silently_omitted_video_counter_is_not_returned(
    tiktok: FakeTikTok,
) -> None:
    """Requirement 46. **The verdict this whole probe exists to produce.**

    TikTok can accept a field, answer ``ok``, and simply leave it out. For the
    four video counters that is the difference between "this account's videos
    have no views" and "the Display API will not serve view counts to this app"
    - and those decide whether the next milestone can derive a 30-day window at
    all. Reporting it as ``EMPTY`` would send somebody to build that derivation
    on a field that is never coming.
    """
    tiktok.videos = [video_row("v1", created=1_700_000_000, view_count=9000)]
    tiktok.silent_fields = frozenset({"view_count"})
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    result = next(r for r in report.videos if r.field == "view_count")
    assert result.verdict is ProbeVerdict.NOT_RETURNED
    assert "view_count" not in report.available()


@pytest.mark.asyncio
async def test_47_a_present_but_zero_counter_is_empty_not_available(
    tiktok: FakeTikTok,
) -> None:
    """Requirement 47.

    A capability probe is not a reading. An account whose videos all have zero
    comments tells an operator nothing about whether the field works, and
    calling it ``AVAILABLE`` beside a real number would blur the one distinction
    this report exists to draw.
    """
    tiktok.videos = [video_row("v1", created=1_700_000_000, comment_count=0)]
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    result = next(r for r in report.videos if r.field == "comment_count")
    assert result.verdict is ProbeVerdict.EMPTY


@pytest.mark.asyncio
async def test_48_one_bad_field_never_invalidates_the_whole_probe(
    tiktok: FakeTikTok,
) -> None:
    """Requirement 48."""
    tiktok.videos = [video_row("v1", created=1_700_000_000, view_count=10)]
    tiktok.denied_fields = frozenset({"likes_count"})
    tiktok.unknown_fields = frozenset({"embed_link"})
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    assert len(report.available()) > 5
    assert {r.field for r in report.limitations()} == {"likes_count", "embed_link"}


@pytest.mark.asyncio
async def test_49_video_pagination_is_bounded(tiktok: FakeTikTok) -> None:
    """Requirement 49.

    Thirty videos and a probe that walks two pages of five. No unbounded history
    crawl exists anywhere in this connector, and the probe is where a later
    milestone's bounded walk gets its evidence that the cursor works.
    """
    tiktok.videos = [video_row(f"v{i}", created=1_700_000_000 - i * 3600) for i in range(30)]
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    assert report.pages_walked == PROBE_MAX_PAGES
    assert report.more_pages_available is True
    assert MAX_VIDEO_PAGES == 5


@pytest.mark.asyncio
async def test_50_a_refused_video_endpoint_is_reported_once(tiktok: FakeTikTok) -> None:
    """Requirement 50.

    An app not approved for ``video.list`` should read as one refusal, not as
    twelve identical ones burying the user-info half of the report.
    """
    tiktok.video_error = "scope_not_authorized"
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    assert report.videos == ()
    assert report.video_refusal is not None
    assert report.video_refusal.verdict is ProbeVerdict.NOT_PERMITTED
    # The user-info half is intact.
    assert "follower_count" in report.available()


@pytest.mark.asyncio
async def test_51_the_rendered_report_contains_no_token(tiktok: FakeTikTok) -> None:
    """Requirement 51. **The secrecy test.**

    A probe report is pasted into chat messages and support tickets. Neither
    credential, nor the client secret, may appear anywhere in it - in a value, in
    a sample, or in an error detail.
    """
    tiktok.videos = [video_row("v1", created=1_700_000_000, view_count=9000)]
    tiktok.denied_fields = frozenset({"likes_count"})
    report = await TikTokCapabilityProbe(tiktok.client()).probe_account(
        access_token=ACCESS_TOKEN, granted_scopes=TIKTOK_SCOPES
    )
    rendered = render("CH-0014", report)
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, ROTATED_REFRESH, CLIENT_SECRET):
        assert secret not in rendered
    # And it does say the useful things.
    assert "CH-0014" in rendered
    assert OPEN_ID in rendered
    assert "Permission / API limitations:" in rendered


@pytest.mark.asyncio
async def test_52_the_probe_writes_nothing_to_tiktok(tiktok: FakeTikTok) -> None:
    """Requirement 52.

    Every request a probe makes is a read. ``video/list`` and ``video/query``
    are POSTs by TikTok's design rather than because anything is being changed,
    so the check is on the *endpoints* reached, not on the HTTP verbs.
    """
    tiktok.videos = [video_row("v1", created=1_700_000_000)]
    await TikTokCapabilityProbe(tiktok.client()).probe_account(access_token=ACCESS_TOKEN)
    paths = {urlsplit(str(request.url)).path for request in tiktok.requests}
    assert paths <= {"/v2/user/info/", "/v2/video/list/"}
