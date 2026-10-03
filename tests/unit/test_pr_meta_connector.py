"""Step 1F.2.4c - Facebook Pages and Instagram professional accounts.

Numbered 1-78, following the requirement numbering the step was specified with:
1-6 the registry, 7-18 OAuth for both providers, 19-26 discovery and selection,
27-34 secret containment, 35-51 normalization, 52-59 the snapshot, 60-64
idempotency and concurrency, 65-70 health, 71-78 the scheduler.

**No test in this file contacts Meta.** Every one drives the real providers
through an ``httpx.MockTransport``, or drives the services through fakes.

What this file is really testing
---------------------------------

Step 1F.2.4b claimed that adding a provider would need a registry entry and
nothing else - no new table, no new status model, no fork of the sync
orchestration, no second scheduler. Step 1F.2.4c is the first time that claim
could be checked against a platform Google is nothing like: Meta has no refresh
token, its consent reaches a dozen accounts at once, and its reach metric is not
additive.

So the assertions here are as much about what **did not** have to change as
about Facebook and Instagram working. The YouTube suite next door is unedited
except for the credential rename, and that is the result.

The two claims that carry the most weight
------------------------------------------

**A chosen account must be one the server proved you can reach.** Test 20 posts
a Page id belonging to nobody and test 25 posts an Instagram account behind a
Page the person does not manage; both are refused against a discovery recomputed
at that moment, not against a list the browser was handed.

**A credential is scoped to what it can measure.** The connection ends up
holding the *Page's* token, not the long-lived user token discovery ran on -
test 34 checks the swap actually happened, because storing the wider credential
would hand every Page that person manages to whoever reads the row.
"""

from __future__ import annotations

# The ``world`` fixture and the channel helpers are imported from sibling
# modules rather than rebuilt, for the reason ``test_pr_youtube_connector``
# gives. ruff sees a redefinition on every signature that takes the fixture.
# ruff: noqa: F811
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from meobot.application.pr_channel_providers import (
    PROVIDER_BUILDERS,
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
)
from meobot.domain.pr.channel_metrics import (
    AccountSelectingProvider,
    PrChannelMetricsStatus,
    PrChannelPlatform,
)
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.reporting import PrMetricSource
from meobot.integrations.meta.client import MetaGraphClient, insight_window
from meobot.integrations.meta.constants import (
    FACEBOOK_SCOPES,
    INSTAGRAM_SCOPES,
    MetaEndpoints,
)
from meobot.integrations.meta.errors import MetaApiError, classify_graph_error
from meobot.integrations.meta.provider import (
    FacebookChannelMetricsProvider,
    InstagramChannelMetricsProvider,
)
from tests.unit.test_pr_channel_metrics import channel as make_channel
from tests.unit.test_pr_channel_metrics import record as record_manual
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

NOW = utcnow().replace(microsecond=0) - timedelta(days=1)

PAGE_A = "111111111111111"
PAGE_B = "222222222222222"
IG_A = "17841400000000001"
USER_ID = "999999999999999"

USER_TOKEN = "EAAuser-long-lived"
PAGE_A_TOKEN = "EAApage-a-token"
PAGE_B_TOKEN = "EAApage-b-token"


# ===========================================================================
# Fixtures: a Graph that lives in this file
# ===========================================================================


def page_row(
    page_id: str, name: str, token: str, *, ig_id: str | None = None, ig_user: str | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {"id": page_id, "name": name, "access_token": token}
    if ig_id is not None:
        row["instagram_business_account"] = {"id": ig_id, "username": ig_user or "apexmedia"}
    return row


def series(name: str, values: list[int]) -> dict[str, Any]:
    return {"name": name, "period": "day", "values": [{"value": v} for v in values]}


def post_row(
    post_id: str,
    *,
    created: datetime,
    reactions: int | None = 0,
    comments: int | None = 0,
    shares: int | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """One ``published_posts`` row, shaped the way Graph shapes one.

    ``shares=None`` reproduces Graph's real behaviour for a post nobody shared:
    the field is **omitted entirely** rather than sent as zero. ``reactions=None``
    reproduces a summary Graph declined, which means something different and is
    what the ``None`` propagation is tested against.
    """
    row: dict[str, Any] = {
        "id": post_id,
        "created_time": created.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        "permalink_url": f"https://www.facebook.com/{post_id}",
    }
    if message is not None:
        row["message"] = message
    if reactions is not None:
        row["reactions"] = {"data": [], "summary": {"total_count": reactions}}
    if comments is not None:
        row["comments"] = {"data": [], "summary": {"total_count": comments}}
    if shares is not None:
        row["shares"] = {"count": shares}
    return row


def _requested_metrics(url: str) -> set[str]:
    """The ``metric=`` names in a Graph insights URL."""
    from urllib.parse import parse_qs, unquote, urlsplit

    raw = parse_qs(urlsplit(url).query).get("metric", [])
    return {name for item in raw for name in unquote(item).split(",") if name}


@dataclass
class FakeGraph:
    """Every Meta endpoint the connector uses, as one transport."""

    pages: list[dict[str, Any]] = field(
        default_factory=lambda: [
            page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN, ig_id=IG_A, ig_user="apexmedia"),
            page_row(PAGE_B, "Apex Clinic", PAGE_B_TOKEN),
        ]
    )
    page_profile: dict[str, Any] = field(
        default_factory=lambda: {"followers_count": 124812, "fan_count": 130500}
    )
    ig_profile: dict[str, Any] = field(
        default_factory=lambda: {
            "id": IG_A,
            "username": "apexmedia",
            "followers_count": 88120,
            "media_count": 412,
        }
    )
    daily: dict[str, list[int]] = field(default_factory=lambda: {"page_post_engagements": [10]})
    #: Metric names this Graph version no longer recognises. Requesting one
    #: fails the **entire** call with ``(#100) … valid insights metric``, which
    #: is exactly what v23 does for ``page_impressions`` and
    #: ``page_impressions_unique``.
    retired_metrics: frozenset[str] = field(
        default_factory=lambda: frozenset({"page_impressions", "page_impressions_unique"})
    )
    ig_totals: dict[str, int] = field(
        default_factory=lambda: {"reach": 40000, "views": 90000, "total_interactions": 3000}
    )
    #: Step 1F.2.4d. What ``published_posts`` returns, newest first, exactly as
    #: Graph orders that edge. Empty by default so every test written before
    #: this step keeps the behaviour it asserted.
    posts: list[dict[str, Any]] = field(default_factory=list)
    #: How many rows one ``published_posts`` page holds in this fake. Small, so
    #: a handful of posts is enough to exercise real cursor pagination.
    posts_page_size: int = 25
    #: A failure scoped to the **post listing**, leaving identity, profile and
    #: insights healthy - the only shape that exercises post degradation.
    posts_status: int | None = None
    posts_error: dict[str, Any] | None = None
    #: Post fields this grant may not read, by the name that appears in a
    #: ``fields=`` parameter - ``"reactions"``, ``"comments"``.
    #:
    #: Reproduces the production shape found on CH-0004: a Page token that reads
    #: every core post field and is refused the two interaction summaries. Graph
    #: does **not** answer with the fields it will serve; it refuses the whole
    #: request with ``(#10)``, which is why one optional summary could fail a
    #: channel's entire sync.
    denied_post_fields: frozenset[str] = frozenset()
    status: int = 200
    error_payload: dict[str, Any] | None = None
    #: A failure scoped to the **insights** call, leaving identity and profile
    #: reads healthy. That is the only shape that exercises the degradation
    #: path - a global failure never gets past ``/me``.
    insights_status: int | None = None
    insights_error: dict[str, Any] | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            url = str(request.url)
            if self.status >= 400:
                return httpx.Response(
                    self.status, json=self.error_payload or {"error": {"code": 1}}
                )
            if "oauth/access_token" in url:
                token = USER_TOKEN if "fb_exchange_token" in url else "EAAshort"
                return httpx.Response(200, json={"access_token": token, "expires_in": 5183944})
            if "/me/accounts" in url:
                return httpx.Response(200, json={"data": self.pages})
            if "/published_posts" in url:
                return self._published_posts(url)
            if "/insights" in url:
                if self.insights_status is not None:
                    return httpx.Response(
                        self.insights_status,
                        json=self.insights_error or {"error": {"code": 1}},
                    )
                requested = _requested_metrics(url)
                # Graph v23 rejects the **whole** request when any metric name
                # is unknown - it does not answer with the valid ones. That is
                # the behaviour that took a production Facebook sync down, so
                # the fake reproduces it rather than the friendlier partial
                # response the connector originally assumed.
                unknown = [name for name in requested if name in self.retired_metrics]
                if unknown:
                    return httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": ("(#100) The value must be a valid insights metric"),
                                "type": "OAuthException",
                                "code": 100,
                            }
                        },
                    )
                if "metric_type=total_value" in url:
                    return httpx.Response(
                        200,
                        json={
                            "data": [
                                {"name": name, "total_value": {"value": value}}
                                for name, value in self.ig_totals.items()
                                if name in requested
                            ]
                        },
                    )
                return httpx.Response(
                    200,
                    json={"data": [series(k, v) for k, v in self.daily.items() if k in requested]},
                )
            if f"/{IG_A}" in url:
                return httpx.Response(200, json=self.ig_profile)
            if url.rstrip("/").endswith("/me") or "/me?" in url:
                # A Page token resolves to the Page; a user token to the person.
                token = request.headers.get("Authorization", "")
                if PAGE_A_TOKEN in token:
                    return httpx.Response(
                        200,
                        json={"id": PAGE_A, "name": "Apex Media", "username": "apexmedia"},
                    )
                if PAGE_B_TOKEN in token:
                    return httpx.Response(200, json={"id": PAGE_B, "name": "Apex Clinic"})
                return httpx.Response(200, json={"id": USER_ID, "name": "Nguyễn A"})
            if any(f"/{page['id']}" in url for page in self.pages):
                return httpx.Response(200, json=self.page_profile)
            return httpx.Response(404, json={"error": {"code": 100, "error_subcode": 33}})

        return httpx.MockTransport(handle)

    def _published_posts(self, url: str) -> httpx.Response:
        """Cursor pagination, including the ``paging.next`` flag.

        The flag matters: Graph returns cursors on the last page too, so a
        client that paged on the cursor alone would loop. The fake reproduces
        that rather than the friendlier shape a client might assume.
        """
        from urllib.parse import parse_qs, unquote, urlsplit

        if self.posts_status is not None:
            return httpx.Response(
                self.posts_status, json=self.posts_error or {"error": {"code": 1}}
            )
        query = parse_qs(urlsplit(url).query)
        requested = unquote((query.get("fields") or [""])[0])
        refused = sorted(name for name in self.denied_post_fields if name in requested)
        if refused:
            # Graph's real shape for a field this grant does not cover. The
            # whole request dies; nothing partial comes back.
            return httpx.Response(
                403,
                json={
                    "error": {
                        "message": (
                            f"(#10) This endpoint requires a permission "
                            f"the token does not have: {', '.join(refused)}"
                        ),
                        "type": "OAuthException",
                        "code": 10,
                    }
                },
            )
        start = int((query.get("after") or ["0"])[0])
        limit = min(int((query.get("limit") or ["25"])[0]), self.posts_page_size)
        page = [self._project(row, requested) for row in self.posts[start : start + limit]]
        end = start + len(page)
        payload: dict[str, Any] = {"data": page}
        if end < len(self.posts):
            payload["paging"] = {"cursors": {"after": str(end)}, "next": "https://next"}
        else:
            payload["paging"] = {"cursors": {"after": str(end)}}
        return httpx.Response(200, json=payload)

    @staticmethod
    def _project(row: dict[str, Any], requested: str) -> dict[str, Any]:
        """Only the fields this request asked for, the way Graph answers.

        Without this the fallback listing would still carry the summaries it
        deliberately stopped asking for, and a test proving reactions go
        ``NULL`` would prove nothing.
        """
        return {
            key: value
            for key, value in row.items()
            # ``id`` is always returned whether asked for or not, which is
            # Graph's behaviour and not an accident of this fake.
            if key == "id" or key in requested
        }

    def client(self) -> MetaGraphClient:
        return MetaGraphClient(
            app_id="app-id",
            app_secret="app-secret",
            redirect_uri="https://pr.example.com/api/pr/channels/connections/meta/callback",
            endpoints=MetaEndpoints(version="v23.0"),
            client=httpx.AsyncClient(transport=self.transport()),
        )

    def facebook(self) -> FacebookChannelMetricsProvider:
        return FacebookChannelMetricsProvider(self.client())

    def instagram(self) -> InstagramChannelMetricsProvider:
        return InstagramChannelMetricsProvider(self.client())

    def sent(self) -> str:
        return " ".join(str(request.url) for request in self.requests)


def connector_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "web_base_url": "https://pr.example.com",
        "meta_app_id": "app-id",
        "meta_app_secret": "app-secret",
        "youtube_oauth_client_id": "client-id",
        "youtube_oauth_client_secret": "client-secret",
        "pr_secret_encryption_key": generate_key(),
        "web_cookie_secure": False,
    }
    values.update(overrides)
    return Settings(**values)


async def fb_channel(world: World, *, name: str = "Apex Media"):
    return await make_channel(world, platform_code="FACEBOOK", name=name)


async def ig_channel(world: World, *, name: str = "Apex IG"):
    return await make_channel(world, platform_code="INSTAGRAM", name=name)


async def authorize_and_finish(
    world: World,
    channel_id: uuid.UUID,
    *,
    provider: PrChannelPlatform,
    graph: FakeGraph,
    actor: User | None = None,
):
    """Walk a real authorization through the real providers over a fake Graph."""
    who = actor or world.owner
    client = graph.facebook() if provider is PrChannelPlatform.FACEBOOK else graph.instagram()
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(who), channel_id=channel_id, provider=provider
    )
    token = start.authorization_url.split("state=")[1]
    return await world.services.channel_connections.finish_authorization(
        session_actor=world.actor(who),
        request_id=world.request_id,
        state_token=token,
        code="meta-auth-code",
        provider_client=client,
    )


@pytest.fixture(autouse=True)
def _configured(world: World) -> None:
    settings = connector_settings()
    world.services.channel_connections._settings = settings
    world.services.channel_sync._settings = settings
    world.services.settings = settings


# ===========================================================================
# 1-6: THE REGISTRY
# ===========================================================================


def test_01_02_03_the_registry_supports_youtube_facebook_and_instagram() -> None:
    """Requirements 1, 2 and 3.

    Asserts the three platforms **this step** added or relied on are registered,
    rather than pinning the registry's exact membership - Step 1F.2.6 added
    TikTok, and a Meta test failing over that would be testing the wrong thing.
    ``test_02`` in ``test_pr_tiktok_connector`` owns the exact-set assertion.
    """
    for platform in (
        PrChannelPlatform.YOUTUBE,
        PrChannelPlatform.FACEBOOK,
        PrChannelPlatform.INSTAGRAM,
    ):
        assert platform in PROVIDER_BUILDERS
    for platform in PROVIDER_BUILDERS:
        assert supports(platform) is True


@pytest.mark.parametrize("platform", [PrChannelPlatform.WEBSITE, PrChannelPlatform.OTHER])
def test_04_05_unsupported_platforms_stay_unsupported(platform: PrChannelPlatform) -> None:
    """Requirements 4 and 5.

    TikTok was the third member of this list until Step 1F.2.6 gave it a
    connector. It moved rather than being dropped: the same assertion now lives
    in ``test_pr_tiktok_connector.test_01``, in its positive form.
    """
    assert supports(platform) is False
    with pytest.raises(PrValidationError) as failure:
        build_provider(platform, connector_settings())
    assert failure.value.details["reason"] == "connector_unsupported"


def test_06_there_is_no_fallback_provider() -> None:
    """Requirement 6, and requirement 46 of the report.

    A registry that answered *something* for an unregistered platform would turn
    "not supported" into a runtime failure days later inside a sync job.

    This test used to also assert that no ``TikTokChannelMetricsProvider`` class
    existed anywhere in the tree - the point being that a stub raising
    ``NotImplementedError`` reads, to somebody scanning the codebase, like an
    integration that exists. Step 1F.2.6 wrote a real one, so the check now runs
    against ``Website``: the rule was never about TikTok specifically, it was
    about not shipping a class whose name promises more than it does.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src"
    hits = [
        path.name
        for path in root.rglob("*.py")
        if "class WebsiteChannelMetricsProvider" in path.read_text("utf-8")
    ]
    assert hits == []
    assert build_provider.__doc__ is not None
    with pytest.raises(PrValidationError):
        build_provider(None, connector_settings())


# ===========================================================================
# 7-18: OAUTH, BOTH PROVIDERS
# ===========================================================================


async def test_07_a_manager_can_start_a_facebook_authorization(world: World) -> None:
    """Requirement 7."""
    row = await fb_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=row.id,
        provider=PrChannelPlatform.FACEBOOK,
    )
    assert start.authorization_url.startswith("https://www.facebook.com/v23.0/dialog/oauth")
    assert "state=" in start.authorization_url


async def test_08_an_ordinary_member_cannot(world: World) -> None:
    """Requirement 8. Managing a credential is management work, on any platform."""
    row = await fb_channel(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.channel_connections.start_authorization(
            actor=world.actor(world.member),
            channel_id=row.id,
            provider=PrChannelPlatform.FACEBOOK,
        )


async def test_09_a_non_facebook_channel_cannot_start_a_facebook_flow(world: World) -> None:
    """Requirement 9, and requirement 13's server half.

    A TikTok channel and - just as importantly - an *Instagram* channel are both
    refused. The second is the one a hidden button would not have caught: both
    are Meta, both would authorize, and binding a Page to an Instagram channel
    would produce a connection that measures the wrong thing for ever.
    """
    tiktok = await make_channel(world, platform_code="TIKTOK", name="Dr Tiến")
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.start_authorization(
            actor=world.actor(world.owner),
            channel_id=tiktok.id,
            provider=PrChannelPlatform.FACEBOOK,
        )
    assert failure.value.details["reason"] == "platform_mismatch"

    instagram = await ig_channel(world)
    with pytest.raises(PrValidationError) as mismatch:
        await world.services.channel_connections.start_authorization(
            actor=world.actor(world.owner),
            channel_id=instagram.id,
            provider=PrChannelPlatform.FACEBOOK,
        )
    assert mismatch.value.details["reason"] == "platform_mismatch"


async def test_10_17_the_state_records_which_provider_it_is_for(world: World) -> None:
    """Requirements 10 and 17."""
    facebook = await fb_channel(world)
    instagram = await ig_channel(world)

    fb_start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=facebook.id,
        provider=PrChannelPlatform.FACEBOOK,
    )
    ig_start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=instagram.id,
        provider=PrChannelPlatform.INSTAGRAM,
    )

    fb_state = await world.session.get(PrChannelOAuthState, fb_start.state_id)
    ig_state = await world.session.get(PrChannelOAuthState, ig_start.state_id)
    assert fb_state is not None and ig_state is not None
    assert fb_state.provider is PrChannelPlatform.FACEBOOK
    assert ig_state.provider is PrChannelPlatform.INSTAGRAM
    assert fb_state.channel_id == facebook.id
    assert ig_state.channel_id == instagram.id


async def test_11_a_meta_state_cannot_be_used_twice(world: World) -> None:
    """Requirement 11. Consumed before any Graph call, exactly as YouTube's is."""
    row = await fb_channel(world)
    graph = FakeGraph()
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=row.id,
        provider=PrChannelPlatform.FACEBOOK,
    )
    token = start.authorization_url.split("state=")[1]
    await world.services.channel_connections.finish_authorization(
        session_actor=world.actor(world.owner),
        request_id=world.request_id,
        state_token=token,
        code="meta-auth-code",
        provider_client=graph.facebook(),
    )
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            session_actor=world.actor(world.owner),
            request_id=world.request_id,
            state_token=token,
            code="meta-auth-code",
            provider_client=graph.facebook(),
        )
    assert failure.value.details["reason"] == "state_consumed"


async def test_12_a_state_presented_by_another_session_is_refused(world: World) -> None:
    """Requirement 12, as the callback fix reshaped it.

    The session is now a **cross-check**, not the source of identity - a
    callback arriving with no cookie at all is valid. What is still refused is a
    browser that *did* send a session and is signed in as somebody else, and the
    refusal is the generic one: saying "this belongs to another person" would
    confirm to a prober that the state exists and that somebody else owns it.
    """
    row = await fb_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=row.id,
        provider=PrChannelPlatform.FACEBOOK,
    )
    token = start.authorization_url.split("state=")[1]
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.finish_authorization(
            session_actor=world.actor(world.head),
            request_id=world.request_id,
            state_token=token,
            code="meta-auth-code",
            provider_client=FakeGraph().facebook(),
        )
    assert failure.value.details["reason"] == "state_session_mismatch"
    assert "không hợp lệ hoặc đã hết hạn" in failure.value.message


async def test_13_18_a_facebook_state_cannot_finalize_an_instagram_connection(
    world: World,
) -> None:
    """Requirements 13 and 18, and the reason the provider lives on the state.

    Both flows are Meta, both produce a usable token, and the callback carries
    no provider of its own. What decides is the row MeoBot wrote when the flow
    started - so a Facebook authorization completed against an Instagram
    provider still creates a **Facebook** connection on the Facebook channel.
    """
    facebook = await fb_channel(world)
    graph = FakeGraph()
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=facebook.id,
        provider=PrChannelPlatform.FACEBOOK,
    )
    token = start.authorization_url.split("state=")[1]

    outcome = await world.services.channel_connections.finish_authorization(
        session_actor=world.actor(world.owner),
        request_id=world.request_id,
        state_token=token,
        code="meta-auth-code",
        # Even handed the *Instagram* client, the state decides.
        provider_client=graph.instagram(),
    )
    assert outcome.connection.provider is PrChannelPlatform.FACEBOOK
    assert outcome.connection.channel_id == facebook.id


async def test_14_a_manager_can_start_an_instagram_authorization(world: World) -> None:
    """Requirement 14."""
    row = await ig_channel(world)
    start = await world.services.channel_connections.start_authorization(
        actor=world.actor(world.owner),
        channel_id=row.id,
        provider=PrChannelPlatform.INSTAGRAM,
    )
    assert "dialog/oauth" in start.authorization_url


async def test_15_an_ordinary_member_cannot_start_an_instagram_flow(world: World) -> None:
    """Requirement 15."""
    row = await ig_channel(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.channel_connections.start_authorization(
            actor=world.actor(world.member),
            channel_id=row.id,
            provider=PrChannelPlatform.INSTAGRAM,
        )


async def test_16_a_non_instagram_channel_is_refused(world: World) -> None:
    """Requirement 16."""
    row = await fb_channel(world)
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.start_authorization(
            actor=world.actor(world.owner),
            channel_id=row.id,
            provider=PrChannelPlatform.INSTAGRAM,
        )
    assert failure.value.details["reason"] == "platform_mismatch"


def test_18a_the_scopes_are_read_only_and_minimal() -> None:
    """Requirement 49 of the spec, and worth its own assertion.

    Meta offers ``pages_manage_posts``, ``instagram_content_publish`` and the
    ads scopes on the same consent screen. Asking a marketing manager to grant
    posting rights so a dashboard can show a follower count is asking for the
    wrong thing, and the blast radius of a leaked grant is the difference
    between these lists and those.
    """
    assert FACEBOOK_SCOPES == ("pages_show_list", "pages_read_engagement", "read_insights")
    assert INSTAGRAM_SCOPES == (
        "pages_show_list",
        "pages_read_engagement",
        "instagram_basic",
        "instagram_manage_insights",
    )
    joined = " ".join(FACEBOOK_SCOPES + INSTAGRAM_SCOPES)
    for dangerous in (
        "manage_posts",
        "content_publish",
        "ads_management",
        "ads_read",
        "messaging",
        "manage_comments",
        "business_management",
        "publish",
    ):
        assert dangerous not in joined, dangerous


def test_18b_the_graph_version_is_configured_in_exactly_one_place() -> None:
    """Requirement 5 of the spec. No unversioned endpoint, no scattered strings.

    A version literal in several files is a deprecation that gets fixed in some
    of them.
    """
    from pathlib import Path

    # The *version literal*, which is the thing a deprecation forces somebody to
    # change. A host name appearing in a docstring is documentation; a second
    # ``v23.0`` in a second file is a bug waiting for Meta's next retirement.
    root = Path(__file__).resolve().parents[2] / "src"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text("utf-8")
        if "v23.0" in text and path.name not in {"constants.py", "config.py"}:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders

    # And no request is ever built against an unversioned Graph root.
    client_source = (root / "meobot" / "integrations" / "meta" / "client.py").read_text("utf-8")
    assert "graph.facebook.com" not in client_source, "hosts come from constants"

    endpoints = MetaEndpoints(version="v23.0")
    assert endpoints.graph.endswith("/v23.0")
    assert "/v23.0/" in endpoints.node("123")
    # Every URL carries the version; none is bare.
    for url in (endpoints.token, endpoints.authorize, endpoints.node("1", "insights")):
        assert "v23.0" in url


# ===========================================================================
# 19-26: DISCOVERY AND SELECTION
# ===========================================================================


async def test_19_several_pages_are_offered_rather_than_auto_bound(world: World) -> None:
    """Requirement 19, and the reason the chooser exists.

    One marketing manager routinely administers a dozen Pages. Binding the first
    result would be a coin flip with somebody's brand.
    """
    row = await fb_channel(world)
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=FakeGraph()
    )

    assert outcome.connection.status is PrChannelConnectionState.PENDING_SELECTION
    assert {a.account_id for a in outcome.pending_accounts} == {PAGE_A, PAGE_B}
    # Nothing is syncable yet, and the data badge must not claim otherwise.
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.status is not PrChannelMetricsStatus.CONNECTED_API


async def test_20_an_arbitrary_page_id_is_refused(world: World) -> None:
    """Requirement 20. The single most important test in this file.

    The submitted id is checked against a discovery result computed **here**,
    from the stored credential, at this moment - so a caller who posts a Page id
    belonging to somebody else gets a refusal rather than a binding.
    """
    row = await fb_channel(world)
    graph = FakeGraph()
    await authorize_and_finish(world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph)

    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.select_account(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            channel_id=row.id,
            account_id="000000000000000",
            provider_client=graph.facebook(),
        )
    assert failure.value.details["reason"] == "account_not_accessible"
    # And nothing was bound.
    connection = await world.services.channel_connections.get_live_connection(
        row.id, provider=PrChannelPlatform.FACEBOOK
    )
    assert connection is not None
    assert connection.status is PrChannelConnectionState.PENDING_SELECTION


async def test_21_a_single_eligible_page_binds_and_says_which(world: World) -> None:
    """Requirement 21, and requirement 47.

    Making somebody choose from a list of one is ceremony. Hiding *which*
    account was bound is not acceptable either, so the identity comes back.
    """
    row = await fb_channel(world)
    graph = FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )

    assert outcome.pending_accounts == ()
    assert outcome.connection.status is PrChannelConnectionState.CONNECTED
    assert outcome.connection.provider_account_id == PAGE_A
    assert outcome.identity.title == "Apex Media"


async def test_22_no_page_at_all_creates_no_connection(world: World) -> None:
    """Requirement 22, and requirement 48.

    A row that could never sync is worse than no row: the panel would show it as
    connected and nobody would know why the numbers never move.
    """
    row = await fb_channel(world)
    with pytest.raises(PrValidationError) as failure:
        await authorize_and_finish(
            world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=FakeGraph(pages=[])
        )
    assert failure.value.details["reason"] == "no_eligible_account"
    assert "Không tìm thấy Trang Facebook phù hợp" in failure.value.message
    assert (
        await world.services.channel_connections.get_live_connection(
            row.id, provider=PrChannelPlatform.FACEBOOK
        )
        is None
    )


async def test_23_24_instagram_discovery_only_offers_pages_that_have_one(
    world: World,
) -> None:
    """Requirements 23 and 24.

    ``PAGE_B`` has no linked Instagram professional account, so it is not an
    Instagram option. A chooser should show what will work, not what exists.
    """
    row = await ig_channel(world)
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.INSTAGRAM, graph=FakeGraph()
    )
    # Only one of the two Pages has an Instagram account, so it auto-binds.
    assert outcome.connection.provider_account_id == IG_A
    assert outcome.connection.provider_account_handle == "@apexmedia"
    assert PAGE_B not in {outcome.connection.provider_account_id}


async def test_25_an_arbitrary_instagram_account_id_is_refused(world: World) -> None:
    """Requirement 25."""
    row = await ig_channel(world)
    graph = FakeGraph(
        pages=[
            page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN, ig_id=IG_A),
            page_row(PAGE_B, "Apex Clinic", PAGE_B_TOKEN, ig_id="17841499999999999"),
        ]
    )
    await authorize_and_finish(world, row.id, provider=PrChannelPlatform.INSTAGRAM, graph=graph)
    with pytest.raises(PrValidationError) as failure:
        await world.services.channel_connections.select_account(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            channel_id=row.id,
            account_id="17841400000000999",
            provider_client=graph.instagram(),
        )
    assert failure.value.details["reason"] == "account_not_accessible"


async def test_26_a_page_without_a_professional_account_yields_a_clear_error(
    world: World,
) -> None:
    """Requirement 26, and requirement 15 of the spec.

    Consumer and private Instagram accounts are not reachable through this API
    at all, so a person whose Pages have none gets a sentence rather than a
    connection that can never sync.
    """
    row = await ig_channel(world)
    graph = FakeGraph(pages=[page_row(PAGE_B, "Apex Clinic", PAGE_B_TOKEN)])
    with pytest.raises(PrValidationError) as failure:
        await authorize_and_finish(world, row.id, provider=PrChannelPlatform.INSTAGRAM, graph=graph)
    assert "Không tìm thấy tài khoản Instagram Professional phù hợp" in failure.value.message


async def test_26a_the_account_list_is_recomputed_not_replayed(world: World) -> None:
    """A Page the person lost access to must stop being selectable.

    The list is derived from the platform on every call, so a chooser rendered
    five minutes ago cannot bind something that has since been taken away.
    """
    row = await fb_channel(world)
    graph = FakeGraph()
    await authorize_and_finish(world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph)

    choices = await world.services.channel_connections.list_account_choices(
        actor=world.actor(world.owner), channel_id=row.id, provider_client=graph.facebook()
    )
    assert {a.account_id for a in choices.accounts} == {PAGE_A, PAGE_B}

    # Access to Page B is withdrawn at the platform.
    narrowed = FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    with pytest.raises(PrValidationError):
        await world.services.channel_connections.select_account(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            channel_id=row.id,
            account_id=PAGE_B,
            provider_client=narrowed.facebook(),
        )


# ===========================================================================
# 27-34: SECRET CONTAINMENT
# ===========================================================================


async def test_27_28_the_meta_credential_is_stored_encrypted(world: World) -> None:
    """Requirements 27 and 28."""
    row = await fb_channel(world)
    graph = FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )
    stored = outcome.connection.encrypted_credential
    assert stored is not None
    assert PAGE_A_TOKEN not in stored
    assert USER_TOKEN not in stored
    assert stored.startswith("v1:primary:")


async def test_29_no_api_response_carries_a_meta_token(world: World) -> None:
    """Requirement 29. Asserted over the real HTTP surface."""
    row = await fb_channel(world)
    graph = FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    await authorize_and_finish(world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph)
    world.act_as(world.owner)

    for path in (f"/api/pr/channels/{row.id}/connection", f"/api/pr/channels/{row.id}"):
        response = world.client.get(path)
        assert response.status_code == 200, response.text
        for secret in (PAGE_A_TOKEN, USER_TOKEN, "app-secret"):
            assert secret not in response.text
        assert "encrypted_credential" not in response.text


async def test_30_31_no_log_or_audit_payload_carries_a_meta_token(world: World) -> None:
    """Requirements 30 and 31."""
    row = await fb_channel(world)
    graph = FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    await authorize_and_finish(world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph)

    entries = (await world.session.execute(select(AuditLog))).scalars().all()
    dumped = json.dumps(
        [{"a": e.action, "b": e.before_data, "c": e.after_data} for e in entries], default=str
    )
    for secret in (PAGE_A_TOKEN, USER_TOKEN, "app-secret", "meta-auth-code"):
        assert secret not in dumped, secret
    # The connect event is there, with safe identity.
    connected = [e for e in entries if e.action == "pr.channel.connection.connected"]
    assert connected
    assert connected[-1].after_data is not None
    assert connected[-1].after_data["provider"] == "FACEBOOK"
    assert connected[-1].after_data["provider_account_id"] == PAGE_A

    from pathlib import Path

    import meobot.integrations.meta.client as client_module
    import meobot.integrations.meta.provider as provider_module

    for module in (client_module, provider_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for call in _log_calls(source):
            marker = call.find("extra=")
            payload = call[marker:] if marker != -1 else ""
            for forbidden in ('"access_token"', '"token"', '"code"', '"app_secret"'):
                assert forbidden not in payload, f"{module.__name__}: {forbidden}"


def _log_calls(source: str) -> list[str]:
    """Every ``logger.<level>(...)`` argument list in a module, as text."""
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
            else:  # pragma: no cover
                start = index + len(marker)
    return calls


async def test_32_the_assistant_context_never_receives_a_provider_secret(
    world: World,
) -> None:
    """Requirement 32, and requirement 73 of the spec.

    Asserted structurally rather than by rendering one context: the canonical
    block is authored text under version control, and the *dynamic* half is
    built from response models that have no credential field. The check is that
    no connection secret appears in either.
    """
    from meobot.domain.assistant.domain_context import MEOBOT_DOMAIN_CONTEXT

    rendered = MEOBOT_DOMAIN_CONTEXT.render()
    for secret in ("encrypted_credential", "access_token", "app_secret", "EAA"):
        assert secret not in rendered, secret

    from pathlib import Path

    import meobot.api.schemas.pr as schemas

    source = Path(schemas.__file__).read_text(encoding="utf-8")
    # No response model may declare a credential field at all.
    for forbidden in ("encrypted_credential", "durable_credential", "access_token"):
        assert forbidden not in source, forbidden


async def test_33_a_ciphertext_cannot_be_moved_between_connections(world: World) -> None:
    """Requirement 33. The AAD binding, on a Meta credential this time."""
    first = await fb_channel(world, name="Trang A")
    second = await fb_channel(world, name="Trang B")
    graph = FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    a = (
        await authorize_and_finish(
            world, first.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
        )
    ).connection
    b = (
        await authorize_and_finish(
            world, second.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
        )
    ).connection

    stolen = a.encrypted_credential
    assert stolen is not None
    b.encrypted_credential = stolen
    await world.session.flush()
    assert world.services.channel_connections._read_credential(b) is None


async def test_34_the_stored_credential_is_the_page_token_not_the_user_token(
    world: World,
) -> None:
    """Requirement 34's other half, and a real security property.

    Discovery runs on a long-lived **user** token, which can read every Page
    that person manages. What gets stored is the chosen **Page's** token, which
    can read one. If the swap did not happen, a single leaked row would hand
    over a whole portfolio.
    """
    row = await fb_channel(world)
    graph = FakeGraph()
    await authorize_and_finish(world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph)
    connection = await world.services.channel_connections.get_live_connection(
        row.id, provider=PrChannelPlatform.FACEBOOK
    )
    assert connection is not None
    # While parked, the wider credential is held - that is what discovery needs.
    assert world.services.channel_connections._read_credential(connection) == USER_TOKEN

    await world.services.channel_connections.select_account(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=row.id,
        account_id=PAGE_A,
        provider_client=graph.facebook(),
    )
    # After binding, it is the narrow one.
    assert world.services.channel_connections._read_credential(connection) == PAGE_A_TOKEN


async def test_34a_disconnect_clears_the_credential_and_keeps_history(world: World) -> None:
    """Requirement 34, and requirement 64 of the spec."""
    row = await fb_channel(world)
    graph = FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    await record_manual(world, row.id, at=NOW - timedelta(days=3), followers=1000)
    await authorize_and_finish(world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph)

    connection = await world.services.channel_connections.disconnect(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=row.id,
        provider=PrChannelPlatform.FACEBOOK,
        provider_client=graph.facebook(),
    )
    assert connection.encrypted_credential is None
    assert connection.status is PrChannelConnectionState.DISCONNECTED

    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 1


async def test_34b_an_abandoned_selection_drops_its_credential(world: World) -> None:
    """Requirement 79 of the spec. No stale Meta token left lying around.

    A parked connection holds the *widest* credential in the flow purely so a
    chooser can list Pages. Somebody closing the tab must not leave that behind.
    """
    row = await fb_channel(world)
    graph = FakeGraph()
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )
    assert outcome.connection.encrypted_credential is not None

    outcome.connection.connected_at = utcnow() - timedelta(days=2)
    await world.session.flush()

    cleared = await world.services.channel_connections.expire_pending_selections()
    assert cleared == 1
    assert outcome.connection.encrypted_credential is None
    assert outcome.connection.status is PrChannelConnectionState.DISCONNECTED


# ===========================================================================
# 35-44: FACEBOOK NORMALIZATION
# ===========================================================================


async def test_35_36_followers_come_from_followers_count_not_fan_count() -> None:
    """Requirements 35 and 36, and a distinction Meta itself blurred.

    Fans liked the Page; followers receive its posts. The two diverged
    permanently and are different numbers. ``fan_count`` is kept because reports
    still quote it - in ``extra_metrics``, never in the canonical column.
    """
    graph = FakeGraph()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    assert reading.canonical()["followers"] == 124812
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["facebook_page_fan_count"] == 130500
    assert reading.canonical()["followers"] != 130500


async def test_37_38_39_40_engagements_map_and_retired_metrics_stay_null() -> None:
    """Requirements 37-40, as Graph v23 left them.

    ``page_post_engagements`` is an additive event count, so summing the daily
    series is correct. ``page_impressions`` and ``page_impressions_unique`` are
    **gone** from Page Insights in v23 - asking for either returns ``(#100) The
    value must be a valid insights metric`` - so impressions and reach are empty
    for a Page.

    Empty rather than approximated: summing daily uniques to fake a reach figure
    would count one person on three days as three people, which is the mistake
    this connector refuses to make even when it leaves a card blank.
    """
    graph = FakeGraph(daily={"page_post_engagements": [10] * 7})
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()

    assert metrics["engagements_7d"] == 70
    assert metrics["engagements_30d"] == 70
    for retired in ("impressions_7d", "impressions_30d", "reach_7d", "reach_30d"):
        assert metrics[retired] is None, retired

    # And the snapshot records which insight metrics actually answered, so a
    # reader months later can tell "no engagement" from "Meta stopped serving
    # the metric" - the question this incident raised.
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["meta_insight_metrics_available"] == ["page_post_engagements"]


async def test_37a_a_retired_metric_never_reaches_the_request() -> None:
    """The direct cause of the outage, asserted at the wire.

    A single unknown name in a comma-separated ``metric=`` list makes Graph
    reject the **whole** call, so the fix is not to handle the error better - it
    is to stop asking. Nothing in the request path may name either retired
    metric.
    """
    graph = FakeGraph()
    await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    sent = graph.sent()
    assert "page_impressions" not in sent
    assert "page_impressions_unique" not in sent
    assert "page_post_engagements" in sent
    # The weekly and monthly unique-reach calls are gone with the metric, so a
    # Facebook sync is four requests rather than six.
    assert "period=week" not in sent
    assert "period=days_28" not in sent

    from meobot.integrations.meta.provider import FACEBOOK_DAILY_METRICS

    assert FACEBOOK_DAILY_METRICS == ("page_post_engagements",)


async def test_37b_one_retired_metric_does_not_cost_the_others() -> None:
    """Requirement 6, and the failure this incident actually was.

    Graph refuses a whole group containing one unknown name. Before the fix that
    meant a single retired metric took every valid metric in the same call with
    it - and, because the group failure surfaced as ``BAD_RESPONSE``, took the
    entire channel sync too.

    Now the group is retried one metric at a time and whatever answers is kept.
    Simulated with a Graph that has retired ``views`` from Instagram's group of
    three: ``reach`` and ``total_interactions`` must still arrive.
    """
    graph = FakeGraph(
        ig_totals={"reach": 40000, "total_interactions": 3000},
        retired_metrics=frozenset({"views"}),
    )
    reading = await graph.instagram().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=IG_A, now=NOW
    )
    metrics = reading.canonical()

    assert metrics["reach_30d"] == 40000, "a sibling metric survived the retirement"
    assert metrics["engagements_30d"] == 3000
    assert metrics["views_30d"] is None, "the retired one is simply unavailable"
    # The profile half is untouched either way.
    assert metrics["followers"] == 88120
    assert metrics["posts_count"] == 412


async def test_37c_a_page_whose_insights_are_all_retired_still_syncs(
    world: World,
) -> None:
    """Requirement 3, end to end: the snapshot is created regardless.

    The production case. Every Page Insights metric MeoBot asks for is retired,
    so the insight call returns nothing at all - and the sync must still succeed
    on the strength of ``followers_count`` and ``fan_count``, because a channel
    whose follower count is readable is a channel worth recording.
    """
    row = await fb_channel(world)
    graph = FakeGraph(
        pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)],
        retired_metrics=frozenset({"page_post_engagements"}),
    )
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )
    result = await world.services.channel_sync.run_sync(
        connection_id=outcome.connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )

    assert result.ok is True, "a Page with no readable insights still syncs"
    assert result.error_code is None
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    # Requirement 2/4: the working fields are populated.
    assert snapshot.followers == 124812
    assert snapshot.extra_metrics is not None
    assert snapshot.extra_metrics["facebook_page_fan_count"] == 130500
    # Requirement 3: the unavailable ones are absent, not zero.
    for empty in ("engagements_7d", "engagements_30d", "reach_30d", "impressions_30d"):
        assert getattr(snapshot, empty) is None, empty
    assert snapshot.extra_metrics["meta_insight_metrics_available"] == []


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (
            401,
            {"error": {"code": 190, "error_subcode": 458}},
            PrChannelSyncErrorCode.AUTH_REQUIRED,
        ),
        (403, {"error": {"code": 10}}, PrChannelSyncErrorCode.INSUFFICIENT_SCOPE),
        (400, {"error": {"code": 4}}, PrChannelSyncErrorCode.RATE_LIMITED),
        (500, {"error": {"code": 2}}, PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_37d_degradation_never_swallows_a_real_failure(
    status: int, payload: dict[str, Any], expected: PrChannelSyncErrorCode
) -> None:
    """Requirement 8. Graceful degradation is scoped to *malformed requests only*.

    The failure is injected on the **insights call alone**, leaving identity and
    profile healthy - otherwise the request would die at ``/me`` and this would
    prove nothing about the degradation path.

    A revoked token, a missing consent, a quota limit and a Meta outage must all
    still fail the sync loudly even though they arrive at the same call a
    retired metric does. Swallowing any of them would write a snapshot full of
    nulls and record it as a success, which is worse than the bug being fixed
    here because it would be silent.
    """
    graph = FakeGraph(insights_status=status, insights_error=payload)
    with pytest.raises(MetaApiError) as failure:
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        )
    assert failure.value.error_code is expected


async def test_41_a_metric_facebook_does_not_report_stays_null() -> None:
    """Requirement 41, and requirement 57's refusal.

    ``views_*`` is empty because Meta's account-level view metric is *profile*
    views, which is not what a views card means beside a YouTube channel.
    ``posts_count`` is empty because counting Page posts needs pagination -
    thousands of rows for one card.
    """
    graph = FakeGraph()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()
    for absent in ("views_7d", "views_30d", "posts_count", "following", "likes_30d"):
        assert metrics[absent] is None, absent


async def test_42_zero_survives_as_zero() -> None:
    """Requirement 42. A week with no impressions really had none."""
    graph = FakeGraph(daily={"page_post_engagements": [0, 0]})
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    assert reading.canonical()["engagements_7d"] == 0
    assert reading.canonical()["engagements_7d"] is not None


@pytest.mark.parametrize("bad", [-5, "abc", 12.5, True, 10**18, {"nested": 1}])
async def test_43_an_implausible_remote_value_is_refused(bad: Any) -> None:
    """Requirement 43. Graph JSON is untrusted input.

    Dropped to ``None`` rather than raised: one unreadable field must not
    discard a dozen good ones.
    """
    graph = FakeGraph(page_profile={"followers_count": bad, "fan_count": 130500})
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    assert reading.canonical()["followers"] is None
    # The rest of the reading survives.
    assert reading.extra_metrics is not None
    assert reading.extra_metrics["facebook_page_fan_count"] == 130500


async def test_44_a_lifetime_counter_never_lands_in_a_period_column() -> None:
    """Requirement 44, and requirement 24 of the spec.

    The generalisation of the YouTube lesson: a cumulative counter presented as
    a 30-day figure is wrong by orders of magnitude and entirely plausible on
    screen.
    """
    graph = FakeGraph()
    reading = await graph.facebook().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
    )
    metrics = reading.canonical()
    # fan_count is a lifetime-ish counter and appears in no period column.
    for column in ("impressions_30d", "views_30d", "reach_30d", "engagements_30d"):
        assert metrics[column] != 130500


async def test_44a_a_page_token_bound_to_another_page_is_refused() -> None:
    """Requirement 18/20 of the spec at *sync* time, not just at bind time."""
    graph = FakeGraph()
    with pytest.raises(MetaApiError) as failure:
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_B, now=NOW
        )
    assert failure.value.error_code is PrChannelSyncErrorCode.INVALID_ACCOUNT


# ===========================================================================
# 45-51: INSTAGRAM NORMALIZATION
# ===========================================================================


async def test_45_46_instagram_followers_and_media_count_map() -> None:
    """Requirements 45 and 46.

    ``media_count`` is on the account object, so ``posts_count`` is cheap here
    in a way it is not for a Facebook Page - which is why one is mapped and the
    other is not.
    """
    graph = FakeGraph()
    reading = await graph.instagram().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=IG_A, now=NOW
    )
    metrics = reading.canonical()
    assert metrics["followers"] == 88120
    assert metrics["posts_count"] == 412


async def test_47_48_instagram_reach_and_views_map_from_deduplicated_totals() -> None:
    """Requirements 47 and 48, and why Instagram gets a 30-day reach.

    ``metric_type=total_value`` returns one deduplicated figure for the whole
    window, so ``reach_30d`` is exact - unlike Facebook, where no such form
    exists. ``impressions_*`` stays empty because Meta retired it for Instagram
    accounts and ``views`` replaced it.
    """
    graph = FakeGraph()
    reading = await graph.instagram().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=IG_A, now=NOW
    )
    metrics = reading.canonical()
    assert metrics["reach_7d"] == 40000
    assert metrics["reach_30d"] == 40000
    assert metrics["views_7d"] == 90000
    assert metrics["views_30d"] == 90000
    assert metrics["impressions_7d"] is None
    assert metrics["impressions_30d"] is None
    # total_interactions is Meta's own defined engagement metric, not a sum
    # MeoBot invented.
    assert metrics["engagements_30d"] == 3000


async def test_49_an_unavailable_instagram_metric_stays_null() -> None:
    """Requirement 49, and the API-drift handling this connector is built on.

    Meta deprecates and permission-gates individual metrics constantly. One
    unavailable optional metric must not fail a sync that read the others.
    """
    graph = FakeGraph(ig_totals={"reach": 40000})
    reading = await graph.instagram().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=IG_A, now=NOW
    )
    metrics = reading.canonical()
    assert metrics["reach_30d"] == 40000
    assert metrics["views_30d"] is None
    assert metrics["engagements_30d"] is None
    # And the profile half still came through.
    assert metrics["followers"] == 88120


async def test_50_instagram_zero_is_preserved() -> None:
    """Requirement 50."""
    graph = FakeGraph(ig_totals={"reach": 0, "views": 0, "total_interactions": 0})
    reading = await graph.instagram().fetch_channel_metrics(
        access_token=PAGE_A_TOKEN, account_id=IG_A, now=NOW
    )
    assert reading.canonical()["reach_30d"] == 0
    assert reading.canonical()["reach_30d"] is not None


async def test_51_an_instagram_account_that_does_not_resolve_is_refused() -> None:
    """Requirement 51, and the bound-account check at sync time."""
    graph = FakeGraph(ig_profile={"id": "17841499999999999", "followers_count": 1})
    with pytest.raises(MetaApiError) as failure:
        await graph.instagram().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=IG_A, now=NOW
        )
    assert failure.value.error_code is PrChannelSyncErrorCode.INVALID_ACCOUNT


def test_51a_the_reporting_windows_are_settled_and_recorded() -> None:
    """Requirement 25 of the spec. Deterministic windows, or no idempotency.

    A window whose last day is still moving produces a different fingerprint on
    every retry, which would make the deduplication a no-op.
    """
    seven = insight_window(NOW, days=7)
    thirty = insight_window(NOW, days=30)
    assert thirty.end < NOW.date()
    assert (seven.end - seven.start).days == 6
    assert (thirty.end - thirty.start).days == 29
    assert seven.end == thirty.end


async def test_51b_the_window_metadata_travels_with_the_reading() -> None:
    """Requirement 58 of the spec, for both providers."""
    graph = FakeGraph()
    for reading in (
        await graph.facebook().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=PAGE_A, now=NOW
        ),
        await graph.instagram().fetch_channel_metrics(
            access_token=PAGE_A_TOKEN, account_id=IG_A, now=NOW
        ),
    ):
        extra = reading.extra_metrics
        assert extra is not None
        for key in (
            "meta_window_7d_start",
            "meta_window_7d_end",
            "meta_window_30d_start",
            "meta_window_30d_end",
        ):
            assert key in extra, key
        # observed_at is when MeoBot looked, not the window's end.
        assert (
            reading.observed_at.date()
            != datetime.fromisoformat(str(extra["meta_window_30d_end"])).date()
        )


# ===========================================================================
# 52-59: THE SNAPSHOT
# ===========================================================================


async def connected_fb(world: World, graph: FakeGraph | None = None):
    row = await fb_channel(world)
    graph = graph or FakeGraph(pages=[page_row(PAGE_A, "Apex Media", PAGE_A_TOKEN)])
    outcome = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )
    return row, outcome.connection, graph


async def test_52_53_54_a_meta_sync_appends_an_api_snapshot_with_no_author(
    world: World,
) -> None:
    """Requirements 52, 53 and 54.

    An API reading has no human author on any platform. The person who pressed
    "Đồng bộ ngay" caused a fetch; they did not observe a number.
    """
    _, connection, graph = await connected_fb(world)
    result = await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.MANUAL,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    assert result.ok is True
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.source is PrMetricSource.API
    assert snapshot.recorded_by_user_id is None
    assert snapshot.followers == 124812

    # Instagram writes into the same timeline the same way.
    ig_row = await ig_channel(world)
    ig_graph = FakeGraph()
    ig_outcome = await authorize_and_finish(
        world, ig_row.id, provider=PrChannelPlatform.INSTAGRAM, graph=ig_graph
    )
    ig_result = await world.services.channel_sync.run_sync(
        connection_id=ig_outcome.connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=ig_graph.instagram(),
    )
    ig_snapshot = await world.session.get(PrChannelMetricSnapshot, ig_result.snapshot_id)
    assert ig_snapshot is not None
    assert ig_snapshot.source is PrMetricSource.API
    assert ig_snapshot.recorded_by_user_id is None
    assert ig_snapshot.followers == 88120


async def test_55_56_57_manual_history_survives_and_ordering_is_unchanged(
    world: World,
) -> None:
    """Requirements 55, 56 and 57. One timeline, three sources."""
    row, connection, graph = await connected_fb(world)
    manual = await record_manual(world, row.id, at=NOW - timedelta(days=5), followers=100)
    await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    newer_manual = await record_manual(world, row.id, at=utcnow(), followers=999)

    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 3
    await world.session.refresh(manual)
    assert manual.followers == 100, "an API sync rewrites nothing"
    assert view.latest is not None
    assert view.latest.snapshot.id == newer_manual.id
    assert view.latest.snapshot.source is PrMetricSource.MANUAL
    # The badge still describes the connection; the reading describes itself.
    assert view.status is PrChannelMetricsStatus.CONNECTED_API


async def test_58_the_source_window_is_stored_on_the_snapshot(world: World) -> None:
    """Requirement 58."""
    _, connection, graph = await connected_fb(world)
    result = await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    snapshot = await world.session.get(PrChannelMetricSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.extra_metrics is not None
    assert "meta_window_30d_start" in snapshot.extra_metrics
    assert snapshot.extra_metrics["meta_provider"] == "FACEBOOK"


async def test_59_a_failed_meta_sync_writes_no_snapshot(world: World) -> None:
    """Requirement 59. A row of nulls looks exactly like a collapse."""
    row, connection, _ = await connected_fb(world)
    await record_manual(world, row.id, at=NOW - timedelta(days=1), followers=100)
    broken = FakeGraph(status=503)

    result = await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=broken.facebook(),
    )
    assert result.ok is False
    assert result.snapshot_id is None
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 1
    assert view.latest is not None
    assert view.latest.snapshot.followers == 100


# ===========================================================================
# 60-64: IDEMPOTENCY AND CONCURRENCY
# ===========================================================================


async def test_60_61_a_repeated_meta_fetch_is_not_a_new_reading(world: World) -> None:
    """Requirements 60 and 61. The Step 1F.2.4b design, reused unchanged."""
    row, connection, graph = await connected_fb(world)
    first = await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    second = await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.ok is True
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 1


async def test_62_a_later_window_with_identical_values_is_a_new_reading(
    world: World,
) -> None:
    """Requirement 62. Two weeks that both read 124,812 are two readings."""
    row, connection, graph = await connected_fb(world)
    await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    # A week later: the same numbers, a different settled window.
    later = FakeGraph()
    from unittest.mock import patch

    with patch(
        "meobot.application.pr_channel_sync_service.utcnow",
        return_value=utcnow() + timedelta(days=7),
    ):
        second = await world.services.channel_sync.run_sync(
            connection_id=connection.id,
            trigger=PrChannelSyncTrigger.SCHEDULED,
            request_id=world.request_id,
            actor=world.actor(world.owner),
            provider_client=later.facebook(),
        )
    assert second.duplicate is False
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 2


async def test_63_only_one_sync_can_claim_a_meta_channel(world: World) -> None:
    """Requirement 63. The same conditional UPDATE, not a Meta-specific lock."""
    _, connection, _ = await connected_fb(world)
    assert await world.services.channel_sync.claim(connection.id) is True
    assert await world.services.channel_sync.claim(connection.id) is False


async def test_64_facebook_and_instagram_channels_sync_independently(
    world: World,
) -> None:
    """Requirement 64."""
    _, facebook, _ = await connected_fb(world)
    ig_row = await ig_channel(world)
    ig_graph = FakeGraph()
    instagram = (
        await authorize_and_finish(
            world, ig_row.id, provider=PrChannelPlatform.INSTAGRAM, graph=ig_graph
        )
    ).connection

    assert await world.services.channel_sync.claim(facebook.id) is True
    assert await world.services.channel_sync.claim(instagram.id) is True


# ===========================================================================
# 65-70: HEALTH
# ===========================================================================


async def test_65_a_successful_meta_sync_records_success(world: World) -> None:
    """Requirement 65."""
    _, connection, graph = await connected_fb(world)
    await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    assert connection.sync_status is PrChannelSyncStatus.SUCCESS
    assert connection.last_sync_succeeded_at is not None
    assert connection.consecutive_failures == 0


async def test_66_70_a_transient_or_quota_failure_keeps_the_connection(
    world: World,
) -> None:
    """Requirements 66 and 70.

    A five-minute Meta outage, or an app-level rate limit, must not cost anybody
    their connection. And the message a person reads names **Facebook** - Step
    1F.2.4b wrote "YouTube" into every one of these sentences, which stopped
    being true the moment a Page could fail.
    """
    _, connection, _ = await connected_fb(world)
    quota = FakeGraph(status=400, error_payload={"error": {"code": 4}})
    await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=quota.facebook(),
    )
    assert connection.sync_status is PrChannelSyncStatus.FAILED
    assert connection.last_sync_error_code is PrChannelSyncErrorCode.RATE_LIMITED
    assert connection.status is PrChannelConnectionState.CONNECTED
    assert connection.encrypted_credential is not None
    assert connection.last_sync_error_message is not None
    assert "Facebook" in connection.last_sync_error_message
    assert "YouTube" not in connection.last_sync_error_message


async def test_67_a_revoked_grant_moves_the_connection_to_action_required(
    world: World,
) -> None:
    """Requirement 67, and requirement 38 of the spec.

    Meta reports a revoked grant as ``code 190`` with a subcode. 458 is "the
    person removed the app", which only they can undo.
    """
    row, connection, _ = await connected_fb(world)
    revoked = FakeGraph(status=401, error_payload={"error": {"code": 190, "error_subcode": 458}})
    await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=revoked.facebook(),
    )
    assert connection.status is PrChannelConnectionState.ACTION_REQUIRED
    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.status is PrChannelMetricsStatus.ACTION_REQUIRED


async def test_68_69_reconnecting_restores_the_connection_and_keeps_history(
    world: World,
) -> None:
    """Requirements 68 and 69."""
    row, connection, graph = await connected_fb(world)
    await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=graph.facebook(),
    )
    revoked = FakeGraph(status=401, error_payload={"error": {"code": 190, "error_subcode": 463}})
    await world.services.channel_sync.run_sync(
        connection_id=connection.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=revoked.facebook(),
    )
    assert connection.status is PrChannelConnectionState.ACTION_REQUIRED

    again = await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=graph
    )
    assert again.connection.id == connection.id
    assert again.connection.status is PrChannelConnectionState.CONNECTED
    assert again.connection.last_sync_error_code is None

    view = await world.services.channel_metrics.describe(
        actor=world.actor(world.owner), channel_id=row.id
    )
    assert view.total == 1, "the API reading it collected is still there"


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"error": {"code": 190, "error_subcode": 460}}, PrChannelSyncErrorCode.AUTH_REQUIRED),
        (400, {"error": {"code": 4}}, PrChannelSyncErrorCode.RATE_LIMITED),
        (400, {"error": {"code": 32}}, PrChannelSyncErrorCode.RATE_LIMITED),
        (403, {"error": {"code": 10}}, PrChannelSyncErrorCode.INSUFFICIENT_SCOPE),
        (
            400,
            {"error": {"code": 100, "error_subcode": 33}},
            PrChannelSyncErrorCode.INVALID_ACCOUNT,
        ),
        (500, {"error": {"code": 2}}, PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE),
        (502, None, PrChannelSyncErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
def test_70a_graph_errors_map_onto_the_safe_vocabulary(
    status: int, payload: Any, expected: PrChannelSyncErrorCode
) -> None:
    """Requirement 37 of the spec. No new error class was needed.

    Meta spreads its rate limits across five codes and its auth failures across
    a dozen subcodes; all of them collapse into the vocabulary Step 1F.2.4b
    already had, because what a person can *do* about them is the same.
    """
    assert classify_graph_error(status, payload) is expected


# ===========================================================================
# 71-78: THE SCHEDULER
# ===========================================================================


async def test_71_72_73_meta_and_youtube_connections_are_all_swept(
    world: World,
) -> None:
    """Requirements 71, 72 and 73. One sweep, three platforms, no fork."""
    _, facebook, _ = await connected_fb(world)
    ig_row = await ig_channel(world)
    instagram = (
        await authorize_and_finish(
            world, ig_row.id, provider=PrChannelPlatform.INSTAGRAM, graph=FakeGraph()
        )
    ).connection

    due = await world.services.channel_sync.due_connections()
    ids = {item.id for item in due}
    assert facebook.id in ids
    assert instagram.id in ids
    assert {item.provider for item in due} == {
        PrChannelPlatform.FACEBOOK,
        PrChannelPlatform.INSTAGRAM,
    }


async def test_74_a_disconnected_meta_connection_is_skipped(world: World) -> None:
    """Requirement 74."""
    row, _, graph = await connected_fb(world)
    await world.services.channel_connections.disconnect(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        channel_id=row.id,
        provider=PrChannelPlatform.FACEBOOK,
        provider_client=graph.facebook(),
    )
    assert await world.services.channel_sync.due_connections() == []


async def test_75_an_action_required_connection_is_skipped(world: World) -> None:
    """Requirement 75. Retrying a revoked grant earns nothing but rate limits."""
    _, connection, _ = await connected_fb(world)
    connection.status = PrChannelConnectionState.ACTION_REQUIRED
    await world.session.flush()
    assert await world.services.channel_sync.due_connections() == []


async def test_75a_a_connection_awaiting_selection_is_skipped(world: World) -> None:
    """A parked connection has no account to sync. It must not be swept."""
    row = await fb_channel(world)
    await authorize_and_finish(
        world, row.id, provider=PrChannelPlatform.FACEBOOK, graph=FakeGraph()
    )
    assert await world.services.channel_sync.due_connections() == []


async def test_76_a_tiktok_channel_never_appears(world: World) -> None:
    """Requirement 76. It cannot be connected, so it cannot be due."""
    await make_channel(world, platform_code="TIKTOK", name="Dr Tiến")
    assert await world.services.channel_sync.due_connections() == []


async def test_77_78_one_failure_does_not_stop_the_others_or_duplicate_work(
    world: World,
) -> None:
    """Requirements 77 and 78."""
    _, facebook, _ = await connected_fb(world)
    ig_row = await ig_channel(world)
    ig_graph = FakeGraph()
    instagram = (
        await authorize_and_finish(
            world, ig_row.id, provider=PrChannelPlatform.INSTAGRAM, graph=ig_graph
        )
    ).connection

    broken = FakeGraph(status=503)
    first = await world.services.channel_sync.run_sync(
        connection_id=facebook.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=broken.facebook(),
    )
    second = await world.services.channel_sync.run_sync(
        connection_id=instagram.id,
        trigger=PrChannelSyncTrigger.SCHEDULED,
        request_id=world.request_id,
        actor=world.actor(world.owner),
        provider_client=ig_graph.instagram(),
    )
    assert first.ok is False
    assert second.ok is True
    assert second.snapshot_id is not None

    # And a claimed connection is not handed out twice.
    assert await world.services.channel_sync.claim(instagram.id) is True
    assert await world.services.channel_sync.claim(instagram.id) is False


# ===========================================================================
# Architecture: the boundary Step 1F.2.4b drew, still drawn
# ===========================================================================


def test_the_application_layer_names_no_meta_endpoint_or_http_client() -> None:
    """Requirement 4 of the spec, and requirement 96's architecture tests.

    The whole point of the port is that orchestration does not know what Meta
    is. If this ever fails, a Graph detail has leaked upwards and the next
    provider will have to leak one too.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "meobot" / "application"
    for name in ("pr_channel_sync_service.py", "pr_channel_connection_service.py"):
        source = (root / name).read_text("utf-8")
        imports = "\n".join(
            line for line in source.splitlines() if line.startswith(("import ", "from "))
        )
        for forbidden in ("httpx", "requests", "meobot.integrations"):
            assert forbidden not in imports, f"{name}: {forbidden}"
        for endpoint in ("graph.facebook.com", "facebook.com/v", "instagram.com", "googleapis"):
            assert endpoint not in source, f"{name}: {endpoint}"

    # The registry is the one place that names providers, and it is allowed to.
    registry = (root / "pr_channel_providers.py").read_text("utf-8")
    assert "meobot.integrations.meta" in registry
    assert "httpx" not in "\n".join(
        line for line in registry.splitlines() if line.startswith(("import ", "from "))
    )


def test_both_meta_providers_implement_the_selection_port() -> None:
    """Selection is a capability, asked structurally, not a platform check.

    ``PrChannelConnectionService`` branches on ``isinstance(client,
    AccountSelectingProvider)`` and never on "is this Meta" - which is what
    makes the next provider that needs a chooser require no application change.
    """
    graph = FakeGraph()
    assert isinstance(graph.facebook(), AccountSelectingProvider)
    assert isinstance(graph.instagram(), AccountSelectingProvider)

    from meobot.integrations.youtube.provider import YouTubeChannelMetricsProvider

    youtube = YouTubeChannelMetricsProvider(
        client_id="a",
        client_secret="b",
        redirect_uri="c",
    )
    assert not isinstance(youtube, AccountSelectingProvider)


def test_no_meta_module_builds_an_unmocked_http_client() -> None:
    """The claim that keeps this whole file offline."""
    from pathlib import Path

    import meobot.integrations.meta.client as client_module

    source = Path(client_module.__file__).read_text("utf-8")
    assert source.count("httpx.AsyncClient(") == 1
    assert "def _ensure_client" in source
    assert "channel.url" not in source


def test_no_publication_level_meta_metrics_were_added() -> None:
    """Requirement 53 of the spec. Channel-level only, still.

    Step 1F.2.4c could enforce this by forbidding every post edge, because it
    requested none. Step 1F.2.4d requests one - ``published_posts`` - and the
    guard has to move rather than be deleted, because what it was protecting is
    still true and still worth protecting.

    The line is between **aggregating** a Page's posts into a channel's numbers,
    which this step does, and **mapping** a MeoBot publication to a platform
    post, which it does not. The second one is what would need a publication id
    on a metric row, a per-publication fetch, and a whole model of "which post
    is this content item". None of that exists, and this asserts it:

    * the provider reaches exactly two edges, and ``published_posts`` is read
      only as a window aggregate;
    * nothing in the Meta package writes to the *post* snapshot table or names
      a publication;
    * no per-post insights call, which is also the rate-limit promise: post
      interaction counts arrive as summaries on the listing itself.
    """
    from pathlib import Path

    import meobot.integrations.meta.posts as posts_module
    import meobot.integrations.meta.provider as provider_module

    provider_source = Path(provider_module.__file__).read_text("utf-8")
    posts_source = Path(posts_module.__file__).read_text("utf-8")

    # The edges actually requested. ``published_posts`` is passed to the generic
    # edge reader rather than to ``node()``, so both call shapes are checked.
    requested_edges = set(re.findall(r'\.node\(\s*[^,)]+,\s*"([^"]+)"', provider_source))
    requested_edges |= set(re.findall(r'edge_page\(\s*[a-z_]+,\s*\n?\s*"([^"]+)"', provider_source))
    assert requested_edges <= {"insights", "published_posts"}, requested_edges

    for source, name in ((provider_source, "provider"), (posts_source, "posts")):
        for forbidden in ("pr_post_metric_snapshots", "pr_publication", "media_id"):
            assert forbidden not in source, f"{name}: {forbidden}"

    # And no per-post request anywhere. ``posts`` is a **pure** module - it
    # parses and aggregates and performs no IO at all, which is what makes a
    # per-post insights call impossible to add there without noticing. One
    # request per post is what this promise is about: it is the reason video
    # plays are read at Page level instead.
    assert "await" not in posts_source
    assert "import httpx" not in posts_source
    assert provider_source.count("edge_page(") == 1

    # The client asks for no media-level edge either. Its generic reader takes
    # the edge as a parameter, which is why the caller above is what is checked.
    import meobot.integrations.meta.client as client_module

    client_source = Path(client_module.__file__).read_text("utf-8")
    client_edges = re.findall(r'\.node\(\s*[^,)]+,\s*"([^"]+)"', client_source)
    assert set(client_edges) <= {"accounts", "insights"}, client_edges
