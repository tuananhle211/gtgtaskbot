"""Step 1C's application services, exercised against real SQL.

These use the offline ``session`` fixture - an in-memory SQLite built from the
same ORM metadata the migrations were written from - for the same reason
``tests/unit/test_hr_requests.py`` does: the *behavioural* rules here are
version binding, transition legality and overlap detection, and none of them
needs PostgreSQL to be true. What does need PostgreSQL - that the migration
applies, and that ``SELECT ... FOR UPDATE`` actually serialises two concurrent
commands - lives in ``tests/integration/test_pr_application_services_pg.py``.

The rule under the most tests, because it is the one the whole module exists
for: **a verdict belongs to the draft it judged.** Version 4's ``PASS`` never
authorises version 5.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_ai_review_service import PrAiReviewService, RecordAiReviewCommand
from meobot.application.pr_approval_service import PrApprovalService, RecordApprovalCommand
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import (
    CreateChannelCommand,
    PrChannelService,
)
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    PrContentService,
    ReviseContentCommand,
)
from meobot.application.pr_publication_service import (
    PrPublicationService,
    RegisterPublicationCommand,
)
from meobot.application.pr_query_service import PrQueryService
from meobot.application.pr_task_service import CreateTaskCommand, PrTaskService
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.config import get_settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrChannelAssignment,
    PrContentItem,
    PrPlatform,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrPostMetricSnapshot
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.codes import CODE_PATTERN
from meobot.domain.pr.errors import (
    PrAiReviewRequiredError,
    PrApprovalStageMismatchError,
    PrAssignmentOverlapError,
    PrConflictError,
    PrImmutableFieldError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrReviewVersionMismatchError,
    PrStaleVersionError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrProductionArtifactType,
    PrTaskAssignmentRole,
    PrTaskStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrMetricSource

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


@dataclass
class PrWorld:
    """One seeded database and every PR service wired onto it."""

    session: AsyncSession
    actor: Actor
    head: Actor
    employee: Actor
    user_id: uuid.UUID
    head_user_id: uuid.UUID
    brand_id: uuid.UUID
    channel_id: uuid.UUID
    second_channel_id: uuid.UUID
    content: PrContentService
    workflow: PrContentWorkflowService
    ai: PrAiReviewService
    approvals: PrApprovalService
    tasks: PrTaskService
    channels: PrChannelService
    publications: PrPublicationService
    queries: PrQueryService
    capabilities: PrCapabilityService
    codes: PrCodeService

    #: A fresh correlation id per command, as a caller would supply.
    @property
    def request_id(self) -> uuid.UUID:
        return uuid.uuid4()


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[PrWorld]:
    """A brand, a platform, two channels, three people, and the services.

    Two reviewers, not one, because Step 1C.1's four-eyes rule makes a
    single-reviewer world unable to reach ``APPROVED`` at all - which is the
    point of the rule, and which the fixture has to model rather than work
    around.
    """
    owner = User(full_name="PR Owner", role=Role.OWNER)
    head = User(full_name="PR Head", role=Role.ADMIN)
    worker = User(full_name="PR Employee", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-1", name="Brand One")
    platform = PrPlatform(code="PLAT-1", name="Platform One")
    session.add_all([owner, head, worker, brand, platform])
    await session.flush()

    audit = AuditService(session)
    capabilities = PrCapabilityService(session, audit)
    codes = PrCodeService(session, get_settings())
    content = PrContentService(session, audit, capabilities, codes)
    workflow = PrContentWorkflowService(session, audit, capabilities)
    ai = PrAiReviewService(session, audit, content, workflow)

    owner_actor = Actor(user_id=owner.id, full_name="PR Owner", role=Role.OWNER)
    head_actor = Actor(user_id=head.id, full_name="PR Head", role=Role.ADMIN)

    # The owner reviews at team-lead and internal level; the head holds the
    # head gate and nothing else. A role alone grants none of the three - that
    # is the whole change Step 1C.1 makes.
    for user_id, capability in (
        (owner.id, PrCapability.PR_TEAM_LEAD_REVIEW),
        (owner.id, PrCapability.PR_INTERNAL_REVIEW),
        (head.id, PrCapability.PR_HEAD_REVIEW),
    ):
        await capabilities.grant(
            actor=owner_actor,
            request_id=uuid.uuid4(),
            user_id=user_id,
            capability=capability,
        )

    # Built through the service so their codes come from the allocator, which
    # is also what stops the seeded rows colliding with anything a test
    # creates later.
    channels = PrChannelService(session, audit, capabilities, codes)
    channel = await channels.create_channel(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        command=CreateChannelCommand(
            name="Channel One",
            platform_id=platform.id,
            brand_id=brand.id,
            category=PrChannelCategory.SCALE,
        ),
    )
    second = await channels.create_channel(
        actor=owner_actor,
        request_id=uuid.uuid4(),
        command=CreateChannelCommand(
            name="Channel Two",
            platform_id=platform.id,
            brand_id=brand.id,
            category=PrChannelCategory.TEST,
        ),
    )

    yield PrWorld(
        session=session,
        actor=owner_actor,
        head=head_actor,
        employee=Actor(user_id=worker.id, full_name="PR Employee", role=Role.EMPLOYEE),
        user_id=owner.id,
        head_user_id=head.id,
        brand_id=brand.id,
        channel_id=channel.id,
        second_channel_id=second.id,
        content=content,
        workflow=workflow,
        ai=ai,
        approvals=PrApprovalService(session, audit, content, workflow, ai, capabilities),
        tasks=PrTaskService(session, audit, capabilities, codes),
        channels=channels,
        publications=PrPublicationService(session, audit, workflow, capabilities, codes, content),
        queries=PrQueryService(session, capabilities),
        capabilities=capabilities,
        codes=codes,
    )


# --- Helpers ---------------------------------------------------------------


async def make_content(world: PrWorld, *, with_targets: bool = False) -> uuid.UUID:
    targets = (ContentTargetSpec(channel_id=world.channel_id),) if with_targets else ()
    snapshot = await world.content.create_content(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateContentCommand(
            title="A first title",
            brand_id=world.brand_id,
            owner_user_id=world.user_id,
            topic="A topic",
            hook="A hook",
            brief="A brief",
            script_text="Draft one.",
            targets=targets,
        ),
    )
    return snapshot.content.id


async def advance(world: PrWorld, content_id: uuid.UUID, *stages: PrWorkflowStage) -> None:
    for stage in stages:
        await world.workflow.request_transition(
            actor=world.actor, request_id=world.request_id, content_id=content_id, target=stage
        )


async def record_full_review(
    world: PrWorld,
    content_id: uuid.UUID,
    *,
    version: int,
    result: PrAiReviewResult = PrAiReviewResult.PASS,
) -> None:
    await world.ai.record_review(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=version,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=result,
            model_name="claude-opus-5",
            prompt_version="pr-script-review@1",
            reviewed_at=NOW,
        ),
    )


async def to_team_lead_review(world: PrWorld, content_id: uuid.UUID, *, version: int = 1) -> None:
    """Take content from IDEA to TEAM_LEAD_REVIEW the only way that exists."""
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    await record_full_review(world, content_id, version=version)


async def stage_of(world: PrWorld, content_id: uuid.UUID) -> PrWorkflowStage:
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    await world.session.refresh(content)
    return content.workflow_stage


async def count(session: AsyncSession, model: type) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


# ===========================================================================
# CONTENT VERSIONING
# ===========================================================================


async def test_create_content_creates_version_one_and_its_targets(world: PrWorld) -> None:
    snapshot = await world.content.create_content(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateContentCommand(
            title="Launch teaser",
            brand_id=world.brand_id,
            owner_user_id=world.user_id,
            script_text="Hook, body, CTA.",
            targets=(
                ContentTargetSpec(channel_id=world.channel_id),
                ContentTargetSpec(channel_id=world.second_channel_id),
            ),
        ),
    )
    assert snapshot.version_no == 1
    assert snapshot.content.workflow_stage is PrWorkflowStage.IDEA
    assert snapshot.version.script_text == "Hook, body, CTA."

    detail = await world.queries.get_content(actor=world.actor, content_id=snapshot.content.id)
    assert detail.current_version_no == 1
    assert len(detail.targets) == 2


async def test_revising_creates_the_next_version(world: PrWorld) -> None:
    content_id = await make_content(world)
    snapshot = await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(
            content_id=content_id,
            expected_version=1,
            title="A second title",
            script_text="Draft two.",
            change_note="Sharpened the hook.",
        ),
    )
    assert snapshot.version_no == 2
    assert snapshot.version.change_note == "Sharpened the hook."


async def test_an_old_version_is_never_touched_by_a_revision(world: PrWorld) -> None:
    """The whole point of the table: version 1 still says what it said."""
    content_id = await make_content(world)
    original = await world.content.require_current_version(content_id)
    original_id, original_title, original_script = (
        original.id,
        original.title,
        original.script_text,
    )

    await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(
            content_id=content_id,
            expected_version=1,
            title="Rewritten",
            script_text="Completely different.",
        ),
    )

    stored = await world.session.get(PrContentVersion, original_id)
    assert stored is not None
    assert stored.title == original_title
    assert stored.script_text == original_script
    assert stored.version_no == 1


async def test_the_projection_follows_the_newest_draft(world: PrWorld) -> None:
    """``pr_content_items`` is a projection, and it is kept honest."""
    content_id = await make_content(world)
    await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(
            content_id=content_id, expected_version=1, title="Projected title", hook="New hook"
        ),
    )
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    assert content.title == "Projected title"
    assert content.hook == "New hook"


async def test_fields_not_supplied_are_carried_forward(world: PrWorld) -> None:
    """A caller editing the hook does not have to resend the brief."""
    content_id = await make_content(world)
    snapshot = await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(content_id=content_id, expected_version=1, hook="Only this"),
    )
    assert snapshot.version.hook == "Only this"
    assert snapshot.version.brief == "A brief"
    assert snapshot.version.title == "A first title"


async def test_a_duplicate_version_number_cannot_be_written(world: PrWorld) -> None:
    """The unique index is what makes the service's allocation safe."""
    content_id = await make_content(world)
    world.session.add(
        PrContentVersion(
            content_id=content_id,
            version_no=1,
            title="Impostor",
            created_by_user_id=world.user_id,
        )
    )
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError shape differs per driver
        await world.session.flush()
    await world.session.rollback()


async def test_a_stale_expected_version_is_refused(world: PrWorld) -> None:
    content_id = await make_content(world)
    await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(content_id=content_id, expected_version=1, title="Second"),
    )
    with pytest.raises(PrStaleVersionError) as raised:
        await world.content.revise_content(
            actor=world.actor,
            request_id=world.request_id,
            command=ReviseContentCommand(
                content_id=content_id, expected_version=1, title="Third, from a stale screen"
            ),
        )
    assert raised.value.details["current_version"] == 2
    assert raised.value.details["expected_version"] == 1


async def test_content_cannot_be_edited_during_ai_review(world: PrWorld) -> None:
    content_id = await make_content(world)
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    with pytest.raises(PrWorkflowTransitionError):
        await world.content.revise_content(
            actor=world.actor,
            request_id=world.request_id,
            command=ReviseContentCommand(
                content_id=content_id, expected_version=1, title="Sneaky edit"
            ),
        )


async def test_content_cannot_be_edited_during_team_lead_review(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    assert await stage_of(world, content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW
    with pytest.raises(PrWorkflowTransitionError):
        await world.content.revise_content(
            actor=world.actor,
            request_id=world.request_id,
            command=ReviseContentCommand(
                content_id=content_id, expected_version=1, title="Sneaky edit"
            ),
        )


async def test_two_content_items_get_different_generated_codes(world: PrWorld) -> None:
    """The duplicate-code case Step 1C tested is now unreachable.

    A caller cannot supply a code, so it cannot supply the same one twice.
    What replaces that test is the property it was protecting: two creations
    produce two different codes.
    """
    first = await make_content(world)
    second = await make_content(world)
    codes = {
        (await world.content.require_content(first)).code,
        (await world.content.require_content(second)).code,
    }
    assert len(codes) == 2
    assert all(CODE_PATTERN.match(code) for code in codes), codes


async def test_a_blank_title_is_refused(world: PrWorld) -> None:
    with pytest.raises(PrValidationError):
        await world.content.create_content(
            actor=world.actor,
            request_id=world.request_id,
            command=CreateContentCommand(
                title="   ",
                brand_id=world.brand_id,
                owner_user_id=world.user_id,
            ),
        )


# ===========================================================================
# AI GATE
# ===========================================================================


async def test_scripting_moves_to_ai_review(world: PrWorld) -> None:
    content_id = await make_content(world)
    await advance(world, content_id, PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING)
    await world.workflow.request_transition(
        actor=world.actor,
        request_id=world.request_id,
        content_id=content_id,
        target=PrWorkflowStage.AI_REVIEW,
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.AI_REVIEW


@pytest.mark.parametrize(
    "review_type",
    [PrAiReviewType.SCRIPT_QUALITY, PrAiReviewType.POLICY_COMPLIANCE, PrAiReviewType.BRAND_TONE],
)
async def test_a_non_gating_review_is_stored_and_moves_nothing(
    world: PrWorld, review_type: PrAiReviewType
) -> None:
    content_id = await make_content(world)
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    outcome = await world.ai.record_review(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=1,
            review_type=review_type,
            result=PrAiReviewResult.PASS,
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=NOW,
        ),
    )
    assert outcome.gated is False
    assert outcome.new_stage is None
    assert await stage_of(world, content_id) is PrWorkflowStage.AI_REVIEW
    assert await count(world.session, PrAiReview) == 1


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (PrAiReviewResult.PASS, PrWorkflowStage.TEAM_LEAD_REVIEW),
        (PrAiReviewResult.PASS_WITH_WARNINGS, PrWorkflowStage.TEAM_LEAD_REVIEW),
        (PrAiReviewResult.REVISION_REQUIRED, PrWorkflowStage.SCRIPTING),
    ],
)
async def test_a_full_review_moves_the_content_where_its_result_says(
    world: PrWorld, result: PrAiReviewResult, expected: PrWorkflowStage
) -> None:
    content_id = await make_content(world)
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    outcome = await world.ai.record_review(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=1,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=result,
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=NOW,
        ),
    )
    assert outcome.new_stage is expected
    assert await stage_of(world, content_id) is expected


async def test_a_review_of_a_stale_version_is_refused(world: PrWorld) -> None:
    content_id = await make_content(world)
    await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(content_id=content_id, expected_version=1, title="Second"),
    )
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    with pytest.raises(PrReviewVersionMismatchError) as raised:
        await record_full_review(world, content_id, version=1)
    assert raised.value.details["current_version"] == 2


async def test_an_ai_review_outside_the_ai_review_stage_is_refused(world: PrWorld) -> None:
    content_id = await make_content(world)
    with pytest.raises(PrWorkflowTransitionError):
        await record_full_review(world, content_id, version=1)


async def test_a_pass_for_version_four_cannot_authorise_version_five(world: PrWorld) -> None:
    """The rule the whole module exists for, walked end to end.

    Version 4 passes, reaches the team lead, is sent back, and becomes version
    5. The old ``PASS`` is still on file and still true *about version 4* - and
    it authorises nothing: version 5 cannot reach ``TEAM_LEAD_REVIEW`` until it
    has a ``FULL_REVIEW`` of its own.
    """
    content_id = await make_content(world)
    for version in (1, 2, 3):
        await world.content.revise_content(
            actor=world.actor,
            request_id=world.request_id,
            command=ReviseContentCommand(
                content_id=content_id, expected_version=version, title=f"Draft {version + 1}"
            ),
        )
    await to_team_lead_review(world, content_id, version=4)
    assert await stage_of(world, content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW

    await world.approvals.record_decision(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.user_id,
            approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
            decision=PrApprovalDecision.REVISION_REQUIRED,
            version_reviewed=4,
            comment="Hook is weak.",
        ),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.SCRIPTING

    await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(
            content_id=content_id, expected_version=4, title="Draft 5", script_text="Rewritten."
        ),
    )
    await advance(world, content_id, PrWorkflowStage.AI_REVIEW)

    # Version 4's PASS is still stored, and still says PASS.
    stale = await world.ai.latest_gating_review(content_id, version_no=4)
    assert stale is not None
    assert stale.result is PrAiReviewResult.PASS

    # It does not exist for version 5.
    assert await world.ai.latest_gating_review(content_id, version_no=5) is None

    # And re-submitting it as a review of version 5 is refused outright.
    with pytest.raises(PrReviewVersionMismatchError):
        await record_full_review(world, content_id, version=4)
    assert await stage_of(world, content_id) is PrWorkflowStage.AI_REVIEW

    # Only a genuine review of version 5 moves it on.
    await record_full_review(world, content_id, version=5)
    assert await stage_of(world, content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW


async def test_an_ai_review_never_writes_an_approval_event(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    assert await count(world.session, PrApprovalEvent) == 0
    reviews = await world.ai.list_reviews(content_id)
    assert len(reviews) == 1
    # And the row names a model, never a person.
    assert not hasattr(reviews[0], "reviewer_user_id")


async def test_ai_reviews_accumulate_rather_than_replace(world: PrWorld) -> None:
    """Append-only in practice: a retry is a second row, not an overwrite."""
    content_id = await make_content(world)
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    for review_type in (PrAiReviewType.SCRIPT_QUALITY, PrAiReviewType.BRAND_TONE):
        await world.ai.record_review(
            actor=world.actor,
            request_id=world.request_id,
            command=RecordAiReviewCommand(
                content_id=content_id,
                reviewed_version=1,
                review_type=review_type,
                result=PrAiReviewResult.PASS,
                model_name="claude-opus-5",
                prompt_version="p@1",
                reviewed_at=NOW,
            ),
        )
    await record_full_review(world, content_id, version=1)
    assert await count(world.session, PrAiReview) == 3


async def test_a_blank_prompt_version_is_refused_before_the_database_sees_it(
    world: PrWorld,
) -> None:
    content_id = await make_content(world)
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    with pytest.raises(PrValidationError):
        await world.ai.record_review(
            actor=world.actor,
            request_id=world.request_id,
            command=RecordAiReviewCommand(
                content_id=content_id,
                reviewed_version=1,
                review_type=PrAiReviewType.FULL_REVIEW,
                result=PrAiReviewResult.PASS,
                model_name="claude-opus-5",
                prompt_version="   ",
                reviewed_at=NOW,
            ),
        )


# ===========================================================================
# HUMAN APPROVAL
# ===========================================================================


async def test_the_approval_stage_must_match_the_workflow_stage(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    with pytest.raises(PrApprovalStageMismatchError) as raised:
        await world.approvals.record_decision(
            actor=world.head,
            request_id=world.request_id,
            command=RecordApprovalCommand(
                content_id=content_id,
                reviewer_user_id=world.head_user_id,
                approval_stage=PrApprovalStage.HEAD_REVIEW,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
            ),
        )
    assert raised.value.details["expected_stage"] == PrApprovalStage.TEAM_LEAD_REVIEW.value


async def test_team_lead_approval_hands_the_work_to_head_review(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    outcome = await world.approvals.record_decision(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.user_id,
            approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
            decision=PrApprovalDecision.APPROVED,
            version_reviewed=1,
        ),
    )
    assert outcome.new_stage is PrWorkflowStage.HEAD_REVIEW
    assert await stage_of(world, content_id) is PrWorkflowStage.HEAD_REVIEW


async def test_head_approval_reaches_approved(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await approve_at(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
    outcome = await approve_at(world, content_id, PrApprovalStage.HEAD_REVIEW)
    assert outcome is PrWorkflowStage.APPROVED


async def approve_at(
    world: PrWorld, content_id: uuid.UUID, stage: PrApprovalStage, *, version: int = 1
) -> PrWorkflowStage:
    """Approve at one gate as whoever is entitled to that gate.

    The head gate is a different person on purpose: since Step 1C.1 the owner
    holds no ``PR_HEAD_REVIEW`` grant, and even if they did the four-eyes rule
    would refuse a second signature from the team-lead approver.
    """
    actor = world.head if stage is PrApprovalStage.HEAD_REVIEW else world.actor
    reviewer_user_id = world.head_user_id if stage is PrApprovalStage.HEAD_REVIEW else world.user_id
    outcome = await world.approvals.record_decision(
        actor=actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=reviewer_user_id,
            approval_stage=stage,
            decision=PrApprovalDecision.APPROVED,
            version_reviewed=version,
        ),
    )
    return outcome.new_stage


async def test_a_team_lead_may_reject_what_the_ai_passed(world: PrWorld) -> None:
    """Human responsibility, asserted: the AI's PASS does not bind anybody."""
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    context = await world.queries.get_content_review_context(
        actor=world.actor, content_id=content_id
    )
    assert context.ai_result is PrAiReviewResult.PASS

    outcome = await world.approvals.record_decision(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.user_id,
            approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
            decision=PrApprovalDecision.REJECTED,
            version_reviewed=1,
            comment="Not for this brand.",
        ),
    )
    assert outcome.new_stage is PrWorkflowStage.CANCELLED
    assert await stage_of(world, content_id) is PrWorkflowStage.CANCELLED


async def test_a_head_revision_request_returns_the_work_to_scripting(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await approve_at(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
    outcome = await world.approvals.record_decision(
        actor=world.head,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.head_user_id,
            approval_stage=PrApprovalStage.HEAD_REVIEW,
            decision=PrApprovalDecision.REVISION_REQUIRED,
            version_reviewed=1,
        ),
    )
    assert outcome.new_stage is PrWorkflowStage.SCRIPTING


async def test_approval_events_accumulate_and_are_never_updated(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await approve_at(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
    await approve_at(world, content_id, PrApprovalStage.HEAD_REVIEW)

    history = await world.approvals.history(content_id)
    assert [event.approval_stage for event in history] == [
        PrApprovalStage.TEAM_LEAD_REVIEW,
        PrApprovalStage.HEAD_REVIEW,
    ]
    assert "updated_at" not in PrApprovalEvent.__table__.columns


async def test_a_decision_about_a_stale_version_is_refused(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    with pytest.raises(PrReviewVersionMismatchError):
        await world.approvals.record_decision(
            actor=world.actor,
            request_id=world.request_id,
            command=RecordApprovalCommand(
                content_id=content_id,
                reviewer_user_id=world.user_id,
                approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=99,
            ),
        )


async def test_a_team_lead_decision_needs_a_full_review_on_file(world: PrWorld) -> None:
    """The second check, at the point of the write.

    Reaching ``TEAM_LEAD_REVIEW`` already requires a gating review, so this can
    only be provoked by putting the content there another way - which is what
    makes it worth asserting rather than assuming.
    """
    content_id = await make_content(world)
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    content.workflow_stage = PrWorkflowStage.TEAM_LEAD_REVIEW
    await world.session.flush()

    with pytest.raises(PrAiReviewRequiredError):
        await world.approvals.record_decision(
            actor=world.actor,
            request_id=world.request_id,
            command=RecordApprovalCommand(
                content_id=content_id,
                reviewer_user_id=world.user_id,
                approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
            ),
        )


async def test_an_employee_may_not_decide_at_a_review_gate(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    with pytest.raises(PrPermissionDeniedError):
        await world.approvals.record_decision(
            actor=world.employee,
            request_id=world.request_id,
            command=RecordApprovalCommand(
                content_id=content_id,
                reviewer_user_id=world.user_id,
                approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
            ),
        )


# ===========================================================================
# PRODUCTION
# ===========================================================================


async def to_approved(world: PrWorld, content_id: uuid.UUID) -> None:
    await to_team_lead_review(world, content_id)
    await approve_at(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
    await approve_at(world, content_id, PrApprovalStage.HEAD_REVIEW)


async def test_approved_content_goes_into_production_and_internal_review(
    world: PrWorld,
) -> None:
    """The two moves after approval, and the two things they now require.

    Step 1F.2.3b put a precondition on each: production needs somebody holding
    the piece, and internal review needs a cut to review. Both are checked on the
    generic transition route - not only behind the panel's buttons - so this test
    walks the route and supplies each precondition as the real flow would.
    """
    content_id = await make_content(world)
    await to_approved(world, content_id)

    # Nobody holds it yet, so production cannot begin.
    with pytest.raises(PrWorkflowTransitionError) as no_producer:
        await advance(world, content_id, PrWorkflowStage.PRODUCTION)
    assert no_producer.value.details["reason"] == "no_producer_assigned"

    await hand_off(world, content_id)
    await advance(world, content_id, PrWorkflowStage.PRODUCTION)
    assert await stage_of(world, content_id) is PrWorkflowStage.PRODUCTION

    # And no cut, so the reviewer has nothing to watch.
    with pytest.raises(PrWorkflowTransitionError) as no_cut:
        await advance(world, content_id, PrWorkflowStage.INTERNAL_REVIEW)
    assert no_cut.value.details["reason"] == "no_production_submission"

    await hand_in(world, content_id)
    await advance(world, content_id, PrWorkflowStage.INTERNAL_REVIEW)
    assert await stage_of(world, content_id) is PrWorkflowStage.INTERNAL_REVIEW


async def hand_off(world: PrWorld, content_id: uuid.UUID) -> None:
    """Give the piece a producer, the short way.

    Step 1F.2.3b requires one before production may start. This world is the
    Step 1C service-level fixture and has no production service in it; the
    handoff has its own tests in ``test_pr_production_lifecycle.py``, and what
    these tests are about is what happens *after* it. So the column is set
    directly, and the rule it satisfies is still the server's.
    """
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    content.producer_user_id = world.actor.user_id
    await world.session.flush()


async def hand_in(world: PrWorld, content_id: uuid.UUID) -> None:
    """Record a production submission, so internal review has a cut to watch.

    Written directly for the same reason ``hand_off`` is: the submission flow is
    tested where it lives, and these tests need the precondition rather than the
    machinery.
    """
    version = await world.content.require_current_version(content_id)
    world.session.add(
        PrProductionSubmission(
            content_id=content_id,
            content_version_id=version.id,
            submission_no=1,
            producer_user_id=world.actor.user_id,
            submitted_by_user_id=world.actor.user_id,
            artifact_type=PrProductionArtifactType.DRIVE_LINK,
            location="https://drive.google.com/file/d/1/view",
        )
    )
    await world.session.flush()


async def to_internal_review(world: PrWorld, content_id: uuid.UUID) -> None:
    """Approved, handed off, started, handed in, and in front of a reviewer."""
    await to_approved(world, content_id)
    await hand_off(world, content_id)
    await advance(world, content_id, PrWorkflowStage.PRODUCTION)
    await hand_in(world, content_id)
    await advance(world, content_id, PrWorkflowStage.INTERNAL_REVIEW)


async def test_internal_approval_reaches_ready_to_publish(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_internal_review(world, content_id)
    outcome = await approve_at(world, content_id, PrApprovalStage.INTERNAL_REVIEW)
    assert outcome is PrWorkflowStage.READY_TO_PUBLISH


async def test_internal_revision_returns_the_work_to_production(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_internal_review(world, content_id)
    outcome = await world.approvals.record_decision(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.user_id,
            approval_stage=PrApprovalStage.INTERNAL_REVIEW,
            decision=PrApprovalDecision.REVISION_REQUIRED,
            version_reviewed=1,
        ),
    )
    assert outcome.new_stage is PrWorkflowStage.PRODUCTION


# ===========================================================================
# PUBLICATION
# ===========================================================================


async def to_ready_to_publish(world: PrWorld, content_id: uuid.UUID) -> None:
    await to_internal_review(world, content_id)
    await approve_at(world, content_id, PrApprovalStage.INTERNAL_REVIEW)


async def master_of(world: PrWorld, content_id: uuid.UUID) -> uuid.UUID:
    """The production submission this item was approved on.

    Step 1F.2.3f: every new publication names the produced output that went out,
    so the tests below say which file rather than only which channel. The
    approved master is what ``hand_in`` left behind.
    """
    found = await world.session.execute(
        select(PrProductionSubmission.id)
        .where(PrProductionSubmission.content_id == content_id)
        .order_by(PrProductionSubmission.submission_no.desc())
    )
    submission_id = found.scalars().first()
    assert submission_id is not None
    return submission_id


async def test_the_first_publication_makes_the_content_published(world: PrWorld) -> None:
    content_id = await make_content(world, with_targets=True)
    await to_ready_to_publish(world, content_id)
    outcome = await world.publications.register_publication(
        actor=world.actor,
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=NOW,
            production_submission_id=await master_of(world, content_id),
            platform_post_id="post-1",
            url="https://example.test/post-1",
        ),
    )
    assert outcome.was_first
    assert outcome.new_stage is PrWorkflowStage.PUBLISHED
    assert outcome.target.status.value == "PUBLISHED"
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


async def test_additional_target_publications_are_allowed_once_published(
    world: PrWorld,
) -> None:
    content_id = await make_content(world)
    # Two planned channels this time.
    snapshot = await world.content.create_content(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateContentCommand(
            title="Fan-out",
            brand_id=world.brand_id,
            owner_user_id=world.user_id,
            targets=(
                ContentTargetSpec(channel_id=world.channel_id),
                ContentTargetSpec(channel_id=world.second_channel_id),
            ),
        ),
    )
    fanout_id = snapshot.content.id
    await to_ready_to_publish(world, fanout_id)

    master = await master_of(world, fanout_id)
    first = await world.publications.register_publication(
        actor=world.actor,
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=fanout_id,
            channel_id=world.channel_id,
            published_at=NOW,
            production_submission_id=master,
        ),
    )
    second = await world.publications.register_publication(
        actor=world.actor,
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=fanout_id,
            channel_id=world.second_channel_id,
            published_at=NOW + timedelta(days=1),
            # The same file on a second channel. Step 1F.2.3f allows it: one
            # output may be published as often as it is actually posted.
            production_submission_id=master,
        ),
    )
    assert first.was_first is True
    assert second.was_first is False
    assert second.new_stage is None
    assert await stage_of(world, fanout_id) is PrWorkflowStage.PUBLISHED
    assert len(await world.publications.list_publications(fanout_id)) == 2
    assert content_id != fanout_id


async def test_publishing_to_an_unplanned_channel_is_recorded(world: PrWorld) -> None:
    """Step 1F.2.3f reversed this, deliberately.

    It used to be a refusal, and that was right for a piece published once on the
    plan somebody wrote in August. It is wrong for what a content item actually
    is: in October a channel exists that did not exist when the targets were
    chosen, and requiring a planned target there leaves exactly two workarounds -
    clone the content, or back-date a plan nobody made - both of which corrupt
    the record worse than an unplanned publication ever could.

    So the target became **linkage rather than permission**: absent here, and the
    publication is recorded against the canonical channel anyway.
    """
    content_id = await make_content(world, with_targets=True)
    await to_ready_to_publish(world, content_id)
    outcome = await world.publications.register_publication(
        actor=world.actor,
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.second_channel_id,
            published_at=NOW,
            production_submission_id=await master_of(world, content_id),
        ),
    )
    assert outcome.target is None
    assert outcome.publication.channel_id == world.second_channel_id
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED

    # A channel that does not exist at all is still a refusal, and names the
    # field - that check moved into the service when the target stopped being a
    # gate, rather than disappearing with it.
    with pytest.raises(PrNotFoundError) as raised:
        await world.publications.register_publication(
            actor=world.actor,
            request_id=world.request_id,
            command=RegisterPublicationCommand(
                content_id=content_id,
                channel_id=uuid.uuid4(),
                published_at=NOW,
                production_submission_id=await master_of(world, content_id),
            ),
        )
    assert raised.value.details["field"] == "channel_id"


async def test_publishing_before_the_content_is_ready_is_refused(world: PrWorld) -> None:
    content_id = await make_content(world, with_targets=True)
    with pytest.raises(PrWorkflowTransitionError):
        await world.publications.register_publication(
            actor=world.actor,
            request_id=world.request_id,
            command=RegisterPublicationCommand(
                content_id=content_id,
                channel_id=world.channel_id,
                published_at=NOW,
            ),
        )


# ===========================================================================
# MEASUREMENT
# ===========================================================================


async def test_measured_is_retired_and_publication_archives_directly(
    world: PrWorld,
) -> None:
    """Step 1F.2.3f.5. The tail of the lifecycle, as it now is.

    These two tests used to be *"``MEASURED`` is refused without a snapshot"*
    and *"accepted once one exists"*. The stage is retired, so both questions
    are gone and the one that replaces them is the one that matters: a published
    piece can still be archived, and it no longer has to pass through a
    measurement claim to get there.

    Archiving used to be reachable only via ``PUBLISHED -> MEASURED ->
    ARCHIVED``, which meant a piece nobody had measured could never be put away.
    """
    content_id = await make_content(world, with_targets=True)
    await to_ready_to_publish(world, content_id)
    await world.publications.register_publication(
        actor=world.actor,
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=NOW,
            production_submission_id=await master_of(world, content_id),
        ),
    )
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED

    # The retired stage is refused by name, and the content does not move.
    with pytest.raises(PrWorkflowTransitionError) as raised:
        await advance(world, content_id, PrWorkflowStage.MEASURED)
    assert raised.value.details["reason"] == "measured_stage_retired"
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED

    # And archiving works straight from publication, with no snapshot involved.
    await advance(world, content_id, PrWorkflowStage.ARCHIVED)
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    assert content.workflow_stage is PrWorkflowStage.ARCHIVED
    assert content.archived_at is not None


async def test_metrics_are_recorded_without_any_lifecycle_move(world: PrWorld) -> None:
    """Part K. **Analytics survived the stage; they never depended on it.**

    This replaces *"a snapshot against another content item does not count"* -
    a test of the per-content ``MEASURED`` precondition, which went with the
    stage. What is worth asserting now is the thing the retirement must not have
    broken: a metric snapshot can still be written against a publication, and
    recording it moves the content nowhere.

    That was always true - ``MEASURED`` was a claim somebody made *after*
    measuring, never a consequence of it - and this pins it so the two stay
    separable.
    """
    content_id = await make_content(world, with_targets=True)
    await to_ready_to_publish(world, content_id)
    outcome = await world.publications.register_publication(
        actor=world.actor,
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=NOW,
            production_submission_id=await master_of(world, content_id),
        ),
    )
    world.session.add(
        PrPostMetricSnapshot(
            publication_id=outcome.publication.id,
            observed_at=NOW + timedelta(days=1),
            source=PrMetricSource.MANUAL,
            views=1200,
        )
    )
    await world.session.flush()

    # The numbers are there, and the content has not moved.
    stored = await world.session.scalar(
        select(func.count())
        .select_from(PrPostMetricSnapshot)
        .where(PrPostMetricSnapshot.publication_id == outcome.publication.id)
    )
    assert stored == 1
    assert await stage_of(world, content_id) is PrWorkflowStage.PUBLISHED


# ===========================================================================
# TASKS
# ===========================================================================


async def make_task(world: PrWorld) -> uuid.UUID:
    task = await world.tasks.create_task(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateTaskCommand(task_type="SCRIPT", title="Write the script"),
    )
    return task.id


async def test_a_new_task_starts_at_todo(world: PrWorld) -> None:
    task_id = await make_task(world)
    task = await world.tasks.require_task(task_id)
    assert task.status is PrTaskStatus.TODO


@pytest.mark.parametrize(
    "path",
    [
        (PrTaskStatus.IN_PROGRESS, PrTaskStatus.DONE),
        (PrTaskStatus.IN_PROGRESS, PrTaskStatus.BLOCKED, PrTaskStatus.IN_PROGRESS),
        (
            PrTaskStatus.IN_PROGRESS,
            PrTaskStatus.IN_REVIEW,
            PrTaskStatus.REVISION_REQUIRED,
            PrTaskStatus.IN_PROGRESS,
            PrTaskStatus.DONE,
        ),
        (PrTaskStatus.CANCELLED,),
        (PrTaskStatus.IN_PROGRESS, PrTaskStatus.IN_REVIEW, PrTaskStatus.DONE),
    ],
)
async def test_legal_task_paths_are_walkable(
    world: PrWorld, path: tuple[PrTaskStatus, ...]
) -> None:
    task_id = await make_task(
        world,
    )
    for status in path:
        await world.tasks.change_status(
            actor=world.actor, request_id=world.request_id, task_id=task_id, target=status
        )
    task = await world.tasks.require_task(task_id)
    assert task.status is path[-1]
    if path[-1] is PrTaskStatus.DONE:
        assert task.completed_at is not None


@pytest.mark.parametrize(
    "target",
    [PrTaskStatus.DONE, PrTaskStatus.IN_REVIEW, PrTaskStatus.BLOCKED, PrTaskStatus.TODO],
)
async def test_illegal_transitions_out_of_todo_are_refused(
    world: PrWorld, target: PrTaskStatus
) -> None:
    task_id = await make_task(world)
    with pytest.raises(PrWorkflowTransitionError):
        await world.tasks.change_status(
            actor=world.actor, request_id=world.request_id, task_id=task_id, target=target
        )


@pytest.mark.parametrize("terminal", [PrTaskStatus.DONE, PrTaskStatus.CANCELLED])
async def test_a_terminal_task_cannot_be_moved_again(
    world: PrWorld, terminal: PrTaskStatus
) -> None:
    task_id = await make_task(world)
    if terminal is PrTaskStatus.DONE:
        await world.tasks.change_status(
            actor=world.actor,
            request_id=world.request_id,
            task_id=task_id,
            target=PrTaskStatus.IN_PROGRESS,
        )
    await world.tasks.change_status(
        actor=world.actor, request_id=world.request_id, task_id=task_id, target=terminal
    )
    for target in PrTaskStatus:
        with pytest.raises(PrWorkflowTransitionError):
            await world.tasks.change_status(
                actor=world.actor, request_id=world.request_id, task_id=task_id, target=target
            )


async def test_a_person_may_hold_two_roles_but_not_the_same_one_twice(world: PrWorld) -> None:
    task_id = await make_task(world)
    await world.tasks.assign_user(
        actor=world.actor,
        request_id=world.request_id,
        task_id=task_id,
        user_id=world.user_id,
        assignment_role=PrTaskAssignmentRole.OWNER,
    )
    await world.tasks.assign_user(
        actor=world.actor,
        request_id=world.request_id,
        task_id=task_id,
        user_id=world.user_id,
        assignment_role=PrTaskAssignmentRole.REVIEWER,
    )
    with pytest.raises(PrConflictError):
        await world.tasks.assign_user(
            actor=world.actor,
            request_id=world.request_id,
            task_id=task_id,
            user_id=world.user_id,
            assignment_role=PrTaskAssignmentRole.OWNER,
        )
    assert len(await world.tasks.list_assignments(task_id)) == 2


async def test_somebody_who_has_not_finished_may_be_unassigned(world: PrWorld) -> None:
    task_id = await make_task(world)
    await world.tasks.assign_user(
        actor=world.actor,
        request_id=world.request_id,
        task_id=task_id,
        user_id=world.user_id,
        assignment_role=PrTaskAssignmentRole.CONTRIBUTOR,
    )
    await world.tasks.unassign_user(
        actor=world.actor,
        request_id=world.request_id,
        task_id=task_id,
        user_id=world.user_id,
        assignment_role=PrTaskAssignmentRole.CONTRIBUTOR,
    )
    assert await world.tasks.list_assignments(task_id) == []


async def test_somebody_who_finished_their_part_is_not_removed(world: PrWorld) -> None:
    task_id = await make_task(world)
    assignment = await world.tasks.assign_user(
        actor=world.actor,
        request_id=world.request_id,
        task_id=task_id,
        user_id=world.user_id,
        assignment_role=PrTaskAssignmentRole.OWNER,
    )
    assignment.completed_at = NOW
    await world.session.flush()
    with pytest.raises(PrConflictError):
        await world.tasks.unassign_user(
            actor=world.actor,
            request_id=world.request_id,
            task_id=task_id,
            user_id=world.user_id,
            assignment_role=PrTaskAssignmentRole.OWNER,
        )


async def test_task_status_is_never_derived_from_the_content_workflow(world: PrWorld) -> None:
    """Approving a script does not finish the task of writing it."""
    content_id = await make_content(world)
    task = await world.tasks.create_task(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateTaskCommand(task_type="SCRIPT", title="Script it", content_id=content_id),
    )
    await to_approved(world, content_id)
    await world.session.refresh(task)
    assert task.status is PrTaskStatus.TODO
    assert task.completed_at is None


# ===========================================================================
# CHANNEL ASSIGNMENTS
# ===========================================================================


async def assign(
    world: PrWorld,
    *,
    effective_from: date,
    effective_to: date | None,
    role: PrChannelAssignmentRole = PrChannelAssignmentRole.CHANNEL_OWNER,
) -> PrChannelAssignment:
    return await world.channels.assign_user(
        actor=world.actor,
        request_id=world.request_id,
        channel_id=world.channel_id,
        user_id=world.user_id,
        assignment_role=role,
        effective_from=effective_from,
        effective_to=effective_to,
    )


async def test_consecutive_historical_ranges_are_accepted(world: PrWorld) -> None:
    """Closed intervals: the next period starts the day after the last ends."""
    await assign(world, effective_from=date(2025, 1, 1), effective_to=date(2025, 6, 30))
    await assign(world, effective_from=date(2025, 7, 1), effective_to=date(2025, 12, 31))
    assignments = await world.channels.list_channel_assignments(
        actor=world.actor, channel_id=world.channel_id
    )
    assert len(assignments) == 2


async def test_overlapping_closed_ranges_are_refused(world: PrWorld) -> None:
    """The case the partial unique index accepts and the service does not."""
    await assign(world, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30))
    with pytest.raises(PrAssignmentOverlapError):
        await assign(world, effective_from=date(2026, 3, 1), effective_to=date(2026, 9, 30))


async def test_a_shared_handover_day_is_refused(world: PrWorld) -> None:
    """Closed semantics, stated as a test rather than only in prose.

    ``effective_to`` is the last day in force, so ending on the 30th and
    starting on the 30th means two owners on the 30th.
    """
    await assign(world, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30))
    with pytest.raises(PrAssignmentOverlapError):
        await assign(world, effective_from=date(2026, 6, 30), effective_to=date(2026, 12, 31))


async def test_a_closed_range_overlapping_an_open_one_is_refused(world: PrWorld) -> None:
    await assign(world, effective_from=date(2026, 1, 1), effective_to=None)
    with pytest.raises(PrAssignmentOverlapError):
        await assign(world, effective_from=date(2026, 3, 1), effective_to=date(2026, 6, 30))


async def test_a_range_entirely_before_an_open_one_is_accepted(world: PrWorld) -> None:
    await assign(world, effective_from=date(2026, 1, 1), effective_to=None)
    await assign(world, effective_from=date(2025, 1, 1), effective_to=date(2025, 12, 31))
    assignments = await world.channels.list_channel_assignments(
        actor=world.actor, channel_id=world.channel_id
    )
    assert len(assignments) == 2


async def test_two_different_roles_over_the_same_dates_stay_legal(world: PrWorld) -> None:
    """Overlap only means something within one (channel, person, role)."""
    await assign(
        world,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        role=PrChannelAssignmentRole.CHANNEL_OWNER,
    )
    await assign(
        world,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        role=PrChannelAssignmentRole.ANALYTICS_OWNER,
    )
    assignments = await world.channels.list_channel_assignments(
        actor=world.actor, channel_id=world.channel_id
    )
    assert len(assignments) == 2


async def test_an_assignment_cannot_end_before_it_starts(world: PrWorld) -> None:
    with pytest.raises(PrValidationError):
        await assign(world, effective_from=date(2026, 3, 10), effective_to=date(2026, 3, 9))


async def test_closing_an_assignment_sets_an_end_date_and_never_deletes(world: PrWorld) -> None:
    assignment = await assign(world, effective_from=date(2026, 1, 1), effective_to=None)
    closed = await world.channels.close_assignment(
        actor=world.actor,
        request_id=world.request_id,
        assignment_id=assignment.id,
        effective_to=date(2026, 6, 30),
    )
    assert closed.effective_to == date(2026, 6, 30)
    assert await count(world.session, PrChannelAssignment) == 1

    with pytest.raises(PrConflictError):
        await world.channels.close_assignment(
            actor=world.actor,
            request_id=world.request_id,
            assignment_id=assignment.id,
            effective_to=date(2026, 7, 31),
        )


async def test_a_brand_code_cannot_be_changed_through_the_service(world: PrWorld) -> None:
    with pytest.raises(PrImmutableFieldError):
        await world.channels.update_brand(
            actor=world.actor,
            request_id=world.request_id,
            brand_id=world.brand_id,
            code="BRND-RENAMED",
        )


async def test_a_channel_can_be_created_and_updated_but_keeps_its_code(world: PrWorld) -> None:
    channel = await world.channels.create_channel(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateChannelCommand(
            name="Channel Three",
            platform_id=(await world.session.get(PrChannel, world.channel_id)).platform_id,  # type: ignore[union-attr]
            category=PrChannelCategory.TEST,
            tier=2,
        ),
    )
    assert channel.tier == 2
    # ``UpdateChannelCommand`` has no ``code`` field at all - the immutability
    # is in the shape of the command, not in a runtime check.
    from meobot.application.pr_channel_service import UpdateChannelCommand

    assert "code" not in UpdateChannelCommand.__dataclass_fields__


async def test_an_employee_may_not_manage_channels(world: PrWorld) -> None:
    with pytest.raises(PrPermissionDeniedError):
        await world.channels.create_channel(
            actor=world.employee,
            request_id=world.request_id,
            command=CreateChannelCommand(
                name="Denied",
                platform_id=uuid.uuid4(),
                category=PrChannelCategory.TEST,
            ),
        )


# ===========================================================================
# TRANSACTIONS AND EVENTS
# ===========================================================================


async def test_a_failed_review_leaves_no_review_and_no_transition(world: PrWorld) -> None:
    """The command raised before writing, so nothing partial survives.

    The caller owns the transaction and would roll back; this asserts the
    stronger property that the service had not written the review row before
    it discovered the problem.
    """
    content_id = await make_content(world)
    await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(content_id=content_id, expected_version=1, title="Second"),
    )
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    before = await count(world.session, PrAiReview)
    with pytest.raises(PrReviewVersionMismatchError):
        await record_full_review(world, content_id, version=1)
    assert await count(world.session, PrAiReview) == before
    assert await stage_of(world, content_id) is PrWorkflowStage.AI_REVIEW


async def test_a_failed_creation_leaves_no_content_and_no_version(world: PrWorld) -> None:
    before_content = await count(world.session, PrContentItem)
    before_versions = await count(world.session, PrContentVersion)
    with pytest.raises(PrNotFoundError):
        await world.content.create_content(
            actor=world.actor,
            request_id=world.request_id,
            command=CreateContentCommand(
                title="Orphan",
                brand_id=uuid.uuid4(),
                owner_user_id=world.user_id,
            ),
        )
    assert await count(world.session, PrContentItem) == before_content
    assert await count(world.session, PrContentVersion) == before_versions


async def test_every_command_records_its_domain_event(world: PrWorld) -> None:
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await approve_at(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)

    result = await world.session.execute(select(AuditLog.action))
    actions = [row[0] for row in result.all()]
    assert "pr.content.created" in actions
    assert "pr.content.version_created" in actions
    assert "pr.content.stage_changed" in actions
    assert "pr.ai_review.recorded" in actions
    assert "pr.approval.recorded" in actions


async def test_no_pr_service_sends_a_telegram_message(world: PrWorld) -> None:
    """Step 1C queues nothing for delivery, so nothing is enqueued.

    The outbox is a *notification* outbox; see
    ``meobot.application.pr_support``. Asserted rather than assumed, because
    "we did not notify anybody" is exactly the kind of omission that is easy
    to reverse by accident.
    """
    from meobot.db.models.notifications import OutboundMessage

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    assert await count(world.session, OutboundMessage) == 0


# ===========================================================================
# QUERY SERVICE
# ===========================================================================


async def test_the_review_context_carries_the_current_version_and_its_verdict(
    world: PrWorld,
) -> None:
    content_id = await make_content(world)
    await advance(
        world,
        content_id,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    )
    await world.ai.record_review(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=1,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=PrAiReviewResult.PASS_WITH_WARNINGS,
            score=Decimal("81.50"),
            summary="Two fixable problems.",
            issues=[{"code": "HOOK_WEAK", "severity": "WARNING", "message": "Flat opening."}],
            suggestions=[{"message": "Lead with the question."}],
            policy_flags=[{"code": "IP", "severity": "WARNING", "message": "Licensed track."}],
            model_name="claude-opus-5",
            model_version="20260501",
            prompt_version="p@2",
            reviewed_at=NOW,
        ),
    )
    await world.approvals.record_decision(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.user_id,
            approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
            decision=PrApprovalDecision.APPROVED,
            version_reviewed=1,
            comment="Warnings noted.",
        ),
    )

    context = await world.queries.get_content_review_context(
        actor=world.actor, content_id=content_id
    )
    assert context.current_version.version_no == 1
    assert context.ai_result is PrAiReviewResult.PASS_WITH_WARNINGS
    assert context.ai_has_warnings is True
    assert context.ai_score == Decimal("81.50")
    assert context.ai_summary == "Two fixable problems."
    assert context.ai_issues[0]["code"] == "HOOK_WEAK"
    assert context.ai_suggestions[0]["message"] == "Lead with the question."
    assert context.ai_policy_flags[0]["code"] == "IP"
    assert len(context.approvals) == 1
    assert context.approvals[0].comment == "Warnings noted."


async def test_a_plain_pass_is_distinguishable_from_pass_with_warnings(world: PrWorld) -> None:
    """The one simplification this module must never make."""
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    context = await world.queries.get_content_review_context(
        actor=world.actor, content_id=content_id
    )
    assert context.ai_result is PrAiReviewResult.PASS
    assert context.ai_has_warnings is False
    # No flattened boolean exists to be misread.
    assert not hasattr(context, "ai_passed")


async def test_the_review_context_reports_no_verdict_for_a_fresh_draft(world: PrWorld) -> None:
    """After a rewrite, the previous draft's PASS is not offered as this one's."""
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await world.approvals.record_decision(
        actor=world.actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.user_id,
            approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
            decision=PrApprovalDecision.REVISION_REQUIRED,
            version_reviewed=1,
        ),
    )
    await world.content.revise_content(
        actor=world.actor,
        request_id=world.request_id,
        command=ReviseContentCommand(content_id=content_id, expected_version=1, title="Draft 2"),
    )
    context = await world.queries.get_content_review_context(
        actor=world.actor, content_id=content_id
    )
    assert context.current_version.version_no == 2
    assert context.ai_review is None
    assert context.ai_result is None
    assert context.ai_review_matches_current_version is False
    # The old approval is still in the history, at the version it was about.
    assert [event.version_reviewed for event in context.approvals] == [1]


async def test_listing_and_overdue_queries(world: PrWorld) -> None:
    content_id = await make_content(world)
    await world.tasks.create_task(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateTaskCommand(
            task_type="EDIT",
            title="Late one",
            content_id=content_id,
            deadline=NOW - timedelta(days=2),
        ),
    )
    done = await world.tasks.create_task(
        actor=world.actor,
        request_id=world.request_id,
        command=CreateTaskCommand(
            task_type="EDIT",
            title="Late but finished",
            deadline=NOW - timedelta(days=3),
        ),
    )
    await world.tasks.change_status(
        actor=world.actor,
        request_id=world.request_id,
        task_id=done.id,
        target=PrTaskStatus.IN_PROGRESS,
    )
    await world.tasks.change_status(
        actor=world.actor, request_id=world.request_id, task_id=done.id, target=PrTaskStatus.DONE
    )

    overdue = await world.queries.list_overdue_tasks(actor=world.actor, now=NOW)
    assert [task.title for task in overdue] == ["Late one"]

    contents = await world.queries.list_contents(actor=world.actor, brand_id=world.brand_id)
    assert content_id in {item.id for item in contents}

    channels = await world.queries.list_channels(actor=world.actor, brand_id=world.brand_id)
    assert len(channels) == 2

    detail = await world.queries.get_channel(actor=world.actor, channel_id=world.channel_id)
    assert detail.channel.code == "CH-0001"
    assert detail.assignments == ()


async def test_reading_requires_the_read_capability(world: PrWorld) -> None:
    """An employee may read PR content; a guest with no role cannot manage it."""
    content_id = await make_content(world)
    detail = await world.queries.get_content(actor=world.employee, content_id=content_id)
    assert detail.content.id == content_id
