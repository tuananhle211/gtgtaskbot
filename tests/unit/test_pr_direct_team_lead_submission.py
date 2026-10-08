"""Step 1F.2.10 - submitting a finished script straight to the Team Lead.

The requirement is one extra button at *Viết kịch bản*: beside *"Gửi đi AI
review"* the author may choose *"Gửi duyệt Trưởng nhóm"*, and the piece lands
on the same *Chờ duyệt Trưởng nhóm* column the AI path lands on. Only the AI
review is skipped. Nothing human is.

Numbered after the test matrix the step was specified with: 1-6 the two
submissions and what the direct one does *not* write, 7-8 who may decide and
who may submit, 9-10 where it is refused, 11 the second time round, 12-13
notifications, 14 history, 15-16 the board, 17-19 the races as the offline
fixture can see them (the PostgreSQL half is
``tests/integration/test_pr_direct_submission_race_pg.py``), and a tail of
contract checks the panel relies on.

Everything runs against real SQL on one transaction, as the handoff suite does.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.pr_action_service import PrActionEmphasis, PrActionKind
from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_service import (
    ContentTargetSpec,
    CreateContentCommand,
    ReviseContentCommand,
)
from meobot.application.pr_policy_readiness_service import (
    REASON_MODE_REQUIRED,
    REASON_PACK_UNAVAILABLE,
    REASON_TARGETS_REQUIRED,
)
from meobot.application.pr_services import PrServices, build_pr_services
from meobot.application.pr_workflow_service import capability_for_target
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.notifications import OutboundMessage
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrContentItem,
    PrContentTarget,
    PrPlatform,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrAiReviewRequiredError,
    PrPermissionDeniedError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelCategory,
    PrDistributionMode,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import PrTransitionTrigger
from meobot.tools.pr_content_tools import MANUAL_TARGETS
from tests.unit.streams import tag_pr

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
TEAM_LEAD = PrApprovalStage.TEAM_LEAD_REVIEW


@dataclass
class World:
    session: AsyncSession
    client: TestClient
    settings: Settings
    services: PrServices
    owner: User
    #: TEAM_LEAD, granted the team-lead and head gates.
    lead: User
    #: EMPLOYEE, the author.
    member: User
    #: EMPLOYEE, an unrelated colleague with no grant.
    other: User
    brand_id: uuid.UUID
    #: On a platform with no policy pack, so both submissions are open.
    website_id: uuid.UUID
    #: On TikTok, a policy-grounded platform: the AI path needs an active pack.
    tiktok_id: uuid.UUID

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

    async def stage(self, content_id: uuid.UUID) -> PrWorkflowStage:
        return (await self.reload(content_id)).workflow_stage

    async def actions(self, user: User, content_id: uuid.UUID):  # type: ignore[no-untyped-def]
        content = await self.reload(content_id)
        return await self.services.actions.for_content(actor=self.actor(user), content=content)

    async def transitions(self, content_id: uuid.UUID) -> list[PrContentTransitionEvent]:
        rows = await self.session.execute(
            select(PrContentTransitionEvent)
            .where(PrContentTransitionEvent.content_id == content_id)
            .order_by(PrContentTransitionEvent.created_at.asc())
        )
        return list(rows.scalars().all())

    async def count(self, model: type) -> int:
        result = await self.session.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())

    async def audit(self, action: str) -> list[AuditLog]:
        rows = await self.session.execute(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
        )
        return list(rows.scalars().all())


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    owner = User(full_name="Chị Chủ", role=Role.OWNER, telegram_user_id=901)
    lead = User(full_name="Lê Trưởng Nhóm", role=Role.TEAM_LEAD, telegram_user_id=902)
    member = User(full_name="Trần Minh Anh", role=Role.EMPLOYEE, telegram_user_id=904)
    other = User(full_name="Nguyễn A", role=Role.EMPLOYEE, telegram_user_id=905)
    for person in (owner, lead, member, other):
        person.telegram_private_chat_id = person.telegram_user_id
        person.private_chat_available = True
    brand = PrBrand(code="BRND-A", name="Apexmed")
    website = PrPlatform(code="WEBSITE", name="Website")
    tiktok = PrPlatform(code="TIKTOK", name="TikTok")
    session.add_all([owner, lead, member, other, brand, website, tiktok])
    await session.flush()
    await tag_pr(session, [owner, lead, member, other])  # untagged sees no stream

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    services = build_pr_services(session, settings)
    granter = Actor(user_id=owner.id, full_name=owner.full_name, role=Role.OWNER)
    for capability in (PrCapability.PR_TEAM_LEAD_REVIEW, PrCapability.PR_HEAD_REVIEW):
        await services.capabilities.grant(
            actor=granter, request_id=uuid.uuid4(), user_id=lead.id, capability=capability
        )
    channels: dict[str, uuid.UUID] = {}
    for name, platform in (("Apexmed Website", website), ("TikTok Apexmed", tiktok)):
        channel = await services.channels.create_channel(
            actor=granter,
            request_id=uuid.uuid4(),
            command=CreateChannelCommand(
                name=name,
                platform_id=platform.id,
                brand_id=brand.id,
                category=PrChannelCategory.SCALE,
            ),
        )
        channels[platform.code] = channel.id

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
            member=member,
            other=other,
            brand_id=brand.id,
            website_id=channels["WEBSITE"],
            tiktok_id=channels["TIKTOK"],
        )
        built.act_as(member)
        yield built
    app.dependency_overrides.clear()


# --- Walking the workflow ---------------------------------------------------


async def scripted(
    world: World,
    *,
    targets: tuple[ContentTargetSpec, ...] | None = None,
) -> uuid.UUID:
    """One item at ``SCRIPTING`` with a first draft, owned by the member."""
    snapshot = await world.services.content.create_content(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateContentCommand(
            title="Bài mới",
            brand_id=world.brand_id,
            owner_user_id=world.member.id,
            script_text="Nội dung.",
            targets=(
                targets
                if targets is not None
                else (ContentTargetSpec(channel_id=world.website_id),)
            ),
        ),
    )
    content_id = snapshot.content.id
    for stage in (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING):
        await world.services.workflow.request_transition(
            actor=world.actor(world.member),
            request_id=world.request_id,
            content_id=content_id,
            target=stage,
        )
    return content_id


async def submit_direct(world: World, content_id: uuid.UUID, *, actor: User | None = None) -> None:
    await world.services.workflow.submit_to_team_lead_review(
        actor=world.actor(actor or world.member),
        request_id=world.request_id,
        content_id=content_id,
    )


async def submit_ai(world: World, content_id: uuid.UUID) -> None:
    await world.services.workflow.request_transition(
        actor=world.actor(world.member),
        request_id=world.request_id,
        content_id=content_id,
        target=PrWorkflowStage.AI_REVIEW,
    )


async def ai_passes(world: World, content_id: uuid.UUID, *, version: int = 1) -> None:
    await world.services.ai_reviews.record_review(
        actor=world.actor(world.owner),
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
    world: World,
    content_id: uuid.UUID,
    decision: PrApprovalDecision = PrApprovalDecision.APPROVED,
    *,
    version: int = 1,
    stage: PrApprovalStage = TEAM_LEAD,
) -> None:
    await world.services.approvals.record_decision(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.lead.id,
            approval_stage=stage,
            decision=decision,
            version_reviewed=version,
        ),
    )


def transitions_of(actions) -> dict[PrWorkflowStage, PrActionEmphasis]:  # type: ignore[no-untyped-def]
    return {
        action.target_stage: action.emphasis
        for action in actions
        if action.kind is PrActionKind.TRANSITION and action.target_stage is not None
    }


# ===========================================================================
# 1-6: TWO SUBMISSIONS, ONE DESTINATION, AND WHAT THE DIRECT ONE DOES NOT WRITE
# ===========================================================================


async def test_01_the_ai_submission_works_exactly_as_before(world: World) -> None:
    """The control: the AI path is untouched, run queued and all."""
    content_id = await scripted(world)
    await submit_ai(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.AI_REVIEW
    assert await world.count(PrAiReviewRun) == 1
    await ai_passes(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW


async def test_02_and_03_the_direct_submission_lands_on_the_same_team_lead_stage(
    world: World,
) -> None:
    content_id = await scripted(world)
    offered = transitions_of(await world.actions(world.member, content_id))
    # Both submissions are on offer at SCRIPTING, and AI review stays the
    # recommended one - the direct path is the second button, not the first.
    assert offered[PrWorkflowStage.AI_REVIEW] is PrActionEmphasis.PRIMARY
    assert offered[PrWorkflowStage.TEAM_LEAD_REVIEW] is PrActionEmphasis.SECONDARY

    await submit_direct(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW


async def test_04_to_06_no_ai_review_row_run_or_pass_is_written(world: World) -> None:
    """The bypass is an absence, and the absence is the point.

    No verdict row, no queued run, no audit line saying a review was requested
    or recorded, and no approval event - the piece is at the gate with nothing
    pretending to have judged it.
    """
    content_id = await scripted(world)
    await submit_direct(world, content_id)

    assert await world.count(PrAiReview) == 0
    assert await world.count(PrAiReviewRun) == 0
    assert await world.count(PrApprovalEvent) == 0
    assert await world.audit("pr.ai_review.recorded") == []
    assert await world.audit("pr.ai_review.requested") == []

    # And the AI panel's own read model says "nothing here", not "passed".
    review = await world.services.ai_reviews.latest_gating_review(content_id, version_no=1)
    assert review is None


async def test_06a_the_audit_line_says_the_ai_was_skipped(world: World) -> None:
    """Explicit in the trail, not merely implied by the edge."""
    content_id = await scripted(world)
    await submit_direct(world, content_id)

    rows = await world.audit("pr.content.stage_changed")
    direct = [row for row in rows if (row.after_data or {}).get("ai_review_bypassed")]
    assert len(direct) == 1
    line = direct[0]
    assert line.actor_user_id == world.member.id
    assert line.entity_id == str(content_id)
    assert line.before_data == {"workflow_stage": "SCRIPTING"}
    after = line.after_data or {}
    assert after["workflow_stage"] == "TEAM_LEAD_REVIEW"
    assert after["trigger"] == "MANUAL"
    assert after["action"] == "submit_team_lead_review"
    assert after["content_version_no"] == 1
    assert after["content_code"]
    # The ordinary stage changes carry no such claim - the flag means something
    # only because it is absent everywhere else.
    assert all(
        "ai_review_bypassed" not in (row.after_data or {}) for row in rows if row is not line
    )


# ===========================================================================
# 7-8: WHO MAY DECIDE, AND WHO MAY SUBMIT
# ===========================================================================


async def test_07_the_team_lead_reviews_direct_submissions_exactly_as_normal(
    world: World,
) -> None:
    """Same gate, same decisions, same outcomes, same notification."""
    content_id = await scripted(world)
    await submit_direct(world, content_id)

    lead_actions = await world.actions(world.lead, content_id)
    decisions = {a.decision for a in lead_actions if a.kind is PrActionKind.APPROVAL}
    assert decisions == {
        PrApprovalDecision.APPROVED,
        PrApprovalDecision.REVISION_REQUIRED,
        PrApprovalDecision.REJECTED,
    }
    # The author - and an ungranted colleague - get no decision buttons, as ever.
    for user in (world.member, world.other):
        assert not any(
            a.kind is PrActionKind.APPROVAL for a in await world.actions(user, content_id)
        )

    await decide(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.HEAD_REVIEW
    # The Team Lead approval notification the AI path produces, produced here.
    queued = (await world.session.execute(select(OutboundMessage))).scalars().all()
    assert [row.template_key for row in queued] == ["pr.team_lead_approved"]
    assert queued[0].recipient_user_id == world.member.id
    # And nothing downstream was skipped: HEAD_REVIEW is a real gate, and the
    # author cannot move past it by hand.
    with pytest.raises(PrWorkflowTransitionError):
        await world.services.workflow.request_transition(
            actor=world.actor(world.member),
            request_id=world.request_id,
            content_id=content_id,
            target=PrWorkflowStage.APPROVED,
        )


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (PrApprovalDecision.REVISION_REQUIRED, PrWorkflowStage.SCRIPTING),
        (PrApprovalDecision.REJECTED, PrWorkflowStage.CANCELLED),
    ],
)
async def test_07a_return_and_reject_behave_as_on_the_ai_path(
    world: World, decision: PrApprovalDecision, expected: PrWorkflowStage
) -> None:
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    await decide(world, content_id, decision)
    assert await world.stage(content_id) is expected


async def test_07b_the_gate_is_pinned_to_the_submitted_draft(world: World) -> None:
    """A direct submission of v1 does not vouch for v2.

    The AI gate has always been per version; the direct route inherits that
    rather than relaxing it. Asked of the predicate the write and the panel
    both use.
    """
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    assert await world.services.approvals.ai_gate_satisfied(
        content_id, approval_stage=TEAM_LEAD, version_no=1
    )
    assert not await world.services.approvals.ai_gate_satisfied(
        content_id, approval_stage=TEAM_LEAD, version_no=2
    )
    # And a decision filed against a version that is not current is refused for
    # the reason it always was - before the gate is even asked.
    with pytest.raises((PrAiReviewRequiredError, Exception)):
        await decide(world, content_id, version=2)


async def test_08_the_direct_submission_needs_the_same_right_as_the_ai_one(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No new permission. The same capability gates both submissions, and a
    person the role matrix does not entitle is refused before the row is read."""
    assert capability_for_target(PrWorkflowStage.TEAM_LEAD_REVIEW) is (
        capability_for_target(PrWorkflowStage.AI_REVIEW)
    )
    assert capability_for_target(PrWorkflowStage.TEAM_LEAD_REVIEW) is (
        PrCapability.PR_CONTENT_TRANSITION
    )

    content_id = await scripted(world)
    from meobot.domain.pr import policy

    real = policy.meets_baseline
    monkeypatch.setattr(
        policy,
        "meets_baseline",
        lambda actor, capability: (
            False if capability is PrCapability.PR_CONTENT_TRANSITION else real(actor, capability)
        ),
    )
    with pytest.raises(PrPermissionDeniedError):
        await submit_direct(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.SCRIPTING
    assert PrWorkflowStage.TEAM_LEAD_REVIEW not in transitions_of(
        await world.actions(world.member, content_id)
    )


async def test_08a_the_submission_grants_nothing_downstream(world: World) -> None:
    """Whoever submits directly is no more a reviewer than before."""
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.approvals.record_decision(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=RecordApprovalCommand(
                content_id=content_id,
                reviewer_user_id=world.member.id,
                approval_stage=TEAM_LEAD,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
            ),
        )
    assert await world.stage(content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW


# ===========================================================================
# 9-10: WHERE IT IS REFUSED
# ===========================================================================


async def test_09_only_a_scripted_draft_may_be_submitted_directly(world: World) -> None:
    """From ``IDEA`` and ``BRIEFING`` the button is absent and the write is refused."""
    snapshot = await world.services.content.create_content(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateContentCommand(
            title="Ý tưởng",
            brand_id=world.brand_id,
            owner_user_id=world.member.id,
            script_text="Nội dung.",
            targets=(ContentTargetSpec(channel_id=world.website_id),),
        ),
    )
    content_id = snapshot.content.id
    for stage in (PrWorkflowStage.IDEA, PrWorkflowStage.BRIEFING):
        assert await world.stage(content_id) is stage
        assert PrWorkflowStage.TEAM_LEAD_REVIEW not in transitions_of(
            await world.actions(world.member, content_id)
        )
        with pytest.raises(PrWorkflowTransitionError) as refused:
            await submit_direct(world, content_id)
        assert refused.value.details["current"] == stage.value
        assert refused.value.details["target"] == "TEAM_LEAD_REVIEW"
        if stage is PrWorkflowStage.IDEA:
            await world.services.workflow.request_transition(
                actor=world.actor(world.member),
                request_id=world.request_id,
                content_id=content_id,
                target=PrWorkflowStage.BRIEFING,
            )


async def test_09a_it_is_refused_from_every_later_stage(world: World) -> None:
    """Once under review, or beyond it, the edge does not exist."""
    under_ai = await scripted(world)
    await submit_ai(world, under_ai)
    at_lead = await scripted(world)
    await submit_direct(world, at_lead)
    at_head = await scripted(world)
    await submit_direct(world, at_head)
    await decide(world, at_head)
    cancelled = await scripted(world)
    await world.services.workflow.cancel(
        actor=world.actor(world.lead), request_id=world.request_id, content_id=cancelled
    )
    for content_id, stage in (
        (under_ai, PrWorkflowStage.AI_REVIEW),
        (at_lead, PrWorkflowStage.TEAM_LEAD_REVIEW),
        (at_head, PrWorkflowStage.HEAD_REVIEW),
        (cancelled, PrWorkflowStage.CANCELLED),
    ):
        assert await world.stage(content_id) is stage
        with pytest.raises(PrWorkflowTransitionError):
            await submit_direct(world, content_id)
        assert await world.stage(content_id) is stage


async def test_09b_a_forged_request_gets_a_structured_409(world: World) -> None:
    """Over HTTP: a business refusal, with the edge in ``details``, and no 500."""
    content_id = await scripted(world)
    await submit_ai(world, content_id)
    response = world.client.post(
        f"/api/pr/contents/{content_id}/transition",
        json={"target_stage": "TEAM_LEAD_REVIEW"},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "pr_invalid_transition"
    assert error["details"]["current"] == "AI_REVIEW"
    assert error["details"]["target"] == "TEAM_LEAD_REVIEW"
    assert await world.stage(content_id) is PrWorkflowStage.AI_REVIEW


async def test_10_an_incomplete_draft_cannot_use_the_bypass(world: World) -> None:
    """Whatever stops the AI submission for *content* reasons stops this one.

    No planned channel, or an undecided Organic/Paid on a grounded platform,
    refuses both - with the same reason codes - and neither button is drawn.
    """
    no_channel = await scripted(world, targets=())
    offered = transitions_of(await world.actions(world.member, no_channel))
    assert PrWorkflowStage.AI_REVIEW not in offered
    assert PrWorkflowStage.TEAM_LEAD_REVIEW not in offered
    with pytest.raises(PrWorkflowTransitionError) as refused:
        await submit_direct(world, no_channel)
    assert refused.value.details["reason"] == REASON_TARGETS_REQUIRED
    assert "gửi duyệt" in refused.value.details["blocked_message"]
    with pytest.raises(PrWorkflowTransitionError) as refused_ai:
        await submit_ai(world, no_channel)
    assert refused_ai.value.details["reason"] == REASON_TARGETS_REQUIRED

    undecided = await scripted(
        world,
        targets=(
            ContentTargetSpec(
                channel_id=world.tiktok_id, distribution_mode=PrDistributionMode.ORGANIC
            ),
        ),
    )
    # Creation refuses an undecided grounded target outright, so the state is
    # produced the way the readiness suite produces it: on the row.
    rows = await world.session.execute(
        select(PrContentTarget).where(PrContentTarget.content_id == undecided)
    )
    for row in rows.scalars().all():
        row.distribution_mode = PrDistributionMode.UNSPECIFIED
    await world.session.flush()
    offered = transitions_of(await world.actions(world.member, undecided))
    assert PrWorkflowStage.TEAM_LEAD_REVIEW not in offered
    with pytest.raises(PrWorkflowTransitionError) as refused:
        await submit_direct(world, undecided)
    assert refused.value.details["reason"] == REASON_MODE_REQUIRED
    assert await world.stage(undecided) is PrWorkflowStage.SCRIPTING


async def test_10a_a_missing_policy_pack_stops_the_ai_but_not_the_person(world: World) -> None:
    """The one readiness check the direct path does not inherit, by design.

    An active policy pack is what a *grounded AI review* runs against. With none
    activated for TikTok, the AI submission is refused as it always was; the
    Team Lead does not need a pack to read a script, so the direct submission is
    open. Content completeness - the channel, the Organic/Paid decision - was
    still required above.
    """
    content_id = await scripted(
        world,
        targets=(
            ContentTargetSpec(
                channel_id=world.tiktok_id, distribution_mode=PrDistributionMode.ORGANIC
            ),
        ),
    )
    offered = transitions_of(await world.actions(world.member, content_id))
    assert PrWorkflowStage.AI_REVIEW not in offered
    assert PrWorkflowStage.TEAM_LEAD_REVIEW in offered
    with pytest.raises(PrWorkflowTransitionError) as refused:
        await submit_ai(world, content_id)
    assert refused.value.details["reason"] == REASON_PACK_UNAVAILABLE

    await submit_direct(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW
    assert await world.count(PrAiReviewRun) == 0


# ===========================================================================
# 11: THE SECOND TIME ROUND
# ===========================================================================


async def test_11_after_a_return_the_author_chooses_again(world: World) -> None:
    """Returned, revised, and both submissions are on offer once more.

    The previous choice is not remembered for them, and the new draft is what
    the gate is then pinned to - not the version the Team Lead sent back.
    """
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    await decide(world, content_id, PrApprovalDecision.REVISION_REQUIRED)
    assert await world.stage(content_id) is PrWorkflowStage.SCRIPTING

    await world.services.content.revise_content(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=ReviseContentCommand(
            content_id=content_id, expected_version=1, script_text="Bản sửa."
        ),
    )
    offered = transitions_of(await world.actions(world.member, content_id))
    assert offered[PrWorkflowStage.AI_REVIEW] is PrActionEmphasis.PRIMARY
    assert offered[PrWorkflowStage.TEAM_LEAD_REVIEW] is PrActionEmphasis.SECONDARY

    await submit_direct(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW
    assert await world.services.approvals.ai_gate_satisfied(
        content_id, approval_stage=TEAM_LEAD, version_no=2
    )
    events = await world.transitions(content_id)
    latest = events[-1]
    version_2 = (
        await world.session.execute(
            select(PrContentVersion.id).where(
                PrContentVersion.content_id == content_id, PrContentVersion.version_no == 2
            )
        )
    ).scalar_one()
    assert latest.content_version_id == version_2
    await decide(world, content_id, version=2)
    assert await world.stage(content_id) is PrWorkflowStage.HEAD_REVIEW


async def test_11a_the_author_may_take_the_ai_path_the_second_time(world: World) -> None:
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    await decide(world, content_id, PrApprovalDecision.REVISION_REQUIRED)
    await world.services.content.revise_content(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=ReviseContentCommand(
            content_id=content_id, expected_version=1, script_text="Bản sửa."
        ),
    )
    await submit_ai(world, content_id)
    assert await world.stage(content_id) is PrWorkflowStage.AI_REVIEW
    assert await world.count(PrAiReviewRun) == 1
    await ai_passes(world, content_id, version=2)
    assert await world.stage(content_id) is PrWorkflowStage.TEAM_LEAD_REVIEW


# ===========================================================================
# 12-13: NOTIFICATIONS
# ===========================================================================


async def test_12_and_13_the_handoff_notifies_exactly_what_the_ai_path_does(
    world: World,
) -> None:
    """Which, on entering ``TEAM_LEAD_REVIEW``, is nothing - on both paths.

    The module has never messaged reviewers when work arrives at a gate (see
    ``pr_notifications``: a capability is not a person, and ``MY_ACTIONS`` is
    the queue). The direct path adds no message and, in particular, no "AI
    review completed" of any kind.
    """
    via_ai = await scripted(world)
    await submit_ai(world, via_ai)
    await ai_passes(world, via_ai)
    after_ai = await world.count(OutboundMessage), await world.count(UserNotification)

    direct = await scripted(world)
    await submit_direct(world, direct)
    after_direct = await world.count(OutboundMessage), await world.count(UserNotification)

    assert after_ai == (0, 0)
    assert after_direct == after_ai
    templates = (await world.session.execute(select(OutboundMessage.template_key))).scalars().all()
    assert not any("ai" in key.lower() for key in templates)


# ===========================================================================
# 14: HISTORY
# ===========================================================================


async def test_14_the_history_records_the_bypass_as_its_own_edge(world: World) -> None:
    """One row, ``SCRIPTING -> TEAM_LEAD_REVIEW``, manual, pinned to the draft.

    No other path produces that pair with that trigger, which is what lets the
    history tab word it as "bỏ qua AI review" and the gate accept it.
    """
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    events = await world.transitions(content_id)
    assert [(e.from_stage.value, e.to_stage.value, e.trigger.value) for e in events] == [
        ("IDEA", "BRIEFING", "MANUAL"),
        ("BRIEFING", "SCRIPTING", "MANUAL"),
        ("SCRIPTING", "TEAM_LEAD_REVIEW", "MANUAL"),
    ]
    direct = events[-1]
    assert direct.actor_user_id == world.member.id
    assert direct.approval_event_id is None
    assert direct.content_version_id is not None

    body = world.client.get(f"/api/pr/contents/{content_id}/history").json()
    assert body[-1]["from_stage"] == "SCRIPTING"
    assert body[-1]["to_stage"] == "TEAM_LEAD_REVIEW"
    assert body[-1]["trigger"] == "MANUAL"

    # And it is not something an undo can take back: nothing was decided.
    assert not any(
        a.kind is PrActionKind.UNDO_LAST_ACTION for a in await world.actions(world.lead, content_id)
    )


# ===========================================================================
# 15-16: THE BOARD
# ===========================================================================


async def test_15_and_16_the_piece_is_counted_once_under_the_team_lead_lane(
    world: World,
) -> None:
    content_id = await scripted(world)
    world.act_as(world.lead)
    await submit_direct(world, content_id)

    def lane(name: str) -> list[str]:
        response = world.client.get("/api/pr/contents/board", params={"lane": name, "scope": "ALL"})
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert lane("TEAM_LEAD_REVIEW") == [str(content_id)]
    assert lane("AI_REVIEW") == []
    assert lane("SCRIPTING") == []

    figures = world.client.get("/api/pr/contents/board", params={"scope": "ALL", "limit": 0}).json()
    counts = {row["stage"]: row["count"] for row in figures["stage_counts"]}
    assert counts.get("TEAM_LEAD_REVIEW") == 1
    assert counts.get("AI_REVIEW", 0) == 0
    review_group = world.client.get(
        "/api/pr/contents/board", params={"scope": "ALL", "group": "EDITORIAL_REVIEW"}
    ).json()
    assert review_group["total"] == 1


# ===========================================================================
# 17-19: THE RACES, AS FAR AS ONE TRANSACTION CAN SEE THEM
# ===========================================================================


async def test_17_to_19_a_second_submission_or_edit_finds_the_piece_gone(world: World) -> None:
    """Whichever writer is first wins; the second is refused, not merged.

    On SQLite the lock is a plain read, so this proves the *rule* - the second
    request re-reads the stage under the lock and finds no edge. The lock being
    real, and two connections actually contending, is the PostgreSQL suite.
    """
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    # 18: a second direct submission.
    with pytest.raises(PrWorkflowTransitionError):
        await submit_direct(world, content_id)
    # 17: the AI submission after the direct one.
    with pytest.raises(PrWorkflowTransitionError):
        await submit_ai(world, content_id)
    # 19: a revision after the submission - the draft is under review.
    with pytest.raises(PrWorkflowTransitionError):
        await world.services.content.revise_content(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=ReviseContentCommand(
                content_id=content_id, expected_version=1, script_text="Sửa muộn."
            ),
        )
    events = await world.transitions(content_id)
    assert sum(1 for e in events if e.to_stage is PrWorkflowStage.TEAM_LEAD_REVIEW) == 1
    assert await world.count(PrContentVersion) == 1
    assert await world.count(PrAiReviewRun) == 0

    # 17, the other order: once under AI review, the direct button is gone too.
    other = await scripted(world)
    await submit_ai(world, other)
    with pytest.raises(PrWorkflowTransitionError):
        await submit_direct(world, other)
    assert await world.stage(other) is PrWorkflowStage.AI_REVIEW


# ===========================================================================
# THE CONTRACT THE PANEL RENDERS FROM
# ===========================================================================


async def test_20_the_available_actions_route_offers_both_submissions(world: World) -> None:
    content_id = await scripted(world)
    body = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()
    offered = {
        action["target_stage"]: action["emphasis"]
        for action in body["available_actions"]
        if action["action"] == "TRANSITION"
    }
    assert offered["AI_REVIEW"] == "PRIMARY"
    assert offered["TEAM_LEAD_REVIEW"] == "SECONDARY"
    # Cancelling takes the approval right, which the author does not hold - so
    # the two submissions are the whole forward offer for them.
    assert set(offered) == {"AI_REVIEW", "TEAM_LEAD_REVIEW"}

    response = world.client.post(
        f"/api/pr/contents/{content_id}/transition",
        json={"target_stage": "TEAM_LEAD_REVIEW", "note": "Bài ngắn, không cần AI."},
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"]["workflow_stage"] == "TEAM_LEAD_REVIEW"
    events = await world.transitions(content_id)
    assert events[-1].note == "Bài ngắn, không cần AI."
    assert events[-1].trigger is PrTransitionTrigger.MANUAL

    after = world.client.get(f"/api/pr/contents/{content_id}/available-actions").json()
    assert not any(
        action["action"] == "TRANSITION"
        and action["target_stage"] in {"AI_REVIEW", "TEAM_LEAD_REVIEW"}
        for action in after["available_actions"]
    )


async def test_21_the_telegram_tool_schema_names_the_edge(world: World) -> None:
    """The bot's ``pr.content.transition`` may describe the move; the service
    still decides it."""
    assert PrWorkflowStage.TEAM_LEAD_REVIEW.value in MANUAL_TARGETS


async def test_22_no_migration_was_needed(world: World) -> None:
    """Every fact the step records fits the tables that already exist."""
    content_id = await scripted(world)
    await submit_direct(world, content_id)
    row = (await world.transitions(content_id))[-1]
    # Stored as an existing trigger value in an existing column, with the
    # existing version pin - nothing new on disk.
    assert row.trigger is PrTransitionTrigger.MANUAL
    assert isinstance(row.content_version_id, uuid.UUID)
    channel = await world.session.get(PrChannel, world.website_id)
    assert channel is not None
