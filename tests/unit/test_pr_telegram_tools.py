"""Step 1D: the Telegram PR tools, exercised through the real tool interface.

Tools are executed the way
:meth:`~meobot.application.conversation_service.ConversationService._run_tool`
executes them - :meth:`ToolDefinition.execute` with raw argument dicts, on a
:class:`~meobot.tools.base.ToolContext` carrying a session the caller owns.
That is the whole point: if a handler needed anything the conversation pipeline
does not supply, these tests would not run.

The offline database is the shared in-memory SQLite fixture, for the reason
``tests/unit/test_pr_application_services.py`` gives - the rules under test here
are routing, resolution and rendering, and none of them needs PostgreSQL.

The structural tests at the bottom are the ones worth keeping longest. They do
not test behaviour; they assert that no future Telegram handler can quietly
acquire business logic of its own.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_ai_review_service import PrAiReviewService, RecordAiReviewCommand
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_services import build_pr_services
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.config import Settings, get_settings
from meobot.core.errors import ToolExecutionError
from meobot.db.models.pr import PrApprovalEvent, PrBrand, PrChannel, PrPlatform
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.policy.models import RiskLevel
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrChannelCategory,
    PrContentType,
    PrProductionArtifactType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.tools.base import ToolContext, ToolDefinition, ToolRegistry
from meobot.tools.registry import build_default_registry
from tests.fakes import StubHealthService

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    """The real registry the bot builds, PR tools included."""
    return build_default_registry(health_service=StubHealthService())  # type: ignore[arg-type]


@dataclass
class ToolWorld:
    """A seeded database plus the machinery to run a tool against it."""

    session: AsyncSession
    settings: Settings
    registry: ToolRegistry
    owner: Actor
    head: Actor
    employee: Actor
    owner_id: uuid.UUID
    head_id: uuid.UUID
    #: A ready-made planned channel. Since Step 1F.2 content with no planned
    #: channel cannot reach ``AI_REVIEW``, so most of these tools need one.
    channel_code: str

    def context(self, actor: Actor | None = None) -> ToolContext:
        """A context shaped exactly like the conversation pipeline's."""
        return ToolContext(
            actor=actor or self.owner,
            request_id=uuid.uuid4(),
            settings=self.settings,
            session=self.session,
        )

    async def run(self, tool_name: str, /, *, actor: Actor | None = None, **arguments: object):  # type: ignore[no-untyped-def]
        """Run one tool.

        ``tool_name`` is positional-only and ``actor`` keyword-only so that a
        tool argument called ``name`` - ``pr.channel.create`` has one - cannot
        collide with this helper's own parameters.
        """
        tool: ToolDefinition = self.registry.get(tool_name)
        return await tool.execute(self.context(actor), arguments)


@pytest_asyncio.fixture
async def world(session: AsyncSession, registry: ToolRegistry) -> AsyncIterator[ToolWorld]:
    """Three people, a brand, a platform - and no review grants at all."""
    owner = User(full_name="Chi Owner", role=Role.OWNER)
    head = User(full_name="Hoa Head", role=Role.ADMIN)
    worker = User(full_name="Linh Nhan Vien", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-APEX", name="Apexmed")
    platform = PrPlatform(code="PLAT-TIKTOK", name="TikTok")
    session.add_all([owner, head, worker, brand, platform])
    await session.flush()
    channel = PrChannel(
        code="CH-TG",
        name="Kênh mặc định",
        category=PrChannelCategory.SCALE,
        platform_id=platform.id,
        brand_id=brand.id,
    )
    session.add(channel)
    await session.flush()

    yield ToolWorld(
        session=session,
        settings=get_settings(),
        registry=registry,
        owner=Actor(user_id=owner.id, full_name="Chi Owner", role=Role.OWNER),
        head=Actor(user_id=head.id, full_name="Hoa Head", role=Role.ADMIN),
        employee=Actor(user_id=worker.id, full_name="Linh Nhan Vien", role=Role.EMPLOYEE),
        owner_id=owner.id,
        head_id=head.id,
        channel_code=channel.code,
    )


# --- Helpers ---------------------------------------------------------------


async def grant(world: ToolWorld, actor: Actor, person: str, capability: str) -> None:
    await world.run("pr.capability.grant", actor=actor, person=person, capability=capability)


async def make_channel(world: ToolWorld) -> str:
    result = await world.run(
        "pr.channel.create", name="TikTok Apexmed", platform="TikTok", brand="Apexmed"
    )
    return str(result.data["code"])


async def make_content(world: ToolWorld, *, channels: list[str] | None = None) -> str:
    """Create a draft, planned on the fixture channel unless told otherwise.

    Defaulting to a channel rather than to none is Step 1F.2: a draft with no
    planned channel is a legitimate thing to create, but it cannot enter
    ``AI_REVIEW``, so a helper used to walk content through the workflow has to
    plan one. Pass ``channels=[]`` explicitly to test the targetless case.
    """
    result = await world.run(
        "pr.content.create",
        brand="Apexmed",
        title="Chăm sóc sau nâng mũi",
        script_text="Hook. Body. CTA.",
        # Step 1F.2.3e: the Telegram tool is a human-facing create path, so it
        # requires a format exactly as the web form does.
        content_type="SHORT_VIDEO_SCRIPT",
        channels=[world.channel_code] if channels is None else channels,
    )
    return str(result.data["code"])


async def advance(world: ToolWorld, code: str, *stages: PrWorkflowStage) -> None:
    for stage in stages:
        await world.run("pr.content.transition", content=code, target=stage.value)


async def to_internal_review(world: ToolWorld, code: str) -> None:
    """Past the handoff and the cut, to a reviewer.

    Step 1F.2.3b gave each of the two moves after approval a precondition -
    somebody holding the production, and a file to review - so this sets both
    and then makes the moves through the ordinary tool. Written here rather than
    driven through the production service because Step 1D has no tool for either
    and these tests are about the *review* tools.
    """
    services = build_pr_services(world.session, world.settings)
    content = await services.queries.get_content_by_code(actor=world.owner, code=code)
    content.producer_user_id = world.owner_id
    version = await services.content.require_current_version(content.id)
    world.session.add(
        PrProductionSubmission(
            content_id=content.id,
            content_version_id=version.id,
            submission_no=1,
            producer_user_id=world.owner_id,
            submitted_by_user_id=world.owner_id,
            artifact_type=PrProductionArtifactType.DRIVE_LINK,
            location="https://drive.google.com/file/d/1/view",
        )
    )
    await world.session.flush()
    await advance(world, code, PrWorkflowStage.PRODUCTION, PrWorkflowStage.INTERNAL_REVIEW)


async def record_ai_pass(
    world: ToolWorld, code: str, *, result: PrAiReviewResult = PrAiReviewResult.PASS
) -> None:
    """Write a legitimate AI verdict *directly through the service*.

    Step 1D has no tool that produces one, and deliberately so. These tests
    need content past the AI gate, so they use the application service the way
    Step 1F eventually will - which also keeps the "no tool fabricates a
    verdict" assertion honest.
    """
    audit = AuditService(world.session)
    capabilities = PrCapabilityService(world.session, audit)
    codes = PrCodeService(world.session, world.settings)
    content_service = PrContentService(world.session, audit, capabilities, codes)
    workflow = PrContentWorkflowService(world.session, audit, capabilities)
    reviews = PrAiReviewService(world.session, audit, content_service, workflow)

    content = await content_service.require_content((await _content_by_code(world, code)).id)
    version = await content_service.require_current_version(content.id)
    await reviews.record_review(
        actor=world.owner,
        request_id=uuid.uuid4(),
        command=RecordAiReviewCommand(
            content_id=content.id,
            reviewed_version=version.version_no,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=result,
            score=Decimal("84.00"),
            summary="Hai điểm cần lưu ý.",
            issues=[{"code": "HOOK", "severity": "WARNING", "message": "Hook hơi nhạt."}],
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=NOW,
        ),
    )


async def _content_by_code(world: ToolWorld, code: str):  # type: ignore[no-untyped-def]
    from meobot.db.models.pr import PrContentItem

    result = await world.session.execute(select(PrContentItem).where(PrContentItem.code == code))
    return result.scalars().one()


async def count(session: AsyncSession, model: type) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# ===========================================================================
# CONTENT
# ===========================================================================


async def test_create_content_returns_a_generated_code(world: ToolWorld) -> None:
    """Requirements 1 and 2."""
    result = await world.run(
        "pr.content.create",
        brand="Apexmed",
        title="Chăm sóc sau nâng mũi",
        content_type="SHORT_VIDEO_SCRIPT",
    )
    assert result.success
    code = str(result.data["code"])
    assert code.startswith("CNT-2")
    assert code in result.message
    assert result.data["workflow_stage"] == PrWorkflowStage.IDEA.value
    assert result.data["version_no"] == 1


async def test_no_creation_tool_accepts_a_code_argument(world: ToolWorld) -> None:
    """Requirement 3. The schema forbids it, so a model cannot supply one."""
    for name in (
        "pr.content.create",
        "pr.task.create",
        "pr.channel.create",
        "pr.publication.register",
    ):
        schema = world.registry.get(name).arguments_model.model_json_schema()
        assert "code" not in schema.get("properties", {}), name

    with pytest.raises(Exception):  # noqa: B017 - ToolArgumentError from extra="forbid"
        await world.run("pr.content.create", brand="Apexmed", title="X", code="CNT-2026-000999")


async def test_get_content_renders_stage_and_version(world: ToolWorld) -> None:
    """Requirement 4."""
    code = await make_content(world)
    result = await world.run("pr.content.get", content=code)
    assert code in result.message
    assert result.data["workflow_stage"] == PrWorkflowStage.IDEA.value
    assert result.data["version_no"] == 1


async def test_revise_passes_the_current_version(world: ToolWorld) -> None:
    """Requirement 5."""
    code = await make_content(world)
    result = await world.run("pr.content.revise", content=code, title="Bản hai")
    assert result.data["version_no"] == 2

    detail = await world.run("pr.content.get", content=code)
    assert detail.data["version_no"] == 2


async def test_a_stale_version_is_explained_not_retried(world: ToolWorld) -> None:
    """Requirement 6. The sentence names both versions, and nothing retries."""
    code = await make_content(world)
    await world.run("pr.content.revise", content=code, title="Bản hai")

    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.content.revise", content=code, title="Bản ba", expected_version=1)
    assert "v2" in raised.value.message
    assert "v1" in raised.value.message
    assert raised.value.details["pr_error_code"] == "pr_stale_version"

    # And no third version was written.
    detail = await world.run("pr.content.get", content=code)
    assert detail.data["version_no"] == 2


async def test_a_valid_manual_transition_works(world: ToolWorld) -> None:
    """Requirement 7."""
    code = await make_content(world)
    result = await world.run(
        "pr.content.transition", content=code, target=PrWorkflowStage.BRIEFING.value
    )
    assert result.data["workflow_stage"] == PrWorkflowStage.BRIEFING.value


async def test_telegram_cannot_force_the_review_gates(world: ToolWorld) -> None:
    """Requirement 8. The refusal comes from the workflow policy, not the tool."""
    code = await make_content(world)
    await advance(world, code, PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING)
    await world.run("pr.ai_review.submit", content=code)

    for target in (
        PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrWorkflowStage.HEAD_REVIEW,
        PrWorkflowStage.APPROVED,
    ):
        with pytest.raises(ToolExecutionError) as raised:
            await world.run("pr.content.transition", content=code, target=target.value)
        assert raised.value.details["pr_error_code"] == "pr_invalid_transition"

    detail = await world.run("pr.content.get", content=code)
    assert detail.data["workflow_stage"] == PrWorkflowStage.AI_REVIEW.value


async def test_an_unknown_content_reference_asks_rather_than_guesses(world: ToolWorld) -> None:
    await make_content(world)
    await world.run(
        "pr.content.create",
        brand="Apexmed",
        title="Chăm sóc sau cắt mí",
        content_type="SHORT_VIDEO_SCRIPT",
    )
    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.content.get", content="Chăm sóc")
    assert raised.value.details["reason"] == "ambiguous_content"
    assert len(raised.value.details["candidates"]) == 2


# ===========================================================================
# AI HANDOFF
# ===========================================================================


async def test_submitting_to_ai_review_only_moves_the_stage(world: ToolWorld) -> None:
    """Requirements 19, 20 and 21, in one walk.

    The stage moves and **no ``pr_ai_reviews`` row appears** - the handler still
    calls no model and records no verdict, which is the part of this test that
    matters and is unchanged by Step 1F.

    What Step 1F changed is downstream: entering ``AI_REVIEW`` queues a durable
    run on the authoritative workflow path, and a worker produces the verdict
    later. So the message may now say a review is starting, because one is.
    """
    code = await make_content(world)
    await advance(world, code, PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING)

    before = await count(world.session, PrAiReview)
    result = await world.run("pr.ai_review.submit", content=code)

    assert result.data["workflow_stage"] == PrWorkflowStage.AI_REVIEW.value
    assert await count(world.session, PrAiReview) == before
    # A review is promised, and the verdict is not: the findings are a
    # screenful and the web panel is where they are read.
    assert "AI Review" in result.message
    assert "PR Admin" in result.message
    for forbidden in ("PASS", "REVISION_REQUIRED", "đang phân tích"):
        assert forbidden not in result.message


async def test_no_pr_tool_can_write_an_ai_review(world: ToolWorld) -> None:
    """Requirement 21, as a property of the tool set rather than one call."""
    ai_tools = [name for name in world.registry.names if name.startswith("pr.ai_review")]
    assert ai_tools == ["pr.ai_review.submit"]
    schema = world.registry.get("pr.ai_review.submit").arguments_model.model_json_schema()
    for field in ("result", "score", "summary", "issues", "model_name", "prompt_version"):
        assert field not in schema.get("properties", {}), field


# ===========================================================================
# REVIEW
# ===========================================================================


async def to_team_lead_review(world: ToolWorld, code: str) -> None:
    await advance(world, code, PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING)
    await world.run("pr.ai_review.submit", content=code)
    await record_ai_pass(world, code)


async def test_pending_review_uses_capabilities_not_roles(world: ToolWorld) -> None:
    """Requirements 9, 10 and 11.

    The owner holds every *permission* and still sees nothing until a grant
    exists - which is exactly what would break if this filtered on role.
    """
    code = await make_content(world)
    await to_team_lead_review(world, code)

    empty = await world.run("pr.review.pending")
    assert empty.data["count"] == 0
    assert code not in empty.message

    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")
    listed = await world.run("pr.review.pending")
    assert listed.data["count"] == 1
    assert code in listed.message
    assert listed.data["items"][0]["ai_result"] == PrAiReviewResult.PASS.value


async def test_a_head_reviewer_sees_only_head_review_content(world: ToolWorld) -> None:
    """Requirement 12."""
    code = await make_content(world)
    await to_team_lead_review(world, code)
    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")
    await grant(world, world.owner, "Hoa Head", "Head Review")

    # The head holds only the head gate, and the content is at team lead.
    head_view = await world.run("pr.review.pending", actor=world.head)
    assert head_view.data["count"] == 0

    await world.run("pr.review.approve", content=code)
    after = await world.run("pr.review.pending", actor=world.head)
    assert after.data["count"] == 1
    assert code in after.message


async def test_review_context_keeps_warnings_visible(world: ToolWorld) -> None:
    """Requirement 13. ``PASS_WITH_WARNINGS`` never renders as "passed"."""
    code = await make_content(world)
    await advance(world, code, PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING)
    await world.run("pr.ai_review.submit", content=code)
    await record_ai_pass(world, code, result=PrAiReviewResult.PASS_WITH_WARNINGS)

    result = await world.run("pr.review.context", content=code)
    assert "PASS WITH WARNINGS" in result.message
    assert "84" in result.message
    assert "Hook hơi nhạt." in result.message
    assert result.data["ai_has_warnings"] is True
    assert result.data["ai_result"] == PrAiReviewResult.PASS_WITH_WARNINGS.value


async def test_approve_records_an_approval_event(world: ToolWorld) -> None:
    """Requirement 14."""
    code = await make_content(world)
    await to_team_lead_review(world, code)
    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")

    before = await count(world.session, PrApprovalEvent)
    result = await world.run("pr.review.approve", content=code, comment="Ổn rồi")
    assert await count(world.session, PrApprovalEvent) == before + 1
    assert result.data["approval_stage"] == "TEAM_LEAD_REVIEW"
    assert result.data["workflow_stage"] == PrWorkflowStage.HEAD_REVIEW.value


async def test_request_revision_records_a_decision(world: ToolWorld) -> None:
    """Requirement 15."""
    code = await make_content(world)
    await to_team_lead_review(world, code)
    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")

    result = await world.run(
        "pr.review.request_revision", content=code, comment="hook chưa đủ mạnh"
    )
    assert result.data["decision"] == "REVISION_REQUIRED"
    assert result.data["workflow_stage"] == PrWorkflowStage.SCRIPTING.value


async def test_reject_is_a_high_risk_tool_so_it_inherits_confirmation(
    world: ToolWorld,
) -> None:
    """Requirement 16.

    Confirmation is the existing :class:`PolicyEngine` behaviour for
    ``RiskLevel.HIGH``, not a PR-specific flow. Asserting the risk level is
    asserting that the existing ``/confirm`` round trip applies.
    """
    reject = world.registry.get("pr.review.reject")
    assert reject.risk_level is RiskLevel.HIGH
    # Not ``destructive``: that flag means "deletes data" and the policy engine
    # refuses such tools outright. Rejecting cancels content; nothing is removed.
    assert reject.destructive is False
    for name in ("pr.capability.revoke", "pr.task.cancel", "pr.channel.close_assignment"):
        assert world.registry.get(name).risk_level is RiskLevel.HIGH


async def test_one_reviewer_holding_both_grants_approves_both_gates_from_telegram(
    world: ToolWorld,
) -> None:
    """Requirement 17, rewritten for Step 1F.2.2.

    This test used to assert that the second ``pr.review.approve`` was refused
    with ``pr_reviewer_separation`` and a Vietnamese sentence explaining that
    somebody else had to sign. The rule is gone, so the sentence is gone with it
    - and the same two calls now walk the content through both gates, which is
    the behaviour the Telegram side has to have if the panel does.

    Two calls, not one: the tool did not learn to skip a stage.
    """
    code = await make_content(world)
    await to_team_lead_review(world, code)
    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")
    await grant(world, world.owner, "Chi Owner", "Head Review")

    before = await count(world.session, PrApprovalEvent)
    team_lead = await world.run("pr.review.approve", content=code)
    assert team_lead.data["approval_stage"] == "TEAM_LEAD_REVIEW"
    assert team_lead.data["workflow_stage"] == PrWorkflowStage.HEAD_REVIEW.value

    head = await world.run("pr.review.approve", content=code)
    assert head.data["approval_stage"] == "HEAD_REVIEW"
    assert head.data["workflow_stage"] == PrWorkflowStage.APPROVED.value

    # Two events for the two gates, not one reused as evidence for both.
    assert await count(world.session, PrApprovalEvent) == before + 2


async def test_an_ungranted_reviewer_is_refused_with_a_useful_sentence(
    world: ToolWorld,
) -> None:
    code = await make_content(world)
    await to_team_lead_review(world, code)
    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.review.approve", content=code)
    assert raised.value.details["pr_error_code"] == "pr_forbidden"
    assert "chưa được cấp quyền" in raised.value.message


# ===========================================================================
# TASKS
# ===========================================================================


async def test_create_task_returns_a_generated_code(world: ToolWorld) -> None:
    """Requirement 22."""
    result = await world.run("pr.task.create", title="Dựng video", task_type="EDIT")
    assert str(result.data["code"]).startswith("TSK-2")


async def test_assignment_resolves_a_real_user(world: ToolWorld) -> None:
    """Requirement 23."""
    created = await world.run(
        "pr.task.create", title="Viết kịch bản", assignee="Linh", deadline="thứ Sáu"
    )
    assert created.data["assigned_to"] == "Linh Nhan Vien"

    detail = await world.run("pr.task.get", task=str(created.data["code"]))
    assert detail.data["assignee_count"] == 1


async def test_an_ambiguous_name_is_never_guessed(world: ToolWorld) -> None:
    """Requirement 24."""
    world.session.add(User(full_name="Linh Khac", role=Role.EMPLOYEE))
    await world.session.flush()

    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.task.create", title="Viết kịch bản", assignee="Linh")
    assert raised.value.details["reason"] == "ambiguous_person"
    assert len(raised.value.details["candidates"]) == 2


async def test_an_unknown_name_is_reported_not_created(world: ToolWorld) -> None:
    before = await count(world.session, User)
    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.task.create", title="X", assignee="Nguoi Khong Ton Tai")
    assert raised.value.details["reason"] == "person_not_found"
    assert await count(world.session, User) == before


async def test_overdue_tasks_come_back_as_a_structured_list(world: ToolWorld) -> None:
    """Requirement 25."""
    created = await world.run("pr.task.create", title="Việc trễ", deadline="2020-01-01")
    code = str(created.data["code"])
    await world.run("pr.task.start", task=code)

    result = await world.run("pr.task.overdue")
    assert code in result.data["codes"]
    assert code in result.message


async def test_an_invalid_task_transition_surfaces_the_service_error(
    world: ToolWorld,
) -> None:
    """Requirement 26. ``TODO -> DONE`` skips the record that anybody worked."""
    created = await world.run("pr.task.create", title="Việc")
    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.task.complete", task=str(created.data["code"]))
    assert raised.value.details["pr_error_code"] == "pr_invalid_transition"


async def test_the_legal_task_path_walks_through_the_tools(world: ToolWorld) -> None:
    created = await world.run("pr.task.create", title="Việc")
    code = str(created.data["code"])
    for tool, expected in (
        ("pr.task.start", "IN_PROGRESS"),
        ("pr.task.submit_review", "IN_REVIEW"),
        ("pr.task.request_revision", "REVISION_REQUIRED"),
        ("pr.task.start", "IN_PROGRESS"),
        ("pr.task.complete", "DONE"),
    ):
        result = await world.run(tool, task=code)
        assert result.data["status"] == expected


# ===========================================================================
# CHANNELS
# ===========================================================================


async def test_channel_list_and_create_work(world: ToolWorld) -> None:
    """Requirements 27 and 28."""
    code = await make_channel(world)
    assert code.startswith("CH-")

    listed = await world.run("pr.channel.list")
    assert code in listed.data["codes"]


async def test_channel_assignment_goes_through_the_service(world: ToolWorld) -> None:
    """Requirement 28."""
    code = await make_channel(world)
    result = await world.run(
        "pr.channel.assign",
        channel=code,
        person="Linh",
        role="CONTENT_OWNER",
        since="hôm nay",
    )
    assert result.data["code"] == code

    assignments = await world.run("pr.channel.assignments", channel=code)
    assert assignments.data["count"] == 1
    assert "Linh Nhan Vien" in assignments.message


async def test_an_overlapping_assignment_is_refused_by_the_service(
    world: ToolWorld,
) -> None:
    """Requirements 29 and 30.

    The Telegram layer never compares two dates - it hands both to
    :class:`PrChannelService` and renders whatever comes back.
    """
    code = await make_channel(world)
    await world.run(
        "pr.channel.assign", channel=code, person="Linh", since="2026-01-01", until="2026-06-30"
    )
    with pytest.raises(ToolExecutionError) as raised:
        await world.run(
            "pr.channel.assign",
            channel=code,
            person="Linh",
            since="2026-03-01",
            until="2026-09-30",
        )
    assert raised.value.details["pr_error_code"] == "pr_assignment_overlap"
    assert "trùng" in raised.value.message


async def test_closing_an_assignment_uses_the_service(world: ToolWorld) -> None:
    code = await make_channel(world)
    await world.run("pr.channel.assign", channel=code, person="Linh", since="2026-01-01")
    result = await world.run(
        "pr.channel.close_assignment", channel=code, person="Linh", until="2026-06-30"
    )
    assert result.data["until"] == "2026-06-30"


# ===========================================================================
# CAPABILITIES
# ===========================================================================


async def test_an_owner_can_grant_and_revoke(world: ToolWorld) -> None:
    """Requirements 31 and 35."""
    granted = await world.run("pr.capability.grant", person="Hoa Head", capability="Head Review")
    assert granted.data["capability"] == PrCapability.PR_HEAD_REVIEW.value

    revoked = await world.run("pr.capability.revoke", person="Hoa Head", capability="Head Review")
    assert revoked.data["capability"] == PrCapability.PR_HEAD_REVIEW.value


async def test_a_chat_grant_keeps_its_pre_scoping_meaning(world: ToolWorld) -> None:
    """Step 1F.2.7. A sentence in a chat cannot express a scope, so it does not.

    The grant this tool issues is the **pre-1F.2.7** one: the whole workspace,
    and in force only where the holder's role already carries the permission.
    Letting a group-chat message hand somebody unrestricted approval rights over
    everything regardless of their role - which is what an unqualified additive
    grant would be - would put the widest authority in the product behind its
    least deliberate channel.

    The reply says where scoped grants are issued instead, because a person who
    wanted "duyệt bài Facebook trên hai kênh" and got "duyệt tất cả" needs to
    hear that they did.
    """
    granted = await world.run("pr.capability.grant", person="Hoa Head", capability="Head Review")
    assert granted.data["requires_role_baseline"] is True
    assert "trang Phân quyền" in granted.message

    row = (
        (
            await world.session.execute(
                select(PrUserCapability).where(
                    PrUserCapability.capability == PrCapability.PR_HEAD_REVIEW
                )
            )
        )
        .scalars()
        .one()
    )
    assert row.requires_role_baseline is True
    assert row.content_type_scope is PrGrantScopeMode.ALL
    assert row.channel_scope is PrGrantScopeMode.ALL


async def test_a_chat_revoke_takes_back_every_grant_of_that_gate(world: ToolWorld) -> None:
    """ "Thu hồi quyền Duyệt Trưởng phòng của Hoa" means all of them.

    Since Step 1F.2.7 one person may hold several grants of one gate over
    different scopes, and a chat message has no way to name one. Revoking only
    ever removes authority, so the plain sentence gets the plain answer rather
    than a refusal that would leave nobody able to take the right back at all.
    """
    capabilities = PrCapabilityService(world.session, AuditService(world.session))
    for content_type in (PrContentType.FACEBOOK_POST, PrContentType.PRESS_ARTICLE):
        await capabilities.grant(
            actor=world.owner,
            request_id=uuid.uuid4(),
            user_id=world.head_id,
            capability=PrCapability.PR_HEAD_REVIEW,
            scope=GrantScope(
                content_type_scope=PrGrantScopeMode.SELECTED,
                content_types=frozenset({content_type}),
                channel_scope=PrGrantScopeMode.ALL,
            ),
        )

    revoked = await world.run("pr.capability.revoke", person="Hoa Head", capability="Head Review")
    assert revoked.data["revoked"] == 2
    assert not await capabilities.granted_capabilities(world.head_id)


async def test_an_unauthorized_user_cannot_grant(world: ToolWorld) -> None:
    """Requirement 32. The refusal comes from the service, not the tool."""
    with pytest.raises(ToolExecutionError) as raised:
        await world.run(
            "pr.capability.grant",
            actor=world.head,
            person="Linh",
            capability="Team Lead Review",
        )
    assert raised.value.details["pr_error_code"] == "pr_forbidden"


async def test_listing_capabilities_for_a_person_and_holders_of_one(
    world: ToolWorld,
) -> None:
    """Requirements 33 and 34."""
    await grant(world, world.owner, "Hoa Head", "Trưởng phòng duyệt")

    mine = await world.run("pr.capability.list_for_user", person="Hoa Head")
    assert PrCapability.PR_HEAD_REVIEW.value in mine.data["capabilities"]

    holders = await world.run("pr.capability.users_for", capability="Head Review")
    assert holders.data["capability"] == PrCapability.PR_HEAD_REVIEW.value
    assert "Hoa Head" in holders.message


async def test_capability_phrases_map_without_typing_enum_constants(
    world: ToolWorld,
) -> None:
    for phrase, expected in (
        ("Team Lead Review", PrCapability.PR_TEAM_LEAD_REVIEW),
        ("duyệt trưởng nhóm", PrCapability.PR_TEAM_LEAD_REVIEW),
        ("Head Review", PrCapability.PR_HEAD_REVIEW),
        ("trưởng phòng duyệt", PrCapability.PR_HEAD_REVIEW),
        ("duyệt nội bộ", PrCapability.PR_INTERNAL_REVIEW),
    ):
        result = await world.run("pr.capability.users_for", capability=phrase)
        assert result.data["capability"] == expected.value

    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.capability.users_for", capability="quyền gì đó")
    assert raised.value.details["reason"] == "unknown_capability"


async def test_authorization_uses_users_not_telegram_usernames(world: ToolWorld) -> None:
    """Requirement 36.

    The same ``users.id`` and role with a different Telegram identity - or none
    at all - gets the same answer, because nothing consults the Telegram fields.
    """
    await grant(world, world.owner, "Hoa Head", "Head Review")
    disguised = world.head.model_copy(
        update={"telegram_user_id": 999_111_222, "telegram_username": "somebody_else"}
    )
    result = await world.run("pr.capability.list_for_user", actor=disguised)
    assert PrCapability.PR_HEAD_REVIEW.value in result.data["capabilities"]


# ===========================================================================
# PUBLICATION
# ===========================================================================


async def test_publication_registration_calls_the_service(world: ToolWorld) -> None:
    """Requirement 37."""
    channel = await make_channel(world)
    code = await make_content(world, channels=[channel])
    await to_team_lead_review(world, code)
    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")
    await grant(world, world.owner, "Hoa Head", "Head Review")
    await grant(world, world.owner, "Chi Owner", "duyệt nội bộ")

    await world.run("pr.review.approve", content=code)
    await world.run("pr.review.approve", content=code, actor=world.head)
    await to_internal_review(world, code)
    await world.run("pr.review.approve", content=code)

    result = await world.run(
        "pr.publication.register", content=code, channel=channel, platform_post_id="post-1"
    )
    assert str(result.data["publication_code"]).startswith("PUB-2")
    assert result.data["first_publication"] is True
    assert result.data["workflow_stage"] == PrWorkflowStage.PUBLISHED.value


async def test_publishing_to_an_untargeted_channel_is_recorded(
    world: ToolWorld,
) -> None:
    """Requirement 38, as Step 1F.2.3f reversed it.

    It used to be a refusal. That was right for a piece published once on the
    plan somebody wrote in August, and wrong for a content item that is reused:
    in October a channel exists that did not exist when the targets were chosen,
    and requiring a planned target there leaves only the workarounds this step
    exists to remove. The planned target became linkage rather than permission -
    see ``PrPublicationService``.

    A channel that does not exist at all is still a refusal; that check moved
    into the service rather than disappearing with the target lookup.
    """
    targeted = await make_channel(world)
    other = await world.run("pr.channel.create", name="Facebook Apexmed", platform="TikTok")
    code = await make_content(world, channels=[targeted])
    await to_team_lead_review(world, code)
    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")
    await grant(world, world.owner, "Hoa Head", "Head Review")
    await grant(world, world.owner, "Chi Owner", "duyệt nội bộ")
    await world.run("pr.review.approve", content=code)
    await world.run("pr.review.approve", content=code, actor=world.head)
    await to_internal_review(world, code)
    await world.run("pr.review.approve", content=code)

    result = await world.run(
        "pr.publication.register", content=code, channel=str(other.data["code"])
    )
    assert result.data["channel_code"] == other.data["code"]
    assert result.data["first_publication"] is True


async def test_a_publication_names_the_produced_output(world: ToolWorld) -> None:
    """Step 1F.2.3f. The tool says which file went out, by its label.

    With exactly one produced output there is nothing to choose and the tool
    uses it; the reply names it, so the person can see what was recorded.
    """
    channel = await make_channel(world)
    code = await make_content(world, channels=[channel])
    await to_team_lead_review(world, code)
    await grant(world, world.owner, "Chi Owner", "duyệt trưởng nhóm")
    await grant(world, world.owner, "Hoa Head", "Head Review")
    await grant(world, world.owner, "Chi Owner", "duyệt nội bộ")
    await world.run("pr.review.approve", content=code)
    await world.run("pr.review.approve", content=code, actor=world.head)
    await to_internal_review(world, code)
    await world.run("pr.review.approve", content=code)

    result = await world.run("pr.publication.register", content=code, channel=channel)
    assert result.data["output_label"]
    assert str(result.data["output_label"]) in result.message


# ===========================================================================
# ERROR MAPPING
# ===========================================================================


async def test_every_pr_refusal_reaches_the_user_in_vietnamese(world: ToolWorld) -> None:
    """No English service message ever reaches a chat."""
    code = await make_content(world)
    with pytest.raises(ToolExecutionError) as raised:
        await world.run(
            "pr.content.transition", content=code, target=PrWorkflowStage.PUBLISHED.value
        )
    message = raised.value.message
    assert "Cannot move" not in message
    assert "PR content" not in message
    assert "Nội dung" in message


async def test_a_missing_content_code_is_reported_plainly(world: ToolWorld) -> None:
    with pytest.raises(ToolExecutionError) as raised:
        await world.run("pr.content.get", content="CNT-2026-999999")
    assert "CNT-2026-999999" in raised.value.message
