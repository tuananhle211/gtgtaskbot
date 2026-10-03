"""Step 1F.2.3b - the production handoff, and taking a decision back.

Numbered 1-45, continuing the requirement numbering the step was specified with:
1-15 the handoff, 16-21 notifications, 22-34 undoing approvals, 35-38 revisions
and downstream work, 39-45 history and concurrency.

Two of these carry the step:

* **test_12** - ``production_started_at`` is stamped by starting production and
  by nothing else. Assignment does not set it, a claim does not set it, and the
  permanent-delete rule from Step 1F.2.3a reads it, so a fixture that stamped it
  early would quietly hand a member back a delete right the previous step spent
  itself removing;
* **test_25** - an approval that has been undone stops being *authority* while
  staying in *history*. The row is still there and the Head gate no longer
  accepts it, which is the difference between an undo and a lie.

Everything runs against real SQL on one transaction, because most of these
questions are about rows: whether a stamp is set, whether a reversal links two
transitions, whether a second undo finds anything left to do.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    ReviseContentCommand,
)
from meobot.application.pr_production_service import SubmitProductionCommand
from meobot.application.pr_services import PrServices, build_pr_services
from meobot.application.pr_undo_service import PrUndoKind
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.pr import PrApprovalEvent, PrBrand, PrContentItem, PrPlatform
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrUndoNotAvailableError,
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
from meobot.domain.pr.workflow import CONTENT_TRANSITIONS, PrTransitionTrigger

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
DRIVE = "https://drive.google.com/file/d/1AbCdEf/view"


@dataclass
class World:
    session: AsyncSession
    client: TestClient
    settings: Settings
    services: PrServices
    owner: User
    #: TEAM_LEAD, granted all three review gates. Management for assignment and
    #: for undoing somebody else's decision.
    lead: User
    #: ADMIN, granted ``PR_HEAD_REVIEW`` only.
    head: User
    #: EMPLOYEE, owner of the content and the usual producer.
    member: User
    #: EMPLOYEE, the unrelated colleague.
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
        content = await self.reload(content_id)
        return {
            action.kind.value
            for action in await self.services.actions.for_content(
                actor=self.actor(user), content=content
            )
        }

    async def action(self, user: User, content_id: uuid.UUID, kind: str):  # type: ignore[no-untyped-def]
        content = await self.reload(content_id)
        for action in await self.services.actions.for_content(
            actor=self.actor(user), content=content
        ):
            if action.kind.value == kind:
                return action
        return None

    async def transitions(self, content_id: uuid.UUID) -> list[PrContentTransitionEvent]:
        rows = await self.session.execute(
            select(PrContentTransitionEvent)
            .where(PrContentTransitionEvent.content_id == content_id)
            .order_by(PrContentTransitionEvent.created_at.asc())
        )
        return list(rows.scalars().all())

    async def messages(self) -> list[OutboundMessage]:
        rows = await self.session.execute(
            select(OutboundMessage).order_by(OutboundMessage.created_at.asc())
        )
        return list(rows.scalars().all())


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    # Telegram ids and an open private chat, so the notification half has
    # somewhere to send: an unreachable recipient is a real state and has its own
    # test, but it must not be the *default* or every assertion below would pass
    # by finding nothing.
    owner = User(full_name="Chị Chủ", role=Role.OWNER, telegram_user_id=901)
    lead = User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD, telegram_user_id=902)
    head = User(full_name="Hà Trưởng Phòng", role=Role.ADMIN, telegram_user_id=903)
    member = User(full_name="Phương Nhung", role=Role.EMPLOYEE, telegram_user_id=904)
    other = User(full_name="Nguyễn A", role=Role.EMPLOYEE, telegram_user_id=905)
    for person in (owner, lead, head, member, other):
        person.telegram_private_chat_id = person.telegram_user_id
        person.private_chat_available = True
    brand = PrBrand(code="BRND-A", name="Apexmed")
    platform = PrPlatform(code="WEBSITE", name="Website")
    session.add_all([owner, lead, head, member, other, brand, platform])
    await session.flush()

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    services = build_pr_services(session, settings)
    granter = Actor(user_id=owner.id, full_name=owner.full_name, role=Role.OWNER)
    for user, capability in (
        (lead, PrCapability.PR_TEAM_LEAD_REVIEW),
        (lead, PrCapability.PR_HEAD_REVIEW),
        (lead, PrCapability.PR_INTERNAL_REVIEW),
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


# --- Walking the workflow ---------------------------------------------------


async def make_content(world: World, *, owner: User | None = None) -> uuid.UUID:
    snapshot = await world.services.content.create_content(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=CreateContentCommand(
            title="Bài mới",
            brand_id=world.brand_id,
            owner_user_id=(owner or world.member).id,
            script_text="Nội dung.",
            targets=(ContentTargetSpec(channel_id=world.channel_id),),
        ),
    )
    return snapshot.content.id


async def decide(
    world: World,
    content_id: uuid.UUID,
    stage: PrApprovalStage,
    *,
    actor: User | None = None,
    decision: PrApprovalDecision = PrApprovalDecision.APPROVED,
    version: int = 1,
) -> None:
    reviewer = actor or world.lead
    await world.services.approvals.record_decision(
        actor=world.actor(reviewer),
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=reviewer.id,
            approval_stage=stage,
            decision=decision,
            version_reviewed=version,
        ),
    )


async def to_team_lead_review(world: World, content_id: uuid.UUID) -> None:
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


async def approved(world: World, *, head: User | None = None) -> uuid.UUID:
    """Content at ``APPROVED``, nobody holding it. *Chờ nhận sản xuất.*"""
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
    await decide(world, content_id, PrApprovalStage.HEAD_REVIEW, actor=head or world.head)
    return content_id


async def assign(world: World, content_id: uuid.UUID, producer: User | None) -> None:
    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=producer.id if producer else None,
    )


async def start(world: World, content_id: uuid.UUID, *, actor: User) -> None:
    await world.services.production.start_production(
        actor=world.actor(actor), request_id=world.request_id, content_id=content_id
    )


async def submit(world: World, content_id: uuid.UUID, *, actor: User | None = None) -> None:
    await world.services.production.submit_production(
        actor=world.actor(actor or world.member),
        request_id=world.request_id,
        command=SubmitProductionCommand(
            content_id=content_id,
            artifact_type=PrProductionArtifactType.DRIVE_LINK,
            location=DRIVE,
        ),
    )


async def in_production(world: World, *, producer: User | None = None) -> uuid.UUID:
    content_id = await approved(world)
    who = producer or world.member
    await assign(world, content_id, who)
    await start(world, content_id, actor=who)
    return content_id


async def in_internal_review(world: World) -> uuid.UUID:
    content_id = await in_production(world)
    await submit(world, content_id)
    return content_id


async def undo(world: World, content_id: uuid.UUID, *, actor: User):  # type: ignore[no-untyped-def]
    return await world.services.undo.undo_last(
        actor=world.actor(actor), request_id=world.request_id, content_id=content_id
    )


# ===========================================================================
# 1-15: THE APPROVED -> PRODUCTION HANDOFF
# ===========================================================================


async def test_01_to_03_head_approval_stops_at_approved(world: World) -> None:
    """Requirements 1-3. Approved is not started, and the panel can say so.

    The derived state is the whole of requirement 3: ``APPROVED`` with nobody
    holding the piece is *Chờ nhận sản xuất*, computed from two columns rather
    than stored as a fourteenth stage, so every existing query about ``APPROVED``
    keeps meaning what it meant.
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


async def test_04_start_is_absent_and_refused_without_a_producer(world: World) -> None:
    """Requirements 4 and 7. Not offered, and not possible either way.

    Both halves matter and only the second is enforcement: the action list
    withholds ``START_PRODUCTION``, *and* the generic transition route refuses -
    so a caller that skips the panel is refused by the same rule rather than by
    the absence of a button.
    """
    content_id = await approved(world)
    assert "START_PRODUCTION" not in await world.actions(world.member, content_id)

    with pytest.raises(PrWorkflowTransitionError) as refusal:
        await world.services.workflow.request_transition(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
            target=PrWorkflowStage.PRODUCTION,
        )
    assert refusal.value.details["reason"] == "no_producer_assigned"
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.APPROVED


async def test_05_and_07_management_assigns_at_approved_without_starting(world: World) -> None:
    """Requirements 5 and 7. The handoff happens before the work does."""
    content_id = await approved(world)
    assert "ASSIGN_PRODUCER" in await world.actions(world.lead, content_id)

    await assign(world, content_id, world.member)
    row = await world.reload(content_id)
    assert row.producer_user_id == world.member.id
    assert row.workflow_stage is PrWorkflowStage.APPROVED
    assert row.production_started_at is None
    assert (
        handoff_state(row.workflow_stage, row.producer_user_id)
        is PrProductionHandoff.READY_FOR_PRODUCTION
    )


async def test_06_and_08_a_member_claims_at_approved_without_starting(world: World) -> None:
    """Requirements 6 and 8. Volunteering is not beginning."""
    content_id = await approved(world)
    assert "CLAIM_PRODUCTION" in await world.actions(world.member, content_id)

    await world.services.production.claim_production(
        actor=world.actor(world.member), request_id=world.request_id, content_id=content_id
    )
    row = await world.reload(content_id)
    assert row.producer_user_id == world.member.id
    assert row.workflow_stage is PrWorkflowStage.APPROVED
    assert row.production_started_at is None


async def test_09_to_12_the_producer_starts_and_only_then_is_it_stamped(world: World) -> None:
    """Requirements 9, 11 and 12. One act, one stage change, one stamp."""
    content_id = await approved(world)
    await assign(world, content_id, world.member)
    assert "START_PRODUCTION" in await world.actions(world.member, content_id)

    await start(world, content_id, actor=world.member)
    row = await world.reload(content_id)
    assert row.workflow_stage is PrWorkflowStage.PRODUCTION
    assert row.production_started_at is not None
    assert handoff_state(row.workflow_stage, row.producer_user_id) is (
        PrProductionHandoff.IN_PRODUCTION
    )


async def test_10_an_unrelated_member_cannot_start_somebody_elses_production(
    world: World,
) -> None:
    """Requirement 10. Holding the capability is not holding the piece."""
    content_id = await approved(world)
    await assign(world, content_id, world.member)

    assert "START_PRODUCTION" not in await world.actions(world.other, content_id)
    with pytest.raises(PrPermissionDeniedError) as refusal:
        await start(world, content_id, actor=world.other)
    assert refusal.value.details["reason"] == "not_the_producer"
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.APPROVED

    # A manager may, because they decide whose work it is - and the audit says
    # who actually pressed it.
    await start(world, content_id, actor=world.lead)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PRODUCTION


async def test_13_and_14_internal_review_needs_a_cut_and_cannot_be_reached_early(
    world: World,
) -> None:
    """Requirements 13 and 14. No artifact, no internal review - by any route.

    The matrix has never had an edge from ``APPROVED`` or ``HEAD_REVIEW`` to
    ``INTERNAL_REVIEW``, and Step 1F.2.3b closed the remaining gap: the one edge
    that *does* exist now refuses until a cut has been handed in.
    """
    assert PrWorkflowStage.INTERNAL_REVIEW not in CONTENT_TRANSITIONS[PrWorkflowStage.APPROVED]
    assert PrWorkflowStage.INTERNAL_REVIEW not in CONTENT_TRANSITIONS[PrWorkflowStage.HEAD_REVIEW]
    assert PrWorkflowStage.READY_TO_PUBLISH not in CONTENT_TRANSITIONS[PrWorkflowStage.APPROVED]

    content_id = await in_production(world)
    with pytest.raises(PrWorkflowTransitionError) as refusal:
        await world.services.workflow.request_transition(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
            target=PrWorkflowStage.INTERNAL_REVIEW,
        )
    assert refusal.value.details["reason"] == "no_production_submission"

    await submit(world, content_id)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.INTERNAL_REVIEW


async def test_15_internal_approval_still_reaches_ready_to_publish(world: World) -> None:
    """Requirement 15. The gate after production is unchanged."""
    content_id = await in_internal_review(world)
    await decide(world, content_id, PrApprovalStage.INTERNAL_REVIEW)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.READY_TO_PUBLISH


async def test_15a_the_http_surface_carries_the_handoff(world: World) -> None:
    """The three new routes, as the panel calls them."""
    content_id = await approved(world)

    world.act_as(world.member)
    detail = world.client.get(f"/api/pr/contents/{content_id}").json()
    assert detail["content"]["production_state"] == "WAITING_FOR_PRODUCER"

    claimed = world.client.post(f"/api/pr/contents/{content_id}/producer/claim")
    assert claimed.status_code == 200
    assert claimed.json()["content"]["production_state"] == "READY_FOR_PRODUCTION"

    started = world.client.post(f"/api/pr/contents/{content_id}/production/start")
    assert started.status_code == 200
    assert started.json()["content"]["production_state"] == "IN_PRODUCTION"
    assert started.json()["content"]["workflow_stage"] == PrWorkflowStage.PRODUCTION.value


# ===========================================================================
# 16-21: NOTIFICATIONS
# ===========================================================================


async def test_16_to_18_head_approval_tells_the_responsible_person_what_to_do(
    world: World,
) -> None:
    """Requirements 16-18. One message, to the owner, saying the next step.

    The instruction is asserted, not just the delivery: "đã duyệt" with no next
    step is what leaves a piece sitting at ``APPROVED``, and the whole reason
    this notification exists is to name the handoff.
    """
    content_id = await approved(world)

    queued = await world.messages()
    approvals = [row for row in queued if row.event_type == "pr_content_approved"]
    assert len(approvals) == 1
    message = approvals[0]
    assert message.recipient_user_id == world.member.id  # 16: the owner
    assert message.telegram_chat_id == world.member.telegram_user_id
    assert message.template_key == "pr.content_approved"
    assert message.safe_payload_json["content_code"]

    # 17: the words tell them to take or arrange production.
    from meobot.domain.notifications.templates import render

    text = render(message.template_key, dict(message.safe_payload_json))
    assert "nhận sản xuất" in text
    assert str(content_id) in text  # the deep link

    # 18: nobody else was told.
    assert {row.recipient_user_id for row in approvals} == {world.member.id}


async def test_19_assignment_tells_the_producer_and_a_self_claim_does_not(
    world: World,
) -> None:
    """Requirement 19, and the quiet half of it."""
    content_id = await approved(world)
    await assign(world, content_id, world.other)

    assigned = [row for row in await world.messages() if row.event_type == "pr_production_assigned"]
    assert len(assigned) == 1
    assert assigned[0].recipient_user_id == world.other.id

    # A claim is somebody telling themselves. No message.
    second = await approved(world)
    await world.services.production.claim_production(
        actor=world.actor(world.member), request_id=world.request_id, content_id=second
    )
    assigned_after = [
        row for row in await world.messages() if row.event_type == "pr_production_assigned"
    ]
    assert len(assigned_after) == 1


async def test_20_undoing_a_head_approval_sends_the_correction(world: World) -> None:
    """Requirement 20. The person told to arrange production is told not to."""
    content_id = await approved(world)
    await undo(world, content_id, actor=world.head)

    corrections = [row for row in await world.messages() if row.event_type == "pr_workflow_undone"]
    assert len(corrections) == 1
    assert corrections[0].recipient_user_id == world.member.id

    from meobot.domain.notifications.templates import render

    text = render(corrections[0].template_key, dict(corrections[0].safe_payload_json))
    assert "hoàn tác" in text.lower()
    assert "Chờ Trưởng phòng duyệt" in text


async def test_21_a_failed_decision_queues_no_message(world: World) -> None:
    """Requirement 21. The message and the decision are one unit of work.

    The approval is refused - the actor holds no Head grant - so there is no
    decision to announce and no row is written. The notification is queued in the
    same transaction as the approval, which is what makes "no decision, no
    message" structural rather than a matter of ordering.
    """
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)

    with pytest.raises(PrPermissionDeniedError):
        await decide(world, content_id, PrApprovalStage.HEAD_REVIEW, actor=world.member)

    assert [row for row in await world.messages() if row.event_type == "pr_content_approved"] == []


# ===========================================================================
# 22-34: UNDOING HUMAN APPROVALS
# ===========================================================================


async def test_22_to_25_a_team_lead_approval_can_be_taken_back(world: World) -> None:
    """Requirements 22-25. The four things an undo has to be at once.

    It moves the content back (22), leaves the approval row alone (23), stops
    that row counting as authority (24), and therefore blocks the Head approval
    that would have rested on it (25). The fourth is the one that makes the
    others worth having: an undo that moved the stage and left the approval
    "valid" would let the next gate sign against a decision somebody withdrew.
    """
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.HEAD_REVIEW

    found = await undo(world, content_id, actor=world.lead)
    assert found.kind is PrUndoKind.UNDO_TEAM_LEAD_APPROVAL
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.TEAM_LEAD_REVIEW  # 22

    approvals = (
        (
            await world.session.execute(
                select(PrApprovalEvent).where(PrApprovalEvent.content_id == content_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(approvals) == 1  # 23: still there
    assert approvals[0].decision is PrApprovalDecision.APPROVED

    # 24 and 25: not authority any more. The Head gate reads the same predicate,
    # so re-approving at the team-lead gate is the only way forward.
    assert (
        await world.services.approvals.successful_team_lead_approval(content_id, version_reviewed=1)
        is None
    )
    assert not await world.services.approvals.head_approval_permitted(
        content_id, version_reviewed=1
    )


async def test_26_to_28_a_head_approval_can_be_taken_back_before_the_handoff(
    world: World,
) -> None:
    """Requirements 26-28."""
    content_id = await approved(world)
    found = await undo(world, content_id, actor=world.head)

    assert found.kind is PrUndoKind.UNDO_HEAD_APPROVAL
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.HEAD_REVIEW  # 26

    heads = (
        (
            await world.session.execute(
                select(PrApprovalEvent).where(
                    PrApprovalEvent.content_id == content_id,
                    PrApprovalEvent.approval_stage == PrApprovalStage.HEAD_REVIEW,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(heads) == 1  # 27
    # 28: the team-lead approval underneath is untouched and still effective, so
    # the piece can simply be approved again.
    assert await world.services.approvals.head_approval_permitted(content_id, version_reviewed=1)


@pytest.mark.parametrize("stage", ["assigned", "claimed", "started"])
async def test_29_to_31_the_head_undo_is_blocked_once_production_has_it(
    world: World, stage: str
) -> None:
    """Requirements 29-31. The handoff is what the approval was *for*.

    Three ways of taking the job, one answer. Note what the service does **not**
    do: it does not clear the producer to make the undo fit. Somebody else's work
    is not this button's to discard, and the refusal says so.
    """
    content_id = await approved(world)
    if stage == "assigned":
        await assign(world, content_id, world.member)
        expected = "production_handed_off"
    elif stage == "claimed":
        await world.services.production.claim_production(
            actor=world.actor(world.member), request_id=world.request_id, content_id=content_id
        )
        expected = "production_handed_off"
    else:
        await assign(world, content_id, world.member)
        await start(world, content_id, actor=world.member)
        # Starting is a manual move, and no manual move is reversible - so the
        # refusal is about the *kind* of action rather than about what has been
        # built on it. Both roads lead to "no".
        expected = "not_reversible"

    assert "UNDO_LAST_ACTION" not in await world.actions(world.head, content_id)
    with pytest.raises(PrUndoNotAvailableError) as refusal:
        await undo(world, content_id, actor=world.head)
    assert refusal.value.details["reason"] == expected


async def test_31a_starting_production_is_not_itself_undoable(world: World) -> None:
    """Requirement 29 of the specification, stated as its own fact.

    ``production_started_at`` is irreversible because the permanent-delete rule
    reads it: a member's right to delete their own work ends the moment somebody
    produces it, for good. An undo that un-stamped it would hand that right back,
    so ``START_PRODUCTION`` is a manual transition and no manual transition is
    reversible.
    """
    content_id = await in_production(world)
    row = await world.reload(content_id)
    stamped = row.production_started_at
    assert stamped is not None

    assert await world.services.undo.candidate(row) is None
    with pytest.raises(PrUndoNotAvailableError) as refusal:
        await undo(world, content_id, actor=world.member)
    assert refusal.value.details["reason"] == "not_reversible"
    assert (await world.reload(content_id)).production_started_at == stamped


async def test_32_to_34_an_internal_approval_can_be_taken_back_before_publication(
    world: World,
) -> None:
    """Requirements 32-34."""
    content_id = await in_internal_review(world)
    await decide(world, content_id, PrApprovalStage.INTERNAL_REVIEW)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.READY_TO_PUBLISH

    found = await undo(world, content_id, actor=world.lead)
    assert found.kind is PrUndoKind.UNDO_INTERNAL_REVIEW
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.INTERNAL_REVIEW  # 32

    # 34: the cut is still there, which is the point - the reviewer is looking at
    # the same file again, not at nothing.
    state = await world.services.production.state(await world.reload(content_id))
    assert len(state.submissions) == 1
    assert state.submissions[0].location == DRIVE


async def test_33_publication_closes_the_internal_undo(world: World) -> None:
    """Requirement 33. Once it is out, the decision behind it stands."""
    content_id = await in_internal_review(world)
    await decide(world, content_id, PrApprovalStage.INTERNAL_REVIEW)
    await world.services.publications.register_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=await _publication(world, content_id),
    )
    # Publishing moved the content on, so the internal approval is no longer the
    # latest action either - both guards agree, and the message says the one a
    # person needs.
    with pytest.raises(PrUndoNotAvailableError) as refusal:
        await undo(world, content_id, actor=world.lead)
    assert refusal.value.details["reason"] in {"published", "superseded"}


async def _publication(world: World, content_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """The command that records this item's approved cut as published.

    Step 1F.2.3f: a publication names the produced output that went out, so the
    master this item was approved on is looked up rather than left absent. It
    exists because everything reaching this helper has been through internal
    review.
    """
    from sqlalchemy import select

    from meobot.application.pr_publication_service import RegisterPublicationCommand
    from meobot.db.models.pr_production import PrProductionSubmission

    found = await world.session.execute(
        select(PrProductionSubmission.id)
        .where(PrProductionSubmission.content_id == content_id)
        .order_by(PrProductionSubmission.submission_no.desc())
    )
    submission_id = found.scalars().first()
    assert submission_id is not None
    return RegisterPublicationCommand(
        content_id=content_id,
        channel_id=world.channel_id,
        published_at=NOW,
        production_submission_id=submission_id,
        platform_post_id="post-1",
        url="https://example.test/post-1",
    )


# ===========================================================================
# 35-38: REVISIONS AND DOWNSTREAM WORK
# ===========================================================================


async def test_35_a_revision_can_be_taken_back_before_anybody_acts_on_it(
    world: World,
) -> None:
    """Requirement 35. The reviewer who pressed the wrong button."""
    content_id = await in_internal_review(world)
    await decide(
        world,
        content_id,
        PrApprovalStage.INTERNAL_REVIEW,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.PRODUCTION

    found = await undo(world, content_id, actor=world.lead)
    assert found.kind is PrUndoKind.UNDO_REVISION
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.INTERNAL_REVIEW


async def test_36_a_revision_undo_is_blocked_once_a_new_draft_exists(world: World) -> None:
    """Requirement 36 and 32 of the specification: do not throw away new work.

    The writer has already rewritten the script in response to the revision.
    Rolling the stage forward again would put the *old* draft in front of a
    reviewer and leave the new one unreviewed, so the decision stops being
    reversible the moment the work it asked for arrives.
    """
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(
        world,
        content_id,
        PrApprovalStage.TEAM_LEAD_REVIEW,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.SCRIPTING

    await world.services.content.revise_content(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=ReviseContentCommand(
            content_id=content_id, expected_version=1, script_text="Bản viết lại."
        ),
    )
    with pytest.raises(PrUndoNotAvailableError) as refusal:
        await undo(world, content_id, actor=world.lead)
    assert refusal.value.details["reason"] == "new_version_written"


async def test_37_a_production_revision_undo_is_blocked_by_a_new_cut(world: World) -> None:
    """Requirement 37. The producer has already re-cut it."""
    content_id = await in_internal_review(world)
    await decide(
        world,
        content_id,
        PrApprovalStage.INTERNAL_REVIEW,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    await submit(world, content_id)  # the second cut, and the content moves on

    with pytest.raises(PrUndoNotAvailableError) as refusal:
        await undo(world, content_id, actor=world.lead)
    # Handing in the new cut also moved the content to ``INTERNAL_REVIEW``, so
    # the newest action is that manual move rather than the revision - which is
    # refused first, and for the stronger reason. The ``new_submission`` guard
    # underneath it is the belt to that pair of braces.
    assert refusal.value.details["reason"] == "not_reversible"
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.INTERNAL_REVIEW


async def test_38_an_older_action_cannot_be_undone_beneath_a_newer_one(world: World) -> None:
    """Requirement 38. One at a time, newest first.

    At ``APPROVED`` the candidate is the Head decision, never the team-lead one
    underneath it - and taking the Head decision back makes the team-lead
    decision the candidate, which is the correct order to unwind in.
    """
    content_id = await approved(world)
    first = await world.services.undo.candidate(await world.reload(content_id))
    assert first is not None and first.kind is PrUndoKind.UNDO_HEAD_APPROVAL

    await undo(world, content_id, actor=world.head)
    second = await world.services.undo.candidate(await world.reload(content_id))
    assert second is not None and second.kind is PrUndoKind.UNDO_TEAM_LEAD_APPROVAL

    await undo(world, content_id, actor=world.lead)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.TEAM_LEAD_REVIEW
    assert await world.services.undo.candidate(await world.reload(content_id)) is None


# ===========================================================================
# 39-45: HISTORY, AUTHORITY AND CONCURRENCY
# ===========================================================================


async def test_39_to_41_the_history_keeps_both_halves(world: World) -> None:
    """Requirements 39-41. Nothing disappears, and the pair is legible.

    The reversal points back and the original points forward, so a client
    renders "Duyệt Trưởng phòng / Hoàn tác duyệt Trưởng phòng" from the rows
    rather than by comparing timestamps and guessing.
    """
    content_id = await approved(world)
    before = await world.transitions(content_id)
    head_move = next(
        event
        for event in before
        if event.to_stage is PrWorkflowStage.APPROVED
        and event.trigger is PrTransitionTrigger.HUMAN_APPROVAL
    )

    await undo(world, content_id, actor=world.head)
    after = await world.transitions(content_id)

    assert len(after) == len(before) + 1  # 39: appended
    assert head_move.id in {event.id for event in after}  # 40: never deleted
    reversal = after[-1]
    assert reversal.trigger is PrTransitionTrigger.UNDO
    assert reversal.reverses_event_id == head_move.id
    await world.session.refresh(head_move)
    assert head_move.reversed_by_event_id == reversal.id

    # 41: and the endpoint shows both.
    world.act_as(world.head)
    body = world.client.get(f"/api/pr/contents/{content_id}/history").json()
    assert len(body) == len(after)
    undone = [row for row in body if row["reversed_by_event_id"]]
    assert len(undone) == 1
    assert undone[0]["to_stage"] == PrWorkflowStage.APPROVED.value
    assert body[-1]["trigger"] == PrTransitionTrigger.UNDO.value


async def test_41a_only_the_actor_or_management_may_undo(world: World) -> None:
    """Requirement 23. Two conditions, and neither is a new power.

    ``head`` holds ``PR_HEAD_REVIEW`` and approved it, so they may take it back.
    ``other`` holds neither the gate nor management. ``lead`` holds the gate
    *and* ``PR_CONTENT_CANCEL``, which is what lets a manager correct somebody
    else's decision - and is an existing right, not an undo superpower.
    """
    content_id = await approved(world)
    assert not await world.services.undo.may_undo(
        world.actor(world.other), await world.reload(content_id)
    )
    assert not await world.services.undo.may_undo(
        world.actor(world.member), await world.reload(content_id)
    )
    assert await world.services.undo.may_undo(
        world.actor(world.head), await world.reload(content_id)
    )
    assert await world.services.undo.may_undo(
        world.actor(world.lead), await world.reload(content_id)
    )

    with pytest.raises(PrPermissionDeniedError):
        await undo(world, content_id, actor=world.other)


async def test_42_a_second_undo_finds_nothing_to_reverse(world: World) -> None:
    """Requirement 42. Two people pressing produce one reversal.

    The second attempt is not an error about racing: it is the honest answer that
    the decision it was aiming at has already been taken back, and what is left
    underneath is a different action with its own rules.
    """
    content_id = await make_content(world)
    await to_team_lead_review(world, content_id)
    await decide(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
    await undo(world, content_id, actor=world.lead)

    with pytest.raises(PrUndoNotAvailableError) as refusal:
        await undo(world, content_id, actor=world.lead)
    # What is left underneath is the AI verdict that moved the content to the
    # gate, and a machine's answer is not somebody's decision to take back.
    assert refusal.value.details["reason"] == "not_reversible"
    reversals = [
        event
        for event in await world.transitions(content_id)
        if event.trigger is PrTransitionTrigger.UNDO
    ]
    assert len(reversals) == 1


async def test_43_and_44_a_later_write_takes_the_undo_out_of_reach(world: World) -> None:
    """Requirements 43 and 44. Whoever gets the row first wins.

    Both commands lock the content, so in life one waits for the other; what this
    asserts is the *outcome* of losing - the undo finds that its action is no
    longer the one that put the content where it is, and refuses rather than
    rolling back over somebody else's work.
    """
    # Racing a producer assignment.
    assigned = await approved(world)
    await assign(world, assigned, world.member)
    with pytest.raises(PrUndoNotAvailableError):
        await undo(world, assigned, actor=world.head)

    # Racing the next approval: the team-lead decision is no longer the latest.
    escalated = await make_content(world)
    await to_team_lead_review(world, escalated)
    await decide(world, escalated, PrApprovalStage.TEAM_LEAD_REVIEW)
    await decide(world, escalated, PrApprovalStage.HEAD_REVIEW, actor=world.head)
    found = await world.services.undo.candidate(await world.reload(escalated))
    assert found is not None and found.kind is PrUndoKind.UNDO_HEAD_APPROVAL


async def test_45_no_raw_database_error_reaches_the_api(world: World) -> None:
    """Requirement 45. Every refusal is a checked business error with a code."""
    content_id = await approved(world)
    await assign(world, content_id, world.member)

    world.act_as(world.head)
    blocked = world.client.post(f"/api/pr/contents/{content_id}/undo")
    assert blocked.status_code == 409
    body = blocked.json()["error"]
    assert body["code"] == "pr_undo_not_available"
    assert body["details"]["reason"] == "production_handed_off"

    world.act_as(world.other)
    refused = world.client.post(f"/api/pr/contents/{content_id}/undo")
    assert refused.status_code == 403
    for leak in ("IntegrityError", "SELECT", "Traceback", "psycopg"):
        assert leak not in refused.json()["error"]["message"]


async def test_45a_the_undo_action_says_what_it_would_reverse(world: World) -> None:
    """Requirement 37 of the specification: the client is told, not left to guess."""
    content_id = await approved(world)
    action = await world.action(world.head, content_id, "UNDO_LAST_ACTION")
    assert action is not None
    assert action.undo_kind == PrUndoKind.UNDO_HEAD_APPROVAL.value
    assert action.target_stage is PrWorkflowStage.HEAD_REVIEW

    world.act_as(world.head)
    listed = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()
    undo_actions = [
        row for row in listed["available_actions"] if row["action"] == "UNDO_LAST_ACTION"
    ]
    assert len(undo_actions) == 1
    assert undo_actions[0]["undo_kind"] == "UNDO_HEAD_APPROVAL"
    assert undo_actions[0]["target_stage"] == PrWorkflowStage.HEAD_REVIEW.value


async def test_45b_undoing_writes_its_own_audit_line(world: World) -> None:
    """The trail says both things happened, in order."""
    content_id = await approved(world)
    await undo(world, content_id, actor=world.head)

    actions = (
        (
            await world.session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == str(content_id))
            )
        )
        .scalars()
        .all()
    )
    assert "pr.workflow.undone" in actions
    assert "pr.content.stage_changed" in actions


async def test_45c_published_content_offers_no_undo_at_all(world: World) -> None:
    """Requirement 34. The boundary, with no capability that crosses it."""
    content_id = await in_internal_review(world)
    await decide(world, content_id, PrApprovalStage.INTERNAL_REVIEW)
    await world.services.publications.register_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=await _publication(world, content_id),
    )
    row = await world.reload(content_id)
    assert row.workflow_stage is PrWorkflowStage.PUBLISHED

    for person in (world.owner, world.lead, world.head):
        assert "UNDO_LAST_ACTION" not in await world.actions(person, content_id)
    with pytest.raises(PrUndoNotAvailableError) as refusal:
        await undo(world, content_id, actor=world.owner)
    assert refusal.value.details["reason"] == "published"


async def test_45d_the_transition_history_records_every_move(world: World) -> None:
    """The structured half of the trail, written by the one writer of the stage.

    Asserted as a sequence rather than a count: what makes the history usable for
    undo is that each row says where the content came from and where it went, and
    that the approvals carry the decision that caused them.
    """
    content_id = await in_internal_review(world)
    events = await world.transitions(content_id)
    assert [(event.from_stage.value, event.to_stage.value) for event in events] == [
        ("IDEA", "BRIEFING"),
        ("BRIEFING", "SCRIPTING"),
        ("SCRIPTING", "AI_REVIEW"),
        ("AI_REVIEW", "TEAM_LEAD_REVIEW"),
        ("TEAM_LEAD_REVIEW", "HEAD_REVIEW"),
        ("HEAD_REVIEW", "APPROVED"),
        ("APPROVED", "PRODUCTION"),
        ("PRODUCTION", "INTERNAL_REVIEW"),
    ]
    approvals = [event for event in events if event.approval_event_id is not None]
    assert len(approvals) == 2
    submissions = [event for event in events if event.production_submission_id is not None]
    assert len(submissions) == 0 or submissions[0].to_stage is PrWorkflowStage.INTERNAL_REVIEW
    assert all(event.actor_user_id is not None for event in events)


async def test_45e_an_unreachable_recipient_does_not_fail_the_approval(world: World) -> None:
    """A teammate who has never opened the bot is not a reason to refuse work."""
    world.member.telegram_private_chat_id = None
    world.member.telegram_user_id = None
    await world.session.flush()

    content_id = await approved(world)
    assert (await world.reload(content_id)).workflow_stage is PrWorkflowStage.APPROVED
    assert [row for row in await world.messages() if row.event_type == "pr_content_approved"] == []


async def test_45f_my_actions_carries_the_handoff(world: World) -> None:
    """Requirement 11 of the specification, and its limit.

    The owner sees their own approved piece waiting for a producer; management
    sees it because arranging that is their job; an unrelated member does not,
    which is what stops every approved item landing in everybody's queue.
    """
    content_id = await approved(world)

    for person, expected in ((world.member, True), (world.lead, True), (world.other, False)):
        world.act_as(person)
        board = world.client.get("/api/pr/contents/board", params={"scope": "MY_ACTIONS"}).json()
        ids = {uuid.UUID(row["id"]) for row in board["items"]}
        assert (content_id in ids) is expected, person.full_name

    # Once it is theirs, the producer sees it - they can start it.
    await assign(world, content_id, world.other)
    world.act_as(world.other)
    board = world.client.get("/api/pr/contents/board", params={"scope": "MY_ACTIONS"}).json()
    assert content_id in {uuid.UUID(row["id"]) for row in board["items"]}


async def test_45g_undo_moves_the_item_back_between_scopes(world: World) -> None:
    """Requirement 64 of the specification: the board follows the undo.

    Counters and scopes are computed from the stage, so this is really an
    assertion that the undo moved the stage properly - which is worth making
    because the alternative implementation, editing history, would leave the
    board exactly as it was.
    """
    content_id = await approved(world)
    world.act_as(world.head)
    before = world.client.get("/api/pr/contents/board", params={"scope": "ALL"}).json()
    counts = {row["stage"]: row["count"] for row in before["stage_counts"]}
    assert counts[PrWorkflowStage.APPROVED.value] == 1
    assert counts[PrWorkflowStage.HEAD_REVIEW.value] == 0

    world.client.post(f"/api/pr/contents/{content_id}/undo")
    after = world.client.get("/api/pr/contents/board", params={"scope": "ALL"}).json()
    counts = {row["stage"]: row["count"] for row in after["stage_counts"]}
    assert counts[PrWorkflowStage.APPROVED.value] == 0
    assert counts[PrWorkflowStage.HEAD_REVIEW.value] == 1
