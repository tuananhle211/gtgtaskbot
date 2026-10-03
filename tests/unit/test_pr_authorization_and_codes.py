"""Step 1C.1: who may review which gate, and where codes come from.

Two things are under test, and they share a fixture because they share a
transaction boundary - a code is allocated by the same command the capability
check guards.

The authorization half exists because Step 1C could not express one rule:
``TEAM_LEAD`` holds both ``script.review`` and ``script.approve``, so no
mapping of gates onto permissions could keep one person from signing both. The
tests below are the evidence that it now can.

Concurrency belongs to PostgreSQL and lives in
``tests/integration/test_pr_code_allocation.py``. What is here is the
behaviour: formats, year scoping, capability resolution and the four-eyes rule.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_ai_review_service import PrAiReviewService, RecordAiReviewCommand
from meobot.application.pr_approval_service import PrApprovalService, RecordApprovalCommand
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import CreateChannelCommand, PrChannelService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    PrContentService,
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
from meobot.db.models.pr import PrApprovalEvent, PrBrand, PrContentItem, PrPlatform
from meobot.db.models.pr_authorization import PrUserCapability
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission
from meobot.domain.pr.codes import (
    CODE_PATTERN,
    PrCodeNamespace,
    format_code,
    is_year_scoped,
)
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelCategory,
    PrProductionArtifactType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import (
    APPROVAL_CAPABILITIES,
    GRANT_BACKED,
    PrCapability,
    baseline_permission,
    requires_grant,
)

#: Mid-afternoon in Ho Chi Minh City, comfortably inside 2026 in both UTC and
#: the application timezone, so tests that are not about the year boundary are
#: not accidentally about it.
NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

#: 02:00 on 1 January 2027 in Ho Chi Minh City is 19:00 on 31 December 2026 in
#: UTC. Which year a code lands in is the whole question this instant asks.
NEW_YEAR_LOCAL = datetime(2026, 12, 31, 19, 0, tzinfo=UTC)


@dataclass
class AuthWorld:
    """One seeded database, three people, and every service wired onto it."""

    session: AsyncSession
    owner: Actor
    head: Actor
    employee: Actor
    owner_id: uuid.UUID
    head_id: uuid.UUID
    employee_id: uuid.UUID
    brand_id: uuid.UUID
    platform_id: uuid.UUID
    capabilities: PrCapabilityService
    codes: PrCodeService
    content: PrContentService
    workflow: PrContentWorkflowService
    ai: PrAiReviewService
    approvals: PrApprovalService
    tasks: PrTaskService
    channels: PrChannelService
    publications: PrPublicationService
    queries: PrQueryService

    @property
    def request_id(self) -> uuid.UUID:
        return uuid.uuid4()


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[AuthWorld]:
    """Three people who hold *no* review grants until a test says so.

    Deliberately ungranted at the start: the default state of the system after
    this migration is that nobody may approve anything, and a fixture that
    quietly granted everything would hide the one behaviour that changed.
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

    yield AuthWorld(
        session=session,
        owner=Actor(user_id=owner.id, full_name="PR Owner", role=Role.OWNER),
        head=Actor(user_id=head.id, full_name="PR Head", role=Role.ADMIN),
        employee=Actor(user_id=worker.id, full_name="PR Employee", role=Role.EMPLOYEE),
        owner_id=owner.id,
        head_id=head.id,
        employee_id=worker.id,
        brand_id=brand.id,
        platform_id=platform.id,
        capabilities=capabilities,
        codes=codes,
        content=content,
        workflow=workflow,
        ai=ai,
        approvals=PrApprovalService(session, audit, content, workflow, ai, capabilities),
        tasks=PrTaskService(session, audit, capabilities, codes),
        channels=PrChannelService(session, audit, capabilities, codes),
        publications=PrPublicationService(session, audit, workflow, capabilities, codes, content),
        queries=PrQueryService(session, capabilities),
    )


# --- Helpers ---------------------------------------------------------------


async def grant(world: AuthWorld, user_id: uuid.UUID, capability: PrCapability) -> None:
    await world.capabilities.grant(
        actor=world.owner,
        request_id=world.request_id,
        user_id=user_id,
        capability=capability,
    )


async def to_internal_review(world: AuthWorld, content_id: uuid.UUID) -> None:
    """From ``APPROVED`` to a reviewer watching a cut.

    Step 1F.2.3b put a precondition on each of the two moves - somebody has to
    hold the production, and there has to be a file to review - so the walk sets
    both before it makes them. Written directly rather than through the
    production service: this suite is about *authorization*, the handoff has its
    own tests, and what these need is the state on the far side of it.
    """
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    content.producer_user_id = world.employee_id
    await world.session.flush()
    await world.workflow.request_transition(
        actor=world.owner,
        request_id=world.request_id,
        content_id=content_id,
        target=PrWorkflowStage.PRODUCTION,
    )
    version = await world.content.require_current_version(content_id)
    world.session.add(
        PrProductionSubmission(
            content_id=content_id,
            content_version_id=version.id,
            submission_no=1,
            producer_user_id=world.employee_id,
            submitted_by_user_id=world.employee_id,
            artifact_type=PrProductionArtifactType.DRIVE_LINK,
            location="https://drive.google.com/file/d/1/view",
        )
    )
    await world.session.flush()
    await world.workflow.request_transition(
        actor=world.owner,
        request_id=world.request_id,
        content_id=content_id,
        target=PrWorkflowStage.INTERNAL_REVIEW,
    )


async def make_content(world: AuthWorld, *, with_targets: uuid.UUID | None = None) -> uuid.UUID:
    targets = (ContentTargetSpec(channel_id=with_targets),) if with_targets else ()
    snapshot = await world.content.create_content(
        actor=world.owner,
        request_id=world.request_id,
        command=CreateContentCommand(
            title="A draft",
            brand_id=world.brand_id,
            owner_user_id=world.owner_id,
            script_text="Body.",
            targets=targets,
        ),
    )
    return snapshot.content.id


async def to_team_lead_review(world: AuthWorld, content_id: uuid.UUID, *, version: int = 1) -> None:
    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        await world.workflow.request_transition(
            actor=world.owner, request_id=world.request_id, content_id=content_id, target=stage
        )
    await world.ai.record_review(
        actor=world.owner,
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=version,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=PrAiReviewResult.PASS,
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=NOW,
        ),
    )


async def decide(
    world: AuthWorld,
    *,
    actor: Actor,
    reviewer_user_id: uuid.UUID,
    content_id: uuid.UUID,
    stage: PrApprovalStage,
    decision: PrApprovalDecision = PrApprovalDecision.APPROVED,
    version: int = 1,
) -> PrWorkflowStage:
    outcome = await world.approvals.record_decision(
        actor=actor,
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=reviewer_user_id,
            approval_stage=stage,
            decision=decision,
            version_reviewed=version,
        ),
    )
    return outcome.new_stage


# ===========================================================================
# AUTHORIZATION
# ===========================================================================


def test_the_capability_vocabulary_is_exactly_the_twenty_specified() -> None:
    """Step 1C.1's ten, Step 1F.2.3's three, Step 1F.2.3f.2's one, and M1's five.

    Spelled out rather than counted, so adding a capability is a decision
    somebody records here rather than a diff nobody notices. None of the four
    later ones is grant-backed - see ``test_only_the_three_review_gates_need_a_grant``
    below, which still passes unchanged and is the assertion that matters.

    ``PR_PUBLICATION_CREATE`` was a **split** rather than a widening: recording
    that a post went out is daily contributor work and now sits on
    ``script.submit``, while ``PR_PUBLICATION_REGISTER`` keeps ``publish.social``
    and becomes the administration half - correcting anybody's row, taking one
    back. Giving ``publish.social`` to ``EMPLOYEE`` instead would have handed
    them everything else that permission guards.

    M1's four are the Work Ledger's, and each is a new **pairing** of an
    existing permission rather than a new permission: ``PR_WORK_EXECUTE`` is
    ``script.submit`` (a contributor's own work), ``PR_WORK_MANAGE`` is
    ``video.approve`` (the same pairing behind ``PR_PRODUCTION_ASSIGN``),
    ``PR_WORK_VALIDATE`` is ``script.approve``, and ``PR_WORK_CONFIGURE`` is
    ``settings.write`` like every other piece of PR master data.

    ``PR_WORK_MANAGE`` and ``PR_WORK_VALIDATE`` currently reach the same roles
    and are still two capabilities, because they are two decisions - and
    holding ``PR_WORK_VALIDATE`` is never sufficient on its own: a validator who
    contributed to the work is refused whatever they hold.

    ``PR_WORK_VIEW_ALL`` is the fifth, added by the M1 scope patch and paired
    with ``user.read`` so that it reaches ``ADMIN`` and ``OWNER`` and **not**
    ``TEAM_LEAD``. It exists because M1 shipped with ``PR_WORK_MANAGE`` gating
    the department-wide view, which let a Trưởng nhóm who had assigned one job
    read every colleague's record. Managing work and surveying it are different
    acts; one capability could not separate them.

    ``PR_PERFORMANCE_REVIEW`` is M6's, the twentieth, and the same shape of
    separation one step further on: **assigning work is not judging a month.**
    Paired with ``user.manage`` - already "may make decisions about people" - so
    it reaches ``ADMIN`` and ``OWNER`` and **not** ``TEAM_LEAD``, and holding it
    never permits reviewing yourself. MeoBot still models no team, so there is no
    honest narrower scope; deriving one from channel assignments would make *who
    may rate you* depend on who publishes your work.
    """
    assert [capability.value for capability in PrCapability] == [
        "PR_CONTENT_CREATE",
        "PR_CONTENT_EDIT",
        "PR_TEAM_LEAD_REVIEW",
        "PR_HEAD_REVIEW",
        "PR_INTERNAL_REVIEW",
        "PR_TASK_MANAGE",
        "PR_CHANNEL_MANAGE",
        "PR_PUBLICATION_REGISTER",
        "PR_PUBLICATION_CREATE",
        "PR_CONTENT_CANCEL",
        "PR_CONTENT_TRANSITION",
        "PR_CONTENT_DELETE",
        "PR_PRODUCTION_ASSIGN",
        "PR_PRODUCTION_EXECUTE",
        "PR_WORK_EXECUTE",
        "PR_WORK_MANAGE",
        "PR_WORK_VALIDATE",
        "PR_WORK_CONFIGURE",
        "PR_WORK_VIEW_ALL",
        "PR_PERFORMANCE_REVIEW",
    ]


def test_every_capability_is_built_on_an_existing_permission() -> None:
    """No ``Permission`` member was added, and no role set was changed."""
    for capability in PrCapability:
        assert isinstance(baseline_permission(capability), Permission)


def test_only_the_three_review_gates_need_a_grant() -> None:
    """The minimum persistent authorization, asserted as a boundary.

    Grant-backing everything would be paperwork with no invariant behind it;
    grant-backing nothing would leave the two gates indistinguishable, which
    is the defect this step exists to fix.
    """
    assert {
        PrCapability.PR_TEAM_LEAD_REVIEW,
        PrCapability.PR_HEAD_REVIEW,
        PrCapability.PR_INTERNAL_REVIEW,
    } == GRANT_BACKED
    for capability in PrCapability:
        assert requires_grant(capability) is (capability in GRANT_BACKED)


def test_the_role_matrix_alone_cannot_separate_the_two_gates() -> None:
    """The defect Step 1C reported, pinned so nobody 'fixes' it by deleting the table.

    ``TEAM_LEAD`` holds the permission behind *both* review capabilities. That
    is why a grant is required in addition, and if this ever stops being true
    the design decision behind ``pr_user_capabilities`` deserves revisiting -
    which is exactly what a failure here should prompt.
    """
    team_lead_permission = baseline_permission(PrCapability.PR_TEAM_LEAD_REVIEW)
    head_permission = baseline_permission(PrCapability.PR_HEAD_REVIEW)
    assert team_lead_permission is not head_permission
    assert has_permission(Role.TEAM_LEAD, team_lead_permission)
    assert has_permission(Role.TEAM_LEAD, head_permission)


def test_every_approval_gate_maps_to_its_own_capability() -> None:
    assert APPROVAL_CAPABILITIES == {
        PrApprovalStage.TEAM_LEAD_REVIEW: PrCapability.PR_TEAM_LEAD_REVIEW,
        PrApprovalStage.HEAD_REVIEW: PrCapability.PR_HEAD_REVIEW,
        PrApprovalStage.INTERNAL_REVIEW: PrCapability.PR_INTERNAL_REVIEW,
    }
    assert len(set(APPROVAL_CAPABILITIES.values())) == 3


@pytest.mark.asyncio
async def test_a_role_alone_grants_no_review_capability(world: AuthWorld) -> None:
    """Even the owner, who holds every permission, starts with no gate rights."""
    for capability in GRANT_BACKED:
        assert await world.capabilities.allows(world.owner, capability) is False


@pytest.mark.asyncio
async def test_a_grant_makes_exactly_one_gate_available(world: AuthWorld) -> None:
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    assert await world.capabilities.allows(world.owner, PrCapability.PR_TEAM_LEAD_REVIEW)
    assert not await world.capabilities.allows(world.owner, PrCapability.PR_HEAD_REVIEW)
    assert not await world.capabilities.allows(world.owner, PrCapability.PR_INTERNAL_REVIEW)


@pytest.mark.asyncio
async def test_a_grant_authorizes_regardless_of_the_role(world: AuthWorld) -> None:
    """Step 1F.2.7 turned the rule this test used to assert on its head.

    An employee holds neither ``script.review`` nor ``script.approve``, and
    until Step 1F.2.7 a grant to one of them did nothing at all. It now
    authorises on its own: that is the business requirement - somebody reviews
    a defined slice of the work without being promoted - and it is why the
    grant carries a scope.

    Their **role is untouched**: they gain the granted gate and nothing else.
    """
    await grant(world, world.employee_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    assert await world.capabilities.allows(world.employee, PrCapability.PR_TEAM_LEAD_REVIEW)
    assert not await world.capabilities.allows(world.employee, PrCapability.PR_HEAD_REVIEW)
    assert world.employee.role is Role.EMPLOYEE
    assert not has_permission(Role.EMPLOYEE, Permission.SCRIPT_REVIEW)


@pytest.mark.asyncio
async def test_a_grant_written_under_the_old_rule_still_narrows(world: AuthWorld) -> None:
    """What revision 0031 migrates every pre-1F.2.7 row to.

    ``requires_role_baseline`` preserves the conjunctive reading for grants that
    were given under it, so migrating them cannot hand anybody an authority
    nobody decided to give.
    """
    await world.capabilities.grant(
        actor=world.owner,
        request_id=world.request_id,
        user_id=world.employee_id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
        requires_role_baseline=True,
    )
    assert not await world.capabilities.allows(world.employee, PrCapability.PR_TEAM_LEAD_REVIEW)
    # And the same row does authorise somebody whose role carries the permission.
    await world.capabilities.grant(
        actor=world.owner,
        request_id=world.request_id,
        user_id=world.head_id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
        requires_role_baseline=True,
    )
    assert await world.capabilities.allows(world.head, PrCapability.PR_TEAM_LEAD_REVIEW)


@pytest.mark.asyncio
async def test_the_two_failure_reasons_are_distinguishable(world: AuthWorld) -> None:
    """A client can tell "ask for a grant" from "this item is outside it"."""
    with pytest.raises(PrPermissionDeniedError) as no_grant:
        await world.capabilities.require(world.owner, PrCapability.PR_HEAD_REVIEW)
    assert no_grant.value.details["reason"] == "missing_grant"

    with pytest.raises(PrPermissionDeniedError) as no_permission:
        await world.capabilities.require(world.employee, PrCapability.PR_CHANNEL_MANAGE)
    assert no_permission.value.details["reason"] == "missing_permission"


@pytest.mark.asyncio
async def test_authorization_never_looks_at_a_telegram_identity(world: AuthWorld) -> None:
    """Requirement 5: the decision is about ``users.id`` and a role.

    Two actors with the same user and role but wildly different Telegram
    identities - including none at all - get the same answer.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    with_telegram = world.owner.model_copy(
        update={"telegram_user_id": 999_000_111, "telegram_username": "someone_else"}
    )
    without_telegram = world.owner.model_copy(
        update={"telegram_user_id": None, "telegram_username": None}
    )
    for actor in (world.owner, with_telegram, without_telegram):
        assert await world.capabilities.allows(actor, PrCapability.PR_TEAM_LEAD_REVIEW)

    # And an actor with no ``users`` row cannot hold a grant at all, whatever
    # Telegram says about them.
    bootstrap = Actor(
        user_id=None,
        telegram_user_id=777_000_111,
        role=Role.OWNER,
        is_bootstrap_owner=True,
    )
    with pytest.raises(PrPermissionDeniedError) as raised:
        await world.capabilities.require(bootstrap, PrCapability.PR_TEAM_LEAD_REVIEW)
    assert raised.value.details["reason"] == "actor_has_no_user_row"


@pytest.mark.asyncio
async def test_a_revoked_grant_stops_working_immediately(world: AuthWorld) -> None:
    """Revocation is an instant, not a date, and no ``on`` argument revives it.

    Step 1F.2.7. Revoking used to mean dating ``effective_to`` to today - and a
    closed interval *includes* its end date, so the holder kept approving until
    midnight. ``revoked_at`` is checked before the dates and outside them, so
    the right is gone on the next request and cannot be recovered by asking the
    question about an earlier day.

    The row survives, which is the other half: an approval recorded while the
    grant was open is still explicable from the audit trail and from the row
    itself, both of which say when it was withdrawn and by whom.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    row = await world.capabilities.revoke(
        actor=world.owner,
        request_id=world.request_id,
        user_id=world.owner_id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
        effective_to=date(2026, 5, 31),
    )
    assert row.revoked_at is not None
    assert row.revoked_by_user_id == world.owner_id
    for day in (date(2026, 5, 1), date(2026, 5, 31), date(2026, 6, 1)):
        assert not await world.capabilities.allows(
            world.owner, PrCapability.PR_TEAM_LEAD_REVIEW, on=day
        )
    assert await world.session.get(PrUserCapability, row.id) is not None


@pytest.mark.asyncio
async def test_an_expiry_is_dated_and_a_revocation_is_not(world: AuthWorld) -> None:
    """``effective_to`` on its own still reads as a closed date interval."""
    await world.capabilities.grant(
        actor=world.owner,
        request_id=world.request_id,
        user_id=world.owner_id,
        capability=PrCapability.PR_TEAM_LEAD_REVIEW,
        effective_to=date(2026, 5, 31),
    )
    assert await world.capabilities.allows(
        world.owner, PrCapability.PR_TEAM_LEAD_REVIEW, on=date(2026, 5, 31)
    )
    assert not await world.capabilities.allows(
        world.owner, PrCapability.PR_TEAM_LEAD_REVIEW, on=date(2026, 6, 1)
    )


@pytest.mark.asyncio
async def test_a_capability_decided_by_role_cannot_be_granted(world: AuthWorld) -> None:
    with pytest.raises(PrValidationError):
        await grant(world, world.owner_id, PrCapability.PR_CONTENT_CREATE)


@pytest.mark.asyncio
async def test_only_an_owner_may_hand_out_review_rights(world: AuthWorld) -> None:
    """Reuses ``user.role.manage`` - no capability guards itself."""
    with pytest.raises(PrPermissionDeniedError):
        await world.capabilities.grant(
            actor=world.head,
            request_id=world.request_id,
            user_id=world.head_id,
            capability=PrCapability.PR_HEAD_REVIEW,
        )


@pytest.mark.asyncio
async def test_granting_twice_is_refused_and_revoking_nothing_is_refused(
    world: AuthWorld,
) -> None:
    await grant(world, world.head_id, PrCapability.PR_HEAD_REVIEW)
    with pytest.raises(PrConflictError):
        await grant(world, world.head_id, PrCapability.PR_HEAD_REVIEW)
    with pytest.raises(PrNotFoundError):
        await world.capabilities.revoke(
            actor=world.owner,
            request_id=world.request_id,
            user_id=world.owner_id,
            capability=PrCapability.PR_HEAD_REVIEW,
        )


@pytest.mark.asyncio
async def test_grants_and_revocations_are_audited(world: AuthWorld) -> None:
    await grant(world, world.head_id, PrCapability.PR_HEAD_REVIEW)
    await world.capabilities.revoke(
        actor=world.owner,
        request_id=world.request_id,
        user_id=world.head_id,
        capability=PrCapability.PR_HEAD_REVIEW,
    )
    actions = [row[0] for row in (await world.session.execute(select(AuditLog.action))).all()]
    assert "pr.capability.granted" in actions
    assert "pr.capability.revoked" in actions


# --- The gates, end to end -------------------------------------------------


@pytest.mark.asyncio
async def test_team_lead_review_requires_its_capability(world: AuthWorld) -> None:
    content_id = await make_content(world)
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await to_team_lead_review(world, content_id)

    with pytest.raises(PrPermissionDeniedError) as raised:
        await decide(
            world,
            actor=world.head,
            reviewer_user_id=world.head_id,
            content_id=content_id,
            stage=PrApprovalStage.TEAM_LEAD_REVIEW,
        )
    assert raised.value.details["capability"] == PrCapability.PR_TEAM_LEAD_REVIEW.value

    assert (
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.TEAM_LEAD_REVIEW,
        )
        is PrWorkflowStage.HEAD_REVIEW
    )


@pytest.mark.asyncio
async def test_head_review_requires_its_capability(world: AuthWorld) -> None:
    content_id = await make_content(world)
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )

    # The owner holds every *permission* and still cannot pass the head gate.
    with pytest.raises(PrPermissionDeniedError) as raised:
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
    assert raised.value.details["reason"] == "missing_grant"

    await grant(world, world.head_id, PrCapability.PR_HEAD_REVIEW)
    assert (
        await decide(
            world,
            actor=world.head,
            reviewer_user_id=world.head_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
        is PrWorkflowStage.APPROVED
    )


@pytest.mark.asyncio
async def test_internal_review_requires_its_capability(world: AuthWorld) -> None:
    content_id = await make_content(world)
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.head_id, PrCapability.PR_HEAD_REVIEW)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )
    await decide(
        world,
        actor=world.head,
        reviewer_user_id=world.head_id,
        content_id=content_id,
        stage=PrApprovalStage.HEAD_REVIEW,
    )
    await to_internal_review(world, content_id)

    with pytest.raises(PrPermissionDeniedError):
        await decide(
            world,
            actor=world.head,
            reviewer_user_id=world.head_id,
            content_id=content_id,
            stage=PrApprovalStage.INTERNAL_REVIEW,
        )

    await grant(world, world.owner_id, PrCapability.PR_INTERNAL_REVIEW)
    assert (
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.INTERNAL_REVIEW,
        )
        is PrWorkflowStage.READY_TO_PUBLISH
    )


# ===========================================================================
# FLEXIBLE REVIEW ROLES  (Step 1F.2.2)
# ===========================================================================
#
# These replace the four-eyes tests. Until Step 1F.2.2 the suite asserted that
# one person could **not** sign both gates for one draft; the requirement was
# withdrawn, because a team whose only two entitled reviewers are one person
# could not approve anything at all. What the tests below hold on to is
# everything that made the two gates two gates - both stages walked, a
# capability each, an event each - which is the part it would be easy to lose
# while relaxing the person rule.


@pytest.mark.asyncio
async def test_one_person_holding_both_grants_may_sign_both_gates(world: AuthWorld) -> None:
    """Requirements 1, 2 and 8: the same human, both gates, one draft.

    The owner holds ``PR_TEAM_LEAD_REVIEW`` and ``PR_HEAD_REVIEW`` in their own
    right, and each approval is checked against its own capability. The content
    still travels ``TEAM_LEAD_REVIEW -> HEAD_REVIEW -> APPROVED``: two
    decisions, two moves, no stage skipped and nothing auto-approved from the
    first signature.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.owner_id, PrCapability.PR_HEAD_REVIEW)

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)

    assert (
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.TEAM_LEAD_REVIEW,
        )
        is PrWorkflowStage.HEAD_REVIEW
    )
    assert (
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
        is PrWorkflowStage.APPROVED
    )


@pytest.mark.asyncio
async def test_both_signatures_are_two_separate_append_only_events(world: AuthWorld) -> None:
    """Requirement 3. One person, two gates, and still two records.

    The failure this guards against is the tempting shortcut: treating the
    team-lead event as evidence for the head gate too, so that one row satisfies
    both. Then "who approved this at head review" would have no answer, and the
    audit trail would claim a gate was passed that nobody signed.

    Each row carries its own stage, actor, version, decision and timestamp, and
    neither is a copy of the other.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.owner_id, PrCapability.PR_HEAD_REVIEW)

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    for stage in (PrApprovalStage.TEAM_LEAD_REVIEW, PrApprovalStage.HEAD_REVIEW):
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=stage,
        )

    history = await world.approvals.history(content_id)
    assert [event.approval_stage for event in history] == [
        PrApprovalStage.TEAM_LEAD_REVIEW,
        PrApprovalStage.HEAD_REVIEW,
    ]
    assert len({event.id for event in history}) == 2
    for event in history:
        assert event.reviewer_user_id == world.owner_id
        assert event.decision is PrApprovalDecision.APPROVED
        assert event.version_reviewed == 1
        assert event.decided_at is not None


@pytest.mark.asyncio
async def test_the_head_gate_still_needs_a_team_lead_approval_of_this_draft(
    world: AuthWorld,
) -> None:
    """Requirement 4. Relaxing *who* did not relax *what came first*.

    The state under test is **structurally unreachable** through the workflow -
    the only edge into ``HEAD_REVIEW`` is a team-lead ``APPROVED``, and
    ``EDITABLE_STAGES`` will not let the draft be rewritten once it is there. So
    it is reached by deleting the event, which is exactly the point: the check is
    a second one at the moment of the write, not a restatement of the matrix, and
    a test that could only reach it through the matrix would not be testing it at
    all.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.owner_id, PrCapability.PR_HEAD_REVIEW)

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )

    signed = (await world.approvals.history(content_id))[0]
    await world.session.delete(signed)
    await world.session.flush()

    with pytest.raises(PrWorkflowTransitionError) as raised:
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
    assert raised.value.details["reason"] == "missing_team_lead_approval"
    # The refusal is total: no head event either, and the content has not moved.
    assert await world.approvals.history(content_id) == []
    content = await world.content.require_content(content_id)
    await world.session.refresh(content)
    assert content.workflow_stage is PrWorkflowStage.HEAD_REVIEW


@pytest.mark.asyncio
async def test_the_head_prerequisite_is_a_boolean_the_read_side_can_ask(
    world: AuthWorld,
) -> None:
    """Requirement 4, as ``/available-actions`` asks it.

    ``head_approval_permitted`` no longer takes a reviewer, which is the whole
    change: the question is about the draft, not about who is looking at it.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)

    assert not await world.approvals.head_approval_permitted(content_id, version_reviewed=1)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )
    assert await world.approvals.head_approval_permitted(content_id, version_reviewed=1)


@pytest.mark.asyncio
async def test_the_team_lead_approver_still_needs_the_head_grant_to_sign_the_head_gate(
    world: AuthWorld,
) -> None:
    """Requirement 5. Holding one grant implies nothing about the other.

    This is the test that keeps the relaxation from becoming role inheritance.
    The owner has just given the team-lead approval and holds every
    ``Permission`` there is; without ``PR_HEAD_REVIEW`` the head gate is still
    shut to them.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )

    with pytest.raises(PrPermissionDeniedError):
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
    # Nothing was written and nothing moved.
    assert len(await world.approvals.history(content_id)) == 1
    content = await world.content.require_content(content_id)
    await world.session.refresh(content)
    assert content.workflow_stage is PrWorkflowStage.HEAD_REVIEW

    # Granted, the same person may now sign it.
    await grant(world, world.owner_id, PrCapability.PR_HEAD_REVIEW)
    assert (
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
        is PrWorkflowStage.APPROVED
    )


@pytest.mark.asyncio
async def test_the_team_lead_gate_still_needs_its_own_grant(world: AuthWorld) -> None:
    """Requirement 6. The head grant does not open the team-lead gate either.

    The mirror of the test above, and worth having separately: an
    implementation that relaxed the pairing by treating the two capabilities as
    interchangeable would pass one of these two and fail the other.
    """
    await grant(world, world.owner_id, PrCapability.PR_HEAD_REVIEW)

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)

    with pytest.raises(PrPermissionDeniedError):
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.TEAM_LEAD_REVIEW,
        )
    assert await world.approvals.history(content_id) == []


@pytest.mark.asyncio
async def test_the_same_person_may_still_reject_or_send_back_at_the_head_gate(
    world: AuthWorld,
) -> None:
    """A reviewer who has to stop bad work must be able to.

    True before Step 1F.2.2 for a subtle reason - the four-eyes rule covered
    only *successful* approvals - and true afterwards for a plain one: nothing
    restricts who may decide at a gate they hold the grant for.
    """
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.owner_id, PrCapability.PR_HEAD_REVIEW)

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )
    assert (
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
            decision=PrApprovalDecision.REVISION_REQUIRED,
        )
        is PrWorkflowStage.SCRIPTING
    )


@pytest.mark.asyncio
async def test_two_different_people_signing_the_two_gates_is_unchanged(
    world: AuthWorld,
) -> None:
    """The ordinary case, which this step must not have disturbed."""
    await grant(world, world.head_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.owner_id, PrCapability.PR_HEAD_REVIEW)

    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.head,
        reviewer_user_id=world.head_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )
    assert (
        await decide(
            world,
            actor=world.owner,
            reviewer_user_id=world.owner_id,
            content_id=content_id,
            stage=PrApprovalStage.HEAD_REVIEW,
        )
        is PrWorkflowStage.APPROVED
    )
    history = await world.approvals.history(content_id)
    assert [event.reviewer_user_id for event in history] == [world.head_id, world.owner_id]


@pytest.mark.asyncio
async def test_the_query_side_can_name_the_team_lead_approver(world: AuthWorld) -> None:
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )
    event = await world.queries.team_lead_approver(
        actor=world.owner, content_id=content_id, version_reviewed=1
    )
    assert isinstance(event, PrApprovalEvent)
    assert event.reviewer_user_id == world.owner_id


@pytest.mark.asyncio
async def test_the_query_side_answers_who_may_review(world: AuthWorld) -> None:
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.head_id, PrCapability.PR_HEAD_REVIEW)

    team_leads = await world.queries.users_allowed_to(
        actor=world.owner, capability=PrCapability.PR_TEAM_LEAD_REVIEW
    )
    heads = await world.queries.users_allowed_to(
        actor=world.owner, capability=PrCapability.PR_HEAD_REVIEW
    )
    assert [grant.user_id for grant in team_leads] == [world.owner_id]
    assert [grant.user_id for grant in heads] == [world.head_id]

    # A role-decided capability has no grant list, and says so honestly.
    assert (
        await world.queries.users_allowed_to(
            actor=world.owner, capability=PrCapability.PR_CONTENT_CREATE
        )
        == []
    )

    mine = await world.queries.capabilities_for_actor(actor=world.owner)
    assert PrCapability.PR_TEAM_LEAD_REVIEW in mine
    assert PrCapability.PR_HEAD_REVIEW not in mine
    assert PrCapability.PR_CONTENT_CREATE in mine

    theirs = await world.queries.capabilities_for_user(actor=world.owner, user_id=world.head_id)
    assert theirs == {PrCapability.PR_HEAD_REVIEW}


# ===========================================================================
# CODE GENERATION
# ===========================================================================


def test_the_five_formats_render_as_specified() -> None:
    assert format_code(PrCodeNamespace.CHANNEL, 1) == "CH-0001"
    assert format_code(PrCodeNamespace.CONTENT, 1, year=2026) == "CNT-2026-000001"
    assert format_code(PrCodeNamespace.TASK, 1, year=2026) == "TSK-2026-000001"
    assert format_code(PrCodeNamespace.PUBLICATION, 1, year=2026) == "PUB-2026-000001"
    assert format_code(PrCodeNamespace.ISSUE, 1, year=2026) == "ISS-2026-000001"
    for code in (
        "CH-0001",
        "CNT-2026-000001",
        "TSK-2026-000001",
        "PUB-2026-000001",
        "ISS-2026-000001",
    ):
        assert CODE_PATTERN.match(code), code


def test_only_the_channel_namespace_is_year_free() -> None:
    assert not is_year_scoped(PrCodeNamespace.CHANNEL)
    for namespace in PrCodeNamespace:
        if namespace is PrCodeNamespace.CHANNEL:
            continue
        assert is_year_scoped(namespace)


def test_rendering_refuses_a_mismatched_year() -> None:
    """The two ways a caller could produce a wrong-shaped code."""
    with pytest.raises(ValueError, match="year"):
        format_code(PrCodeNamespace.CONTENT, 1)
    with pytest.raises(ValueError, match="not year-scoped"):
        format_code(PrCodeNamespace.CHANNEL, 1, year=2026)
    with pytest.raises(ValueError, match="at least 1"):
        format_code(PrCodeNamespace.CHANNEL, 0)


@pytest.mark.asyncio
async def test_the_first_code_in_each_namespace_is_number_one(world: AuthWorld) -> None:
    assert await world.codes.allocate_channel_code() == "CH-0001"
    assert await world.codes.allocate_content_code(at=NOW) == "CNT-2026-000001"
    assert await world.codes.allocate_task_code(at=NOW) == "TSK-2026-000001"
    assert await world.codes.allocate_publication_code(at=NOW) == "PUB-2026-000001"
    assert await world.codes.allocate_issue_code(at=NOW) == "ISS-2026-000001"


@pytest.mark.asyncio
async def test_allocations_increment(world: AuthWorld) -> None:
    assert await world.codes.allocate_channel_code() == "CH-0001"
    assert await world.codes.allocate_channel_code() == "CH-0002"
    assert await world.codes.allocate_channel_code() == "CH-0003"
    assert await world.codes.allocate_content_code(at=NOW) == "CNT-2026-000001"
    assert await world.codes.allocate_content_code(at=NOW) == "CNT-2026-000002"


@pytest.mark.asyncio
async def test_a_new_year_restarts_at_one(world: AuthWorld) -> None:
    assert await world.codes.allocate_content_code(at=NOW) == "CNT-2026-000001"
    assert await world.codes.allocate_content_code(at=NOW) == "CNT-2026-000002"
    next_year = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)
    assert await world.codes.allocate_content_code(at=next_year) == "CNT-2027-000001"
    # And 2026 carries on from where it was, untouched by the new namespace.
    assert await world.codes.allocate_content_code(at=NOW) == "CNT-2026-000003"


@pytest.mark.asyncio
async def test_channel_numbering_ignores_the_year(world: AuthWorld) -> None:
    assert await world.codes.allocate_channel_code() == "CH-0001"
    assert await world.codes.allocate_channel_code() == "CH-0002"
    # Nothing about a channel code changes when the calendar does: there is one
    # counter and no year in the format.
    assert await world.codes.allocate_channel_code() == "CH-0003"


@pytest.mark.asyncio
async def test_the_year_comes_from_the_application_timezone(world: AuthWorld) -> None:
    """19:00 UTC on 31 December is already 2 January-ish locally.

    ``Asia/Ho_Chi_Minh`` is UTC+7, so this instant is 02:00 on 1 January 2027
    in the timezone the business works in - and the code has to say 2027, or
    the first week of the year files itself under the previous one.
    """
    assert world.codes.business_year(NEW_YEAR_LOCAL) == 2027
    assert await world.codes.allocate_content_code(at=NEW_YEAR_LOCAL) == "CNT-2027-000001"


# --- The creation services no longer take a code ---------------------------


def test_no_creation_command_accepts_a_code() -> None:
    """Requirements 22-25, as a property of the contracts themselves."""
    for command in (
        CreateContentCommand,
        CreateTaskCommand,
        CreateChannelCommand,
        RegisterPublicationCommand,
    ):
        assert "code" not in command.__dataclass_fields__, command.__name__


@pytest.mark.asyncio
async def test_created_entities_carry_generated_codes(world: AuthWorld) -> None:
    channel = await world.channels.create_channel(
        actor=world.owner,
        request_id=world.request_id,
        command=CreateChannelCommand(
            name="Channel One",
            platform_id=world.platform_id,
            brand_id=world.brand_id,
            category=PrChannelCategory.SCALE,
        ),
    )
    assert channel.code == "CH-0001"

    content_id = await make_content(world, with_targets=channel.id)
    content = await world.content.require_content(content_id)
    assert content.code.startswith("CNT-")
    assert CODE_PATTERN.match(content.code)

    task = await world.tasks.create_task(
        actor=world.owner,
        request_id=world.request_id,
        command=CreateTaskCommand(task_type="SCRIPT", title="Write it"),
    )
    assert task.code.startswith("TSK-")
    assert CODE_PATTERN.match(task.code)


@pytest.mark.asyncio
async def test_a_publication_code_uses_the_publication_year(world: AuthWorld) -> None:
    """The explicit business instant, not the moment somebody typed it in."""
    channel = await world.channels.create_channel(
        actor=world.owner,
        request_id=world.request_id,
        command=CreateChannelCommand(
            name="Channel One",
            platform_id=world.platform_id,
            brand_id=world.brand_id,
            category=PrChannelCategory.SCALE,
        ),
    )
    content_id = await make_content(world, with_targets=channel.id)
    await grant(world, world.owner_id, PrCapability.PR_TEAM_LEAD_REVIEW)
    await grant(world, world.head_id, PrCapability.PR_HEAD_REVIEW)
    await grant(world, world.owner_id, PrCapability.PR_INTERNAL_REVIEW)

    await to_team_lead_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.TEAM_LEAD_REVIEW,
    )
    await decide(
        world,
        actor=world.head,
        reviewer_user_id=world.head_id,
        content_id=content_id,
        stage=PrApprovalStage.HEAD_REVIEW,
    )
    await to_internal_review(world, content_id)
    await decide(
        world,
        actor=world.owner,
        reviewer_user_id=world.owner_id,
        content_id=content_id,
        stage=PrApprovalStage.INTERNAL_REVIEW,
    )

    # Step 1F.2.3f: a publication names the produced output that went out, so
    # the approved master is looked up rather than left absent.
    found = await world.session.execute(
        select(PrProductionSubmission.id).where(PrProductionSubmission.content_id == content_id)
    )
    outcome = await world.publications.register_publication(
        actor=world.owner,
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=channel.id,
            published_at=NEW_YEAR_LOCAL,
            production_submission_id=found.scalars().one(),
        ),
    )
    assert outcome.publication.code == "PUB-2027-000001"


@pytest.mark.asyncio
async def test_a_code_is_immutable_once_created(world: AuthWorld) -> None:
    """Nothing in the PR services offers a way to change one.

    Asserted as a property of the contracts rather than by trying an update:
    there is no update path to try, and a test that asserted an
    ``AttributeError`` would be testing Python rather than the design.
    """
    from meobot.application.pr_channel_service import UpdateChannelCommand
    from meobot.application.pr_content_service import ReviseContentCommand

    assert "code" not in UpdateChannelCommand.__dataclass_fields__
    assert "code" not in ReviseContentCommand.__dataclass_fields__

    content_id = await make_content(world)
    original = (await world.content.require_content(content_id)).code
    from meobot.application.pr_content_service import ReviseContentCommand as Revise

    await world.content.revise_content(
        actor=world.owner,
        request_id=world.request_id,
        command=Revise(content_id=content_id, expected_version=1, title="Rewritten"),
    )
    assert (await world.content.require_content(content_id)).code == original
