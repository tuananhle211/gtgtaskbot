"""Step 1F.2.3 - producing content, and the third review gate.

Numbered 11-39, continuing the requirement numbering the step was specified
with: 11-19 the producer, 20-29 the artifact, 30-39 internal review.

Requirements 1-10 were the delete half, and they are **not here**. Step 1F.2.3a
replaced that soft delete with permanent aggregate deletion, which is a different
enough thing to have its own file: ``tests/unit/test_pr_permanent_delete.py``.
What remains of the original ten in this file is the part that is about
production rather than about deletion - that a member's right ends at
``PRODUCTION`` - and it lives there too, next to the rule it belongs to.

Everything runs against real SQL on one transaction, and most of it against the
real router over ``TestClient``, because the questions are about SQL and about
authorization: whether two people claiming at once produce one producer, whether
a re-cut leaves two submissions, whether a decision names the file it judged. A
mocked service would answer all three by fiat.

The walk is real
----------------

Content reaches ``PRODUCTION`` here the way it does in life - briefing,
scripting, an AI verdict, a team-lead approval, a head approval, then the manual
move - rather than by assigning ``workflow_stage``. That is deliberate and it is
load-bearing for the whole delete half: ``production_started_at`` is stamped by
the transition, so a fixture that forced the stage would leave the column null
and quietly pass the tests that matter most.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_production_service import SubmitProductionCommand
from meobot.application.pr_services import PrServices, build_pr_services
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrApprovalEvent, PrBrand, PrContentItem, PrPlatform
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrProductionClaimConflictError,
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
    PrProductionHandoff,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.production import handoff_state
from tests.unit.streams import tag_pr

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

#: A well-formed Drive link, used wherever the artifact is not what is being
#: tested. Real-looking on purpose - a validator that only ever saw
#: ``https://x`` would not prove much about the host rule.
DRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view"


@dataclass
class World:
    """One database, five people, and both a service bundle and an HTTP client.

    Both surfaces, because the two halves of this step fail differently: the
    business rules are service-level and the "a direct API call cannot bypass
    this" requirement is only meaningful over the router.
    """

    session: AsyncSession
    client: TestClient
    settings: Settings
    services: PrServices
    #: OWNER. Holds every permission, and therefore management deletion.
    owner: User
    #: TEAM_LEAD, granted ``PR_TEAM_LEAD_REVIEW``. Also manages production.
    lead: User
    #: ADMIN, granted ``PR_HEAD_REVIEW``.
    head: User
    #: EMPLOYEE. May write, may produce, may delete their own untouched work.
    member: User
    #: EMPLOYEE. The unrelated colleague every "not yours" test needs.
    other: User
    brand_id: uuid.UUID
    channel_id: uuid.UUID

    def actor(self, user: User) -> Actor:
        return Actor(user_id=user.id, full_name=user.full_name, role=user.role)

    def act_as(self, user: User) -> None:
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
            user_id=user.id, full_name=user.full_name, role=user.role, active=user.active
        )

    @property
    def request_id(self) -> uuid.UUID:
        return uuid.uuid4()

    async def reload(self, content_id: uuid.UUID) -> PrContentItem:
        row = await self.session.get(PrContentItem, content_id)
        assert row is not None
        await self.session.refresh(row)
        return row

    async def actions(self, user: User, content_id: uuid.UUID) -> set[str]:
        """The action *kinds* the server offers this person, as strings."""
        content = await self.reload(content_id)
        return {
            action.kind.value
            for action in await self.services.actions.for_content(
                actor=self.actor(user), content=content
            )
        }


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    owner = User(full_name="Chị Chủ", role=Role.OWNER)
    lead = User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD)
    head = User(full_name="Hà Trưởng Phòng", role=Role.ADMIN)
    member = User(full_name="Phương Nhung", role=Role.EMPLOYEE)
    other = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-A", name="Apexmed")
    # Not FACEBOOK or TIKTOK: those are policy-grounded, and AI review would then
    # be blocked until a distribution mode was set - a Step 1F.1 rule that has
    # its own tests and nothing to do with production.
    platform = PrPlatform(code="WEBSITE", name="Website")
    session.add_all([owner, lead, head, member, other, brand, platform])
    await session.flush()
    await tag_pr(session, [owner, lead, head, member, other])  # untagged sees no stream

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    services = build_pr_services(session, settings)
    granter = Actor(user_id=owner.id, full_name=owner.full_name, role=Role.OWNER)

    for user, capability in (
        (lead, PrCapability.PR_TEAM_LEAD_REVIEW),
        (head, PrCapability.PR_HEAD_REVIEW),
    ):
        await services.capabilities.grant(
            actor=granter, request_id=uuid.uuid4(), user_id=user.id, capability=capability
        )

    channel = await services.channels.create_channel(
        actor=granter,
        request_id=uuid.uuid4(),
        command=CreateChannelCommand(
            name="Apexmed Website",
            platform_id=platform.id,
            brand_id=brand.id,
            category=PrChannelCategory.SCALE,
        ),
    )

    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        built = World(
            session=session,
            client=client,
            settings=settings,
            services=services,
            owner=owner,
            lead=lead,
            head=head,
            member=member,
            other=other,
            brand_id=brand.id,
            channel_id=channel.id,
        )
        built.act_as(owner)
        yield built
    app.dependency_overrides.clear()


# --- Walking a piece of work through its life -------------------------------


async def make_content(world: World, *, owner: User, title: str = "Bài mới") -> uuid.UUID:
    snapshot = await world.services.content.create_content(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=CreateContentCommand(
            title=title,
            brand_id=world.brand_id,
            owner_user_id=owner.id,
            script_text="Nội dung.",
            targets=(ContentTargetSpec(channel_id=world.channel_id),),
        ),
    )
    return snapshot.content.id


async def to_approved(world: World, content_id: uuid.UUID) -> None:
    """The real walk as far as ``APPROVED``: brief, script, AI review, both gates.

    Stops there, which is the whole of Step 1F.2.3b's Part A: a Head approval
    ends at ``APPROVED`` with nobody producing anything, and getting to
    ``PRODUCTION`` from here takes two further, deliberate acts.
    """
    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        await world.services.workflow.request_transition(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
            target=stage,
        )
    await world.services.ai_reviews.record_review(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=1,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=PrAiReviewResult.PASS,
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=NOW,
        ),
    )
    await decide(
        world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.TEAM_LEAD_REVIEW
    )
    await decide(world, actor=world.head, content_id=content_id, stage=PrApprovalStage.HEAD_REVIEW)


async def to_production(world: World, content_id: uuid.UUID, *, producer: User) -> None:
    """``APPROVED``, then the handoff, then the start - the way it really goes.

    Step 1F.2.3b: production cannot begin without somebody holding the piece, so
    a helper that walked straight from ``APPROVED`` to ``PRODUCTION`` would be
    walking a path the server no longer has.
    """
    await to_approved(world, content_id)
    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=producer.id,
    )
    await world.services.production.start_production(
        actor=world.actor(producer), request_id=world.request_id, content_id=content_id
    )


async def decide(
    world: World,
    *,
    actor: User,
    content_id: uuid.UUID,
    stage: PrApprovalStage,
    decision: PrApprovalDecision = PrApprovalDecision.APPROVED,
    version: int = 1,
) -> PrWorkflowStage:
    outcome = await world.services.approvals.record_decision(
        actor=world.actor(actor),
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=actor.id,
            approval_stage=stage,
            decision=decision,
            version_reviewed=version,
        ),
    )
    return outcome.new_stage


async def approved(world: World) -> uuid.UUID:
    """Content at ``APPROVED`` with nobody holding it - *chờ nhận sản xuất*."""
    content_id = await make_content(world, owner=world.member)
    await to_approved(world, content_id)
    return content_id


async def in_production(world: World, *, producer: User | None = None) -> uuid.UUID:
    """Content standing at ``PRODUCTION``.

    ``producer=None`` produces the one state that needs explaining: production
    that *started* with somebody holding it and was then un-assigned by a
    manager. Since Step 1F.2.3b it is the only way ``PRODUCTION`` and a null
    producer coexist, and the tests that use it are about exactly that.
    """
    content_id = await make_content(world, owner=world.member)
    await to_production(world, content_id, producer=producer or world.member)
    if producer is None:
        await world.services.production.assign_producer(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            content_id=content_id,
            producer_user_id=None,
        )
    return content_id


async def submit(
    world: World,
    content_id: uuid.UUID,
    *,
    actor: User,
    location: str = DRIVE,
    artifact_type: PrProductionArtifactType = PrProductionArtifactType.DRIVE_LINK,
    note: str | None = None,
) -> PrProductionSubmission:
    return await world.services.production.submit_production(
        actor=world.actor(actor),
        request_id=world.request_id,
        command=SubmitProductionCommand(
            content_id=content_id, artifact_type=artifact_type, location=location, note=note
        ),
    )


async def submissions(world: World, content_id: uuid.UUID) -> list[PrProductionSubmission]:
    rows = await world.session.execute(
        select(PrProductionSubmission)
        .where(PrProductionSubmission.content_id == content_id)
        .order_by(PrProductionSubmission.submission_no.asc())
    )
    return list(rows.scalars().all())


async def audit_actions(world: World, entity_id: uuid.UUID) -> list[str]:
    rows = await world.session.execute(
        select(AuditLog.action).where(AuditLog.entity_id == str(entity_id))
    )
    return list(rows.scalars().all())


# ===========================================================================
# 11-19: THE PRODUCER
# ===========================================================================


async def test_11_approval_stops_at_approved_with_nobody_producing(world: World) -> None:
    """Requirement 11, as Step 1F.2.3b corrected it.

    A Head approval ends at ``APPROVED``. Nothing moves on to ``PRODUCTION``,
    ``producer_user_id`` is null, and ``production_started_at`` is unset - which
    together are the state the panel renders as *"Chờ nhận sản xuất"* and the
    reason the handoff is a decision somebody makes rather than a side effect of
    signing off a script.
    """
    content_id = await approved(world)
    row = await world.reload(content_id)
    assert row.workflow_stage is PrWorkflowStage.APPROVED
    assert row.producer_user_id is None
    assert row.production_started_at is None
    assert (
        handoff_state(row.workflow_stage, row.producer_user_id)
        is PrProductionHandoff.WAITING_FOR_PRODUCER
    )


async def test_12_a_manager_assigns_the_producer(world: World) -> None:
    """Requirement 12."""
    content_id = await in_production(world)
    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=world.member.id,
    )
    assert (await world.reload(content_id)).producer_user_id == world.member.id
    assert "pr.production.assigned" in await audit_actions(world, content_id)


async def test_13_an_eligible_member_claims_an_unassigned_production(world: World) -> None:
    """Requirement 13. Volunteering, recorded as its own kind of event.

    ``pr.production.claimed`` rather than ``assigned``: a manager's decision
    about somebody else and a person putting their hand up are different facts,
    and a report that could not tell them apart would misread how work is
    actually distributed.
    """
    content_id = await in_production(world)
    await world.services.production.claim_production(
        actor=world.actor(world.member), request_id=world.request_id, content_id=content_id
    )
    assert (await world.reload(content_id)).producer_user_id == world.member.id
    assert "pr.production.claimed" in await audit_actions(world, content_id)


async def test_14_a_member_cannot_claim_work_somebody_else_holds(world: World) -> None:
    """Requirement 14."""
    content_id = await in_production(world, producer=world.member)
    with pytest.raises(PrProductionClaimConflictError):
        await world.services.production.claim_production(
            actor=world.actor(world.other), request_id=world.request_id, content_id=content_id
        )
    assert (await world.reload(content_id)).producer_user_id == world.member.id


async def test_15_two_simultaneous_claims_produce_exactly_one_producer(world: World) -> None:
    """Requirements 15 and 16. The race, and the loser's answer.

    The other transaction's write is applied with ``synchronize_session=False``
    on purpose: it leaves this transaction's in-memory row saying
    ``producer_user_id IS NULL``, which is exactly the state a second claimant
    is in - they read before the winner wrote. A read-then-write implementation
    would consult that stale row, find it free, and overwrite the winner.

    What refuses is the conditional ``UPDATE ... WHERE producer_user_id IS
    NULL`` changing no rows, and the refusal is a typed domain error carrying
    who actually holds it - not an integrity error and not a raw database
    message.
    """
    content_id = await in_production(world)
    loaded = await world.reload(content_id)
    assert loaded.producer_user_id is None

    await world.session.execute(
        update(PrContentItem)
        .where(PrContentItem.id == content_id)
        .values(producer_user_id=world.other.id)
        .execution_options(synchronize_session=False)
    )
    assert loaded.producer_user_id is None, "the racer's row is stale, as it would be in life"

    with pytest.raises(PrProductionClaimConflictError) as conflict:
        await world.services.production.claim_production(
            actor=world.actor(world.member), request_id=world.request_id, content_id=content_id
        )
    assert conflict.value.details["producer_user_id"] == str(world.other.id)
    assert conflict.value.code == "pr_production_claimed"
    assert (await world.reload(content_id)).producer_user_id == world.other.id


async def test_17_a_manager_reassigns_during_production(world: World) -> None:
    """Requirement 17. Including clearing it, which is the same decision."""
    content_id = await in_production(world, producer=world.member)
    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=world.other.id,
    )
    assert (await world.reload(content_id)).producer_user_id == world.other.id

    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=None,
    )
    assert (await world.reload(content_id)).producer_user_id is None


async def test_18_an_unrelated_member_cannot_take_assigned_work(world: World) -> None:
    """Requirement 18. Two doors, both shut.

    Reassignment needs ``PR_PRODUCTION_ASSIGN`` - ``video.approve``, which no
    ``EMPLOYEE`` holds - and claiming needs the producer to be unset. Neither is
    a role check: both are the capability matrix and the row's own state.
    """
    content_id = await in_production(world, producer=world.member)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.production.assign_producer(
            actor=world.actor(world.other),
            request_id=world.request_id,
            content_id=content_id,
            producer_user_id=world.other.id,
        )
    with pytest.raises(PrProductionClaimConflictError):
        await world.services.production.claim_production(
            actor=world.actor(world.other), request_id=world.request_id, content_id=content_id
        )
    assert (await world.reload(content_id)).producer_user_id == world.member.id


async def test_19_a_revision_keeps_the_producer(world: World) -> None:
    """Requirement 19. Sending a cut back is not taking it away.

    The person who made the first version is who makes the second, unless a
    manager says otherwise - and after the revision they still can, because the
    item is back at ``PRODUCTION`` where assignment is allowed.
    """
    content_id = await in_production(world, producer=world.member)
    await submit(world, content_id, actor=world.member)
    await grant_internal_review(world, world.lead)
    await decide(
        world,
        actor=world.lead,
        content_id=content_id,
        stage=PrApprovalStage.INTERNAL_REVIEW,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    assert (await world.reload(content_id)).producer_user_id == world.member.id

    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=world.other.id,
    )
    assert (await world.reload(content_id)).producer_user_id == world.other.id


async def test_19a_only_eligible_active_people_may_be_assigned(world: World) -> None:
    """ "Active eligible users only", as a rule rather than a dropdown filter."""
    content_id = await in_production(world)
    world.other.active = False
    await world.session.flush()
    with pytest.raises(PrValidationError) as refusal:
        await world.services.production.assign_producer(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            content_id=content_id,
            producer_user_id=world.other.id,
        )
    assert refusal.value.details["reason"] == "inactive_user"


async def test_19b_producer_work_is_not_folded_into_responsibility(world: World) -> None:
    """Requirement 27 of the specification: the two stay distinct.

    ``Của tôi`` answers "what am I accountable for" and is ``owner_user_id`` or
    an unfinished task. Being handed one file to cut is not that, and folding it
    in would silently redefine a filter people already use. The producer's work
    reaches them through ``Cần tôi xử lý`` instead - test 45 in
    ``test_pr_content_views.py``.
    """
    content_id = await in_production(world, producer=world.other)
    world.act_as(world.other)
    mine = world.client.get("/api/pr/contents/board", params={"scope": "MY_CONTENT"}).json()
    assert content_id not in {uuid.UUID(row["id"]) for row in mine["items"]}
    queue = world.client.get("/api/pr/contents/board", params={"scope": "MY_ACTIONS"}).json()
    assert content_id in {uuid.UUID(row["id"]) for row in queue["items"]}


# ===========================================================================
# 20-29: THE ARTIFACT
# ===========================================================================


async def test_20_production_cannot_be_submitted_with_no_artifact(world: World) -> None:
    """Requirement 20. An empty submit is refused, and nothing moves."""
    content_id = await in_production(world, producer=world.member)
    for empty in ("", "   "):
        with pytest.raises(PrValidationError) as refusal:
            await submit(world, content_id, actor=world.member, location=empty)
        assert refusal.value.details["reason"] == "empty"
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PRODUCTION
    assert await submissions(world, content_id) == []


@pytest.mark.parametrize(
    ("location", "artifact_type", "reason"),
    [
        ("drive.google.com/file/d/1", PrProductionArtifactType.DRIVE_LINK, "missing_scheme"),
        ("https://", PrProductionArtifactType.EXTERNAL_LINK, "missing_host"),
        ("http:///no-host", PrProductionArtifactType.NAS_LINK, "missing_host"),
        ("https://dropbox.com/x", PrProductionArtifactType.DRIVE_LINK, "not_a_drive_host"),
        # ``volume1/PR/cut.mp4`` used to be here as ``not_absolute``. Step
        # 1F.2.3f.2 accepts a relative location: the team writes them that way
        # against a root everybody already shares, and refusing one meant the
        # value that eventually got stored was less accurate than the one
        # somebody started with. See ``test_pr_asset_locations_and_contributors``.
        ("https://nas/x", PrProductionArtifactType.NAS_PATH, "not_a_path"),
        ("\\\\nas", PrProductionArtifactType.NAS_PATH, "incomplete_unc_path"),
    ],
)
async def test_21_a_malformed_reference_is_refused_with_a_reason(
    world: World,
    location: str,
    artifact_type: PrProductionArtifactType,
    reason: str,
) -> None:
    """Requirement 21. Each refusal names which rule, so a client can word it.

    The Drive case is the interesting one: a Dropbox URL is a perfectly good URL
    and is refused *for this type*, because a record that says "Google Drive"
    and holds something else is a promise the row should not make.
    """
    content_id = await in_production(world, producer=world.member)
    with pytest.raises(PrValidationError) as refusal:
        await submit(
            world, content_id, actor=world.member, location=location, artifact_type=artifact_type
        )
    assert refusal.value.details["reason"] == reason


@pytest.mark.parametrize(
    "location",
    [
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
        "JavaScript:alert(1)",
    ],
)
async def test_22_an_unsafe_scheme_is_refused(world: World, location: str) -> None:
    """Requirement 22. The security half of the validator.

    A stored reference is rendered as a link, which makes the scheme set a
    boundary rather than a formatting rule: ``javascript:`` survives every layer
    that only escapes HTML, and ``data:`` smuggles a document into an address.
    The mixed-case variant is here because a case-sensitive check is the usual
    way this protection is lost.
    """
    content_id = await in_production(world, producer=world.member)
    with pytest.raises(PrValidationError) as refusal:
        await submit(world, content_id, actor=world.member, location=location)
    assert refusal.value.details["reason"] in {"unsafe_scheme", "unsupported_scheme"}
    assert await submissions(world, content_id) == []


async def test_23_the_producer_submits_and_the_item_moves(world: World) -> None:
    """Requirements 23 and 27. The whole handover, end to end."""
    content_id = await in_production(world, producer=world.member)
    submission = await submit(world, content_id, actor=world.member, note="Bản 30 giây")

    row = await world.reload(content_id)
    assert row.workflow_stage is PrWorkflowStage.INTERNAL_REVIEW
    assert submission.submission_no == 1
    assert submission.producer_user_id == world.member.id
    assert submission.submitted_by_user_id == world.member.id
    assert submission.note == "Bản 30 giây"


async def test_24_an_unrelated_member_cannot_submit_somebody_elses_work(world: World) -> None:
    """Requirement 24. Holding the capability is not holding the assignment.

    ``Nguyễn A`` may produce - every ``EMPLOYEE`` may - and is refused because
    this piece is not theirs to hand in. A manager may, and the row then records
    both people, which is why ``submitted_by_user_id`` exists beside
    ``producer_user_id``.
    """
    content_id = await in_production(world, producer=world.member)
    with pytest.raises(PrPermissionDeniedError) as refusal:
        await submit(world, content_id, actor=world.other)
    assert refusal.value.details["reason"] == "not_the_producer"
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PRODUCTION

    submission = await submit(world, content_id, actor=world.lead)
    assert submission.producer_user_id == world.member.id
    assert submission.submitted_by_user_id == world.lead.id


async def test_25_a_valid_submission_is_durable(world: World) -> None:
    """Requirement 25. It is a row, with the version it was cut from."""
    content_id = await in_production(world, producer=world.member)
    await submit(world, content_id, actor=world.member)

    stored = await submissions(world, content_id)
    assert len(stored) == 1
    assert stored[0].location == DRIVE
    assert stored[0].artifact_type is PrProductionArtifactType.DRIVE_LINK
    version = await world.services.content.require_current_version(content_id)
    assert stored[0].content_version_id == version.id


async def test_26_the_row_the_audit_and_the_transition_are_one_unit(world: World) -> None:
    """Requirement 26. All three, or none.

    The failing half is asserted first and matters more: a refused artifact
    leaves no submission, no event and no stage change, because validation
    happens before anything is written and the whole command is one transaction.
    """
    content_id = await in_production(world, producer=world.member)
    with pytest.raises(PrValidationError):
        await submit(world, content_id, actor=world.member, location="javascript:alert(1)")
    assert await submissions(world, content_id) == []
    assert "pr.production.submitted" not in await audit_actions(world, content_id)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PRODUCTION

    submission = await submit(world, content_id, actor=world.member)
    assert await audit_actions(world, submission.id) == ["pr.production.submitted"]
    assert "pr.content.stage_changed" in await audit_actions(world, content_id)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.INTERNAL_REVIEW


async def test_28_a_second_submission_leaves_the_first_untouched(world: World) -> None:
    """Requirements 28 and 29. Two cuts, two rows, both readable.

    This is the case a ``production_url`` column would have destroyed: the file
    the first internal review rejected is still there, still numbered 1, and
    still the thing that decision points at.
    """
    content_id = await in_production(world, producer=world.member)
    first = await submit(world, content_id, actor=world.member, location=DRIVE)
    await grant_internal_review(world, world.lead)
    await decide(
        world,
        actor=world.lead,
        content_id=content_id,
        stage=PrApprovalStage.INTERNAL_REVIEW,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    second = await submit(
        world,
        content_id,
        actor=world.member,
        location="/volume1/PR/2026/cnt-42-v2.mp4",
        artifact_type=PrProductionArtifactType.NAS_PATH,
    )

    stored = await submissions(world, content_id)
    assert [row.submission_no for row in stored] == [1, 2]
    assert stored[0].id == first.id
    assert stored[0].location == DRIVE
    assert stored[1].id == second.id
    assert stored[1].artifact_type is PrProductionArtifactType.NAS_PATH


# ===========================================================================
# 30-39: INTERNAL REVIEW
# ===========================================================================


async def grant_internal_review(world: World, user: User) -> None:
    await world.services.capabilities.grant(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=user.id,
        capability=PrCapability.PR_INTERNAL_REVIEW,
    )


async def test_30_internal_review_requires_its_own_grant(world: World) -> None:
    """Requirement 30."""
    content_id = await in_production(world, producer=world.member)
    await submit(world, content_id, actor=world.member)
    with pytest.raises(PrPermissionDeniedError):
        await decide(
            world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.INTERNAL_REVIEW
        )

    await grant_internal_review(world, world.lead)
    assert (
        await decide(
            world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.INTERNAL_REVIEW
        )
        is PrWorkflowStage.READY_TO_PUBLISH
    )


async def test_31_and_32_neither_script_gate_implies_the_third(world: World) -> None:
    """Requirements 31 and 32. No inheritance between the three grants.

    The lead holds ``PR_TEAM_LEAD_REVIEW`` and the head holds
    ``PR_HEAD_REVIEW``; both have just used them to approve this very piece, and
    neither may sign off the cut. Grants are per gate and hold nothing else.
    """
    content_id = await in_production(world, producer=world.member)
    await submit(world, content_id, actor=world.member)
    for reviewer in (world.lead, world.head):
        with pytest.raises(PrPermissionDeniedError):
            await decide(
                world,
                actor=reviewer,
                content_id=content_id,
                stage=PrApprovalStage.INTERNAL_REVIEW,
            )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.INTERNAL_REVIEW


async def test_33_34_35_one_person_may_sign_all_three_gates(world: World) -> None:
    """Requirements 33, 34 and 35. The reason Step 1F.2.2 removed the four-eyes rule.

    A two-person team's only entitled reviewer is one person, and the workflow
    has to be finishable by them. So the lead here holds all three grants
    independently and walks the piece from team-lead review to ready-to-publish
    alone.

    What must **not** collapse is the record. Three gates are walked, three
    ``pr_approval_events`` rows are written, each with its own stage, decision
    and timestamp, and none of them is read as evidence for another - the head
    approval still requires a team-lead ``APPROVED`` on file for the same draft,
    which is checked whoever signed it.
    """
    for capability in (
        PrCapability.PR_HEAD_REVIEW,
        PrCapability.PR_INTERNAL_REVIEW,
    ):
        await world.services.capabilities.grant(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.lead.id,
            capability=capability,
        )

    content_id = await make_content(world, owner=world.member)
    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        await world.services.workflow.request_transition(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
            target=stage,
        )
    await world.services.ai_reviews.record_review(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=1,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=PrAiReviewResult.PASS,
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=NOW,
        ),
    )

    # One human, three gates, in order.
    assert (
        await decide(
            world,
            actor=world.lead,
            content_id=content_id,
            stage=PrApprovalStage.TEAM_LEAD_REVIEW,
        )
        is PrWorkflowStage.HEAD_REVIEW
    )
    assert (
        await decide(
            world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.HEAD_REVIEW
        )
        is PrWorkflowStage.APPROVED
    )
    # The handoff, then the start: one person may sign every gate and still
    # cannot begin production on a piece nobody holds.
    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=world.member.id,
    )
    await world.services.production.start_production(
        actor=world.actor(world.member), request_id=world.request_id, content_id=content_id
    )
    await submit(world, content_id, actor=world.member)
    assert (
        await decide(
            world, actor=world.lead, content_id=content_id, stage=PrApprovalStage.INTERNAL_REVIEW
        )
        is PrWorkflowStage.READY_TO_PUBLISH
    )

    events = (
        (
            await world.session.execute(
                select(PrApprovalEvent)
                .where(PrApprovalEvent.content_id == content_id)
                .order_by(PrApprovalEvent.decided_at.asc())
            )
        )
        .scalars()
        .all()
    )
    assert [event.approval_stage for event in events] == [
        PrApprovalStage.TEAM_LEAD_REVIEW,
        PrApprovalStage.HEAD_REVIEW,
        PrApprovalStage.INTERNAL_REVIEW,
    ]
    assert {event.reviewer_user_id for event in events} == {world.lead.id}
    assert all(event.decision is PrApprovalDecision.APPROVED for event in events)
    # Only the internal one names a cut. The script gates judged a script, which
    # ``version_reviewed`` already identifies.
    assert [event.production_submission_id is None for event in events] == [True, True, False]


async def test_36_internal_approval_goes_to_ready_to_publish(world: World) -> None:
    """Requirement 36. Through the matrix, and no stage skipped."""
    content_id = await in_production(world, producer=world.member)
    await submit(world, content_id, actor=world.member)
    await grant_internal_review(world, world.head)
    assert (
        await decide(
            world, actor=world.head, content_id=content_id, stage=PrApprovalStage.INTERNAL_REVIEW
        )
        is PrWorkflowStage.READY_TO_PUBLISH
    )


async def test_37_internal_revision_goes_back_to_production(world: World) -> None:
    """Requirement 37. Back to ``PRODUCTION``, never to ``SCRIPTING``.

    An internal reviewer is looking at a cut. Sending a bad edit back to the
    scriptwriter would ask the wrong person to fix the wrong thing, and would
    invalidate two approvals of a script nobody complained about.
    """
    content_id = await in_production(world, producer=world.member)
    await submit(world, content_id, actor=world.member)
    await grant_internal_review(world, world.head)
    assert (
        await decide(
            world,
            actor=world.head,
            content_id=content_id,
            stage=PrApprovalStage.INTERNAL_REVIEW,
            decision=PrApprovalDecision.REVISION_REQUIRED,
        )
        is PrWorkflowStage.PRODUCTION
    )


async def test_38_and_39_each_decision_names_the_cut_it_judged(world: World) -> None:
    """Requirements 38 and 39. Two reviews, one script version, two files.

    Without ``production_submission_id`` these two rows would be indistinguishable
    - same content, same stage, same ``version_reviewed`` - while saying opposite
    things. With it, each decision points at the file it was about, the earlier
    one still points at the earlier file, and the later review targets the latest
    submission because that is what was on screen.
    """
    content_id = await in_production(world, producer=world.member)
    first = await submit(world, content_id, actor=world.member)
    await grant_internal_review(world, world.head)
    await decide(
        world,
        actor=world.head,
        content_id=content_id,
        stage=PrApprovalStage.INTERNAL_REVIEW,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    second = await submit(
        world, content_id, actor=world.member, location="https://drive.google.com/file/d/2/view"
    )
    await decide(
        world, actor=world.head, content_id=content_id, stage=PrApprovalStage.INTERNAL_REVIEW
    )

    events = (
        (
            await world.session.execute(
                select(PrApprovalEvent)
                .where(PrApprovalEvent.approval_stage == PrApprovalStage.INTERNAL_REVIEW)
                .order_by(PrApprovalEvent.decided_at.asc())
            )
        )
        .scalars()
        .all()
    )
    assert [event.production_submission_id for event in events] == [first.id, second.id]
    assert [event.decision for event in events] == [
        PrApprovalDecision.REVISION_REQUIRED,
        PrApprovalDecision.APPROVED,
    ]
    # And both files are still there, in order, unedited.
    assert [row.submission_no for row in await submissions(world, content_id)] == [1, 2]


async def test_39a_the_production_actions_come_from_the_server(world: World) -> None:
    """What the panel is allowed to draw, at each point of the production flow."""
    content_id = await in_production(world)
    assert "CLAIM_PRODUCTION" in await world.actions(world.member, content_id)
    assert "ASSIGN_PRODUCER" in await world.actions(world.lead, content_id)
    # Nothing to submit while nobody holds it.
    assert "SUBMIT_PRODUCTION" not in await world.actions(world.member, content_id)

    await world.services.production.claim_production(
        actor=world.actor(world.member), request_id=world.request_id, content_id=content_id
    )
    after = await world.actions(world.member, content_id)
    assert "SUBMIT_PRODUCTION" in after
    assert "CLAIM_PRODUCTION" not in after
    # Somebody else's production is not theirs to hand in.
    assert "SUBMIT_PRODUCTION" not in await world.actions(world.other, content_id)


async def test_39b_the_http_surface_carries_the_whole_flow(world: World) -> None:
    """Claim, submit and review over the router, as the panel does it.

    The service tests above prove the rules; this proves the wiring - that the
    routes exist, take what the panel sends, and hand back the shape it renders.
    """
    content_id = await in_production(world)

    world.act_as(world.member)
    claimed = world.client.post(f"/api/pr/contents/{content_id}/producer/claim")
    assert claimed.status_code == 200
    assert claimed.json()["content"]["producer_user_id"] == str(world.member.id)

    submitted = world.client.post(
        f"/api/pr/contents/{content_id}/production-submissions",
        json={"artifact_type": "DRIVE_LINK", "location": DRIVE, "note": "Bản đầu"},
    )
    assert submitted.status_code == 201
    body = submitted.json()
    assert body["workflow_stage"] == PrWorkflowStage.INTERNAL_REVIEW.value
    assert body["submissions"][0]["submission_no"] == 1
    assert body["submissions"][0]["is_link"] is True

    state = world.client.get(f"/api/pr/contents/{content_id}/production").json()
    assert state["producer_user_id"] == str(world.member.id)
    assert len(state["submissions"]) == 1

    # A NAS path is not a link, and the server says so rather than the browser
    # guessing from the string.
    await grant_internal_review(world, world.head)
    world.act_as(world.head)
    revision = world.client.post(
        f"/api/pr/contents/{content_id}/reviews",
        json={"decision": "REVISION_REQUIRED", "version_reviewed": 1},
    )
    assert revision.status_code == 201
    world.act_as(world.member)
    nas = world.client.post(
        f"/api/pr/contents/{content_id}/production-submissions",
        json={"artifact_type": "NAS_PATH", "location": "/volume1/PR/cut-v2.mp4"},
    )
    assert nas.status_code == 201
    assert nas.json()["submissions"][0]["is_link"] is False


async def test_39c_a_bad_artifact_over_http_is_a_422_with_a_reason(world: World) -> None:
    """The refusal a person sees, in the shape the panel branches on."""
    content_id = await in_production(world, producer=world.member)
    world.act_as(world.member)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/production-submissions",
        json={"artifact_type": "DRIVE_LINK", "location": "javascript:alert(1)"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "pr_validation_error"
    assert error["details"]["reason"] == "unsafe_scheme"


async def test_39d_production_is_refused_outside_the_production_stage(world: World) -> None:
    """Assigning and submitting are meaningless anywhere else, and say so."""
    content_id = await make_content(world, owner=world.member)
    with pytest.raises(PrWorkflowTransitionError) as refusal:
        await world.services.production.assign_producer(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            content_id=content_id,
            producer_user_id=world.member.id,
        )
    assert PrWorkflowStage.PRODUCTION.value in refusal.value.details["expected"]

    unassigned = await in_production(world)
    with pytest.raises(PrWorkflowTransitionError) as no_producer:
        await submit(world, unassigned, actor=world.member)
    assert no_producer.value.details["reason"] == "no_producer"
