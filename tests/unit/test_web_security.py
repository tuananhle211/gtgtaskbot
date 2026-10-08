"""Step 1E.1 - the HTTP surface, the synthetic OWNER, and session security.

Numbered to the hardening brief. Four groups:

* **1-10 route security** - what is served, what is refused, and what a caller
  cannot inject;
* **11-22 magic link** - single use, replay, expiry, and where the URL comes from;
* **23-34 session** - hashing, revocation, live revalidation, cookie flags;
* **35-42 web security** - CORS, headers, error bodies, redirects.

Then a structural sweep that greps the tree, because a rule enforced only by
review stops being enforced.

The one thing not testable here
-------------------------------

Test 17 (two requests racing one login token) needs real row locks. SQLite has
none, so it lives in ``tests/integration/test_web_auth_concurrency.py`` and is
named as absent here rather than faked with a lock this database does not have.
"""

from __future__ import annotations

import io
import logging
import re
import tokenize
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_system_actor, get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_service import CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.application.web_auth_service import SESSION_COOKIE, WebAuthService, hash_token
from meobot.core.config import Settings
from meobot.core.errors import ConfigurationError
from meobot.core.logging import SENSITIVE_KEYS, configure_logging
from meobot.core.time import utcnow
from meobot.db.models.pr import PrBrand, PrChannel, PrPlatform
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSession, WebSessionKind
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.models import PrChannelCategory, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability

ROOT = Path(".")
SRC = ROOT / "src" / "meobot"
FRONTEND = ROOT / "frontend"

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The only paths a browser is meant to reach. Everything else served by this app
#: is either a health probe or an internal router that must not be mounted.
BROWSER_PREFIXES = ("/api/pr", "/api/auth", "/auth/")


def _settings(**overrides: object) -> Settings:
    """Web-configured settings. ``web_cookie_secure`` off for the test transport.

    ``TestClient`` speaks ``http://``, and a cookie jar imitating a browser drops
    a ``Secure`` cookie sent over plain HTTP. Tests 31-34 pin the production
    behaviour separately, so turning it off here hides nothing.
    """
    base: dict[str, object] = {
        "web_base_url": "https://pr.example.com",
        "web_cookie_secure": False,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


# --- Route inventory --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RouteFact:
    """One route, and how it decides who is calling."""

    method: str
    path: str
    module: str
    synthetic_owner: bool
    web_session: bool

    @property
    def is_write(self) -> bool:
        return self.method in WRITE_METHODS

    @property
    def browser_facing(self) -> bool:
        return self.path.startswith(BROWSER_PREFIXES)


def _dependency_names(dependant: object, seen: set[int] | None = None) -> set[str]:
    """Every callable in a route's dependency tree, by name.

    Walks the tree rather than reading the signature text. An earlier version of
    this inventory matched on the string ``"ActorDep"`` and reported every PR
    route as using the synthetic owner - because ``CurrentActorDep`` contains it
    as a substring. That false positive would have hidden the real finding.
    """
    seen = seen if seen is not None else set()
    found: set[str] = set()
    call = getattr(dependant, "call", None)
    if call is not None:
        found.add(getattr(call, "__name__", str(call)))
    for sub in getattr(dependant, "dependencies", []):
        if id(sub) in seen:
            continue
        seen.add(id(sub))
        found |= _dependency_names(sub, seen)
    return found


def route_inventory(*, internal_enabled: bool) -> list[RouteFact]:
    """Every route the app serves, with its resolved auth mechanism."""
    app = create_app(_settings(api_internal_routers_enabled=internal_enabled))
    facts: list[RouteFact] = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        dependant = getattr(route, "dependant", None)
        names = _dependency_names(dependant) if dependant is not None else set()
        endpoint = getattr(route, "endpoint", None)
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            facts.append(
                RouteFact(
                    method=method,
                    path=getattr(route, "path", "?"),
                    module=getattr(endpoint, "__module__", "?"),
                    synthetic_owner="get_current_system_actor" in names,
                    web_session="get_current_web_actor" in names,
                )
            )
    return facts


# --- The world --------------------------------------------------------------


@dataclass(slots=True)
class World:
    session: AsyncSession
    client: TestClient
    settings: Settings
    auth: WebAuthService
    lead: User
    other: User
    brand_id: uuid.UUID
    channel_id: uuid.UUID

    def act_as(self, user: User) -> None:
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
            user_id=user.id, full_name=user.full_name, role=user.role, active=user.active
        )

    def sign_in(self, user: User) -> str:
        """Drop the override and use a real cookie. Returns the session token."""
        self.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
        return ""


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    lead = User(full_name="Le Trưởng Nhóm", role=Role.TEAM_LEAD)
    other = User(full_name="Nguyen Nhân Viên", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-S", name="Security Brand")
    platform = PrPlatform(code="YOUTUBE", name="YouTube")
    session.add_all([lead, other, brand, platform])
    await session.flush()
    channel = PrChannel(
        code="CH-S",
        name="Kênh bảo mật",
        category=PrChannelCategory.SCALE,
        platform_id=platform.id,
        brand_id=brand.id,
    )
    session.add(channel)
    await session.flush()

    settings = _settings()
    await PrCapabilityService(session, AuditService(session)).grant(
        actor=Actor(user_id=lead.id, full_name=lead.full_name, role=Role.OWNER),
        request_id=uuid.uuid4(),
        user_id=lead.id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
    )

    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield World(
            session=session,
            client=client,
            settings=settings,
            auth=WebAuthService(session, settings),
            lead=lead,
            other=other,
            brand_id=brand.id,
            channel_id=channel.id,
        )
    app.dependency_overrides.clear()


async def _token_for(world: World, user: User) -> str:
    issued = await world.auth.issue_login_link(user_id=user.id)
    return issued.url.split("t=")[1]


async def _cookie_login(world: World, user: User) -> None:
    """Sign in through the real redeem route, so the cookie is genuine."""
    world.client.cookies.clear()
    world.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
    token = await _token_for(world, user)
    response = world.client.get(f"/auth/login?t={token}", follow_redirects=False)
    assert response.status_code == 303, response.text


# --- 1-10: route security ---------------------------------------------------


def test_01_the_inventory_finds_the_synthetic_owner_where_it_lives() -> None:
    """The audit tool itself works - otherwise tests 2-3 prove nothing.

    With the internal routers on, the inventory must *find* synthetic-OWNER
    routes. A detector that reported zero because it was broken would make the
    rest of this file a green light for an unaudited surface.
    """
    facts = route_inventory(internal_enabled=True)
    synthetic = [fact for fact in facts if fact.synthetic_owner]
    assert synthetic, "The detector found no synthetic-OWNER routes with api/v1 mounted."
    # Every one of them is a write, and every one is under /api/v1.
    assert all(fact.is_write for fact in synthetic)
    assert all(fact.path.startswith("/api/v1/") for fact in synthetic)
    # The specific one that made this step necessary.
    assert any(fact.path == "/api/v1/users/{user_id}/role" for fact in synthetic)


def test_02_no_browser_facing_route_uses_the_synthetic_owner() -> None:
    """The central assertion of Step 1E.1.

    Checked with the internal routers **on**, which is the harder case: even a
    deployment that deliberately serves them must not have a synthetic-OWNER
    route under a browser-facing prefix.
    """
    for fact in route_inventory(internal_enabled=True):
        if fact.browser_facing:
            assert not fact.synthetic_owner, f"{fact.method} {fact.path}"


def test_03_the_default_surface_serves_no_synthetic_owner_route_at_all() -> None:
    """With the default configuration there is no such route anywhere.

    Not merely "not browser-facing" - absent. 34 routes, and the 24
    unauthenticated OWNER writes are not among them.
    """
    facts = route_inventory(internal_enabled=False)
    assert not [fact for fact in facts if fact.synthetic_owner]
    assert not [fact for fact in facts if fact.path.startswith("/api/v1")]
    # Every write that is served requires a real session - except signing out,
    # signing in with a password (0045), which is how a session is made, and
    # "Quên mật khẩu?" (0046), which is for somebody who cannot sign in and
    # only ever sends a temporary password to that account's own Telegram.
    sessionless = {"/api/auth/logout", "/api/auth/password-login", "/api/auth/password-reset"}
    for fact in facts:
        if fact.is_write and fact.path not in sessionless:
            assert fact.web_session, f"{fact.method} {fact.path} writes without a session"


def test_03b_the_synthetic_actor_dependency_itself_fails_closed() -> None:
    """Defence in depth: the function refuses when the routers are off.

    So a router mistakenly added to the browser-facing list raises rather than
    quietly minting an OWNER. Numbered as a rider on 3 because it guards the same
    property from the other side.
    """
    with pytest.raises(ConfigurationError):
        get_current_system_actor(_settings(api_internal_routers_enabled=False))
    actor = get_current_system_actor(_settings(api_internal_routers_enabled=True))
    assert actor.role is Role.OWNER


@pytest.mark.asyncio
async def test_04_an_unauthenticated_pr_request_is_refused(world: World) -> None:
    """401, on a read and on a write, with no detail about why."""
    world.client.cookies.clear()
    assert world.client.get("/api/pr/dashboard").status_code == 401
    write = world.client.post(
        "/api/pr/contents",
        json={
            "title": "x",
            "brand_id": str(world.brand_id),
            "owner_user_id": str(world.lead.id),
        },
    )
    assert write.status_code == 401
    # Nothing was created.
    world.act_as(world.lead)
    assert world.client.get("/api/pr/contents").json() == []


@pytest.mark.asyncio
async def test_05_a_deactivated_user_is_refused_on_the_next_request(world: World) -> None:
    """No waiting for the cookie to expire."""
    await _cookie_login(world, world.lead)
    assert world.client.get("/api/pr/dashboard").status_code == 200

    world.lead.active = False
    await world.session.flush()
    assert world.client.get("/api/pr/dashboard").status_code == 401


@pytest.mark.asyncio
async def test_06_a_role_demotion_takes_effect_on_the_next_request(world: World) -> None:
    """The actor is rebuilt from the row, not read out of the session.

    Somebody demoted at 09:00 is an EMPLOYEE at 09:00.
    """
    await _cookie_login(world, world.lead)
    assert world.client.get("/api/auth/session").json()["role"] == Role.TEAM_LEAD.value

    world.lead.role = Role.EMPLOYEE
    await world.session.flush()
    assert world.client.get("/api/auth/session").json()["role"] == Role.EMPLOYEE.value


@pytest.mark.asyncio
async def test_07_a_capability_revoke_takes_effect_on_the_next_request(world: World) -> None:
    """Grants are re-read every request, **and a revoke is immediate.**

    Two facts, and the second one is what Step 1F.2.7 changed.

    The session layer caches nothing: capabilities are recomputed from
    ``pr_user_capabilities`` on every request, so a grant that has ended is gone
    on the next click.

    The residual risk this test used to record has been **fixed**. Until Step
    1F.2.7, revoking meant dating ``effective_to`` to today, and Step 1C.1
    defines ``effective_to`` as the *last day in force* - so "revoke Head Review
    from this person" left them able to approve until midnight. Consistent
    interval arithmetic, and not what an operator revoking access from somebody
    who has just left expects.

    ``revoked_at`` is now a **timestamp**, checked before and outside the date
    interval, so the right is gone on the very next request. ``effective_to`` is
    still dated alongside it, and the interval semantics of an *expiry* are
    untouched - which is asserted in
    ``tests/unit/test_pr_scoped_approval_grants.py``.
    """
    await _cookie_login(world, world.lead)
    body = world.client.get("/api/auth/session").json()
    assert PrCapability.PR_TEAM_LEAD_REVIEW.value in body["capabilities"]

    capabilities = PrCapabilityService(world.session, AuditService(world.session))
    admin = Actor(user_id=world.lead.id, full_name="admin", role=Role.OWNER)

    await capabilities.revoke(
        actor=admin,
        request_id=uuid.uuid4(),
        user_id=world.lead.id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
    )
    await world.session.flush()

    after = world.client.get("/api/auth/session").json()["capabilities"]
    assert PrCapability.PR_TEAM_LEAD_REVIEW.value not in after
    # And the queue that depended on it is empty rather than stale.
    assert world.client.get("/api/pr/reviews/pending").json() == []

    # The row survives, stamped rather than deleted: an approval taken while the
    # grant was open is still explicable.
    grant = (
        (
            await world.session.execute(
                select(PrUserCapability).where(
                    PrUserCapability.user_id == world.lead.id,
                    PrUserCapability.capability == PrCapability.PR_TEAM_LEAD_REVIEW,
                )
            )
        )
        .scalars()
        .one()
    )
    assert grant.revoked_at is not None
    assert grant.revoked_by_user_id == world.lead.id


@pytest.mark.asyncio
async def test_08_a_caller_cannot_inject_a_reviewer_a_stage_or_a_code(world: World) -> None:
    """Three fields nobody may supply, each a 422 rather than a silent ignore.

    A silently dropped ``reviewer_user_id`` would look to the caller like it
    worked, which is worse than a refusal.
    """
    world.act_as(world.lead)
    services = build_pr_services(world.session, world.settings)
    snapshot = await services.content.create_content(
        actor=Actor(user_id=world.lead.id, full_name="l", role=Role.TEAM_LEAD),
        request_id=uuid.uuid4(),
        command=CreateContentCommand(
            title="Bài kiểm tra",
            brand_id=world.brand_id,
            owner_user_id=world.lead.id,
            script_text="nội dung",
        ),
    )
    content_id = snapshot.content.id

    injections = (
        (
            "/api/pr/contents",
            {
                "title": "x",
                "brand_id": str(world.brand_id),
                "owner_user_id": str(world.lead.id),
                "code": "CNT-2026-999999",
            },
        ),
        (
            "/api/pr/contents",
            {
                "title": "x",
                "brand_id": str(world.brand_id),
                "owner_user_id": str(world.lead.id),
                "workflow_stage": PrWorkflowStage.APPROVED.value,
            },
        ),
        (
            f"/api/pr/contents/{content_id}/reviews",
            {
                "decision": "APPROVED",
                "version_reviewed": 1,
                "reviewer_user_id": str(world.other.id),
            },
        ),
        (
            f"/api/pr/contents/{content_id}/versions",
            {"expected_version": 1, "script_text": "y", "created_by_user_id": str(world.other.id)},
        ),
    )
    for path, payload in injections:
        response = world.client.post(path, json=payload)
        assert response.status_code == 422, (path, response.text)

    # Nothing landed: the item is still at IDEA on version 1.
    detail = world.client.get(f"/api/pr/contents/{content_id}").json()
    assert detail["content"]["workflow_stage"] == PrWorkflowStage.IDEA.value
    assert detail["current_version"]["version_no"] == 1


@pytest.mark.asyncio
async def test_09_a_caller_cannot_spoof_a_role_or_a_capability(world: World) -> None:
    """Body fields naming authority are refused, and would change nothing anyway.

    Authority comes from the ``users`` row the session points at. The 422 is the
    schema being strict; the real control is that no service reads these.
    """
    world.act_as(world.other)
    for payload in (
        {
            "user_id": str(world.other.id),
            "capability": PrCapability.PR_HEAD_REVIEW.value,
            "role": "OWNER",
        },
        {
            "user_id": str(world.other.id),
            "capability": PrCapability.PR_HEAD_REVIEW.value,
            "actor_role": "OWNER",
        },
    ):
        assert world.client.post("/api/pr/capabilities/grant", json=payload).status_code == 422

    # Without the spoof field it is a clean 403 from the service, not a 422.
    clean = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.other.id),
            "capability": PrCapability.PR_HEAD_REVIEW.value,
            # Well-formed on purpose: a 422 would prove the body was wrong, not
            # that the caller was refused.
            "scope": {"content_type_scope": "ALL", "channel_scope": "ALL"},
        },
    )
    assert clean.status_code == 403


@pytest.mark.asyncio
async def test_10_a_forbidden_action_is_403_and_a_blocked_one_is_409(world: World) -> None:
    """The two refusals a UI must tell apart.

    403 means "not you" and retrying never helps; 409 means the record moved and
    reloading is the right next step.
    """
    world.act_as(world.other)
    forbidden = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.other.id),
            "capability": PrCapability.PR_HEAD_REVIEW.value,
            # Well-formed on purpose: a 422 would prove the body was wrong, not
            # that the caller was refused.
            "scope": {"content_type_scope": "ALL", "channel_scope": "ALL"},
        },
    )
    assert forbidden.status_code == 403

    world.act_as(world.lead)
    services = build_pr_services(world.session, world.settings)
    snapshot = await services.content.create_content(
        actor=Actor(user_id=world.lead.id, full_name="l", role=Role.TEAM_LEAD),
        request_id=uuid.uuid4(),
        command=CreateContentCommand(
            title="Bài", brand_id=world.brand_id, owner_user_id=world.lead.id
        ),
    )
    blocked = world.client.post(
        f"/api/pr/contents/{snapshot.content.id}/transition",
        json={"target_stage": PrWorkflowStage.APPROVED.value},
    )
    assert blocked.status_code == 409


# --- 11-22: magic link ------------------------------------------------------


@pytest.mark.asyncio
async def test_11_and_18_only_the_hash_is_stored(world: World) -> None:
    """The raw token is absent from every column of every row."""
    token = await _token_for(world, world.lead)
    rows = (await world.session.execute(select(WebSession))).scalars().all()
    assert rows
    for row in rows:
        assert row.token_hash != token
        assert re.fullmatch(r"[0-9a-f]{64}", row.token_hash)
        for value in (row.user_agent, row.created_ip):
            assert token not in (value or "")
    assert hash_token(token) in {row.token_hash for row in rows}


@pytest.mark.asyncio
async def test_12_a_valid_token_creates_a_session(world: World) -> None:
    await _cookie_login(world, world.lead)
    assert world.client.get("/api/auth/session").json()["user_id"] == str(world.lead.id)
    sessions = (
        (
            await world.session.execute(
                select(WebSession).where(WebSession.kind == WebSessionKind.SESSION)
            )
        )
        .scalars()
        .all()
    )
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_13_replay_is_rejected(world: World) -> None:
    """The second use of a link fails, and mints nothing."""
    token = await _token_for(world, world.lead)
    first = world.client.get(f"/auth/login?t={token}", follow_redirects=False)
    assert first.headers["location"] == "/pr"

    second = world.client.get(f"/auth/login?t={token}", follow_redirects=False)
    assert second.headers["location"] == "/auth/failed"

    sessions = (
        (
            await world.session.execute(
                select(WebSession).where(WebSession.kind == WebSessionKind.SESSION)
            )
        )
        .scalars()
        .all()
    )
    assert len(sessions) == 1, "replay minted a second session"


@pytest.mark.asyncio
async def test_14_an_expired_token_is_rejected(world: World) -> None:
    """Expiry is read from the stored row, not from anything the client sends.

    Inserted already-expired rather than backdated by an update, because
    ``ck_web_sessions_expiry_after_creation`` refuses to move an expiry behind its
    creation - which is the constraint doing its job.
    """
    stale = "an-expired-login-token"
    past = utcnow() - timedelta(days=1)
    world.session.add(
        WebSession(
            user_id=world.lead.id,
            kind=WebSessionKind.LOGIN_TOKEN,
            token_hash=hash_token(stale),
            created_at=past,
            expires_at=past + timedelta(minutes=10),
        )
    )
    await world.session.flush()
    response = world.client.get(f"/auth/login?t={stale}", follow_redirects=False)
    assert response.headers["location"] == "/auth/failed"


@pytest.mark.asyncio
async def test_15_an_unknown_token_is_rejected_and_says_nothing(world: World) -> None:
    """Unknown, expired and spent all land on the same page.

    The difference is only useful to somebody probing.
    """
    response = world.client.get("/auth/login?t=never-existed", follow_redirects=False)
    assert response.headers["location"] == "/auth/failed"
    assert SESSION_COOKIE not in response.cookies
    assert "expired" not in response.text.lower()


@pytest.mark.asyncio
async def test_16_redemption_marks_the_token_used_in_the_same_transaction(
    world: World,
) -> None:
    """``redeemed_at`` is stamped, and it is what makes replay fail.

    Test 17 - two requests racing one token - needs real row locks and lives in
    ``tests/integration/test_web_auth_concurrency.py``. SQLite has no
    ``SELECT … FOR UPDATE``, so proving it here would prove the wrong thing.
    """
    token = await _token_for(world, world.lead)
    world.client.get(f"/auth/login?t={token}", follow_redirects=False)
    row = (
        await world.session.execute(
            select(WebSession).where(WebSession.token_hash == hash_token(token))
        )
    ).scalar_one()
    assert row.redeemed_at is not None
    assert row.kind is WebSessionKind.LOGIN_TOKEN
    # And the service takes the row FOR UPDATE before checking it.
    source = (SRC / "application" / "web_auth_service.py").read_text(encoding="utf-8")
    assert "for_update=True" in source
    assert "with_for_update()" in source


@pytest.mark.asyncio
async def test_19_the_raw_token_never_reaches_a_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue, redeem and use - then read every line that was emitted."""
    configure_logging(service="test", level="DEBUG", log_format="console")
    with caplog.at_level(logging.DEBUG):
        token = await _token_for(world, world.lead)
        world.client.get(f"/auth/login?t={token}", follow_redirects=False)
        world.client.get("/api/pr/dashboard")

    emitted = "\n".join(
        [record.getMessage() for record in caplog.records]
        + [str(value) for record in caplog.records for value in record.__dict__.values()]
    )
    assert token not in emitted
    assert hash_token(token) not in emitted
    # The access log records the path, and a path has no query string - which is
    # where the token lives. Pinned, because switching to str(request.url) would
    # start logging credentials.
    middleware = (SRC / "api" / "middleware.py").read_text(encoding="utf-8")
    assert "request.url.path" in middleware
    assert "str(request.url)" not in middleware
    assert "url.query" not in middleware


def test_20_and_21_the_login_url_comes_from_config_not_the_host_header() -> None:
    """The base URL is configuration. A Host header cannot move it.

    An attacker who can set ``Host: evil.test`` on a request that triggers link
    generation would otherwise receive a link pointing at their own server -
    which the victim would then open, handing over the token.
    """
    service_source = (SRC / "application" / "web_auth_service.py").read_text(encoding="utf-8")
    assert "self._settings.web_base_url" in service_source
    for host_ish in ("request.headers", 'headers["host"]', "request.url", "base_url ="):
        assert host_ish not in service_source, host_ish
    # Nothing anywhere builds a login URL from a request.
    handler = (SRC / "bot" / "handlers" / "web.py").read_text(encoding="utf-8")
    assert "issue_login_link" in handler
    assert "http" not in handler.replace("https://", "").replace("http://localhost", "")


@pytest.mark.asyncio
async def test_20b_production_requires_an_https_base_url() -> None:
    """A login link is a credential; ``http://`` would publish it.

    Refused at settings construction, so the process dies at startup rather than
    after emitting one.
    """
    with pytest.raises(ValueError, match="https"):
        Settings(app_env="production", web_base_url="http://pr.example.com")
    ok = Settings(app_env="production", web_base_url="https://pr.example.com")
    assert ok.web_base_url.startswith("https://")


@pytest.mark.asyncio
async def test_22_the_login_route_is_not_an_open_redirect(world: World) -> None:
    """Redirect targets are two hardcoded local paths. Nothing else.

    A magic-link endpoint that honoured ``?next=`` would be an open redirect with
    a session cookie attached - the most useful kind to an attacker.
    """
    token = await _token_for(world, world.lead)
    response = world.client.get(
        f"/auth/login?t={token}&next=https://evil.test/steal"
        "&redirect=//evil.test&return_to=/../../etc",
        follow_redirects=False,
    )
    assert response.headers["location"] == "/pr"

    failed = world.client.get("/auth/login?t=bogus&next=https://evil.test", follow_redirects=False)
    assert failed.headers["location"] == "/auth/failed"

    # And the router names no redirect parameter at all.
    source = (SRC / "api" / "routers" / "web_auth.py").read_text(encoding="utf-8")
    for parameter in ("next", "return_to", "redirect_uri", "continue"):
        assert f'"{parameter}"' not in source, parameter


# --- 23-34: session ---------------------------------------------------------


@pytest.mark.asyncio
async def test_23_the_session_credential_is_stored_hashed(world: World) -> None:
    issued = await world.auth.redeem_login_token(token=await _token_for(world, world.lead))
    rows = (
        (
            await world.session.execute(
                select(WebSession).where(WebSession.kind == WebSessionKind.SESSION)
            )
        )
        .scalars()
        .all()
    )
    assert [row.token_hash for row in rows] == [hash_token(issued.token)]
    assert issued.token not in {row.token_hash for row in rows}


@pytest.mark.asyncio
async def test_24_a_valid_session_authenticates(world: World) -> None:
    await _cookie_login(world, world.lead)
    assert world.client.get("/api/pr/dashboard").status_code == 200


@pytest.mark.asyncio
async def test_25_and_27_logout_revokes_server_side(world: World) -> None:
    """The cookie is cleared *and* the row is revoked.

    Clearing only the cookie would leave a live credential that anybody holding a
    copy could keep using.
    """
    await _cookie_login(world, world.lead)
    stolen = world.client.cookies[SESSION_COOKIE]

    assert world.client.post("/api/auth/logout").status_code == 204
    row = (
        await world.session.execute(
            select(WebSession).where(WebSession.token_hash == hash_token(stolen))
        )
    ).scalar_one()
    assert row.revoked_at is not None

    # Replaying the copied cookie fails.
    world.client.cookies.set(SESSION_COOKIE, stolen)
    assert world.client.get("/api/pr/dashboard").status_code == 401


@pytest.mark.asyncio
async def test_26_an_expired_session_is_rejected(world: World) -> None:
    stale = "an-expired-session-token"
    past = utcnow() - timedelta(days=2)
    world.session.add(
        WebSession(
            user_id=world.lead.id,
            kind=WebSessionKind.SESSION,
            token_hash=hash_token(stale),
            created_at=past,
            expires_at=past + timedelta(hours=12),
        )
    )
    await world.session.flush()
    world.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
    world.client.cookies.set(SESSION_COOKIE, stale)
    assert world.client.get("/api/pr/dashboard").status_code == 401


@pytest.mark.asyncio
async def test_28_and_30_a_session_without_a_user_resolves_to_nobody(world: World) -> None:
    """A session whose subject is missing authenticates nobody - not a default.

    Two layers guard this. In PostgreSQL the ``RESTRICT`` foreign key makes the
    orphan impossible in the first place; that is asserted against a real database
    in ``tests/integration/test_web_session_migration.py``. Here the orphan is
    constructed on purpose - SQLite does not enforce foreign keys unless asked -
    to check the *code path* fails closed rather than inventing an identity.
    """
    orphan = "a-session-for-nobody"
    world.session.add(
        WebSession(
            user_id=uuid.uuid4(),
            kind=WebSessionKind.SESSION,
            token_hash=hash_token(orphan),
            expires_at=utcnow() + timedelta(hours=1),
        )
    )
    await world.session.flush()

    assert await world.auth.resolve_session(token=orphan) is None
    world.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
    world.client.cookies.set(SESSION_COOKIE, orphan)
    assert world.client.get("/api/pr/dashboard").status_code == 401

    # And the schema is what prevents it for real.
    foreign_keys = list(WebSession.__table__.c.user_id.foreign_keys)
    assert [key.ondelete for key in foreign_keys] == ["RESTRICT"]


@pytest.mark.asyncio
async def test_29_a_login_token_cannot_be_used_as_a_session_cookie(world: World) -> None:
    """The ``kind`` filter, which is the point of storing both in one table.

    Without it, a short-lived single-use secret pasted into the cookie would
    resolve as a long-lived session.
    """
    token = await _token_for(world, world.lead)
    assert await world.auth.resolve_session(token=token) is None
    world.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
    world.client.cookies.set(SESSION_COOKIE, token)
    assert world.client.get("/api/pr/dashboard").status_code == 401


@pytest.mark.asyncio
async def test_31_and_33_the_cookie_is_httponly_and_samesite_strict(world: World) -> None:
    """Read off the real ``Set-Cookie`` header, not off the source.

    ``SameSite=Strict`` is the CSRF control: the browser will not attach the
    cookie to a request another site caused.
    """
    token = await _token_for(world, world.lead)
    response = world.client.get(f"/auth/login?t={token}", follow_redirects=False)
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "SameSite=strict" in header.replace("samesite", "SameSite")
    assert "Path=/" in header
    # No Domain: a domain would widen the cookie to every subdomain.
    assert "Domain" not in header
    # Max-Age matches the server-side expiry, so the browser and the database
    # agree about when the session ends.
    assert f"Max-Age={world.settings.web_session_ttl_seconds}" in header


def test_32_and_34_production_refuses_an_insecure_cookie() -> None:
    """``APP_ENV=production`` with ``WEB_COOKIE_SECURE=false`` is fatal.

    This is the tempting mistake: a browser drops a ``Secure`` cookie over
    ``http://``, so the fastest way to make a plain-HTTP deployment work is to
    turn the flag off - which puts the session on the wire in clear.
    """
    with pytest.raises(ValueError, match="WEB_COOKIE_SECURE"):
        Settings(app_env="production", web_cookie_secure=False)
    assert Settings(app_env="production", web_base_url="https://x.test").web_cookie_secure
    # Development is unaffected: localhost has no network to sniff.
    assert Settings(app_env="development", web_cookie_secure=False).app_env == "development"


def test_32b_the_cookie_flag_is_read_from_settings_not_hardcoded() -> None:
    """And the router uses the setting rather than a literal."""
    source = (SRC / "api" / "routers" / "web_auth.py").read_text(encoding="utf-8")
    assert "secure=settings.web_cookie_secure" in source
    assert "secure=False" not in source
    assert "httponly=True" in source
    assert 'samesite="strict"' in source


# --- 35-42: web security ----------------------------------------------------


def test_35_cors_is_never_wildcard_and_is_off_by_default() -> None:
    """No CORS middleware at all unless an operator names an origin.

    Step 1E derived an allowed origin from ``WEB_BASE_URL``, which opened a
    credentialed cross-origin path that the same-origin topology never uses.
    """
    from starlette.middleware.cors import CORSMiddleware

    def cors_of(settings: Settings) -> object | None:
        app = create_app(settings)
        for middleware in app.user_middleware:
            if middleware.cls is CORSMiddleware:
                return middleware
        return None

    assert cors_of(_settings()) is None, "CORS installed with no explicit origin list"
    configured = cors_of(_settings(web_extra_allowed_origins="https://a.test, https://b.test"))
    assert configured is not None
    origins = configured.kwargs["allow_origins"]  # type: ignore[attr-defined]
    assert origins == ["https://a.test", "https://b.test"]
    assert "*" not in origins
    assert configured.kwargs["allow_credentials"] is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_36_a_same_origin_mutation_works_end_to_end(world: World) -> None:
    """The flow the deployment actually uses: cookie in, write performed."""
    await _cookie_login(world, world.lead)
    response = world.client.post(
        "/api/pr/contents",
        json={
            "title": "Bài viết qua web",
            "brand_id": str(world.brand_id),
            "owner_user_id": str(world.lead.id),
            # Step 1F.2.3e: the create route requires a format.
            "content_type": "SHORT_VIDEO_SCRIPT",
            "script_text": "kịch bản",
            "targets": [{"channel_id": str(world.channel_id)}],
        },
    )
    assert response.status_code == 201, response.text
    assert re.fullmatch(r"CNT-\d{4}-\d{6}", response.json()["content"]["code"])


def test_37_the_security_headers_live_in_the_browser_facing_layer() -> None:
    """Headers are set by Next, which is the layer a browser talks to.

    The API is only ever reached through that proxy, so duplicating them there
    would be belt-and-braces on a door nobody opens directly. Asserted at the
    source because the runtime check needs a built Next server - which
    ``frontend/tests/security.test.ts`` and the deployment checklist cover.
    """
    middleware = (FRONTEND / "src" / "middleware.ts").read_text(encoding="utf-8")
    for directive in (
        "default-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "connect-src 'self'",
        "Permissions-Policy",
        "Strict-Transport-Security",
    ):
        assert directive in middleware, directive
    # A nonce, not a blanket allowance for inline script. Scoped to the policy
    # builder rather than the whole file: the module comment *names* the
    # directives it deliberately avoids, and a whole-file sweep would trip on the
    # explanation of why they are absent.
    policy_source = middleware[
        middleware.index("function contentSecurityPolicy") : middleware.index(
            "export function middleware"
        )
    ]
    # Comments stripped, for the same reason ``_executable_source`` strips Python
    # docstrings: the comment inside this function *names* ``'unsafe-eval'`` while
    # explaining its absence, and a raw substring check would read that as its
    # presence.
    policy = "\n".join(
        line for line in policy_source.splitlines() if not line.strip().startswith("//")
    )
    assert "'nonce-${nonce}'" in policy
    assert "'strict-dynamic'" in policy
    assert "'unsafe-eval'" not in policy
    # style-src is the one concession, and it is script-src that matters.
    script_line = next(line for line in policy.splitlines() if "script-src" in line)
    assert "unsafe" not in script_line

    config = (FRONTEND / "next.config.mjs").read_text(encoding="utf-8")
    for header in ("X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options"):
        assert header in config, header


@pytest.mark.asyncio
async def test_38_errors_carry_no_traceback_or_sql(world: World) -> None:
    """Checked on 401, 403, 404, 409 and 422 - every shape a client can provoke."""
    world.client.cookies.clear()
    world.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
    probes = [world.client.get("/api/pr/dashboard")]
    world.act_as(world.other)
    probes += [
        world.client.get(f"/api/pr/contents/{uuid.uuid4()}"),
        world.client.post("/api/pr/contents", json={"title": "x"}),
        world.client.post(
            "/api/pr/capabilities/grant",
            json={"user_id": str(world.other.id), "capability": "PR_HEAD_REVIEW"},
        ),
    ]
    for response in probes:
        assert response.status_code >= 400
        lowered = response.text.lower()
        for leak in ("traceback", "select ", "sqlalchemy", "asyncpg", "psycopg", "/home/", '.py",'):
            assert leak not in lowered, (response.status_code, leak)


def test_39_the_health_endpoints_expose_nothing_sensitive() -> None:
    """Unauthenticated by design - so what they say matters.

    Component up/down and a timestamp. No configuration, no credentials, no user
    data, and (unlike ``/api/v1/system/info``, which is now internal-only) no map
    of the tool surface.
    """
    from meobot.api.schemas.common import ReadinessResponse

    fields = set(ReadinessResponse.model_fields)
    assert fields == {"status", "healthy", "checked_at", "components"}
    for banned in ("database_url", "version", "settings", "token", "user"):
        assert banned not in fields

    facts = route_inventory(internal_enabled=False)
    served = {fact.path for fact in facts}
    assert "/health/live" in served and "/health/ready" in served
    # system/info is no longer part of the default surface.
    assert "/api/v1/system/info" not in served


def test_40_docs_are_off_in_production() -> None:
    """``/openapi.json`` is a map of the surface, including internal routers."""
    app = create_app(_settings(app_env="production", api_docs_enabled=True, web_cookie_secure=True))
    assert app.openapi_url is None
    assert app.docs_url is None
    dev = create_app(_settings(app_env="development", api_docs_enabled=True))
    assert dev.openapi_url == "/openapi.json"


def test_41_there_is_no_http_endpoint_that_issues_a_login_link() -> None:
    """Issuing a link is proving identity. Only ``/web`` in the bot does it.

    An HTTP endpoint for it would hand a credential to whoever asked, whatever
    body validation sat in front.
    """
    for fact in route_inventory(internal_enabled=True):
        assert "issue" not in fact.path, fact.path
    router_dir = SRC / "api" / "routers"
    for path in router_dir.glob("*.py"):
        assert "issue_login_link" not in path.read_text(encoding="utf-8"), path.name
    # The bot handler is the only caller, and it takes the resolved Actor's id.
    handler = (SRC / "bot" / "handlers" / "web.py").read_text(encoding="utf-8")
    assert "issue_login_link(\n                user_id=actor.user_id" in handler.replace(
        "issue_login_link(user_id=actor.user_id",
        "issue_login_link(\n                user_id=actor.user_id",
    )


@pytest.mark.asyncio
async def test_42_a_forbidden_user_gets_403_not_a_login_loop(world: World) -> None:
    """403 and 401 must stay distinct, or the UI logs somebody out for being
    insufficiently privileged - and they log back in to the same refusal."""
    await _cookie_login(world, world.other)
    forbidden = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.other.id),
            "capability": PrCapability.PR_HEAD_REVIEW.value,
            # Well-formed on purpose: a 422 would prove the body was wrong, not
            # that the caller was refused.
            "scope": {"content_type_scope": "ALL", "channel_scope": "ALL"},
        },
    )
    assert forbidden.status_code == 403
    # The session is still good.
    assert world.client.get("/api/auth/session").status_code == 200


# --- Structural sweep -------------------------------------------------------


def _executable_source(path: Path) -> str:
    """Source with comments and strings stripped.

    So a docstring explaining "no router writes ``workflow_stage``" does not trip
    the sweep looking for that write.
    """
    kept: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return " ".join(kept)


def test_sweep_no_api_module_writes_pr_state_or_bypasses_auth() -> None:
    """The rules that must hold across the whole HTTP layer."""
    for path in sorted((SRC / "api").rglob("*.py")):
        source = _executable_source(path)
        assert not re.search(r"\.workflow_stage\s*=(?!=)", source), path
        assert ".commit(" not in source, path
        # No bypass: nothing reads an identity out of a header or a query param.
        for bypass in ("X-Telegram", "x-telegram", "DEBUG_ACTOR", "allow_insecure"):
            assert bypass not in source, (path, bypass)


def test_sweep_the_synthetic_owner_has_exactly_one_definition_and_one_gate() -> None:
    """One definition, one flag, and no second way to get an OWNER."""
    deps = (SRC / "api" / "deps.py").read_text(encoding="utf-8")
    assert deps.count("def get_current_system_actor") == 1
    assert "api_internal_routers_enabled" in deps
    # No other module constructs an OWNER actor for an HTTP request.
    for path in sorted((SRC / "api").rglob("*.py")):
        if path.name == "deps.py":
            continue
        assert "Role.OWNER" not in path.read_text(encoding="utf-8"), path


def test_sweep_the_secret_key_list_covers_the_web_auth_vocabulary() -> None:
    """Log redaction knows the names this subsystem could reach for."""
    for key in ("token", "session_token", "login_token", "token_hash", "login_url", "cookie"):
        assert key in SENSITIVE_KEYS, key


def test_sweep_the_frontend_leaks_no_secret_and_trusts_no_client_role() -> None:
    """No ``NEXT_PUBLIC_`` anything, and no client-side authority."""
    for path in sorted((FRONTEND / "src").rglob("*.ts*")):
        source = path.read_text(encoding="utf-8")
        assert "NEXT_PUBLIC" not in source, path
        assert "dangerouslySetInnerHTML" not in source, path
        for forbidden in ("hasPermission", "ROLE_RANK", "isAdmin"):
            assert forbidden not in source, (path, forbidden)
    # The server-only variable stays server-only.
    config = (FRONTEND / "next.config.mjs").read_text(encoding="utf-8")
    assert "process.env.MEOBOT_API_URL" in config
    assert "NEXT_PUBLIC_MEOBOT_API_URL" not in config
