"""Step 1F.2.3a - "Xóa nội dung" destroys the aggregate, and who may do it.

Numbered 1-39, continuing the requirement numbering the step was specified with:
1-12 the policy, 13-29 what is and is not removed, 30-35 transactions and races,
36-39 the surviving audit.

Two of these matter more than the rest, and they pull in opposite directions:

* **test 12** - no capability, role or combination crosses the published
  boundary. There is no override to find and no flag to set, and a change that
  introduced one would fail here;
* **test 29a** - the delete plan is complete. It is written against the
  metadata's own foreign keys rather than a list, so a table added next year that
  points at content, a version, a task, a run or a submission fails this test
  until somebody decides what deleting content should do about it.

Everything runs against real SQL on one transaction, because the questions are
about SQL: whether a row is gone, whether a foreign key would have stopped it,
whether one statement's rollback takes the others with it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.pr_ai_review_run_service import PrAiReviewRunService
from meobot.application.pr_ai_review_service import RecordAiReviewCommand
from meobot.application.pr_approval_service import RecordApprovalCommand
from meobot.application.pr_channel_service import CreateChannelCommand
from meobot.application.pr_content_resource_service import AddContentResourceCommand
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_lifecycle_service import PrContentLifecycleService
from meobot.application.pr_production_service import SubmitProductionCommand
from meobot.application.pr_publication_service import RegisterPublicationCommand
from meobot.application.pr_services import PrServices, build_pr_services
from meobot.application.pr_task_service import CreateTaskCommand
from meobot.core.config import Settings
from meobot.db.base import Base
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrContentItem,
    PrContentTarget,
    PrPlatform,
    PrTask,
    PrTaskAssignment,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_ai_review_run import PrAiReviewRun
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_platform_policy import (
    PrAiReviewRunPolicyPack,
    PrPlatformPolicyPack,
)
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrIssue, PrPublication
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrPublishedContentError,
)
from meobot.domain.pr.lifecycle import (
    PUBLISHED_ONWARD_STAGES,
    is_published_onward,
    may_hard_delete,
)
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewRunStatus,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelCategory,
    PrContentResourceType,
    PrPolicyPackStatus,
    PrProductionArtifactType,
    PrTaskAssignmentRole,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrIssueSeverity
from meobot.domain.pr.work import PrWorkCategory
from tests.unit.streams import tag_pr

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
DRIVE = "https://drive.google.com/file/d/1AbCdEf/view"


@dataclass
class World:
    session: AsyncSession
    client: TestClient
    settings: Settings
    services: PrServices
    #: OWNER. Holds every permission, so also the management delete.
    owner: User
    #: TEAM_LEAD, granted every review capability. Management for delete.
    lead: User
    #: EMPLOYEE. Writes content, produces it, deletes their own untouched work.
    member: User
    #: EMPLOYEE. The colleague every "not yours" test needs.
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

    async def row(self, content_id: uuid.UUID) -> PrContentItem | None:
        return await self.session.get(PrContentItem, content_id)

    async def count(self, model: type[Base], *conditions: object) -> int:
        statement = select(func.count()).select_from(model)
        if conditions:
            statement = statement.where(*conditions)  # type: ignore[arg-type]
        return int((await self.session.scalar(statement)) or 0)

    async def delete(self, user: User, content_id: uuid.UUID, *, reason: str | None = None):  # type: ignore[no-untyped-def]
        return await self.services.lifecycle.delete_content(
            actor=self.actor(user),
            request_id=self.request_id,
            content_id=content_id,
            reason=reason,
        )

    async def may_delete(self, user: User, content_id: uuid.UUID) -> bool:
        content = await self.row(content_id)
        assert content is not None
        return await self.services.lifecycle.may_delete(self.actor(user), content)

    async def actions(self, user: User, content_id: uuid.UUID) -> set[str]:
        content = await self.row(content_id)
        assert content is not None
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
    member = User(full_name="Phương Nhung", role=Role.EMPLOYEE)
    other = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-A", name="Apexmed")
    platform = PrPlatform(code="WEBSITE", name="Website")
    session.add_all([owner, lead, member, other, brand, platform])
    await session.flush()
    await tag_pr(session, [owner, lead, member, other])  # untagged sees no stream

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    services = build_pr_services(session, settings)
    granter = Actor(user_id=owner.id, full_name=owner.full_name, role=Role.OWNER)
    for capability in (
        PrCapability.PR_TEAM_LEAD_REVIEW,
        PrCapability.PR_HEAD_REVIEW,
        PrCapability.PR_INTERNAL_REVIEW,
    ):
        await services.capabilities.grant(
            actor=granter, request_id=uuid.uuid4(), user_id=lead.id, capability=capability
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
            member=member,
            other=other,
            brand_id=brand.id,
            channel_id=channel.id,
        )
        built.act_as(owner)
        yield built
    app.dependency_overrides.clear()


# --- Building content at a given point in its life --------------------------


async def make_content(
    world: World, *, owner: User | None = None, title: str = "Bài mới"
) -> uuid.UUID:
    snapshot = await world.services.content.create_content(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=CreateContentCommand(
            title=title,
            brand_id=world.brand_id,
            owner_user_id=(owner or world.member).id,
            script_text="Nội dung.",
            targets=(ContentTargetSpec(channel_id=world.channel_id),),
        ),
    )
    return snapshot.content.id


async def walk(world: World, content_id: uuid.UUID, *, to: PrWorkflowStage) -> None:
    """Walk a piece to ``to`` the way the workflow actually goes.

    Never by assigning ``workflow_stage``: the transition into ``PRODUCTION`` is
    what stamps ``production_started_at``, and the delete rule reads that column.
    A fixture that forced the stage would quietly pass the tests that matter.
    """
    order = list(PrWorkflowStage)
    if order.index(to) >= order.index(PrWorkflowStage.AI_REVIEW):
        for stage in (
            PrWorkflowStage.BRIEFING,
            PrWorkflowStage.SCRIPTING,
            PrWorkflowStage.AI_REVIEW,
        ):
            await transition(world, content_id, stage)
        if to is PrWorkflowStage.AI_REVIEW:
            return
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
        if to is PrWorkflowStage.TEAM_LEAD_REVIEW:
            return
        await decide(world, content_id, PrApprovalStage.TEAM_LEAD_REVIEW)
        if to is PrWorkflowStage.HEAD_REVIEW:
            return
        await decide(world, content_id, PrApprovalStage.HEAD_REVIEW)
        if to is PrWorkflowStage.APPROVED:
            return
        # Step 1F.2.3b: the handoff, then the start. ``APPROVED -> PRODUCTION``
        # is no longer a move anybody can make on a piece nobody holds.
        await world.services.production.assign_producer(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            content_id=content_id,
            producer_user_id=world.member.id,
        )
        await world.services.production.start_production(
            actor=world.actor(world.member),
            request_id=world.request_id,
            content_id=content_id,
        )
        if to is PrWorkflowStage.PRODUCTION:
            return
        submission = await submit(world, content_id)
        if to is PrWorkflowStage.INTERNAL_REVIEW:
            return
        await decide(world, content_id, PrApprovalStage.INTERNAL_REVIEW)
        if to is PrWorkflowStage.READY_TO_PUBLISH:
            return
        # Step 1F.2.3f.1: ``READY_TO_PUBLISH -> PUBLISHED`` is no longer a move
        # anybody can make by naming a stage. A piece becomes published by
        # recording *where it went*, and the publication writes the transition in
        # the same commit - which is the honest way to reach this state and is
        # what these delete tests are actually about.
        await publish(world, content_id, submission)
        if to is PrWorkflowStage.PUBLISHED:
            return
        raise AssertionError(f"{to} is past what this helper walks")

    for stage in (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING):
        await transition(world, content_id, stage)
        if stage is to:
            return


async def transition(world: World, content_id: uuid.UUID, target: PrWorkflowStage) -> None:
    await world.services.workflow.request_transition(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        content_id=content_id,
        target=target,
    )


async def decide(
    world: World,
    content_id: uuid.UUID,
    stage: PrApprovalStage,
    decision: PrApprovalDecision = PrApprovalDecision.APPROVED,
) -> None:
    await world.services.approvals.record_decision(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecordApprovalCommand(
            content_id=content_id,
            reviewer_user_id=world.lead.id,
            approval_stage=stage,
            decision=decision,
            version_reviewed=1,
        ),
    )


async def submit(world: World, content_id: uuid.UUID) -> PrProductionSubmission:
    return await world.services.production.submit_production(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=SubmitProductionCommand(
            content_id=content_id,
            artifact_type=PrProductionArtifactType.DRIVE_LINK,
            location=DRIVE,
        ),
    )


async def publish(world: World, content_id: uuid.UUID, submission: PrProductionSubmission) -> None:
    """Record that the approved master went out, which is what publishes a piece."""
    await world.services.publications.register_publication(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RegisterPublicationCommand(
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=NOW,
            production_submission_id=submission.id,
            url="https://example.test/post",
        ),
    )


async def at(world: World, stage: PrWorkflowStage, *, owner: User | None = None) -> uuid.UUID:
    content_id = await make_content(world, owner=owner)
    if stage is not PrWorkflowStage.IDEA:
        await walk(world, content_id, to=stage)
    return content_id


# ===========================================================================
# 1-12: WHO MAY DELETE WHAT
# ===========================================================================


async def test_01_a_member_deletes_their_own_pre_production_work(world: World) -> None:
    """Requirement 1, at two stages, because the member rule is not stage-based.

    ``HEAD_REVIEW`` is the interesting one: the piece is deep in review and has
    still never been produced, so it is still the member's to remove. What stops
    them is production, not progress.
    """
    for stage in (PrWorkflowStage.SCRIPTING, PrWorkflowStage.HEAD_REVIEW):
        content_id = await at(world, stage)
        await world.delete(world.member, content_id)
        assert await world.row(content_id) is None


async def test_02_a_member_cannot_delete_somebody_elses_work(world: World) -> None:
    """Requirement 2. The responsibility relation is the existing one."""
    content_id = await at(world, PrWorkflowStage.SCRIPTING)
    with pytest.raises(PrPermissionDeniedError) as refusal:
        await world.delete(world.other, content_id)
    assert refusal.value.details["reason"] == "not_responsible"
    assert await world.row(content_id) is not None


async def test_03_a_member_cannot_delete_produced_work(world: World) -> None:
    """Requirement 3. Their own piece, and refused because somebody has cut it."""
    content_id = await at(world, PrWorkflowStage.PRODUCTION)
    with pytest.raises(PrPermissionDeniedError) as refusal:
        await world.delete(world.member, content_id)
    assert refusal.value.details["reason"] == "already_produced"
    assert await world.row(content_id) is not None


async def test_04_moving_backwards_does_not_restore_the_member_right(world: World) -> None:
    """Requirement 4. The whole reason ``production_started_at`` exists.

    The internal reviewer sends the cut back, so the item is at ``PRODUCTION``
    again having been there before - and a rule reading the current stage would
    be reading a stage that says nothing about how much work is in the piece. The
    stamp is not cleared, so the answer does not change.
    """
    content_id = await at(world, PrWorkflowStage.INTERNAL_REVIEW)
    await decide(
        world,
        content_id,
        PrApprovalStage.INTERNAL_REVIEW,
        PrApprovalDecision.REVISION_REQUIRED,
    )
    row = await world.row(content_id)
    assert row is not None and row.workflow_stage is PrWorkflowStage.PRODUCTION
    assert row.production_started_at is not None

    with pytest.raises(PrPermissionDeniedError) as refusal:
        await world.delete(world.member, content_id)
    assert refusal.value.details["reason"] == "already_produced"


async def test_05_to_08_management_deletes_anything_before_publication(world: World) -> None:
    """Requirements 5-8. Every stage below the boundary, walked for real.

    Four stages and one loop rather than four tests, because the rule genuinely
    is "anything below the floor" and writing it out four times would suggest
    there is something stage-specific about it. ``INTERNAL_REVIEW`` and
    ``READY_TO_PUBLISH`` are the two that were impossible under the member rule,
    and are the reason management deletion exists.
    """
    for stage in (
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.PRODUCTION,
        PrWorkflowStage.INTERNAL_REVIEW,
        PrWorkflowStage.READY_TO_PUBLISH,
    ):
        content_id = await at(world, stage)
        await world.delete(world.lead, content_id, reason=f"dọn {stage.value}")
        assert await world.row(content_id) is None, stage


async def test_09_to_11_nobody_deletes_published_work(world: World) -> None:
    """Requirements 9-11, and the boundary in one place.

    ``PUBLISHED`` is walked; ``MEASURED`` and ``ARCHIVED`` are set directly,
    because reaching them needs a metric snapshot and a publication and this test
    is about the refusal rather than about the walk. Both the lead and the
    ``OWNER`` are refused, with the error that says *"lưu trữ thay thế"* rather
    than one that invites them to find somebody senior.
    """
    published = await at(world, PrWorkflowStage.PUBLISHED)
    for stage in (PrWorkflowStage.PUBLISHED, PrWorkflowStage.MEASURED, PrWorkflowStage.ARCHIVED):
        row = await world.row(published)
        assert row is not None
        row.workflow_stage = stage
        await world.session.flush()
        for manager in (world.lead, world.owner):
            with pytest.raises(PrPublishedContentError) as refusal:
                await world.delete(manager, published)
            assert refusal.value.details["workflow_stage"] == stage.value
            assert refusal.value.code == "pr_published_content"
        assert await world.row(published) is not None


async def test_12_no_capability_crosses_the_published_boundary(world: World) -> None:
    """Requirement 12. Asserted at the rule, over every combination.

    The service tests above prove two actors cannot; this proves *no* actor can,
    by enumerating the inputs rather than the people. Whatever a future
    capability set looks like, if it reaches this function with
    ``published_onward``, the answer is no - and the only way to change that is
    to move the line, which is a review nobody can miss.
    """
    for manages in (True, False):
        for responsible in (True, False):
            for produced in (True, False):
                assert not may_hard_delete(
                    may_delete=True,
                    manages_content=manages,
                    responsible=responsible,
                    reached_production=produced,
                    published_onward=True,
                )
    # And the floor is the three stages, not a hand-written list somewhere else.
    assert {
        PrWorkflowStage.PUBLISHED,
        PrWorkflowStage.MEASURED,
        PrWorkflowStage.ARCHIVED,
    } == PUBLISHED_ONWARD_STAGES
    assert not is_published_onward(PrWorkflowStage.READY_TO_PUBLISH)
    assert not is_published_onward(PrWorkflowStage.CANCELLED)


async def test_12a_a_direct_api_call_obeys_the_same_rules(world: World) -> None:
    """The rule is the server's, not the button's - over HTTP, for both refusals."""
    mine = await at(world, PrWorkflowStage.SCRIPTING)
    world.act_as(world.other)
    assert world.client.request("DELETE", f"/api/pr/contents/{mine}").status_code == 403

    published = await at(world, PrWorkflowStage.PUBLISHED)
    world.act_as(world.owner)
    refused = world.client.request("DELETE", f"/api/pr/contents/{published}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "pr_published_content"

    world.act_as(world.member)
    accepted = world.client.request("DELETE", f"/api/pr/contents/{mine}")
    assert accepted.status_code == 204
    assert not accepted.content
    assert world.client.get(f"/api/pr/contents/{mine}").status_code == 404


async def test_12aa_a_deleted_item_is_gone_from_every_read(world: World) -> None:
    """Requirement 26. Absent everywhere, and not as a tombstone.

    The four scopes, the flat list, the search and the counters, all asserted
    after a real deletion - and none of them needed a "not deleted" filter to
    make it true, because the row is gone. That is the whole practical difference
    from Step 1F.2.3, where the same list of places had to be taught to hide
    something.
    """
    kept = await make_content(world, title="Bài giữ lại")
    removed = await make_content(world, title="Bài sẽ xóa")
    await world.delete(world.member, removed)

    world.act_as(world.member)
    for scope in ("ALL", "MY_ACTIONS", "MY_CONTENT", "TEAM"):
        board = world.client.get("/api/pr/contents/board", params={"scope": scope}).json()
        titles = {row["title"] for row in board["items"]}
        assert "Bài sẽ xóa" not in titles, scope
        assert sum(row["count"] for row in board["stage_counts"]) == board["total"], scope

    listed = world.client.get("/api/pr/contents", params={"scope": "ALL"}).json()
    assert {row["title"] for row in listed} == {"Bài giữ lại"}
    found = world.client.get("/api/pr/contents", params={"search": "Bài"}).json()
    assert {row["title"] for row in found} == {"Bài giữ lại"}
    assert world.client.get(f"/api/pr/contents/{removed}").status_code == 404
    assert world.client.get(f"/api/pr/contents/{kept}").status_code == 200


async def test_12b_a_cancelled_item_follows_the_same_rules(world: World) -> None:
    """Cancelling is not deleting, and does not change who may delete.

    An abandoned pre-production piece is still the member's to remove, and
    nothing deletes it automatically - it sits in ``Đã hủy`` until somebody says
    so. A cancelled item that had reached production is management's, exactly as
    it was before it was cancelled.
    """
    mine = await at(world, PrWorkflowStage.SCRIPTING)
    await world.services.workflow.cancel(
        actor=world.actor(world.lead), request_id=world.request_id, content_id=mine
    )
    assert await world.row(mine) is not None, "cancelling deletes nothing"
    assert await world.may_delete(world.member, mine)

    produced = await at(world, PrWorkflowStage.PRODUCTION)
    await world.services.workflow.cancel(
        actor=world.actor(world.lead), request_id=world.request_id, content_id=produced
    )
    assert not await world.may_delete(world.member, produced)
    assert await world.may_delete(world.lead, produced)


async def test_12c_the_action_list_offers_delete_exactly_where_it_works(world: World) -> None:
    """Requirement 14. The read model and the write agree, by construction."""
    mine = await at(world, PrWorkflowStage.SCRIPTING)
    assert "DELETE_CONTENT" in await world.actions(world.member, mine)
    assert "DELETE_CONTENT" not in await world.actions(world.other, mine)

    produced = await at(world, PrWorkflowStage.PRODUCTION)
    assert "DELETE_CONTENT" not in await world.actions(world.member, produced)
    assert "DELETE_CONTENT" in await world.actions(world.lead, produced)

    ready = await at(world, PrWorkflowStage.READY_TO_PUBLISH)
    assert "DELETE_CONTENT" in await world.actions(world.lead, ready)

    published = await at(world, PrWorkflowStage.PUBLISHED)
    assert "DELETE_CONTENT" not in await world.actions(world.lead, published)
    assert "DELETE_CONTENT" not in await world.actions(world.owner, published)


# ===========================================================================
# 13-29: WHAT IS REMOVED, AND WHAT IS NOT
# ===========================================================================


@dataclass
class Aggregate:
    """One content item with as much hanging off it as this schema allows."""

    content_id: uuid.UUID
    version_ids: list[uuid.UUID]
    task_id: uuid.UUID
    run_id: uuid.UUID
    review_id: uuid.UUID
    submission_ids: list[uuid.UUID]
    issue_id: uuid.UUID
    pack_id: uuid.UUID
    #: Step 1F.2.3e. Review material, which the aggregate must take with it.
    resource_id: uuid.UUID


async def build_aggregate(world: World) -> Aggregate:
    """A realistic piece at ``INTERNAL_REVIEW``, with every child it can have.

    Built through the services wherever a service owns the write, and directly
    only for the two rows this module has no writer for - a pinned policy pack
    association and a reporting issue. Both are real shapes: the first is what
    Step 1F.1 writes when a review is queued, the second what a weekly report
    writes when somebody logs a problem against a piece.
    """
    content_id = await at(world, PrWorkflowStage.INTERNAL_REVIEW)

    # A second cut, so the aggregate has two submissions and two internal-review
    # events - the state a re-cut leaves behind.
    await decide(
        world,
        content_id,
        PrApprovalStage.INTERNAL_REVIEW,
        PrApprovalDecision.REVISION_REQUIRED,
    )
    await submit(world, content_id)

    task = await world.services.tasks.create_task(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateTaskCommand(task_type="EDIT", title="Dựng video", content_id=content_id),
    )
    await world.services.tasks.assign_user(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        task_id=task.id,
        user_id=world.member.id,
        assignment_role=PrTaskAssignmentRole.OWNER,
    )

    run = (
        (
            await world.session.execute(
                select(PrAiReviewRun).where(PrAiReviewRun.content_id == content_id)
            )
        )
        .scalars()
        .first()
    )
    assert run is not None, "entering AI_REVIEW queues a run"
    review = (
        (await world.session.execute(select(PrAiReview).where(PrAiReview.content_id == content_id)))
        .scalars()
        .first()
    )
    assert review is not None

    # One active pack per (platform, mode) - the partial unique index Step 1F.1
    # created - so a second aggregate pins the same pack rather than a second
    # one. Which is also the real shape: packs are shared, and this test is
    # partly about them surviving.
    pack = (
        (
            await world.session.execute(
                select(PrPlatformPolicyPack).where(PrPlatformPolicyPack.platform_code == "WEBSITE")
            )
        )
        .scalars()
        .first()
    )
    if pack is None:
        pack = PrPlatformPolicyPack(
            platform_code="WEBSITE",
            distribution_mode="ORGANIC",
            version=1,
            label="WEBSITE/ORGANIC v1",
            status=PrPolicyPackStatus.ACTIVE,
            manifest_hash="a" * 64,
        )
        world.session.add(pack)
        await world.session.flush()
    world.session.add(
        PrAiReviewRunPolicyPack(
            run_id=run.id,
            policy_pack_id=pack.id,
            platform_code="WEBSITE",
            distribution_mode="ORGANIC",
        )
    )
    issue = PrIssue(
        code=f"ISS-2026-{uuid.uuid4().hex[:6]}",
        content_id=content_id,
        title="Sai thông tin sản phẩm",
        severity=PrIssueSeverity.MEDIUM,
    )
    world.session.add(issue)
    await world.session.flush()

    # Step 1F.2.3e. A brief attached for review. Written through the service, so
    # the row is exactly the shape the application produces.
    resource = await world.services.content_resources.add_resource(
        actor=world.actor(world.lead),
        request_id=uuid.uuid4(),
        command=AddContentResourceCommand(
            content_id=content_id,
            resource_type=PrContentResourceType.REFERENCE,
            label="Brief khách hàng",
            location="https://drive.google.com/file/d/1AbCdEf/view",
            required_for_review=True,
        ),
    )

    versions = list(
        (
            await world.session.execute(
                select(PrContentVersion.id).where(PrContentVersion.content_id == content_id)
            )
        ).scalars()
    )
    submissions = list(
        (
            await world.session.execute(
                select(PrProductionSubmission.id).where(
                    PrProductionSubmission.content_id == content_id
                )
            )
        ).scalars()
    )
    return Aggregate(
        content_id=content_id,
        version_ids=versions,
        task_id=task.id,
        run_id=run.id,
        review_id=review.id,
        submission_ids=submissions,
        issue_id=issue.id,
        pack_id=pack.id,
        resource_id=resource.id,
    )


async def test_13_to_22_the_whole_aggregate_goes(world: World) -> None:
    """Requirements 13-22. Every content-owned table, emptied for this item.

    The aggregate is built first and asserted **non-empty**, because a deletion
    test whose fixture was quietly missing a child would pass by finding nothing
    to delete.
    """
    aggregate = await build_aggregate(world)
    content_id = aggregate.content_id

    before = {
        "versions": await world.count(PrContentVersion, PrContentVersion.content_id == content_id),
        "targets": await world.count(PrContentTarget, PrContentTarget.content_id == content_id),
        "tasks": await world.count(PrTask, PrTask.content_id == content_id),
        "assignments": await world.count(
            PrTaskAssignment, PrTaskAssignment.task_id == aggregate.task_id
        ),
        "runs": await world.count(PrAiReviewRun, PrAiReviewRun.content_id == content_id),
        "reviews": await world.count(PrAiReview, PrAiReview.content_id == content_id),
        "pinned": await world.count(
            PrAiReviewRunPolicyPack, PrAiReviewRunPolicyPack.run_id == aggregate.run_id
        ),
        "approvals": await world.count(PrApprovalEvent, PrApprovalEvent.content_id == content_id),
        "submissions": await world.count(
            PrProductionSubmission, PrProductionSubmission.content_id == content_id
        ),
        "resources": await world.count(
            PrContentResource, PrContentResource.content_id == content_id
        ),
    }
    assert all(count > 0 for count in before.values()), before
    # Two cuts, and three decisions: team lead, head, and the internal "sửa lại".
    assert before["submissions"] == 2 and before["approvals"] == 3

    await world.delete(world.lead, content_id, reason="khách hủy dự án")

    assert await world.row(content_id) is None  # 13
    assert await world.count(PrContentVersion, PrContentVersion.content_id == content_id) == 0  # 14
    assert await world.count(PrContentTarget, PrContentTarget.content_id == content_id) == 0  # 15
    assert await world.count(PrTask, PrTask.content_id == content_id) == 0  # 16
    assert (
        await world.count(PrTaskAssignment, PrTaskAssignment.task_id == aggregate.task_id) == 0
    )  # 17
    assert await world.count(PrAiReviewRun, PrAiReviewRun.content_id == content_id) == 0  # 18
    assert await world.count(PrAiReview, PrAiReview.content_id == content_id) == 0  # 19
    assert (
        await world.count(
            PrAiReviewRunPolicyPack, PrAiReviewRunPolicyPack.run_id == aggregate.run_id
        )
        == 0
    )  # 20
    assert await world.count(PrApprovalEvent, PrApprovalEvent.content_id == content_id) == 0  # 21
    assert (
        await world.count(PrProductionSubmission, PrProductionSubmission.content_id == content_id)
        == 0
    )  # 22
    # Step 1F.2.3e. Review material goes with the content it supported: a brief
    # left behind would be a row nothing can reach, pointing at a client
    # document, with no record of what it was for.
    assert await world.count(PrContentResource, PrContentResource.content_id == content_id) == 0


async def test_23_publication_records_are_refused_rather_than_destroyed(world: World) -> None:
    """Requirement 23, answered the other way round - and deliberately.

    This schema has no pre-publish publication row: ``register_publication``
    requires ``READY_TO_PUBLISH`` and moves the item to ``PUBLISHED`` in the same
    transaction, so a publication *is* the record that something went out. Rather
    than carry a branch that deletes those - unreachable if the stage rule holds,
    catastrophic if it ever does not - the service refuses when one exists.

    Asserted with the stage tampered back to ``READY_TO_PUBLISH``, which is the
    only way to reach the check, and is exactly the corruption it defends
    against.
    """
    content_id = await at(world, PrWorkflowStage.READY_TO_PUBLISH)
    world.session.add(
        PrPublication(
            code="PUB-2026-000001",
            content_id=content_id,
            channel_id=world.channel_id,
            published_at=NOW,
        )
    )
    await world.session.flush()

    with pytest.raises(PrPublishedContentError) as refusal:
        await world.delete(world.lead, content_id)
    assert refusal.value.details["reason"] == "has_publications"
    assert await world.row(content_id) is not None
    assert await world.count(PrPublication, PrPublication.content_id == content_id) == 1


async def test_23a_a_reporting_issue_is_detached_rather_than_deleted(world: World) -> None:
    """An issue belongs to the weekly report, not to the content it mentions.

    ``pr_issues.content_id`` is nullable for that reason, so deleting a draft
    takes the *link* and leaves the line somebody wrote in a retro. Deleting the
    issue would be removing a paragraph from one document as a side effect of
    tidying another.
    """
    aggregate = await build_aggregate(world)
    await world.delete(world.lead, aggregate.content_id)

    issue = await world.session.get(PrIssue, aggregate.issue_id)
    assert issue is not None
    assert issue.content_id is None
    assert issue.title == "Sai thông tin sản phẩm"


async def test_24_to_28_shared_data_survives(world: World) -> None:
    """Requirements 24-28. Master data is not part of anybody's aggregate."""
    aggregate = await build_aggregate(world)
    await world.delete(world.lead, aggregate.content_id)

    assert await world.count(PrChannel) == 1  # 24
    assert await world.count(PrPlatform) == 1  # 25
    assert await world.count(PrBrand) == 1  # 26
    assert await world.session.get(PrPlatformPolicyPack, aggregate.pack_id) is not None  # 27
    assert await world.count(User) == 4  # 28
    # Users who approved and produced are untouched, by name.
    for person in (world.lead, world.member):
        assert await world.session.get(User, person.id) is not None


async def test_29_an_unrelated_aggregate_is_untouched(world: World) -> None:
    """Requirement 29. The delete is scoped by id, and this proves it in rows.

    Two full aggregates, one deleted. The predicate on every statement is this
    content's id or a subquery over its own children; a mistake there - a missing
    ``WHERE``, a subquery over the wrong parent - empties the other one too, and
    nothing else in the suite would notice.
    """
    keep = await build_aggregate(world)
    drop = await build_aggregate(world)
    await world.delete(world.lead, drop.content_id)

    assert await world.row(keep.content_id) is not None
    assert await world.count(PrContentVersion, PrContentVersion.content_id == keep.content_id) > 0
    assert await world.count(PrApprovalEvent, PrApprovalEvent.content_id == keep.content_id) == 3
    assert (
        await world.count(
            PrProductionSubmission, PrProductionSubmission.content_id == keep.content_id
        )
        == 2
    )
    assert await world.count(PrAiReviewRun, PrAiReviewRun.content_id == keep.content_id) == 1
    assert await world.count(PrTask, PrTask.content_id == keep.content_id) == 1


async def test_29a_the_delete_plan_covers_every_foreign_key_into_the_aggregate(
    world: World,
) -> None:
    """The test that keeps this correct as the schema grows.

    Walks ``Base.metadata``'s own foreign keys from ``pr_content_items``
    transitively, and asserts that after a delete **no row anywhere** still
    points at any id that belonged to the aggregate. It knows nothing about the
    service's list, so a table added next year that references content, a
    version, a task, a run or a submission fails here until somebody decides what
    deletion should do about it.

    ``pr_issues`` is expected to survive with a null link, which the walk covers
    for free: a detached row points at nothing.
    """
    aggregate = await build_aggregate(world)
    parents: dict[str, set[uuid.UUID]] = {
        "pr_content_items": {aggregate.content_id},
        "pr_content_versions": set(aggregate.version_ids),
        "pr_tasks": {aggregate.task_id},
        "pr_ai_review_runs": {aggregate.run_id},
        "pr_ai_reviews": {aggregate.review_id},
        "pr_production_submissions": set(aggregate.submission_ids),
    }
    await world.delete(world.lead, aggregate.content_id)

    checked = 0
    for table in Base.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            referred = constraint.referred_table.name
            if referred not in parents or not parents[referred]:
                continue
            column = next(iter(constraint.columns))
            survivors = (
                await world.session.execute(
                    select(func.count()).select_from(table).where(column.in_(parents[referred]))
                )
            ).scalar()
            assert survivors == 0, f"{table.name}.{column.name} still points at a deleted row"
            checked += 1
    # A sanity floor: if the walk found nothing to check, it proved nothing.
    assert checked >= 10, checked


async def test_29b_content_that_produced_recorded_work_cannot_be_deleted(
    world: World,
) -> None:
    """M3. A ledger record is not a child row to be tidied away.

    Work projected out of a content item links back by ``source_key`` **text**,
    not by a foreign key - so nothing in the database would stop this delete, and
    the walk in 29a would never see it. What it would leave is a counted
    contribution, possibly already inside somebody's reported month, whose source
    no longer exists.

    So the refusal is the same shape as the publication one: **refuse, do not
    reverse**. Reversal is a decision an operator takes at source, where the
    projector takes the work back out with its own audit trail. Hiding a KPI
    change inside a deletion whose audit row does not even mention work is the
    failure this prevents.
    """
    from meobot.db.models.pr_work import PrWorkItem
    from meobot.domain.pr.work import PrWorkSourceType, PrWorkStatus

    content_id = await at(world, PrWorkflowStage.SCRIPTING)
    work_type = await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code="SHORT_SCRIPT",
        name="Kịch bản ngắn",
        category=PrWorkCategory.CONTENT,
    )
    # Written directly rather than projected: this test is about the *refusal*,
    # and walking the whole content workflow to APPROVED to get a real
    # projection would make it a test of the projector instead.
    world.session.add(
        PrWorkItem(
            code="WRK-2026-000900",
            title="Viết nội dung",
            work_type_id=work_type.id,
            source_type=PrWorkSourceType.CONTENT,
            source_key=f"content:{content_id}:SCRIPT_APPROVED",
            status=PrWorkStatus.APPROVED,
            created_by_user_id=world.owner.id,
        )
    )
    await world.session.flush()

    from meobot.domain.pr.errors import PrContentHasRecordedWorkError

    with pytest.raises(PrContentHasRecordedWorkError) as refused:
        await world.delete(world.lead, content_id)
    assert refused.value.details["reason"] == "has_recorded_work"

    # And it really is still there - a refusal that had already deleted half the
    # aggregate would be worse than one that let the delete through.
    assert await world.count(PrContentItem, PrContentItem.id == content_id) == 1


async def _recorded_work_at_handoff(world: World) -> uuid.UUID:
    """``APPROVED``, waiting for a producer, with one recorded result.

    The period-container patch made a content milestone contribute a **result**
    to the writer's monthly stream rather than a work item of its own, so this
    is the row a real projection leaves today - written directly, because the
    refusal and not the projector is under test.
    """
    from decimal import Decimal

    from meobot.core.time import utcnow
    from meobot.db.models.pr_work_result import PrWorkResult
    from meobot.domain.pr.work import PrWorkCountStatus
    from meobot.domain.pr.work_results import PrWorkResultSource

    content_id = await at(world, PrWorkflowStage.APPROVED)
    work_type = await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code="SHORT_SCRIPT",
        name="Kịch bản ngắn",
        category=PrWorkCategory.CONTENT,
    )
    period = await world.services.work_periods.period_for_or_create(
        utcnow(), actor=world.actor(world.owner), request_id=world.request_id
    )
    stream = await world.services.work_results.ensure_container(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=work_type.id,
        subject_user_id=world.member.id,
        period=period,
    )
    world.session.add(
        PrWorkResult(
            work_item_id=stream.id,
            user_id=world.member.id,
            quantity=Decimal("1"),
            source_type=PrWorkResultSource.CONTENT,
            source_key=f"content:{content_id}:CONTENT_CREATION",
            status=PrWorkCountStatus.COUNTED,
            reported_by_user_id=world.member.id,
            reported_at=utcnow(),
            counted_at=utcnow(),
            counted_by_user_id=world.lead.id,
        )
    )
    await world.session.flush()
    return content_id


async def test_29c_a_recorded_result_blocks_the_delete_and_nothing_moves(
    world: World,
) -> None:
    """The delete guard reads results as well as work items, and refuses before
    it writes anything: stage, handoff, work and audit trail are as they were."""
    from meobot.db.models.audit_log import AuditLog
    from meobot.db.models.pr_work_result import PrWorkResult
    from meobot.domain.pr.errors import PrContentHasRecordedWorkError
    from meobot.domain.pr.models import PrProductionHandoff
    from meobot.domain.pr.production import handoff_state

    content_id = await _recorded_work_at_handoff(world)
    before = await world.row(content_id)
    assert before is not None
    assert handoff_state(before.workflow_stage, before.producer_user_id) is (
        PrProductionHandoff.WAITING_FOR_PRODUCER
    )
    audit_rows = await world.count(AuditLog)

    with pytest.raises(PrContentHasRecordedWorkError) as refused:
        await world.delete(world.lead, content_id)
    assert refused.value.code == "pr_content_delete_blocked_recorded_work"
    assert refused.value.details["reason"] == "has_recorded_work"

    after = await world.row(content_id)
    assert after is not None, "the entity still exists"
    assert after.workflow_stage is PrWorkflowStage.APPROVED, "status unchanged"
    assert after.archived_at is None and after.producer_user_id is None
    assert handoff_state(after.workflow_stage, after.producer_user_id) is (
        PrProductionHandoff.WAITING_FOR_PRODUCER
    ), "production handoff unchanged"
    assert await world.count(PrWorkResult) == 1, "recorded work unchanged"
    assert await world.count(AuditLog) == audit_rows, "no deletion was audited"


async def test_29d_the_action_list_survives_recorded_work_and_keeps_production(
    world: World,
) -> None:
    """**The bug.** ``may_delete`` let the recorded-work refusal escape as an
    exception, so the *available actions* read failed for any piece with
    recorded work - and the panel that draws every next step, including *Nhận
    sản xuất*, was replaced by an error box with a retry button. The read must
    answer "no delete" and go on listing what is valid."""
    content_id = await _recorded_work_at_handoff(world)

    offered = await world.actions(world.lead, content_id)
    assert "DELETE_CONTENT" not in offered
    assert "CLAIM_PRODUCTION" in offered, "the production handoff is still actionable"

    # And the accept itself still goes through afterwards.
    world.act_as(world.lead)
    refused = world.client.request("DELETE", f"/api/pr/contents/{content_id}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "pr_content_delete_blocked_recorded_work"
    listed = world.client.get(f"/api/pr/contents/{content_id}/available-actions")
    assert listed.status_code == 200, listed.text
    kinds = {one["action"] for one in listed.json()["available_actions"]}
    assert "CLAIM_PRODUCTION" in kinds and "DELETE_CONTENT" not in kinds
    claimed = world.client.post(f"/api/pr/contents/{content_id}/producer/claim", json={})
    assert claimed.status_code == 200, claimed.text
    after = await world.row(content_id)
    assert after is not None and after.producer_user_id == world.lead.id


# ===========================================================================
# 30-35: TRANSACTION AND RACES
# ===========================================================================


async def test_30_a_failing_child_delete_rolls_the_whole_thing_back(world: World) -> None:
    """Requirement 30. All of it, or none of it.

    The failure is induced after several children are already gone, which is the
    only interesting case: the audit row is written, some deletes have run, and
    then something raises. Nothing may survive that halfway state.

    Note what this test does **not** prove offline: that the database itself
    would refuse an incomplete plan. Every foreign key here is ``RESTRICT`` - the
    backstop this service's docstring relies on, and asserted structurally by
    ``test_pr_core_schema_parity`` - but SQLite does not enforce foreign keys
    unless ``PRAGMA foreign_keys`` is on, and this suite runs without it. So the
    rollback is proven here and the constraint is proven there, and the pair of
    them is what makes an incomplete plan safe on PostgreSQL.
    """
    aggregate = await build_aggregate(world)
    content_id = aggregate.content_id

    # A subclass rather than a monkeypatch: patching the attribute back
    # afterwards restores a plain function where a ``staticmethod`` was, and the
    # leak shows up as an unrelated failure three tests later.
    class _FailsHalfway(PrContentLifecycleService):
        async def _delete_aggregate(self, content):  # type: ignore[no-untyped-def,override]
            await super()._delete_aggregate(content)
            raise RuntimeError("a child delete failed")

    broken = _FailsHalfway(
        world.session,
        world.services.audit,
        world.services.workflow,
        world.services.capabilities,
    )
    # A savepoint stands in for the caller's transaction: the service commits
    # nothing, so rolling back the unit of work is the whole recovery, and this
    # is how to do that in a test whose fixture data lives in the outer one.
    with pytest.raises(RuntimeError):
        async with world.session.begin_nested():
            await broken.delete_content(
                actor=world.actor(world.lead),
                request_id=world.request_id,
                content_id=content_id,
            )

    # Nothing half-deleted: the item and its children are exactly as they were.
    assert await world.row(content_id) is not None
    assert await world.count(PrContentVersion, PrContentVersion.content_id == content_id) > 0
    assert await world.count(PrApprovalEvent, PrApprovalEvent.content_id == content_id) == 3
    assert await world.count(AuditLog, AuditLog.action == "pr.content.deleted_permanently") == 0, (
        "the audit row rolls back with the deletes it describes"
    )


async def test_31_a_second_delete_finds_nothing(world: World) -> None:
    """Requirement 31. The loser of a concurrent delete gets not-found.

    Which is the truth rather than a special case: after the first transaction
    commits, the id does not exist, and "no such content" is what any caller
    naming it should hear. No half-delete, no integrity error, nothing raw.
    """
    content_id = await at(world, PrWorkflowStage.SCRIPTING)
    await world.delete(world.member, content_id)
    with pytest.raises(PrNotFoundError):
        await world.delete(world.member, content_id)

    world.act_as(world.member)
    assert world.client.request("DELETE", f"/api/pr/contents/{content_id}").status_code == 404


async def test_32_an_ai_worker_holding_a_deleted_run_exits_cleanly(world: World) -> None:
    """Requirement 32. The race with the review worker.

    A worker claims a run, the content is deleted while it is thinking, and the
    worker then tries to settle what it was holding. Before Step 1F.2.3a's guard
    that was an ``UPDATE`` matching no rows - ``StaleDataError``, an unhandled
    exception in a Celery task, and a retry storm against content that no longer
    exists.

    Now every settling path checks first and exits: nothing is written, and in
    particular **no run row is recreated**, which is the orphan this test is
    named for.
    """
    content_id = await at(world, PrWorkflowStage.AI_REVIEW)
    run = (
        (
            await world.session.execute(
                select(PrAiReviewRun).where(PrAiReviewRun.content_id == content_id)
            )
        )
        .scalars()
        .one()
    )
    run.status = PrAiReviewRunStatus.RUNNING
    await world.session.flush()

    await world.delete(world.lead, content_id)

    runs = PrAiReviewRunService(world.session)
    # The worker's three ways of settling, all after the row has gone.
    await runs.mark_failed(run, error_code="content_missing")
    await runs.mark_superseded(run, outcome=None, reason="content_missing")
    assert await runs.requeue(run, error_code="provider_timeout") is False
    await world.session.flush()

    assert await world.count(PrAiReviewRun) == 0
    assert await world.count(PrAiReview) == 0
    assert await world.row(content_id) is None


async def test_33_and_34_the_row_lock_serialises_writers(world: World) -> None:
    """Requirements 33 and 34. Production submit and approval, after a delete.

    Both commands begin by locking the content row through
    ``PrContentWorkflowService.lock``, which is the same row this deletion took -
    so on PostgreSQL one waits for the other, and whichever runs second finds the
    content gone. The assertion here is the *outcome* of losing that race: a
    checked not-found, and no orphan row written against a content id that is not
    there.
    """
    for stage, action in (
        (PrWorkflowStage.PRODUCTION, "submit"),
        (PrWorkflowStage.INTERNAL_REVIEW, "approve"),
    ):
        content_id = await at(world, stage)
        if stage is PrWorkflowStage.PRODUCTION:
            await world.services.production.assign_producer(
                actor=world.actor(world.lead),
                request_id=world.request_id,
                content_id=content_id,
                producer_user_id=world.member.id,
            )
        await world.delete(world.lead, content_id)

        with pytest.raises(PrNotFoundError):
            if action == "submit":
                await submit(world, content_id)
            else:
                await decide(world, content_id, PrApprovalStage.INTERNAL_REVIEW)

        assert (
            await world.count(
                PrProductionSubmission, PrProductionSubmission.content_id == content_id
            )
            == 0
        ), action
        assert await world.count(PrApprovalEvent, PrApprovalEvent.content_id == content_id) == 0, (
            action
        )


async def test_35_no_raw_database_error_reaches_the_api(world: World) -> None:
    """Requirement 35. Every refusal over HTTP is a checked business error.

    Three shapes - not yours, published, gone - and none of them leaks an
    ``IntegrityError``, a constraint name or SQL. The envelope carries a stable
    code and a ``details`` mapping, which is what the clients branch on.
    """
    mine = await at(world, PrWorkflowStage.SCRIPTING)
    published = await at(world, PrWorkflowStage.PUBLISHED)
    missing = uuid.uuid4()

    world.act_as(world.other)
    cases = [
        (f"/api/pr/contents/{mine}", 403, "pr_forbidden"),
        (f"/api/pr/contents/{published}", 403, "pr_forbidden"),
        (f"/api/pr/contents/{missing}", 404, "pr_not_found"),
    ]
    world.act_as(world.lead)
    cases[1] = (f"/api/pr/contents/{published}", 409, "pr_published_content")
    for path, expected_status, expected_code in cases:
        world.act_as(world.other if expected_status == 403 else world.lead)
        response = world.client.request("DELETE", path)
        assert response.status_code == expected_status, path
        body = response.json()["error"]
        assert body["code"] == expected_code
        for leak in ("IntegrityError", "ForeignKeyViolation", "SELECT", "psycopg", "Traceback"):
            assert leak not in body["message"], body


# ===========================================================================
# 36-39: THE SURVIVING AUDIT
# ===========================================================================


async def test_36_to_38_one_audit_row_survives_the_aggregate(world: World) -> None:
    """Requirements 36-38. What is left of a deleted piece.

    ``audit_logs.entity_id`` is a ``String`` with no foreign key, so the row
    outlives the content it names - which is the whole reason the trace is
    possible. It carries who, what, and where in its life the piece was, and
    deliberately **not** the script: the operation exists to remove that text, and
    copying it into a table with no access control of its own would be keeping it
    somewhere worse.
    """
    aggregate = await build_aggregate(world)
    content = await world.row(aggregate.content_id)
    assert content is not None
    code, script = content.code, "Nội dung."

    await world.delete(world.lead, aggregate.content_id, reason="khách hủy dự án")

    row = (
        (
            await world.session.execute(
                select(AuditLog).where(AuditLog.action == "pr.content.deleted_permanently")
            )
        )
        .scalars()
        .one()
    )
    assert row.entity_id == str(aggregate.content_id)  # 37: the id, as text
    assert row.actor_user_id == world.lead.id
    assert row.after_data is not None
    assert row.after_data["content_code"] == code
    assert row.after_data["stage_at_delete"] == PrWorkflowStage.INTERNAL_REVIEW.value
    assert row.after_data["reason"] == "khách hủy dự án"
    assert row.after_data["deleted_at"]

    # 38: no payload. The title is there because a person reading the trail needs
    # to know *which* piece; the script, the findings and the comments are not.
    payload = str(row.after_data)
    assert script not in payload
    assert "script_text" not in payload
    assert set(row.after_data) == {
        "content_id",
        "content_code",
        "title",
        "stage_at_delete",
        "reason",
        "deleted_at",
        "deleted_by_user_id",
    }


async def test_39_the_audit_row_does_not_block_the_deletion(world: World) -> None:
    """Requirement 39. The trace and the deletion coexist.

    Worth its own test because the obvious way to write an audit trail - a
    foreign key to the thing being audited - would make this operation impossible
    to record. This asserts the two facts together: the content is gone *and* the
    row describing its removal is there, in the same transaction.
    """
    content_id = await at(world, PrWorkflowStage.SCRIPTING)
    await world.delete(world.member, content_id)

    assert await world.row(content_id) is None
    surviving = await world.count(
        AuditLog,
        AuditLog.entity_id == str(content_id),  # type: ignore[arg-type]
    )
    assert surviving >= 1
    # And the audit table has no foreign key to content at all, which is what
    # makes that survivable rather than lucky.
    referred = {
        constraint.referred_table.name
        for constraint in inspect(AuditLog).local_table.foreign_key_constraints
    }
    assert "pr_content_items" not in referred
