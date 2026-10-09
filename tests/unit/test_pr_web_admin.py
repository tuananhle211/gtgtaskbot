"""Step 1E - the web admin API and the session that authenticates it.

Twenty-five numbered tests, in three groups.

**1-9 authentication.** The part that did not exist before Step 1E. These are
the tests that matter most, because the alternative to a real session was
``get_current_system_actor`` handing OWNER to anybody who reached the port.

**10-20 the HTTP surface.** Real routes, real services, real SQL, over
``TestClient``. The session dependency is overridden onto one in-memory SQLite
transaction so the whole request path runs - deps, router, service, database -
rather than a mocked service returning what the test wants to see.

**21-25 the boundary.** Source-level sweeps proving the web layer did not grow
its own copy of a business rule. The same shape as the Step 1D architecture
tests, for the same reason: a rule enforced only by review stops being enforced.

Why ``TestClient`` and not the services directly
------------------------------------------------

Everything below the router is already covered by
``test_pr_application_services.py``. What is unproven here is the *wiring*: that
a route reaches the right method with the actor from the cookie rather than one
it invented, that a refusal becomes the right status, and that a body cannot
carry a field it should not. None of that is visible from a service test.
"""

from __future__ import annotations

import json
import re
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

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import _STATUS_MAP, create_app
from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import CreateChannelCommand, PrChannelService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_publication_service import RegisterPublicationCommand
from meobot.application.pr_services import build_pr_services
from meobot.application.web_auth_service import (
    SESSION_COOKIE,
    WebAuthService,
    hash_token,
)
from meobot.core.config import Settings
from meobot.core.errors import (
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ValidationError,
    WorkflowStateError,
)
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrApprovalEvent, PrBrand, PrContentItem, PrPlatform
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.user import User
from meobot.db.models.web_session import WebSession, WebSessionKind
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrAiReviewRequiredError,
    PrApprovalStageMismatchError,
    PrConflictError,
    PrImmutableFieldError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrStaleVersionError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import (
    PrAiReviewRunStatus,
    PrApprovalDecision,
    PrChannelCategory,
    PrEntityStatus,
    PrProductionArtifactType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import PrTransitionTrigger, allowed_content_targets
from tests.unit.streams import tag_pr

SRC = Path("src/meobot")
WEB_MODULES = (
    SRC / "api" / "routers" / "pr.py",
    SRC / "api" / "routers" / "web_auth.py",
    SRC / "api" / "schemas" / "pr.py",
    SRC / "application" / "web_auth_service.py",
)


def _web_settings(**overrides: object) -> Settings:
    """Settings with the web panel configured.

    ``web_cookie_secure=False`` because ``TestClient`` speaks ``http://``, and a
    browser - which is what ``requests``' cookie jar imitates - drops a
    ``Secure`` cookie sent over plain HTTP. Test 6 pins the production default
    separately, so turning it off here does not hide it.
    """
    defaults: dict[str, object] = {
        "web_base_url": "https://pr.example.com",
        "web_cookie_secure": False,
    }
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


# --- The world --------------------------------------------------------------


@dataclass(slots=True)
class WebWorld:
    """One seeded database, an app wired onto it, and two people."""

    session: AsyncSession
    client: TestClient
    settings: Settings
    auth: WebAuthService
    lead: User
    head: User
    outsider: User
    brand_id: uuid.UUID
    channel_id: uuid.UUID

    def act_as(self, user: User) -> None:
        """Point the app's actor dependency at one person.

        An override rather than a real cookie for the route tests: tests 1-9
        exercise the cookie path itself, and repeating a login in each of the
        eleven route tests would only prove test 4 eleven times.
        """
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
            user_id=user.id,
            full_name=user.full_name,
            role=user.role,
            active=user.active,
        )


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[WebWorld]:
    """A brand, a platform, a channel, three people, and a running app."""
    lead = User(full_name="Le Trưởng Nhóm", role=Role.TEAM_LEAD)
    head = User(full_name="Ha Trưởng Phòng", role=Role.ADMIN)
    outsider = User(full_name="Nguyen Nhân Viên", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-W", name="Web Brand")
    platform = PrPlatform(code="PLAT-W", name="Web Platform")
    session.add_all([lead, head, outsider, brand, platform])
    await session.flush()
    await tag_pr(session, [lead, head, outsider])  # untagged sees no stream

    settings = _web_settings()
    audit = AuditService(session)
    capabilities = PrCapabilityService(session, audit)
    granter = Actor(user_id=head.id, full_name=head.full_name, role=Role.OWNER)
    await capabilities.grant(
        actor=granter,
        request_id=uuid.uuid4(),
        user_id=lead.id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
    )
    await capabilities.grant(
        actor=granter,
        request_id=uuid.uuid4(),
        user_id=head.id,
        capability=PrCapability.PR_HEAD_REVIEW,
    )
    channel = await PrChannelService(
        session, audit, capabilities, PrCodeService(session, settings)
    ).create_channel(
        actor=granter,
        request_id=uuid.uuid4(),
        command=CreateChannelCommand(
            name="Web Channel",
            platform_id=platform.id,
            brand_id=brand.id,
            category=PrChannelCategory.SCALE,
        ),
    )

    app = create_app(settings)
    # One transaction for the whole test, shared with the fixture's own writes.
    # ``get_session`` would otherwise open a second one against a different
    # in-memory database and see none of the seed data.
    # A plain provider, not an async generator: FastAPI accepts a value override
    # for a yield dependency, and returning the generator object itself hands the
    # services an ``async_generator`` where a session belongs.
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield WebWorld(
            session=session,
            client=client,
            settings=settings,
            auth=WebAuthService(session, settings),
            lead=lead,
            head=head,
            outsider=outsider,
            brand_id=brand.id,
            channel_id=channel.id,
        )
    app.dependency_overrides.clear()


async def _make_content(
    world: WebWorld, *, title: str = "Bài viết thử", with_target: bool = True
) -> uuid.UUID:
    """Create one content item through the service, as a fixture would.

    ``with_target`` plans the fixture's channel, and defaults on since Step
    1F.2: content with no planned channel cannot enter ``AI_REVIEW`` at all, and
    registering a publication has always required the channel to be a planned
    target - a rule ``PrPublicationService`` owns.
    """
    services = build_pr_services(world.session, world.settings)
    snapshot = await services.content.create_content(
        actor=Actor(user_id=world.lead.id, full_name=world.lead.full_name, role=Role.TEAM_LEAD),
        request_id=uuid.uuid4(),
        command=CreateContentCommand(
            title=title,
            brand_id=world.brand_id,
            owner_user_id=world.lead.id,
            script_text="Nội dung kịch bản.",
            targets=((ContentTargetSpec(channel_id=world.channel_id),) if with_target else ()),
        ),
    )
    return snapshot.content.id


async def _drive_to_team_lead_gate(world: WebWorld, content_id: uuid.UUID) -> None:
    """Walk one item to ``TEAM_LEAD_REVIEW`` the way the workflow intends.

    Manual edges to ``AI_REVIEW``, then a gating ``FULL_REVIEW`` verdict - which
    is the *only* edge into the human gate. Deliberately not a direct write of
    ``workflow_stage``: an earlier version of these tests forced the column, and
    from Step 1E.2.1 that produces an item standing at a gate with no verdict on
    file, which is a state the workflow cannot actually produce.
    """
    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        assert (
            world.client.post(
                f"/api/pr/contents/{content_id}/transition", json={"target_stage": stage.value}
            ).status_code
            == 200
        ), stage.value
    accepted = world.client.post(
        f"/api/pr/contents/{content_id}/submit-ai-review",
        json={
            "reviewed_version": 1,
            "review_type": "FULL_REVIEW",
            "result": "PASS",
            "model_name": "gpt-x",
            "prompt_version": "pr-review-v1",
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["content"]["workflow_stage"] == PrWorkflowStage.TEAM_LEAD_REVIEW.value


async def _force_stage(world: WebWorld, content_id: uuid.UUID, stage: PrWorkflowStage) -> None:
    """Put one item at a stage without walking there, for test setup only.

    Re-reads the row first: a ``TestClient`` call runs the app against this same
    session and expires the instances it touched, so assigning to a stale one
    would lazy-load outside the greenlet SQLAlchemy's async layer needs.

    Used only to skip stages a test is not about. It is never used to reach a
    *gate*, because from Step 1E.2.1 a gate without a verdict on file is a state
    the workflow cannot produce - which is exactly what these tests must not
    accidentally assert about.
    """
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    content.workflow_stage = stage
    await world.session.flush()


async def _publish(world: WebWorld, content_id: uuid.UUID) -> uuid.UUID:
    """Get one item to ``PUBLISHED`` with a real publication row behind it.

    The stage is set to ``READY_TO_PUBLISH`` directly, which is a shortcut past
    three approval gates this test is not about - and, unlike forcing a gate, it
    is a state the workflow reaches perfectly ordinarily. Everything after that
    is the real path: ``register_publication`` writes the row, checks
    ``PUBLISHABLE_STAGES`` and is what moves the item to ``PUBLISHED``.
    """
    await _force_stage(world, content_id, PrWorkflowStage.READY_TO_PUBLISH)

    services = build_pr_services(world.session, world.settings)
    # Step 1F.2.3f: a publication names the produced output that went out, so
    # there has to be one. Written directly for the same reason the stage is
    # forced above - the submission flow is tested where it lives, and this
    # helper needs the precondition rather than the machinery.
    version = await services.content.require_current_version(content_id)
    submission = PrProductionSubmission(
        content_id=content_id,
        content_version_id=version.id,
        submission_no=1,
        producer_user_id=world.head.id,
        submitted_by_user_id=world.head.id,
        artifact_type=PrProductionArtifactType.DRIVE_LINK,
        location="https://drive.google.com/file/d/1PublishedMaster/view",
    )
    world.session.add(submission)
    await world.session.flush()

    outcome = await services.publications.register_publication(
        actor=Actor(user_id=world.head.id, full_name=world.head.full_name, role=Role.ADMIN),
        request_id=uuid.uuid4(),
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=utcnow(),
            production_submission_id=submission.id,
            publisher_user_id=world.head.id,
        ),
    )
    assert outcome.new_stage is PrWorkflowStage.PUBLISHED
    return outcome.publication.id


# --- 1-9: authentication ----------------------------------------------------


@pytest.mark.asyncio
async def test_01_a_pr_route_with_no_cookie_is_refused(world: WebWorld) -> None:
    """The whole point of Step 1E.

    Before it, this request would have been served as OWNER. There is no header,
    query parameter or environment variable that changes this answer.
    """
    response = world.client.get("/api/pr/dashboard")
    assert response.status_code == 401
    # And it must not leak which of the several reasons applied.
    assert "expired" not in response.text.lower()


@pytest.mark.asyncio
async def test_02_a_forged_cookie_is_refused(world: WebWorld) -> None:
    """A random string is not a session, and guessing is not a login."""
    world.client.cookies.set(SESSION_COOKIE, "definitely-not-a-real-token")
    assert world.client.get("/api/pr/dashboard").status_code == 401


@pytest.mark.asyncio
async def test_03_a_telegram_id_is_not_a_web_credential(world: WebWorld) -> None:
    """No header carrying an identity is honoured.

    Telegram ids are public. If one authenticated a request, anybody who knew
    somebody's id could act as them - which is precisely why Telegram is the
    delivery channel for a link and never the credential itself.
    """
    for header in ("X-Telegram-User-Id", "X-User-Id", "Authorization"):
        response = world.client.get("/api/pr/dashboard", headers={header: "123456789"})
        assert response.status_code == 401, header


@pytest.mark.asyncio
async def test_04_a_login_link_redeems_once_and_only_once(world: WebWorld) -> None:
    """The full path: issue, redeem, get a cookie, second attempt refused."""
    issued = await world.auth.issue_login_link(user_id=world.lead.id)
    token = issued.url.split("t=")[1]

    first = world.client.get(f"/auth/login?t={token}", follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["location"] == "/pr"
    assert SESSION_COOKIE in first.cookies

    # The link is now spent. A forwarded link is a dead link.
    second = world.client.get(f"/auth/login?t={token}", follow_redirects=False)
    assert second.status_code == 303
    assert second.headers["location"] == "/auth/failed"


@pytest.mark.asyncio
async def test_05_the_session_cookie_authenticates_real_requests(world: WebWorld) -> None:
    """The cookie from a redeemed link resolves to that person, not to an OWNER."""
    issued = await world.auth.issue_login_link(user_id=world.lead.id)
    world.client.get(f"/auth/login?t={issued.url.split('t=')[1]}", follow_redirects=False)

    response = world.client.get("/api/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(world.lead.id)
    assert body["role"] == Role.TEAM_LEAD.value
    # The grant the fixture made, reported live rather than baked into the cookie.
    assert PrCapability.PR_TEAM_LEAD_REVIEW.value in body["capabilities"]


def test_06_the_cookie_defaults_are_the_strict_ones() -> None:
    """``HttpOnly``, ``Secure``, ``SameSite=Strict`` - and why each matters.

    ``SameSite=Strict`` is the CSRF control: the browser will not attach the
    cookie to a request another site caused, so there is no cross-site
    credentialed request for a token to have to protect. Checked at the source
    rather than through a response header, because the test client's transport
    has to run without ``Secure`` (test fixture note) and a runtime assertion
    would therefore be asserting the test's own override.
    """
    source = (SRC / "api" / "routers" / "web_auth.py").read_text(encoding="utf-8")
    assert "httponly=True" in source
    assert 'samesite="strict"' in source
    assert "secure=settings.web_cookie_secure" in source
    # And the production default is on.
    assert Settings().web_cookie_secure is True


@pytest.mark.asyncio
async def test_07_only_the_hash_of_a_token_is_ever_stored(world: WebWorld) -> None:
    """A database dump must contain nothing replayable."""
    issued = await world.auth.issue_login_link(user_id=world.lead.id)
    token = issued.url.split("t=")[1]

    rows = (await world.session.execute(select(WebSession))).scalars().all()
    stored = {row.token_hash for row in rows}
    assert token not in stored
    assert hash_token(token) in stored
    # sha256 hex, so nothing token-shaped slipped into the column.
    assert all(re.fullmatch(r"[0-9a-f]{64}", row.token_hash) for row in rows)


@pytest.mark.asyncio
async def test_08_an_expired_or_revoked_session_stops_working(world: WebWorld) -> None:
    """Both failure modes, plus a constraint that turned out to matter.

    Expiry is checked against the stored timestamp rather than the cookie's own
    ``max-age``, which a client controls.

    The expired row is **inserted** already-expired rather than made expired by
    an update, because ``ck_web_sessions_expiry_after_creation`` refuses to move
    an expiry behind its creation time - so a session cannot be retroactively
    invalidated by rewriting its expiry. That is the constraint working: revoking
    is what ends a session early, and it leaves a dated record of who ended it.
    """
    issued = await world.auth.redeem_login_token(
        token=(await world.auth.issue_login_link(user_id=world.lead.id)).url.split("t=")[1]
    )
    assert await world.auth.resolve_session(token=issued.token) is not None

    # Revocation: the supported way to end a session early.
    assert await world.auth.revoke_session(token=issued.token) is True
    assert await world.auth.resolve_session(token=issued.token) is None
    # Idempotent - a second logout is not an error.
    assert await world.auth.revoke_session(token=issued.token) is False

    # Expiry: a row whose whole lifetime is in the past.
    stale_token = "a-token-that-was-valid-yesterday"
    past = utcnow() - timedelta(days=2)
    world.session.add(
        WebSession(
            user_id=world.lead.id,
            kind=WebSessionKind.SESSION,
            token_hash=hash_token(stale_token),
            created_at=past,
            expires_at=past + timedelta(hours=12),
        )
    )
    await world.session.flush()
    assert await world.auth.resolve_session(token=stale_token) is None
    # And it is refused over HTTP too, not merely by the service.
    world.client.cookies.set(SESSION_COOKIE, stale_token)
    assert world.client.get("/api/pr/dashboard").status_code == 401


@pytest.mark.asyncio
async def test_09_deactivation_takes_effect_immediately(world: WebWorld) -> None:
    """A live cookie for a deactivated person resolves to nobody.

    The actor is rebuilt from the ``users`` row on every request, so somebody
    switched off at 09:00 loses access at 09:00 rather than whenever their
    cookie happens to expire. The same mechanism is what makes a role change
    take effect on the next click.
    """
    issued = await world.auth.redeem_login_token(
        token=(await world.auth.issue_login_link(user_id=world.outsider.id)).url.split("t=")[1]
    )
    assert await world.auth.resolve_session(token=issued.token) is not None

    world.outsider.active = False
    await world.session.flush()
    assert await world.auth.resolve_session(token=issued.token) is None

    # And a link cannot be issued for them at all any more.
    with pytest.raises(AuthorizationError):
        await world.auth.issue_login_link(user_id=world.outsider.id)


@pytest.mark.asyncio
async def test_09b_an_unconfigured_deployment_refuses_to_mint_links(
    session: AsyncSession,
) -> None:
    """No ``WEB_BASE_URL`` means no link, rather than a link to nowhere.

    Numbered as a rider on 9 because it is the same question - who may get a
    credential - answered for the deployment rather than the person. A link
    built against the wrong host is a token handed to a stranger's server.
    """
    user = User(full_name="Somebody", role=Role.EMPLOYEE)
    session.add(user)
    await session.flush()
    service = WebAuthService(session, Settings(web_base_url=""))
    with pytest.raises(ConfigurationError):
        await service.issue_login_link(user_id=user.id)


# --- 10-20: the HTTP surface ------------------------------------------------


@pytest.mark.asyncio
async def test_10_creating_content_allocates_the_code_server_side(world: WebWorld) -> None:
    """The API never accepts a code, and the counter produces the real one."""
    world.act_as(world.lead)
    response = world.client.post(
        "/api/pr/contents",
        json={
            "title": "Chăm sóc sau nâng mũi",
            "brand_id": str(world.brand_id),
            "owner_user_id": str(world.lead.id),
            # Step 1F.2.3e: the web create route requires a format.
            "content_type": "SHORT_VIDEO_SCRIPT",
            "script_text": "Kịch bản đầu tiên.",
            "targets": [{"channel_id": str(world.channel_id)}],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert re.fullmatch(r"CNT-\d{4}-\d{6}", body["content"]["code"])
    assert body["content"]["workflow_stage"] == PrWorkflowStage.IDEA.value
    assert body["current_version"]["version_no"] == 1

    # A body that tries to supply one is rejected outright, not ignored.
    forged = world.client.post(
        "/api/pr/contents",
        json={
            "title": "Bài khác",
            "brand_id": str(world.brand_id),
            "owner_user_id": str(world.lead.id),
            # Step 1F.2.3e: the web create route requires a format.
            "content_type": "SHORT_VIDEO_SCRIPT",
            "code": "CNT-2026-999999",
        },
    )
    assert forged.status_code == 422


@pytest.mark.asyncio
async def test_11_an_illegal_transition_is_refused_with_409(world: WebWorld) -> None:
    """The matrix decides, and the API reports a conflict rather than a 400.

    ``IDEA -> APPROVED`` is not an edge. Before Step 1E extended ``_STATUS_MAP``
    this surfaced as a bare 400, which tells a client nothing about whether to
    retry.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/transition",
        json={"target_stage": PrWorkflowStage.APPROVED.value},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_12_a_stale_expected_version_is_refused(world: WebWorld) -> None:
    """Two people editing one draft: the second is told, not overwritten."""
    world.act_as(world.lead)
    content_id = await _make_content(world)
    ok = world.client.post(
        f"/api/pr/contents/{content_id}/versions",
        json={"expected_version": 1, "script_text": "Bản sửa thứ nhất."},
    )
    assert ok.status_code == 201

    stale = world.client.post(
        f"/api/pr/contents/{content_id}/versions",
        json={"expected_version": 1, "script_text": "Bản sửa của người thứ hai."},
    )
    assert stale.status_code == 409

    versions = world.client.get(f"/api/pr/contents/{content_id}/versions").json()
    assert [row["version_no"] for row in versions] == [2, 1]
    # Version 1 still says what it said.
    assert versions[-1]["script_text"] == "Nội dung kịch bản."


@pytest.mark.asyncio
async def test_13_review_context_reports_an_absent_ai_review_as_null(world: WebWorld) -> None:
    """Never an empty verdict card, which would read as "reviewed, all clear"."""
    world.act_as(world.lead)
    content_id = await _make_content(world)
    for stage in (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING):
        world.client.post(
            f"/api/pr/contents/{content_id}/transition", json={"target_stage": stage.value}
        )

    body = world.client.get(f"/api/pr/contents/{content_id}/review-context").json()
    assert body["ai_review"] is None
    assert body["ai_reviews_for_version"] == []
    assert body["approvals"] == []
    assert world.client.get(f"/api/pr/contents/{content_id}/ai-reviews").json() == []


@pytest.mark.asyncio
async def test_14_no_route_produces_an_ai_verdict(world: WebWorld) -> None:
    """``submit-ai-review`` records; it does not review.

    Entering ``AI_REVIEW`` must leave ``pr_ai_reviews`` empty. A verdict
    appearing here would mean something is fabricating one.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        response = world.client.post(
            f"/api/pr/contents/{content_id}/transition", json={"target_stage": stage.value}
        )
        assert response.status_code == 200, response.text

    assert response.json()["content"]["workflow_stage"] == PrWorkflowStage.AI_REVIEW.value
    assert world.client.get(f"/api/pr/contents/{content_id}/ai-reviews").json() == []
    # And no approval event was manufactured on the way.
    assert world.client.get(f"/api/pr/contents/{content_id}/approvals").json() == []


@pytest.mark.asyncio
async def test_15_an_approval_body_cannot_name_its_own_reviewer(world: WebWorld) -> None:
    """The reviewer is the session. Nothing else.

    ``extra="forbid"`` turns an attempt into a 422 rather than a silently
    ignored field - which matters, because a silently ignored
    ``reviewer_user_id`` would look to the caller like it worked.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/reviews",
        json={
            "decision": "APPROVED",
            "version_reviewed": 1,
            "reviewer_user_id": str(world.head.id),
        },
    )
    assert response.status_code == 422
    assert "reviewer_user_id" in response.text


@pytest.mark.asyncio
async def test_16_approving_outside_a_gate_is_refused(world: WebWorld) -> None:
    """An item at ``IDEA`` is not waiting for anybody's approval.

    The gate is derived from the stage, so there is no body that can aim an
    approval at a gate the content is not standing at.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/reviews",
        json={"decision": "APPROVED", "version_reviewed": 1},
    )
    assert response.status_code == 422
    assert world.client.get(f"/api/pr/contents/{content_id}/approvals").json() == []


@pytest.mark.asyncio
async def test_17_an_employee_with_a_valid_session_still_cannot_grant(world: WebWorld) -> None:
    """Authentication is not authorization.

    The refusal comes from ``PrCapabilityService``, inside the service - not
    from a check in the router, and not from the frontend hiding a button.
    """
    world.act_as(world.outsider)
    response = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.outsider.id),
            "capability": PrCapability.PR_HEAD_REVIEW.value,
            # A well-formed body, on purpose: a 422 would prove only that the
            # request was malformed, and the property under test is that a
            # perfectly valid grant request from the wrong person is refused.
            "scope": {"content_type_scope": "ALL", "channel_scope": "ALL"},
        },
    )
    assert response.status_code == 403
    grants = (await world.session.execute(select(PrUserCapability))).scalars().all()
    # Only the two the fixture made.
    assert len(grants) == 2


@pytest.mark.asyncio
async def test_18_the_pending_queue_is_empty_without_a_grant(world: WebWorld) -> None:
    """Including for somebody senior. An unactionable queue is a misleading one."""
    world.act_as(world.outsider)
    assert world.client.get("/api/pr/reviews/pending").json() == []
    assert world.client.get("/api/pr/dashboard").json()["awaiting_my_review"] == []

    world.act_as(world.lead)
    dashboard = world.client.get("/api/pr/dashboard").json()
    assert PrCapability.PR_TEAM_LEAD_REVIEW.value in dashboard["my_capabilities"]
    # Every stage is counted, and every count is a real query.
    assert {row["stage"] for row in dashboard["stage_counts"]} == {
        stage.value for stage in PrWorkflowStage
    }


@pytest.mark.asyncio
async def test_19_a_missing_record_is_404_and_leaks_no_sql(world: WebWorld) -> None:
    """No traceback, no table name, no statement text."""
    world.act_as(world.lead)
    response = world.client.get(f"/api/pr/contents/{uuid.uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) == {"code", "message", "details"}
    lowered = response.text.lower()
    for leak in ("traceback", "select ", "sqlalchemy", "psycopg", "asyncpg"):
        assert leak not in lowered, leak


@pytest.mark.asyncio
async def test_20_channel_assignment_overlap_is_refused_by_the_domain(
    world: WebWorld,
) -> None:
    """The interval rule stays in one place.

    ``effective_to`` is the **last day in force**, so 1-10 January and 10-20
    January overlap on the 10th. The router does no date arithmetic at all - a
    second implementation would eventually disagree about that boundary.
    """
    world.act_as(world.head)
    first = world.client.post(
        f"/api/pr/channels/{world.channel_id}/assignments",
        json={
            "user_id": str(world.lead.id),
            "assignment_role": "CHANNEL_OWNER",
            "effective_from": "2026-01-01",
            "effective_to": "2026-01-10",
        },
    )
    assert first.status_code == 201, first.text

    clash = world.client.post(
        f"/api/pr/channels/{world.channel_id}/assignments",
        json={
            "user_id": str(world.lead.id),
            "assignment_role": "CHANNEL_OWNER",
            "effective_from": "2026-01-10",
            "effective_to": "2026-01-20",
        },
    )
    assert clash.status_code == 409


# --- 21-25: the boundary ----------------------------------------------------


def _executable_source(path: Path) -> str:
    """Source with comments and string literals removed.

    Same helper idea as the Step 1D architecture tests: a docstring explaining
    "no route writes ``workflow_stage``" must not itself trip the sweep looking
    for that write.
    """
    import io
    import tokenize

    kept: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return " ".join(kept)


def test_21_no_web_module_writes_a_workflow_stage_or_a_code() -> None:
    """The two fields only a service may set.

    ``workflow_stage`` because the matrix owns it, and ``code`` because
    ``PrCodeService`` owns the counter. A route that set either would be a second
    authority over a rule that has exactly one.
    """
    for path in WEB_MODULES:
        source = _executable_source(path)
        assert not re.search(r"\.workflow_stage\s*=(?!=)", source), path
        assert not re.search(r"\.code\s*=(?!=)", source), path
        # And no route fabricates a code by any of the forbidden means.
        assert "MAX(" not in source.upper(), path
        assert "next_value" not in source, path


def test_22_no_web_module_commits_or_constructs_a_pr_row() -> None:
    """Transactions belong to ``get_session``; rows belong to the services.

    A router that committed would break the one-command-one-transaction rule,
    and a router that constructed ``PrContentItem(...)`` would bypass every
    validation, audit line and outbox row the service writes.
    """
    for path in WEB_MODULES:
        source = _executable_source(path)
        assert ".commit(" not in source, path
        assert not re.search(
            r"\bPr(ContentItem|ContentVersion|Task|ApprovalEvent|AiReview)\s*\(", source
        ), path


def test_23_the_matrices_and_capability_rules_are_not_duplicated() -> None:
    """The web layer imports the domain's tables; it does not restate them.

    ``pr.py`` must reference ``STAGE_APPROVAL_GATES`` rather than listing
    stage/gate pairs of its own. An earlier draft of ``_stage_gate_for`` did
    exactly that and named two stages that do not exist - which is how a
    duplicated matrix fails: quietly, and only for the case nobody tested.

    Step 1F.2.7 removed the *other* half of this assertion, and removing it was
    the improvement: the router no longer imports ``APPROVAL_CAPABILITIES`` at
    all, because translating a gate into the capability it needs is now
    ``PrCapabilityService.can_approve``. One mapping, consulted through one
    method, rather than a mapping any caller may look up and then decide with.
    """
    source = (SRC / "api" / "routers" / "pr.py").read_text(encoding="utf-8")
    assert "STAGE_APPROVAL_GATES" in source
    assert "APPROVAL_CAPABILITIES" not in source
    executable = _executable_source(SRC / "api" / "routers" / "pr.py")
    # No literal transition table, and no hand-rolled permission comparison.
    assert "CONTENT_TRANSITIONS" not in executable
    assert "has_permission" not in executable
    assert "PrTransitionTrigger" not in executable


def test_24_both_clients_share_one_service_wiring() -> None:
    """Telegram and the web API build the bundle from the same function.

    Two copies of that dependency graph would eventually differ by one edge, and
    the edge that matters is ``PrApprovalService(…, ai_reviews, …)``: without it
    an approval can be recorded for a draft no AI review ever judged.
    """
    telegram = (SRC / "tools" / "pr_support.py").read_text(encoding="utf-8")
    api = (SRC / "api" / "deps.py").read_text(encoding="utf-8")
    assert "build_pr_services" in telegram
    assert "build_pr_services" in api
    # And the Telegram module no longer constructs the graph itself.
    assert "PrApprovalService(" not in _executable_source(SRC / "tools" / "pr_support.py")


def test_25_every_pr_error_maps_to_the_intended_status() -> None:
    """The mapping the frontend branches on, asserted for the whole vocabulary.

    ``_STATUS_MAP`` is ordered and first-match-wins, so this is also the test
    that catches somebody moving ``ValidationError`` above
    ``PrImmutableFieldError`` and quietly turning a 409 back into a 422.
    """
    from meobot.api.main import _http_status_for

    expected: tuple[tuple[type[Exception], int], ...] = (
        (PrNotFoundError, 404),
        (PrValidationError, 422),
        (PrWorkflowTransitionError, 409),
        (PrStaleVersionError, 409),
        (PrAiReviewRequiredError, 409),
        (PrApprovalStageMismatchError, 409),
        (PrPermissionDeniedError, 403),
        # ``PrReviewerSeparationError`` was here until Step 1F.2.2 and went with
        # the rule; ``PrConflictError`` covers the same ``ConflictError`` branch.
        (PrConflictError, 409),
        (PrImmutableFieldError, 409),
    )
    for error_type, status_code in expected:
        assert _http_status_for(error_type("boom")) == status_code, error_type.__name__

    # The base classes the PR errors inherit from still behave, so nothing above
    # is working by accident.
    assert _http_status_for(NotFoundError("x")) == 404
    assert _http_status_for(ConflictError("x")) == 409
    assert _http_status_for(WorkflowStateError("x")) == 409
    assert _http_status_for(ValidationError("x")) == 422
    assert _http_status_for(AuthorizationError("x")) == 403
    # And the map is ordered narrow-first.
    assert _STATUS_MAP[0][0] is PrImmutableFieldError


def test_26_the_migration_kinds_match_the_enum() -> None:
    """Migration 0017's literal vocabulary against the live enum.

    The migration holds a frozen tuple on purpose - it must keep meaning what it
    meant when it ran - so a drift check is the only thing that keeps the two
    honest. Numbered past 25 because it is the schema-parity rider the other PR
    steps each carry.
    """
    source = Path("alembic/versions/0017_web_sessions.py").read_text(encoding="utf-8")
    match = re.search(r"SESSION_KINDS = \(([^)]*)\)", source)
    assert match is not None
    listed = tuple(re.findall(r'"([A-Z_]+)"', match.group(1)))
    assert listed == tuple(kind.value for kind in WebSessionKind)


# --- 27-32: Step 1E.2, the available-actions read model ----------------------
#
# The panel stopped drawing thirteen buttons and started asking what it may do.
# These six prove the answer is the *same* answer the write path gives - and
# that asking changes nothing.


@pytest.mark.asyncio
async def test_27_available_actions_come_from_the_transition_matrix(world: WebWorld) -> None:
    """The forward moves offered are exactly the matrix's manual edges.

    Not "everything", and not a hand-written list in the route: an item at
    ``IDEA`` has one manual edge forward - ``BRIEFING`` - plus cancelling, and
    that is what the endpoint reports.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)

    body = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()
    assert body["workflow_stage"] == PrWorkflowStage.IDEA.value
    targets = {
        action["target_stage"]
        for action in body["available_actions"]
        if action["action"] == "TRANSITION"
    }
    assert targets == {PrWorkflowStage.BRIEFING.value, PrWorkflowStage.CANCELLED.value}

    # The same set the domain would give for a manual driver, asserted against
    # the table rather than against a copy of it.
    assert targets == {
        stage.value
        for stage in allowed_content_targets(
            PrWorkflowStage.IDEA, trigger=PrTransitionTrigger.MANUAL
        )
    }


@pytest.mark.asyncio
async def test_28_an_invalid_transition_is_never_offered(world: WebWorld) -> None:
    """Every offered move actually works, and the ones left out actually fail.

    The strongest available form of this: take each stage the endpoint did *not*
    list and post it. If one of them succeeded, the panel would be hiding a
    legitimate action; if an offered one failed, it would be promising a refusal.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    offered = {
        action["target_stage"]
        for action in world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
            "available_actions"
        ]
        if action["action"] == "TRANSITION"
    }

    for stage in PrWorkflowStage:
        if stage.value in offered or stage is PrWorkflowStage.IDEA:
            continue
        refused = world.client.post(
            f"/api/pr/contents/{content_id}/transition", json={"target_stage": stage.value}
        )
        assert refused.status_code == 409, stage.value

    # And the one it did offer is accepted.
    accepted = world.client.post(
        f"/api/pr/contents/{content_id}/transition",
        json={"target_stage": PrWorkflowStage.BRIEFING.value},
    )
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_29_the_actor_decides_what_is_offered(world: WebWorld) -> None:
    """Two people, one item, two different lists.

    The lead holds ``PR_TEAM_LEAD_REVIEW``; the head does not, at this gate. The
    endpoint reports each person's own answer, which is the whole reason a
    browser must not compute it: the browser knows the stage, not the grants.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _drive_to_team_lead_gate(world, content_id)

    world.act_as(world.lead)
    lead_actions = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
        "available_actions"
    ]
    assert {action["decision"] for action in lead_actions if action["action"] == "APPROVAL"} == {
        decision.value for decision in PrApprovalDecision
    }

    world.act_as(world.head)
    head_actions = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
        "available_actions"
    ]
    # No grant for *this* gate, so nothing to decide - the same answer
    # ``PrApprovalService`` would give, arrived at before anybody presses.
    assert [action for action in head_actions if action["action"] == "APPROVAL"] == []

    world.act_as(world.outsider)
    outsider = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()
    # An EMPLOYEE may read PR data and may not **move** it, so nothing that
    # touches the workflow is offered - no transition, no decision, no edit -
    # rather than a row of buttons that 403.
    #
    # Step 1F.2.3g added the two things a reader *may* do without moving
    # anything: record a derivative, and say something. The list is therefore
    # exactly those two rather than empty, and asserting the set rather than
    # emptiness is what keeps this test meaningful - an offer that started
    # reaching an outsider by accident would still fail here.
    assert {action["action"] for action in outsider["available_actions"]} == {
        "ADD_CONTENT_DERIVATIVE",
        "ADD_CONTENT_COMMENT",
    }


@pytest.mark.asyncio
async def test_30_cancellation_is_classified_apart_from_the_forward_move(
    world: WebWorld,
) -> None:
    """``DANGER`` is presentation metadata, and it is on the right actions.

    It grants nothing: the emphasis exists so a client can keep "Hủy nội dung"
    out of the row that contains "Chuyển sang Brief", and the write path checks
    a ``DANGER`` action exactly as it checks a ``PRIMARY`` one.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    actions = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
        "available_actions"
    ]
    by_target = {action["target_stage"]: action["emphasis"] for action in actions}
    assert by_target[PrWorkflowStage.CANCELLED.value] == "DANGER"
    assert by_target[PrWorkflowStage.BRIEFING.value] == "PRIMARY"
    # Ordered so a client rendering the list in order gets the forward path
    # first and the destructive actions last.
    emphases = [action["emphasis"] for action in actions]
    assert emphases == sorted(emphases, key=["PRIMARY", "SECONDARY", "DANGER"].index)


@pytest.mark.asyncio
async def test_31_reading_available_actions_mutates_nothing(world: WebWorld) -> None:
    """Asking what you may do is not a thing that does anything.

    No stage change, no version, no approval and no audit row. A read model that
    wrote would be the worst possible bug here, because every screen calls it on
    every load.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    before = await world.session.get(PrContentItem, content_id)
    assert before is not None
    stage_before, updated_before = before.workflow_stage, before.updated_at
    events_before = len((await world.session.execute(select(AuditLog))).scalars().all())

    for _ in range(3):
        assert (
            world.client.get(f"/api/pr/contents/{content_id}/available-actions").status_code == 200
        )

    after = await world.session.get(PrContentItem, content_id)
    assert after is not None
    assert after.workflow_stage is stage_before
    assert after.updated_at == updated_before
    assert len((await world.session.execute(select(AuditLog))).scalars().all()) == events_before


def test_32_the_action_read_model_restates_no_rule() -> None:
    """It imports every table it consults, and owns none of them.

    The failure this guards against is the one the whole step could have made:
    moving the matrix out of the browser and into a second Python module that
    looks authoritative and drifts. ``pr_action_service.py`` must reach for
    ``allowed_content_targets``, ``STAGE_APPROVAL_GATES``, ``APPROVAL_OUTCOMES``
    and ``EDITABLE_STAGES`` rather than listing stages of its own - and it must
    ask the same ``capability_for_target`` the write path asks.

    Step 1F.2.7 replaced its ``APPROVAL_CAPABILITIES`` lookup with
    ``can_approve``, which is a stronger form of the same rule: the read model
    now asks the *whole* approval question - gate, grant and scope - of the one
    service the write asks, rather than looking a capability up and asking a
    narrower one.
    """
    path = SRC / "application" / "pr_action_service.py"
    source = path.read_text(encoding="utf-8")
    for imported in (
        "allowed_content_targets",
        "STAGE_APPROVAL_GATES",
        "APPROVAL_OUTCOMES",
        "EDITABLE_STAGES",
        "capability_for_target",
    ):
        assert imported in source, imported
    assert "APPROVAL_CAPABILITIES" not in source
    assert "can_approve(actor, content, gate, on=on)" in source

    executable = _executable_source(path)
    # No literal stage pairs, and no writes of any kind.
    assert "PrWorkflowStage.BRIEFING" not in executable
    assert "PrWorkflowStage.TEAM_LEAD_REVIEW" not in executable
    assert not re.search(r"\.workflow_stage\s*=(?!=)", executable)
    for forbidden in (".commit(", ".flush(", "session"):
        assert forbidden not in executable, forbidden

    # And the capability question has exactly one implementation.
    workflow = _executable_source(SRC / "application" / "pr_workflow_service.py")
    assert "def capability_for_target" in workflow
    assert workflow.count("PrCapability . PR_CONTENT_CANCEL") == 1


# --- 33-40: Step 1E.2.1, only actions that actually work ---------------------
#
# Step 1E.2 filtered the action list by the matrix and the capability, and left
# three prerequisites to be discovered by pressing the button. These prove the
# list is now a list of things that work - and, in 38, that it says so using the
# write path's own checks rather than a copy of them.


@pytest.mark.asyncio
async def test_33_the_retired_measured_stage_is_offered_nowhere(
    world: WebWorld,
) -> None:
    """Step 1F.2.3f.5. ``PUBLISHED -> MEASURED`` is no longer an edge at all.

    It used to be a legal edge with an unmet precondition, and this pair of
    tests checked that the panel offered it exactly when the write would accept
    it. There is now nothing to offer: the stage is retired, publication is
    where a piece stays, and archiving is the one step available from it.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world, with_target=True)
    await _publish(world, content_id)

    body = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()
    assert body["workflow_stage"] == PrWorkflowStage.PUBLISHED.value
    targets = {action["target_stage"] for action in body["available_actions"]}
    assert PrWorkflowStage.MEASURED.value not in targets
    # And the step that replaced it is there, so publication is not a dead end.
    assert PrWorkflowStage.ARCHIVED.value in targets


@pytest.mark.asyncio
async def test_34_a_stale_client_asking_for_measured_is_told_it_is_retired(
    world: WebWorld,
) -> None:
    """Part Z. A specific refusal, and never a silent remap.

    A browser that rendered *Đã đo hiệu quả* before this shipped will still send
    the transition. It gets a reason naming the retirement rather than a generic
    "cannot move" - and the content does not move, which is the half that
    matters: guessing at the intention would write a lifecycle event nobody
    asked for.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world, with_target=True)
    await _publish(world, content_id)

    refused = world.client.post(
        f"/api/pr/contents/{content_id}/transition",
        json={"target_stage": PrWorkflowStage.MEASURED.value},
    )
    assert refused.status_code == 409
    details = refused.json()["error"]["details"]
    assert details["reason"] == "measured_stage_retired"
    assert PrWorkflowStage.ARCHIVED.value in details["allowed"]

    after = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()
    assert after["workflow_stage"] == PrWorkflowStage.PUBLISHED.value


@pytest.mark.asyncio
async def test_35_no_approval_is_offered_without_a_gating_ai_verdict(world: WebWorld) -> None:
    """A draft nothing has machine-checked offers no decision at all.

    Not only "no approve": ``PrApprovalService`` checks the AI gate before it
    looks at the decision, so requesting revision and rejecting are refused
    too, and the action list withholds all three.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _drive_to_team_lead_gate(world, content_id)

    # A rewrite lands a draft the FULL_REVIEW on file does not describe. The
    # verdict is still findable for version 1; it says nothing about version 2.
    await _force_stage(world, content_id, PrWorkflowStage.SCRIPTING)
    assert (
        world.client.post(
            f"/api/pr/contents/{content_id}/versions",
            json={"expected_version": 1, "script_text": "Bản viết lại."},
        ).status_code
        == 201
    )
    await _force_stage(world, content_id, PrWorkflowStage.TEAM_LEAD_REVIEW)

    actions = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
        "available_actions"
    ]
    assert [action for action in actions if action["action"] == "APPROVAL"] == []

    # The write refuses the same thing, for the reason the read model withheld it.
    refused = world.client.post(
        f"/api/pr/contents/{content_id}/reviews",
        json={"decision": "APPROVED", "version_reviewed": 2},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "pr_ai_review_required"


@pytest.mark.asyncio
async def test_36_a_non_gating_verdict_does_not_open_the_gate(world: WebWorld) -> None:
    """A ``BRAND_TONE`` review is advice. Only ``FULL_REVIEW`` is the gate.

    The insufficient-verdict case: a review exists for this exact draft, and the
    rule still says no because its ``review_type`` is not the gating one.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _drive_to_team_lead_gate(world, content_id)

    await _force_stage(world, content_id, PrWorkflowStage.SCRIPTING)
    world.client.post(
        f"/api/pr/contents/{content_id}/versions",
        json={"expected_version": 1, "script_text": "Bản viết lại."},
    )
    # Recorded against the new draft, but not the gating type.
    world.client.post(
        f"/api/pr/contents/{content_id}/submit-ai-review",
        json={
            "reviewed_version": 2,
            "review_type": "BRAND_TONE",
            "result": "PASS",
            "model_name": "gpt-x",
            "prompt_version": "tone-v1",
        },
    )
    await _force_stage(world, content_id, PrWorkflowStage.TEAM_LEAD_REVIEW)

    actions = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
        "available_actions"
    ]
    assert [action for action in actions if action["action"] == "APPROVAL"] == []


@pytest.mark.asyncio
async def test_37_the_gate_opens_for_an_authorized_actor_with_a_verdict(
    world: WebWorld,
) -> None:
    """All three decisions, for the person who holds the grant - and nobody else.

    The positive case for 35 and 36, and the authorization case in one: the same
    item, at the same stage, with the same verdict on file, offers the lead
    everything and the employee nothing.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _drive_to_team_lead_gate(world, content_id)

    offered = {
        action["decision"]
        for action in world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
            "available_actions"
        ]
        if action["action"] == "APPROVAL"
    }
    assert offered == {decision.value for decision in PrApprovalDecision}
    # And it works, which is the only proof that matters.
    assert (
        world.client.post(
            f"/api/pr/contents/{content_id}/reviews",
            json={"decision": "APPROVED", "version_reviewed": 1},
        ).status_code
        == 201
    )


@pytest.mark.asyncio
async def test_38_an_unauthorized_actor_is_offered_nothing_at_the_gate(
    world: WebWorld,
) -> None:
    """A satisfied AI gate does not hand the item to somebody without the grant."""
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _drive_to_team_lead_gate(world, content_id)

    for person in (world.head, world.outsider):
        world.act_as(person)
        actions = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
            "available_actions"
        ]
        assert [action for action in actions if action["action"] == "APPROVAL"] == [], (
            person.full_name
        )


@pytest.mark.asyncio
async def test_39_the_head_gate_offers_duyet_to_the_team_lead_approver_who_holds_the_grant(
    world: WebWorld,
) -> None:
    """Step 1F.2.2, requirement 7. The inverse of what this test used to assert.

    Until 1F.2.2 the read model withheld "Duyệt" at the head gate from whoever
    gave the team-lead approval, because the write would have refused it. The
    write no longer refuses it, so the offer is back - and the offer is
    *followed through*: the same actor's head approval is accepted and the
    content reaches ``APPROVED``.

    The grant is what makes it legal, and it is granted here rather than assumed.
    ``PR_HEAD_REVIEW`` on top of the lead's existing ``PR_TEAM_LEAD_REVIEW`` is
    the whole precondition; test 38 covers the actor who holds neither.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _drive_to_team_lead_gate(world, content_id)
    assert (
        world.client.post(
            f"/api/pr/contents/{content_id}/reviews",
            json={"decision": "APPROVED", "version_reviewed": 1},
        ).status_code
        == 201
    )

    # Without the head grant, the head gate offers this person nothing - the
    # capability check is untouched by the step and is asserted before the grant
    # so the pass below cannot be mistaken for role inheritance.
    world.act_as(world.lead)
    assert [
        action
        for action in world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
            "available_actions"
        ]
        if action["action"] == "APPROVAL"
    ] == []

    # Now give the same person the head grant. Role ADMIN is not needed: the
    # lead already holds SCRIPT_APPROVE by role.
    services = build_pr_services(world.session, world.settings)
    await services.capabilities.grant(
        actor=Actor(user_id=world.head.id, full_name=world.head.full_name, role=Role.OWNER),
        request_id=uuid.uuid4(),
        user_id=world.lead.id,
        capability=PrCapability.PR_HEAD_REVIEW,
    )

    world.act_as(world.lead)
    offered = {
        action["decision"]
        for action in world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
            "available_actions"
        ]
        if action["action"] == "APPROVAL"
    }
    assert offered == {
        PrApprovalDecision.APPROVED.value,
        PrApprovalDecision.REVISION_REQUIRED.value,
        PrApprovalDecision.REJECTED.value,
    }

    accepted = world.client.post(
        f"/api/pr/contents/{content_id}/reviews",
        json={"decision": "APPROVED", "version_reviewed": 1},
    )
    assert accepted.status_code == 201
    assert accepted.json()["content"]["workflow_stage"] == PrWorkflowStage.APPROVED.value

    # Two events, from one person, one per gate. Requirement 3 over HTTP.
    history = world.client.get(f"/api/pr/contents/{content_id}/approvals").json()
    assert [(row["approval_stage"], row["reviewer_user_id"]) for row in history] == [
        ("TEAM_LEAD_REVIEW", str(world.lead.id)),
        ("HEAD_REVIEW", str(world.lead.id)),
    ]
    assert len({row["id"] for row in history}) == 2

    # And the head, who signed nothing, would have been offered it too.
    world.act_as(world.head)
    assert (
        world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()[
            "workflow_stage"
        ]
        == PrWorkflowStage.APPROVED.value
    )


@pytest.mark.asyncio
async def test_40_asking_what_is_possible_still_writes_nothing(world: WebWorld) -> None:
    """The prerequisite queries added in 1E.2.1 are reads, at a real gate.

    Test 31 covers an item at ``IDEA``, where none of the new checks run. This
    is the same assertion where all of them do.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _drive_to_team_lead_gate(world, content_id)

    before = await world.session.get(PrContentItem, content_id)
    assert before is not None
    stage_before = before.workflow_stage
    approvals_before = len((await world.session.execute(select(PrApprovalEvent))).scalars().all())
    reviews_before = len((await world.session.execute(select(PrAiReview))).scalars().all())
    events_before = len((await world.session.execute(select(AuditLog))).scalars().all())

    for _ in range(3):
        assert (
            world.client.get(f"/api/pr/contents/{content_id}/available-actions").status_code == 200
        )

    after = await world.session.get(PrContentItem, content_id)
    assert after is not None
    assert after.workflow_stage is stage_before
    assert len((await world.session.execute(select(PrApprovalEvent))).scalars().all()) == (
        approvals_before
    )
    assert len((await world.session.execute(select(PrAiReview))).scalars().all()) == reviews_before
    assert len((await world.session.execute(select(AuditLog))).scalars().all()) == events_before


def test_41_the_read_and_write_paths_share_one_prerequisite_implementation() -> None:
    """The rules are asked, not restated.

    The failure this guards against is the obvious way to have implemented
    1E.2.1: an ``if a snapshot exists`` and an ``if a FULL_REVIEW exists`` in the
    read model, looking correct and drifting the first time either rule moved.

    Each predicate is the write path's own check with the refusal caught - the
    pattern ``PrCapabilityService.allows`` established - so the two paths cannot
    disagree.
    """
    action_source = _executable_source(SRC / "application" / "pr_action_service.py")
    # ``is_measurable`` was the third of these and went with the stage it gated -
    # Step 1F.2.3f.5. The property is unchanged for the two that remain: the read
    # model asks the write path's own check rather than restating the rule.
    for asked in ("ai_gate_satisfied", "head_approval_permitted"):
        assert asked in action_source, asked
    # None of the underlying rules is re-implemented here. Matched on whole
    # words, and the model names carry a negative lookahead for ``Service``:
    # Step 1F.2.3f.2 has this file *ask* ``PrPublicationService`` whether a
    # contributor may record a posting, which is the pattern the test exists to
    # encourage - while naming ``PrPublication``, the table, would still be the
    # re-implementation it exists to forbid.
    for restated in (
        r"PrPostMetricSnapshot\b",
        r"PrPublication\b(?!Service)",
        r"PrAiReview\b(?!Service|Run)",
        r"latest_gating_review\b",
        r"AI_GATED_APPROVAL_STAGES\b",
        r"successful_team_lead_approval\b",
        r"\bexists\b",
        r"\bselect\b",
    ):
        assert re.search(restated, action_source) is None, restated

    # And each predicate delegates rather than running its own query. Each
    # raising check is named exactly three times: where it is defined, where the
    # write path calls it, and where the boolean calls it.
    workflow = _executable_source(SRC / "application" / "pr_workflow_service.py")
    approvals = _executable_source(SRC / "application" / "pr_approval_service.py")
    assert approvals.count("_require_ai_gate") == 3
    assert approvals.count("_require_prior_team_lead_approval") == 3
    # The queries behind them exist once each, in the raising check.
    assert approvals.count("latest_gating_review") == 1
    # Step 1F.2.3f.5. ``_require_measurable`` and its snapshot query went with
    # the stage they gated, and the absence is asserted rather than assumed: a
    # leftover metric query in the workflow service would be a rule with nothing
    # calling it, which is how a retired stage quietly comes back.
    assert "_require_measurable" not in workflow
    assert "PrPostMetricSnapshot" not in workflow


# --- 42-44: Step 1E.2.1, brands by name -------------------------------------


@pytest.mark.asyncio
async def test_42_active_brands_are_listed_by_name(world: WebWorld) -> None:
    """The picker's data: an id, a code and a name, and nothing else.

    ``status`` is deliberately absent from the response. The route already
    decided which brands may be chosen, and a client that received the status
    would be one refactor away from filtering on it.
    """
    world.act_as(world.lead)
    body = world.client.get("/api/pr/brands").json()

    assert [row["name"] for row in body] == ["Web Brand"]
    assert set(body[0]) == {"id", "code", "name"}
    assert body[0]["code"] == "BRND-W"
    assert body[0]["id"] == str(world.brand_id)


@pytest.mark.asyncio
async def test_43_a_retired_brand_is_not_offered_for_selection(world: WebWorld) -> None:
    """``INACTIVE`` means retired, and a picker must not undo that.

    Rows are never deleted here - archived work points at them - so retiring is
    the only way to stop a brand being used, and listing it anyway would defeat
    it one new content item at a time.
    """
    world.act_as(world.lead)
    retired = PrBrand(code="BRND-OLD", name="Thương hiệu cũ", status=PrEntityStatus.INACTIVE)
    world.session.add(retired)
    await world.session.flush()

    names = [row["name"] for row in world.client.get("/api/pr/brands").json()]
    assert "Web Brand" in names
    assert "Thương hiệu cũ" not in names


@pytest.mark.asyncio
async def test_44_the_brand_list_needs_a_session(world: WebWorld) -> None:
    """Who a department works for is not public.

    Guarded by the same read permission as every other list, through
    ``PrQueryService`` - so this is not an open directory of clients.
    """
    world.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
    assert world.client.get("/api/pr/brands").status_code == 401


@pytest.mark.asyncio
async def test_45_the_detail_response_names_the_brand_and_the_channels(
    world: WebWorld,
) -> None:
    """ "Apexmed · Facebook", server-side, without an N+1.

    One ``get`` for the brand and one ``IN`` for the targets' channels, however
    many there are - which is why the enrichment lives in the query service
    rather than in a loop over targets.
    """
    world.act_as(world.lead)
    created = world.client.post(
        "/api/pr/contents",
        json={
            "title": "Bài có kênh",
            "brand_id": str(world.brand_id),
            "owner_user_id": str(world.lead.id),
            # Step 1F.2.3e: the web create route requires a format.
            "content_type": "SHORT_VIDEO_SCRIPT",
            "targets": [{"channel_id": str(world.channel_id)}],
        },
    )
    assert created.status_code == 201, created.text

    body = created.json()
    assert body["brand"]["name"] == "Web Brand"
    assert body["brand"]["code"] == "BRND-W"
    assert [target["channel_name"] for target in body["targets"]] == ["Web Channel"]
    # The id is still there - it is what a write sends - but it is no longer the
    # only thing a screen could show.
    assert body["targets"][0]["channel_id"] == str(world.channel_id)


# --- 46-51: Step 1F, the AI review panel's API ------------------------------


async def _at_ai_review(world: WebWorld, content_id: uuid.UUID) -> None:
    """Walk one item to ``AI_REVIEW`` through the real transitions."""
    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        assert (
            world.client.post(
                f"/api/pr/contents/{content_id}/transition", json={"target_stage": stage.value}
            ).status_code
            == 200
        ), stage.value


@pytest.mark.asyncio
async def test_46_the_panel_sees_the_queued_run(world: WebWorld) -> None:
    """Entering ``AI_REVIEW`` through the web queues a run the web can see.

    The whole Step 1F loop from a browser's point of view: transition, then the
    panel has something to poll.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _at_ai_review(world, content_id)

    body = world.client.get(f"/api/pr/contents/{content_id}/ai-review").json()
    assert body["active"] is True
    assert body["run"]["status"] == "QUEUED"
    assert body["run"]["trigger"] == "AUTO"
    assert body["run"]["attempt_count"] == 0
    assert body["review"] is None
    # Nothing to retry while something is already going to happen.
    assert body["can_retry"] is False


@pytest.mark.asyncio
async def test_47_a_failed_run_exposes_a_code_and_never_provider_internals(
    world: WebWorld,
) -> None:
    """What a browser is allowed to know about a failure.

    A stable machine string it renders its own sentence from - never a provider
    message, a URL or a traceback.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _at_ai_review(world, content_id)

    run = (
        (
            await world.session.execute(
                select(PrAiReviewRun).where(PrAiReviewRun.content_id == content_id)
            )
        )
        .scalars()
        .one()
    )
    run.status = PrAiReviewRunStatus.FAILED
    run.started_at = utcnow()
    run.finished_at = utcnow()
    run.error_code = "llm_error"
    await world.session.flush()

    body = world.client.get(f"/api/pr/contents/{content_id}/ai-review").json()
    assert body["active"] is False
    assert body["run"]["status"] == "FAILED"
    assert body["run"]["error_code"] == "llm_error"
    # The whole response, checked: nothing that looks like provider internals.
    serialised = json.dumps(body)
    for leak in ("Traceback", "http://", "https://", "api_key", "Bearer"):
        assert leak not in serialised, leak
    # And a person who may record AI reviews is offered the retry.
    assert body["can_retry"] is True


@pytest.mark.asyncio
async def test_48_retry_queues_one_run_and_refuses_a_second(world: WebWorld) -> None:
    """The double-tap case, refused by the server rather than by a disabled button."""
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _at_ai_review(world, content_id)
    run = (
        (
            await world.session.execute(
                select(PrAiReviewRun).where(PrAiReviewRun.content_id == content_id)
            )
        )
        .scalars()
        .one()
    )
    run.status = PrAiReviewRunStatus.FAILED
    run.started_at = utcnow()
    run.finished_at = utcnow()
    await world.session.flush()

    first = world.client.post(f"/api/pr/contents/{content_id}/ai-review/retry")
    assert first.status_code == 202, first.text
    assert first.json()["run"]["trigger"] == "MANUAL_RETRY"
    assert first.json()["active"] is True

    # The second press, with one already queued.
    second = world.client.post(f"/api/pr/contents/{content_id}/ai-review/retry")
    assert second.status_code == 409
    runs = (
        (
            await world.session.execute(
                select(PrAiReviewRun).where(PrAiReviewRun.content_id == content_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(list(runs)) == 2  # the failed one, and exactly one retry


@pytest.mark.asyncio
async def test_49_retry_is_refused_once_the_content_has_moved_on(world: WebWorld) -> None:
    """A review that could gate nothing is not queued."""
    world.act_as(world.lead)
    content_id = await _make_content(world)
    # Still at IDEA: never entered AI_REVIEW at all.
    refused = world.client.post(f"/api/pr/contents/{content_id}/ai-review/retry")
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["required"] == PrWorkflowStage.AI_REVIEW.value

    body = world.client.get(f"/api/pr/contents/{content_id}/ai-review").json()
    assert body["can_retry"] is False
    assert body["run"] is None


@pytest.mark.asyncio
async def test_50_an_unauthorized_actor_cannot_ask_for_a_review(world: WebWorld) -> None:
    """``SCRIPT_REVIEW`` - the permission recording an AI review has always taken.

    **No new capability was added for Step 1F.** An EMPLOYEE may read PR data
    and may not cause a review to run, and the refusal comes from the service.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _at_ai_review(world, content_id)

    world.act_as(world.outsider)
    refused = world.client.post(f"/api/pr/contents/{content_id}/ai-review/retry")
    assert refused.status_code == 403
    # And the panel does not offer them the button either.
    assert world.client.get(f"/api/pr/contents/{content_id}/ai-review").json()["can_retry"] is False


@pytest.mark.asyncio
async def test_51_the_panel_is_scoped_to_the_draft_on_screen(world: WebWorld) -> None:
    """A finished run about an old draft is not the state of the new one.

    The read-side half of the version pin: after a rewrite the panel reports
    "no run for this draft" rather than presenting the previous draft's verdict
    as though it applied.
    """
    world.act_as(world.lead)
    content_id = await _make_content(world)
    await _at_ai_review(world, content_id)
    run = (
        (
            await world.session.execute(
                select(PrAiReviewRun).where(PrAiReviewRun.content_id == content_id)
            )
        )
        .scalars()
        .one()
    )
    run.status = PrAiReviewRunStatus.SUCCEEDED
    run.started_at = utcnow()
    run.finished_at = utcnow()
    await world.session.flush()

    # Back to SCRIPTING and rewritten.
    await _force_stage(world, content_id, PrWorkflowStage.SCRIPTING)
    assert (
        world.client.post(
            f"/api/pr/contents/{content_id}/versions",
            json={"expected_version": 1, "script_text": "Bản viết lại."},
        ).status_code
        == 201
    )

    body = world.client.get(f"/api/pr/contents/{content_id}/ai-review").json()
    assert body["run"] is None
    assert body["review"] is None
    assert body["active"] is False
