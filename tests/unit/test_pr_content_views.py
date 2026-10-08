"""Step 1F.2.2 - role-aware content views, and the filters that narrow them.

Numbered 9-25, continuing the requirement numbering the step was specified with.
Section 26 is Step 1F.2.3c1, which made the board's workflow group one more of
these filters: it belongs here because "which rows come back, in what order,
counted how" is the question this file is about, and a group that narrows only
the page it was handed is that question answered wrongly.
Requirements 1-8 are the reviewer-flexibility half and live where the approval
rules already were - ``test_pr_authorization_and_codes.py`` for the service
rules, ``test_pr_web_admin.py`` test 39 for ``/available-actions``.

Everything here runs over ``TestClient`` against real SQL, because the questions
are about SQL: whether a multi-target item matches its platform once or twice,
whether a count agrees with the list it captions, whether a day in Ho Chi Minh
City is the same window as a day in UTC. A mocked query service would answer all
three by fiat.

The one thing these tests do **not** assert is that a scope hides anything from
anybody. It does not: the read permission is the access rule and it is unchanged,
so every actor below can ask for ``ALL`` and get it. A scope decides what is on
screen first, which is why "default scope" tests sit next to filter tests rather
than next to authorization tests.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import get_current_web_actor, get_session
from meobot.api.main import create_app
from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_channel_service import CreateChannelCommand, PrChannelService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_content_query import ContentQuery, day_bounds
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_query_service import PrQueryService
from meobot.application.pr_services import build_pr_services
from meobot.application.pr_task_service import CreateTaskCommand
from meobot.core.config import Settings
from meobot.core.time import ensure_utc
from meobot.db.models.pr import PrBrand, PrChannel, PrContentItem, PrPlatform
from meobot.db.models.pr_content_comment import PrContentComment
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.content_views import (
    ARCHIVE_LANES,
    ARCHIVE_STAGES,
    LANE_PRODUCTION_STATES,
    LANE_STAGES,
    PrContentBoardView,
    PrContentDateField,
    PrContentGroup,
    PrContentLane,
    PrContentViewScope,
    board_stages,
    default_scope,
    gate_stages_for,
    group_of_lane,
    lane_stage,
    lanes_in_group,
    stages_in_group,
)
from meobot.domain.pr.models import (
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrContentType,
    PrDistributionMode,
    PrPriority,
    PrProductionHandoff,
    PrTaskAssignmentRole,
    PrTaskStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import RETIRED_STAGES
from tests.unit.streams import tag_pr

#: The zone every date assertion below is expressed in - the configured default,
#: and the one that makes the UTC-versus-local question interesting.
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")

#: The fixture's "today". Every ``created_at`` is set explicitly relative to it,
#: because a row left with the database's ``CURRENT_TIMESTAMP`` would make a date
#: assertion depend on when the suite ran.
TODAY = date(2026, 8, 10)

#: 01:00 on 2026-08-10 in Ho Chi Minh City, which is **2026-08-09** in UTC. An
#: item created at this instant belongs to "Hôm nay" for the team and to
#: yesterday for a naive UTC comparison, which is the whole of requirement 16's
#: interesting case.
EARLY_TODAY_LOCAL = datetime(2026, 8, 9, 18, 0, tzinfo=UTC)


@dataclass(slots=True)
class ViewWorld:
    """Two brands, two platforms, three channels, three people, six items.

    Small enough to reason about and wide enough that a filter can be wrong in a
    visible way: each filter below has at least one row it must exclude, which is
    the half of a filter test that actually fails when the ``WHERE`` clause is
    missing.
    """

    session: AsyncSession
    client: TestClient
    settings: Settings
    #: Holds ``PR_TEAM_LEAD_REVIEW``. A reviewer.
    lead: User
    #: Holds ``PR_HEAD_REVIEW``. Also a reviewer.
    head: User
    #: Holds no review grant. A content writer.
    writer: User
    tiktok: PrPlatform
    facebook: PrPlatform
    #: Apexmed on TikTok. The channel most items target.
    tiktok_a: PrChannel
    #: Apexmed on Facebook.
    facebook_a: PrChannel
    #: A second Apexmed TikTok, so one item can hold **two** targets on one
    #: platform - the state a ``JOIN`` implementation would return twice.
    tiktok_c: PrChannel
    #: A second brand's TikTok, which the writer is assigned to - the ``TEAM``
    #: relationship, and the one channel nothing else uses.
    tiktok_b: PrChannel
    brand_a: uuid.UUID
    brand_b: uuid.UUID
    #: Owned by the writer, at ``IDEA``, on the Apexmed TikTok. Yesterday.
    writers_item: uuid.UUID
    #: Owned by the lead, at ``IDEA``, on Apexmed Facebook. Today, early.
    leads_item: uuid.UUID
    #: Owned by the head, on three channels - two of them TikTok. Multi-target,
    #: and the row every "matches once" assertion is about. Today.
    multi_item: uuid.UUID
    #: Owned by the head, at ``TEAM_LEAD_REVIEW``. The lead's queue. Today.
    gated_item: uuid.UUID
    #: Owned by the head, on the second brand's TikTok - the writer's team. Today.
    team_item: uuid.UUID
    #: Owned by the head, on Apexmed Facebook, a month ago. The row every "recent"
    #: date filter has to leave out.
    old_item: uuid.UUID
    #: A task on ``leads_item``, assigned to the writer. Task responsibility.
    writers_task: uuid.UUID

    def act_as(self, user: User) -> None:
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
            user_id=user.id, full_name=user.full_name, role=user.role, active=user.active
        )

    def actor(self, user: User) -> Actor:
        return Actor(user_id=user.id, full_name=user.full_name, role=user.role)

    def board(self, **params: object) -> dict[str, object]:
        """``GET /contents/board`` with query params, as the panel calls it."""
        response = self.client.get("/api/pr/contents/board", params=params)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def queries(self, *, timezone: ZoneInfo = SAIGON) -> PrQueryService:
        """A query service with the business timezone, for service-level asks."""
        audit = AuditService(self.session)
        return PrQueryService(
            self.session, PrCapabilityService(self.session, audit), timezone=timezone
        )


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[ViewWorld]:
    lead = User(full_name="Le Trưởng Nhóm", role=Role.TEAM_LEAD)
    head = User(full_name="Ha Trưởng Phòng", role=Role.ADMIN)
    writer = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
    brand_a = PrBrand(code="BRND-A", name="Apexmed")
    brand_b = PrBrand(code="BRND-B", name="Brand Hai")
    tiktok = PrPlatform(code="TIKTOK", name="TikTok")
    facebook = PrPlatform(code="FACEBOOK", name="Facebook")
    session.add_all([lead, head, writer, brand_a, brand_b, tiktok, facebook])
    await session.flush()
    await tag_pr(session, [lead, head, writer])  # untagged sees no stream

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
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

    channels = PrChannelService(session, audit, capabilities, PrCodeService(session, settings))

    async def channel(name: str, platform: PrPlatform, brand: PrBrand) -> PrChannel:
        return await channels.create_channel(
            actor=granter,
            request_id=uuid.uuid4(),
            command=CreateChannelCommand(
                name=name,
                platform_id=platform.id,
                brand_id=brand.id,
                category=PrChannelCategory.SCALE,
            ),
        )

    tiktok_a = await channel("Apexmed TikTok", tiktok, brand_a)
    facebook_a = await channel("Apexmed Facebook", facebook, brand_a)
    tiktok_c = await channel("Apexmed TikTok Phụ", tiktok, brand_a)
    tiktok_b = await channel("Brand Hai TikTok", tiktok, brand_b)

    services = build_pr_services(session, settings)

    async def content(
        title: str, owner: User, *targets: PrChannel, brand: PrBrand = brand_a
    ) -> uuid.UUID:
        snapshot = await services.content.create_content(
            actor=granter,
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title=title,
                brand_id=brand.id,
                owner_user_id=owner.id,
                script_text="Nội dung.",
                # ``ORGANIC`` because TikTok and Facebook are policy-grounded
                # platforms and Step 1F.1 refuses ``UNSPECIFIED`` on them. Real
                # platform codes are used deliberately - a platform filter tested
                # against invented codes would not prove it reaches
                # ``pr_platforms`` at all.
                targets=tuple(
                    ContentTargetSpec(
                        channel_id=row.id, distribution_mode=PrDistributionMode.ORGANIC
                    )
                    for row in targets
                ),
            ),
        )
        return snapshot.content.id

    writers_item = await content("Bài của Nguyễn A", writer, tiktok_a)
    leads_item = await content("Bài của trưởng nhóm", lead, facebook_a)
    # Two TikTok targets and one Facebook: on both platforms, and matched twice
    # over by anything that joins rather than asking ``EXISTS``.
    multi_item = await content("Bài đa kênh", head, tiktok_a, tiktok_c, facebook_a)
    gated_item = await content("Bài chờ duyệt", head, tiktok_a)
    team_item = await content("Bài của team khác", head, tiktok_b, brand=brand_b)
    old_item = await content("Bài tháng trước", head, facebook_a)

    # ``created_at`` is set explicitly rather than left to CURRENT_TIMESTAMP, so
    # the date assertions are about the filter and not about the clock.
    at: dict[uuid.UUID, datetime] = {
        writers_item: datetime(2026, 8, 9, 3, 0, tzinfo=UTC),
        leads_item: EARLY_TODAY_LOCAL,
        multi_item: datetime(2026, 8, 10, 3, 0, tzinfo=UTC),
        gated_item: datetime(2026, 8, 10, 4, 0, tzinfo=UTC),
        team_item: datetime(2026, 8, 10, 5, 0, tzinfo=UTC),
        old_item: datetime(2026, 7, 10, 3, 0, tzinfo=UTC),
    }
    for content_id, moment in at.items():
        row = await session.get(PrContentItem, content_id)
        assert row is not None
        row.created_at = moment
        # A planned date a week after creation, so the two dimensions disagree and
        # ``date_field`` has something to choose between.
        row.planned_publish_at = moment + timedelta(days=7)
    await session.flush()

    # The gated item, put at the human gate. Forced rather than walked: this suite
    # is about reading, and ``test_pr_web_admin`` already proves the walk.
    gated = await session.get(PrContentItem, gated_item)
    assert gated is not None
    gated.workflow_stage = PrWorkflowStage.TEAM_LEAD_REVIEW
    await session.flush()

    # Task responsibility: the writer is on a task belonging to the lead's item.
    task = await services.tasks.create_task(
        actor=granter,
        request_id=uuid.uuid4(),
        command=CreateTaskCommand(task_type="SCRIPT", title="Viết kịch bản", content_id=leads_item),
    )
    await services.tasks.assign_user(
        actor=granter,
        request_id=uuid.uuid4(),
        task_id=task.id,
        user_id=writer.id,
        assignment_role=PrTaskAssignmentRole.OWNER,
    )

    # Team membership: the writer answers for the second brand's TikTok. Open
    # ended, so it is in force whatever day the suite decides "today" is.
    await channels.assign_user(
        actor=granter,
        request_id=uuid.uuid4(),
        channel_id=tiktok_b.id,
        user_id=writer.id,
        assignment_role=PrChannelAssignmentRole.CHANNEL_OWNER,
        effective_from=date(2026, 1, 1),
    )

    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        built = ViewWorld(
            session=session,
            client=client,
            settings=settings,
            lead=lead,
            head=head,
            writer=writer,
            tiktok=tiktok,
            facebook=facebook,
            tiktok_a=tiktok_a,
            facebook_a=facebook_a,
            tiktok_c=tiktok_c,
            tiktok_b=tiktok_b,
            brand_a=brand_a.id,
            brand_b=brand_b.id,
            writers_item=writers_item,
            leads_item=leads_item,
            multi_item=multi_item,
            gated_item=gated_item,
            team_item=team_item,
            old_item=old_item,
            writers_task=task.id,
        )
        built.act_as(head)
        yield built
    app.dependency_overrides.clear()


def _titles(body: dict[str, object]) -> set[str]:
    return {row["title"] for row in body["items"]}  # type: ignore[index]


async def _put_at(world: ViewWorld, stage: PrWorkflowStage, *content_ids: uuid.UUID) -> None:
    """Stand these items at ``stage``, without walking them there.

    The gate tests below are about *reading* a queue, and what put an item at a
    gate does not change what the queue should contain. Walking each one through
    briefing, scripting, AI review and a lead approval would make the fixture the
    subject of the test - and ``test_pr_web_admin`` already proves the walk.
    """
    for content_id in content_ids:
        row = await world.session.get(PrContentItem, content_id)
        assert row is not None
        row.workflow_stage = stage
    await world.session.flush()


async def _set_producer(world: ViewWorld, content_id: uuid.UUID, user: User | None) -> None:
    """Hand an item's production to somebody, for a test about *reading* queues.

    Set directly for the same reason ``_put_at`` forces a stage: what put the
    producer there does not change what the queue should contain, and
    ``test_pr_production_lifecycle`` already proves the assignment path.
    """
    row = await world.session.get(PrContentItem, content_id)
    assert row is not None
    row.producer_user_id = user.id if user else None
    await world.session.flush()


async def _grant(world: ViewWorld, user: User, capability: PrCapability) -> None:
    """One more review grant, through the service that owns the grant table."""
    await PrCapabilityService(world.session, AuditService(world.session)).grant(
        actor=Actor(user_id=world.head.id, full_name=world.head.full_name, role=Role.OWNER),
        request_id=uuid.uuid4(),
        user_id=user.id,
        capability=capability,
    )


def _count_for(body: dict[str, object], stage: PrWorkflowStage) -> int:
    for row in body["stage_counts"]:  # type: ignore[union-attr]
        if row["stage"] == stage.value:
            return int(row["count"])
    raise AssertionError(f"{stage.value} missing from stage_counts")


def _handoff_count(body: dict[str, object], state: PrProductionHandoff) -> int:
    """Step 1F.2.3c. The same filtered set, counted by derived production state."""
    for row in body["production_state_counts"]:  # type: ignore[union-attr]
        if row["production_state"] == state.value:
            return int(row["count"])
    raise AssertionError(f"{state.value} missing from production_state_counts")


# ===========================================================================
# 9-10: THE DEFAULT SCOPE
# ===========================================================================


@pytest.mark.asyncio
async def test_09_10_everybody_lands_on_all(world: ViewWorld) -> None:
    """Step 1F.2.3f.6a. The default is *Tất cả* - for a writer and for both reviewers.

    Requirements 9 and 10 used to land a writer on ``MY_CONTENT`` and a
    reviewer on ``MY_ACTIONS``, decided from the grants held. Since the board
    opened on one reporting month, *Tất cả* is this month's board rather than
    every row ever, and that is the screen everybody orients by; the queue is
    one click away. Decided on the server and echoed, so the panel highlights
    the tab the server chose rather than one it guessed.
    """
    for person in (world.writer, world.lead, world.head):
        world.act_as(person)
        assert world.board()["scope"] == PrContentViewScope.ALL.value, person.full_name


def test_10a_the_default_is_all_whatever_is_held() -> None:
    """The same decision, at the level it is actually made.

    Still a function of the capabilities held, so a future grant-dependent
    default needs no caller to change; today every input gives ``ALL``.
    """
    assert default_scope(frozenset()) is PrContentViewScope.ALL
    assert default_scope(frozenset({PrCapability.PR_CONTENT_EDIT})) is PrContentViewScope.ALL
    for capability in (
        PrCapability.PR_TEAM_LEAD_REVIEW,
        PrCapability.PR_HEAD_REVIEW,
        PrCapability.PR_INTERNAL_REVIEW,
    ):
        assert default_scope(frozenset({capability})) is PrContentViewScope.ALL, capability
    assert default_scope(frozenset(PrCapability)) is PrContentViewScope.ALL


def test_10b_gates_held_is_empty_without_the_grant() -> None:
    """``MY_ACTIONS`` cannot widen through a role.

    ``gate_stages_for`` is what puts a gate in somebody's queue, and it reads the
    grant-backed capability. An ``OWNER`` holding every ``Permission`` and no
    grant gets nothing, which is the invariant the dashboard already relied on
    and that the scope now shares.
    """
    assert gate_stages_for(frozenset()) == ()
    assert gate_stages_for(frozenset({PrCapability.PR_HEAD_REVIEW})) == (
        PrWorkflowStage.HEAD_REVIEW,
    )
    assert gate_stages_for(
        frozenset({PrCapability.PR_TEAM_LEAD_REVIEW, PrCapability.PR_HEAD_REVIEW})
    ) == (PrWorkflowStage.TEAM_LEAD_REVIEW, PrWorkflowStage.HEAD_REVIEW)


# ===========================================================================
# 11-14: WHAT EACH SCOPE CONTAINS
# ===========================================================================


@pytest.mark.asyncio
async def test_11_my_actions_holds_only_what_the_actor_can_act_on(world: ViewWorld) -> None:
    """Requirement 11. Not ``stage IN (...)`` with a friendlier name.

    The lead's queue contains the item at their gate, the draft they own, and
    nothing else - in particular not the head's drafts, which are at the same
    stages as the lead's own and differ only in who may act.
    """
    world.act_as(world.lead)
    body = world.board(scope="MY_ACTIONS")
    assert _titles(body) == {"Bài chờ duyệt", "Bài của trưởng nhóm"}

    # The head holds the *other* gate, so the item at TEAM_LEAD_REVIEW is not
    # theirs to move - even though they own it.
    world.act_as(world.head)
    assert "Bài chờ duyệt" not in _titles(world.board(scope="MY_ACTIONS"))


@pytest.mark.asyncio
async def test_11a_a_task_assignment_puts_an_item_in_the_queue(world: ViewWorld) -> None:
    """The third way into ``MY_ACTIONS``, and the writer's only one here.

    The writer owns ``writers_item`` and is on a task against ``leads_item``;
    both are work with a next action for them. Nothing else is.
    """
    world.act_as(world.writer)
    assert _titles(world.board(scope="MY_ACTIONS")) == {
        "Bài của Nguyễn A",
        "Bài của trưởng nhóm",
    }


@pytest.mark.asyncio
async def test_11b_the_review_queue_is_the_whole_gate_not_the_reviewer_s_share(
    world: ViewWorld,
) -> None:
    """A gate holder's queue is *everything* waiting at their gate.

    Three items stand at ``TEAM_LEAD_REVIEW``. The lead owns none of them, is
    assigned no task on any of them, and holds no channel assignment for their
    channels - the deliberate arrangement, because it is the normal one: a
    reviewer owns almost nothing they review, and a queue intersected with
    ownership would have shown them an empty screen while three decisions waited
    on them.

    What puts all three in the queue is the one thing they have in common, which
    is that each is waiting for a team-lead decision and the lead is authorized
    to make it.
    """
    await _put_at(world, PrWorkflowStage.TEAM_LEAD_REVIEW, world.writers_item, world.multi_item)
    world.act_as(world.lead)
    assert _titles(world.board(scope="MY_ACTIONS")) == {
        "Bài chờ duyệt",  # owned by the head
        "Bài của Nguyễn A",  # owned by the writer
        "Bài đa kênh",  # owned by the head, on channels the lead answers for none of
        "Bài của trưởng nhóm",  # their own draft, the personal branch, still there
    }


@pytest.mark.asyncio
async def test_11c_the_head_queue_follows_the_head_gate_the_same_way(world: ViewWorld) -> None:
    """Requirement 4's other half, so neither gate passes by a coincidence.

    The head owns neither of the two items moved to ``HEAD_REVIEW``, and the
    item at ``TEAM_LEAD_REVIEW`` is one they *do* own - so if ownership were
    leaking into the gate branch, this assertion would fail in both directions
    at once: the two they may decide missing, the one they may not present.

    The three drafts they own at ``IDEA`` are in the queue for the *other*
    reason, unchanged by this patch: they hold ``PR_CONTENT_EDIT`` and the stage
    is editable.
    """
    await _put_at(world, PrWorkflowStage.HEAD_REVIEW, world.writers_item, world.leads_item)
    world.act_as(world.head)
    titles = _titles(world.board(scope="MY_ACTIONS"))
    assert titles == {
        "Bài của Nguyễn A",  # HEAD_REVIEW, owned by the writer
        "Bài của trưởng nhóm",  # HEAD_REVIEW, owned by the lead
        "Bài đa kênh",  # their own editable draft
        "Bài của team khác",  # their own editable draft
        "Bài tháng trước",  # their own editable draft
    }
    # Owned by the head, and still not theirs to decide: it waits at the gate
    # they hold no grant for.
    assert "Bài chờ duyệt" not in titles


@pytest.mark.asyncio
async def test_11d_two_grants_are_two_queues_at_once(world: ViewWorld) -> None:
    """Holding both gates means seeing both, not the more senior one.

    The team of two this module is for has one person entitled at both gates;
    Step 1F.2.2's approval change is what made that workable, and a queue that
    showed them only one of their two backlogs would have handed the problem
    back. ``INTERNAL_REVIEW`` is the same mechanism - it is already in
    ``STAGE_APPROVAL_GATES`` - so a third grant will need no third branch.
    """
    await _grant(world, world.lead, PrCapability.PR_HEAD_REVIEW)
    await _put_at(world, PrWorkflowStage.HEAD_REVIEW, world.writers_item)
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.multi_item)

    world.act_as(world.lead)
    assert _titles(world.board(scope="MY_ACTIONS")) == {
        "Bài chờ duyệt",  # TEAM_LEAD_REVIEW
        "Bài của Nguyễn A",  # HEAD_REVIEW
        "Bài của trưởng nhóm",  # their own editable draft
    }
    # Not the internal gate, which they hold no grant for - two queues, not all
    # of them.
    assert gate_stages_for(
        frozenset({PrCapability.PR_TEAM_LEAD_REVIEW, PrCapability.PR_HEAD_REVIEW})
    ) == (PrWorkflowStage.TEAM_LEAD_REVIEW, PrWorkflowStage.HEAD_REVIEW)

    await _grant(world, world.lead, PrCapability.PR_INTERNAL_REVIEW)
    assert "Bài đa kênh" in _titles(world.board(scope="MY_ACTIONS"))


@pytest.mark.asyncio
async def test_11e_no_grant_is_no_queue_however_much_is_waiting(world: ViewWorld) -> None:
    """The other side of capability-based: the gate does not widen anybody.

    Five of the six items are parked at the two human gates and the writer, who
    holds no review grant, sees exactly the two they are responsible for - the
    one they own and the one they hold a task on. If the gate branch had become
    ``stage IN (...)``, this is the test that would notice.
    """
    await _put_at(
        world, PrWorkflowStage.TEAM_LEAD_REVIEW, world.multi_item, world.team_item, world.old_item
    )
    await _put_at(world, PrWorkflowStage.HEAD_REVIEW, world.leads_item)

    world.act_as(world.writer)
    assert _titles(world.board(scope="MY_ACTIONS")) == {
        "Bài của Nguyễn A",  # owner, at an editable stage
        "Bài của trưởng nhóm",  # their unfinished task, gate or no gate
    }


@pytest.mark.asyncio
async def test_11f_the_internal_review_queue_is_capability_based_too(world: ViewWorld) -> None:
    """Step 1F.2.3's gate, on the mechanism Step 1F.2.2 built.

    ``INTERNAL_REVIEW`` needed no new branch: it was already in
    ``STAGE_APPROVAL_GATES`` and ``APPROVAL_CAPABILITIES``, so the queue follows
    the grant like the other two. Asserted from both sides - the holder sees
    every item waiting there whoever owns them, and the two script-gate holders
    see none of it.
    """
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.multi_item, world.writers_item)
    for reviewer in (world.lead, world.head):
        world.act_as(reviewer)
        titles = _titles(world.board(scope="MY_ACTIONS"))
        assert "Bài đa kênh" not in titles, reviewer.full_name
        assert "Bài của Nguyễn A" not in titles, reviewer.full_name

    await _grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    world.act_as(world.head)
    assert {"Bài đa kênh", "Bài của Nguyễn A"} <= _titles(world.board(scope="MY_ACTIONS"))


@pytest.mark.asyncio
async def test_11g_production_assigned_to_me_is_in_my_queue(world: ViewWorld) -> None:
    """Step 1F.2.3. Somebody was handed an edit and has not filed it yet.

    No capability is consulted for this branch: being the producer *is* the
    assignment, and dropping it out of their queue because a grant lapsed would
    hide work they are still expected to hand in.
    """
    await _put_at(world, PrWorkflowStage.PRODUCTION, world.multi_item)
    await _set_producer(world, world.multi_item, world.writer)

    world.act_as(world.writer)
    assert "Bài đa kênh" in _titles(world.board(scope="MY_ACTIONS"))
    # And not in somebody else's, which is the half that fails if the branch
    # forgot the ``producer_user_id`` comparison.
    world.act_as(world.lead)
    assert "Bài đa kênh" not in _titles(world.board(scope="MY_ACTIONS"))


@pytest.mark.asyncio
async def test_11h_unclaimed_production_follows_the_team_relation(world: ViewWorld) -> None:
    """The one branch that is neither a gate nor an assignment.

    Every ``EMPLOYEE`` may claim any unclaimed edit - that is authorization, and
    it is unchanged. What is narrowed here is the *queue*: an unclaimed item
    appears in somebody's "Cần tôi xử lý" only when they answer for one of its
    channels, because otherwise every member's queue would fill with every other
    team's backlog, which is the problem Step 1F.2.2 existed to solve.

    ``team_item`` targets the channel the writer is assigned to; ``multi_item``
    does not. Both are unclaimed, and only one of them is theirs to be shown.
    """
    await _put_at(world, PrWorkflowStage.PRODUCTION, world.team_item, world.multi_item)

    world.act_as(world.writer)
    titles = _titles(world.board(scope="MY_ACTIONS"))
    assert "Bài của team khác" in titles
    assert "Bài đa kênh" not in titles


@pytest.mark.asyncio
async def test_11i_the_approved_handoff_reaches_the_right_queues(world: ViewWorld) -> None:
    """Step 1F.2.3b. ``APPROVED`` with nobody holding it is somebody's problem.

    Three answers, and the third is the one that keeps the queue usable:

    * the **writer** whose script was just approved sees it - it is their piece,
      and arranging production is the next thing that has to happen to it;
    * **management** sees it, because deciding who produces things is their work
      and somebody has to answer for the pieces nobody has picked up;
    * an **unrelated member** does not. Every ``EMPLOYEE`` may claim any
      unclaimed production, so without this narrowing every approved item would
      land in everybody's "Cần tôi xử lý" - the exact flooding Step 1F.2.2
      existed to stop.
    """
    await _put_at(world, PrWorkflowStage.APPROVED, world.writers_item)

    world.act_as(world.writer)  # owner of ``writers_item``
    assert "Bài của Nguyễn A" in _titles(world.board(scope="MY_ACTIONS"))

    # ``PR_PRODUCTION_ASSIGN`` is role-decided rather than granted - it is
    # ``video.approve``, which every ``TEAM_LEAD`` holds - so the lead has it
    # already and there is nothing to grant.
    world.act_as(world.lead)
    assert "Bài của Nguyễn A" in _titles(world.board(scope="MY_ACTIONS"))

    # And the writer, an ``EMPLOYEE`` with no assign right, sees only what is
    # theirs. ``old_item`` belongs to the head, targets a channel the writer does
    # not answer for, and carries no task of theirs - so it stays out of the way
    # even though they could perfectly well claim it if somebody sent them the
    # link. Being *able* to act and being *shown* it are the two halves Step
    # 1F.2.2 separated, and this is the second one.
    await _put_at(world, PrWorkflowStage.APPROVED, world.old_item)
    world.act_as(world.writer)
    assert "Bài tháng trước" not in _titles(world.board(scope="MY_ACTIONS"))


@pytest.mark.asyncio
async def test_11j_holding_a_production_puts_it_in_my_queue_before_it_starts(
    world: ViewWorld,
) -> None:
    """Step 1F.2.3b. "Sẵn sàng sản xuất" is work, not a waiting room.

    The producer's queue picks the piece up at ``APPROVED`` - the moment they
    take the job - rather than at ``PRODUCTION``, because starting it is the
    thing they are being asked to do.
    """
    await _put_at(world, PrWorkflowStage.APPROVED, world.multi_item)
    await _set_producer(world, world.multi_item, world.writer)

    world.act_as(world.writer)
    assert "Bài đa kênh" in _titles(world.board(scope="MY_ACTIONS"))
    world.act_as(world.head)
    assert "Bài đa kênh" not in _titles(world.board(scope="MY_ACTIONS"))


@pytest.mark.asyncio
async def test_12_my_content_follows_owner_and_task_responsibility(world: ViewWorld) -> None:
    """Requirement 12. The authoritative relationships, and only those.

    ``owner_user_id`` - Step 1A's "who is accountable for this now" - plus an
    unfinished task assignment. Creation is deliberately excluded: every item in
    this fixture was *created by* the head, and a definition built on
    ``created_by_user_id`` would put all six in everybody's "Của tôi".
    """
    world.act_as(world.writer)
    assert _titles(world.board(scope="MY_CONTENT")) == {
        "Bài của Nguyễn A",  # owner
        "Bài của trưởng nhóm",  # task assignee
    }

    world.act_as(world.lead)
    assert _titles(world.board(scope="MY_CONTENT")) == {"Bài của trưởng nhóm"}


@pytest.mark.asyncio
async def test_12a_a_finished_task_stops_counting_as_responsibility(world: ViewWorld) -> None:
    """ "Unfinished" is load-bearing, or "Của tôi" only ever grows.

    Finishing the task removes the item from the writer's list, leaving the one
    they own. Done through the task workflow rather than by editing a column, so
    the state under test is one the system can actually reach.
    """
    services = build_pr_services(world.session, world.settings)
    for target in (PrTaskStatus.IN_PROGRESS, PrTaskStatus.DONE):
        await services.tasks.change_status(
            actor=world.actor(world.head),
            request_id=uuid.uuid4(),
            task_id=world.writers_task,
            target=target,
        )
    world.act_as(world.writer)
    assert _titles(world.board(scope="MY_CONTENT")) == {"Bài của Nguyễn A"}
    # And out of the queue too, for the same reason.
    assert _titles(world.board(scope="MY_ACTIONS")) == {"Bài của Nguyễn A"}


@pytest.mark.asyncio
async def test_13_all_preserves_the_broad_list(world: ViewWorld) -> None:
    """Requirement 13. Nothing was taken away from anybody.

    Every actor here, including the writer with no grant, sees all six items
    when they ask for ``ALL`` - because the read permission is what governs
    visibility and this step did not touch it. A scope that quietly narrowed
    ``ALL`` would be row-level security wearing a preference's clothes.
    """
    for user in (world.lead, world.head, world.writer):
        world.act_as(user)
        body = world.board(scope="ALL")
        assert body["total"] == 6, user.full_name
        assert len(body["items"]) == 6, user.full_name


@pytest.mark.asyncio
async def test_14_team_follows_the_channel_assignment_that_already_exists(
    world: ViewWorld,
) -> None:
    """Requirement 14. ``TEAM`` is real here, and it is a real relation.

    ``pr_channel_assignments`` is the module's existing answer to "who answers
    for this channel", so ``TEAM`` means content aimed at a channel the actor is
    assigned to. No hierarchy was invented, and the writer's team is exactly the
    one channel they hold a row for.

    The head, with no channel assignment at all, has an empty team - not
    everything, which is what a "team" implemented as "no filter" would give.
    """
    world.act_as(world.writer)
    assert _titles(world.board(scope="TEAM")) == {"Bài của team khác"}

    world.act_as(world.head)
    body = world.board(scope="TEAM")
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_14a_a_closed_channel_assignment_leaves_the_team(world: ViewWorld) -> None:
    """The interval is read the way Step 1A defined it: closed and inclusive.

    Closing the writer's row yesterday empties their team today; the row still
    exists, and "who was on this team in March" is still answerable.
    """
    queries = world.queries()
    assignments = await queries.list_channel_assignments(
        actor=world.actor(world.writer), user_id=world.writer.id
    )
    assert len(assignments) == 1
    assignments[0].effective_to = TODAY - timedelta(days=1)
    await world.session.flush()

    page = await queries.content_page(
        actor=world.actor(world.writer),
        query=ContentQuery(scope=PrContentViewScope.TEAM),
        on=TODAY,
    )
    assert page.items == ()

    # And it was in force the day before it closed.
    still = await queries.content_page(
        actor=world.actor(world.writer),
        query=ContentQuery(scope=PrContentViewScope.TEAM),
        on=TODAY - timedelta(days=1),
    )
    assert [row.title for row in still.items] == ["Bài của team khác"]


# ===========================================================================
# 15: THE SCOPE SURVIVES A RELOAD
# ===========================================================================


@pytest.mark.asyncio
async def test_15_the_scope_is_a_query_parameter_and_so_survives_a_reload(
    world: ViewWorld,
) -> None:
    """Requirement 15, server side.

    A reload is a fresh ``GET`` with the same query string, which is exactly what
    this asserts: the same URL produces the same view for the same person, with
    no session-side memory of what they had chosen. The browser half - that the
    filters *are* the URL - is asserted in ``frontend/tests/content-views.test.tsx``.
    """
    world.act_as(world.writer)
    # An explicit scope overrides the default, and keeps overriding it.
    for _ in range(3):
        body = world.board(scope="ALL", stage=PrWorkflowStage.IDEA.value)
        assert body["scope"] == PrContentViewScope.ALL.value
        assert body["total"] == 5

    # And with no scope in the URL, the answer is the default rather than the
    # last thing this person looked at. Step 1F.2.3f.6a: that default is ALL.
    assert world.board()["scope"] == PrContentViewScope.ALL.value


@pytest.mark.asyncio
async def test_15a_an_unknown_scope_is_refused_with_the_options(world: ViewWorld) -> None:
    """A typo'd URL is a 422 naming the four, not a silent fall back to ``ALL``.

    Falling back would be the worst of both: somebody links a colleague to
    ``?scope=mine``, and they see everything while believing they see one slice.
    """
    response = world.client.get("/api/pr/contents/board", params={"scope": "mine"})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["allowed"] == [
        "ALL",
        "MY_ACTIONS",
        "MY_CONTENT",
        "TEAM",
    ]


# ===========================================================================
# 16: DATES
# ===========================================================================


@pytest.mark.asyncio
async def test_16_an_exact_day_is_read_in_the_business_timezone(world: ViewWorld) -> None:
    """Requirement 16, and the reason it is not a naive comparison.

    ``leads_item`` was created at 2026-08-09T18:00Z, which is 01:00 on the 10th
    in Ho Chi Minh City. Asking for the 10th must return it. A filter comparing
    against ``2026-08-10T00:00Z`` would drop it - along with everything else
    created before 07:00 local, which is most of a Vietnamese morning, every day.
    """
    world.act_as(world.head)
    body = world.board(scope="ALL", date_from="2026-08-10", date_to="2026-08-10")
    assert _titles(body) == {
        "Bài của trưởng nhóm",
        "Bài đa kênh",
        "Bài chờ duyệt",
        "Bài của team khác",
    }
    assert body["total"] == 4

    # The day before holds exactly the one item created on it.
    yesterday = world.board(scope="ALL", date_from="2026-08-09", date_to="2026-08-09")
    assert _titles(yesterday) == {"Bài của Nguyễn A"}


@pytest.mark.asyncio
async def test_16a_a_range_includes_both_of_its_endpoint_days(world: ViewWorld) -> None:
    """Inclusive at both ends, which is what somebody picking two dates means."""
    world.act_as(world.head)
    body = world.board(scope="ALL", date_from="2026-08-09", date_to="2026-08-10")
    assert body["total"] == 5
    assert "Bài tháng trước" not in _titles(body)

    # One-sided ranges work too: everything from a day onwards, and up to a day.
    assert world.board(scope="ALL", date_from="2026-08-10")["total"] == 4
    assert world.board(scope="ALL", date_to="2026-07-31")["total"] == 1


def test_16b_day_bounds_is_half_open_in_utc() -> None:
    """The conversion itself, pinned at the level it happens.

    Lower bound inclusive, upper bound exclusive and one day past ``date_to`` -
    which is how ``date_to`` covers its whole day without anybody writing
    23:59:59 and losing the last second of it.
    """
    lower, upper = day_bounds(date(2026, 8, 10), date(2026, 8, 10), tz=SAIGON)
    assert lower == datetime(2026, 8, 9, 17, 0, tzinfo=UTC)
    assert upper == datetime(2026, 8, 10, 17, 0, tzinfo=UTC)
    # A UTC service would produce a different, and wrong for this team, window.
    assert day_bounds(date(2026, 8, 10), None, tz=ZoneInfo("UTC"))[0] == datetime(
        2026, 8, 10, tzinfo=UTC
    )
    assert day_bounds(None, None, tz=SAIGON) == (None, None)


@pytest.mark.asyncio
async def test_16c_the_date_dimension_is_chosen_and_labelled(world: ViewWorld) -> None:
    """Requirement 16. Two dimensions, and they disagree on purpose.

    Every item's planned date is a week after its creation, so a range that holds
    four items by ``CREATED_AT`` holds none of them by ``PLANNED_PUBLISH_AT``.
    A single unlabelled "date" control would silently pick one and be wrong for
    half the people using it.
    """
    world.act_as(world.head)
    created = world.board(
        scope="ALL", date_field="CREATED_AT", date_from="2026-08-10", date_to="2026-08-10"
    )
    planned = world.board(
        scope="ALL",
        date_field="PLANNED_PUBLISH_AT",
        date_from="2026-08-10",
        date_to="2026-08-10",
    )
    assert created["total"] == 4
    assert planned["total"] == 0
    assert (
        world.board(
            scope="ALL",
            date_field="PLANNED_PUBLISH_AT",
            date_from="2026-08-17",
            date_to="2026-08-17",
        )["total"]
        == 4
    )


@pytest.mark.asyncio
async def test_16d_content_with_no_planned_date_falls_out_of_a_planned_date_filter(
    world: ViewWorld,
) -> None:
    """Which is why ``CREATED_AT`` is the default rather than this one."""
    row = await world.session.get(PrContentItem, world.multi_item)
    assert row is not None
    row.planned_publish_at = None
    await world.session.flush()

    world.act_as(world.head)
    body = world.board(
        scope="ALL",
        date_field=PrContentDateField.PLANNED_PUBLISH_AT.value,
        date_from="2026-08-17",
        date_to="2026-08-17",
    )
    assert "Bài đa kênh" not in _titles(body)
    # And it is still there when the filter is on the dimension it does have.
    assert "Bài đa kênh" in _titles(
        world.board(scope="ALL", date_from="2026-08-10", date_to="2026-08-10")
    )


async def _touched_at(world: ViewWorld, content_id: uuid.UUID, moment: datetime) -> None:
    """Stamp one item's ``updated_at``, without walking a real edit.

    An explicit assignment wins over ``onupdate``: SQLAlchemy only supplies the
    default for a column the UPDATE does not already set. The fixture's own
    flushes left every row's ``updated_at`` at the real clock, which is what
    makes an August window below hold exactly the rows a test stamped and
    nothing else.
    """
    row = await world.session.get(PrContentItem, content_id)
    assert row is not None
    row.updated_at = moment
    await world.session.flush()


@pytest.mark.asyncio
async def test_16e_the_latest_update_is_its_own_date_dimension(world: ViewWorld) -> None:
    """**The third dimension, and the case the other two cannot answer.**

    A piece drafted in July, planned for July and edited on 30 August. A recent
    window on ``CREATED_AT`` misses it, a window on ``PLANNED_PUBLISH_AT``
    misses it, and it is exactly the piece somebody scanning for activity is
    looking for.
    """
    await _touched_at(world, world.old_item, datetime(2026, 8, 30, 3, 0, tzinfo=UTC))
    world.act_as(world.head)

    day = {"date_from": "2026-08-30", "date_to": "2026-08-30", "scope": "ALL"}
    assert _titles(world.board(**day, date_field="UPDATED_AT")) == {"Bài tháng trước"}
    # The same day, on the dimensions that do not know about it.
    assert world.board(**day, date_field="CREATED_AT")["total"] == 0
    assert world.board(**day, date_field="PLANNED_PUBLISH_AT")["total"] == 0

    # And the item is still found by its own creation day, unchanged.
    assert "Bài tháng trước" in _titles(
        world.board(
            scope="ALL", date_field="CREATED_AT", date_from="2026-07-10", date_to="2026-07-10"
        )
    )


@pytest.mark.asyncio
async def test_16f_the_three_dimensions_disagree_independently(world: ViewWorld) -> None:
    """One item, three different days, three different answers.

    The archetype from the request: created 20/08, scheduled 10/09, updated
    06/09. Each filter reports its own column and none of them borrows another's
    - which is the whole reason the control is labelled rather than called
    "date".
    """
    row = await world.session.get(PrContentItem, world.writers_item)
    assert row is not None
    row.created_at = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)
    row.planned_publish_at = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    await world.session.flush()
    await _touched_at(world, world.writers_item, datetime(2026, 9, 6, 3, 0, tzinfo=UTC))

    world.act_as(world.head)
    title = "Bài của Nguyễn A"
    for field, day in (
        ("CREATED_AT", "2026-08-20"),
        ("PLANNED_PUBLISH_AT", "2026-09-10"),
        ("UPDATED_AT", "2026-09-06"),
    ):
        assert title in _titles(
            world.board(scope="ALL", date_field=field, date_from=day, date_to=day)
        ), field
    # And each day belongs to exactly one of them.
    assert title not in _titles(
        world.board(
            scope="ALL", date_field="UPDATED_AT", date_from="2026-08-20", date_to="2026-08-20"
        )
    )
    assert title not in _titles(
        world.board(
            scope="ALL", date_field="CREATED_AT", date_from="2026-09-06", date_to="2026-09-06"
        )
    )


@pytest.mark.asyncio
async def test_16g_an_item_created_and_updated_in_the_window_is_in_both(
    world: ViewWorld,
) -> None:
    """The ordinary case. A new piece nobody has touched since is in either."""
    await _touched_at(world, world.multi_item, datetime(2026, 8, 10, 6, 0, tzinfo=UTC))
    world.act_as(world.head)

    day = {"scope": "ALL", "date_from": "2026-08-10", "date_to": "2026-08-10"}
    assert "Bài đa kênh" in _titles(world.board(**day, date_field="CREATED_AT"))
    assert "Bài đa kênh" in _titles(world.board(**day, date_field="UPDATED_AT"))


@pytest.mark.asyncio
async def test_16h_the_update_dimension_composes_with_every_other_filter(
    world: ViewWorld,
) -> None:
    """**The intersection, on the server.**

    A date basis that only narrowed a page already fetched would agree with this
    for one screenful and diverge on the second. Each clause below removes the
    item, which is what proves the window is one condition among many in the same
    query rather than a filter applied afterwards.
    """
    moment = datetime(2026, 8, 30, 3, 0, tzinfo=UTC)
    await _touched_at(world, world.multi_item, moment)
    world.act_as(world.head)

    base = {
        "scope": "ALL",
        "date_field": "UPDATED_AT",
        "date_from": "2026-08-30",
        "date_to": "2026-08-30",
    }
    assert _titles(world.board(**base)) == {"Bài đa kênh"}

    # Platform: the item targets both, and each holds it exactly once even
    # though it carries two TikTok targets.
    assert _titles(world.board(**base, platform_id=str(world.tiktok.id))) == {"Bài đa kênh"}
    assert _titles(world.board(**base, platform_id=str(world.facebook.id))) == {"Bài đa kênh"}

    # Channel: one it has, and one it does not.
    assert _titles(world.board(**base, channel_id=str(world.tiktok_a.id))) == {"Bài đa kênh"}
    assert world.board(**base, channel_id=str(world.tiktok_b.id))["total"] == 0

    # The person responsible, and the same person on a day nothing moved - which
    # is the pair that shows the two clauses are ANDed rather than either one
    # winning.
    assert _titles(world.board(**base, user_id=str(world.head.id))) == {"Bài đa kênh"}
    assert (
        world.board(
            scope="ALL",
            date_field="UPDATED_AT",
            date_from="2026-08-29",
            date_to="2026-08-29",
            user_id=str(world.head.id),
        )["total"]
        == 0
    )

    # And the workflow stage.
    assert world.board(**base, stage=PrWorkflowStage.TEAM_LEAD_REVIEW.value)["total"] == 0


@pytest.mark.asyncio
async def test_16i_the_update_window_honours_the_business_timezone(
    world: ViewWorld,
) -> None:
    """The same calendar boundary as the other two, from the same helper.

    22:00 UTC on 29 August is already 05:00 on the 30th in Asia/Ho_Chi_Minh, and
    the window is the department's day rather than UTC's. There is no second date
    implementation here - ``_date_conditions`` only chooses the column, and
    ``day_bounds`` decides the instants for all three.
    """
    await _touched_at(world, world.old_item, datetime(2026, 8, 29, 22, 0, tzinfo=UTC))
    world.act_as(world.head)

    assert "Bài tháng trước" in _titles(
        world.board(
            scope="ALL", date_field="UPDATED_AT", date_from="2026-08-30", date_to="2026-08-30"
        )
    )
    assert "Bài tháng trước" not in _titles(
        world.board(
            scope="ALL", date_field="UPDATED_AT", date_from="2026-08-29", date_to="2026-08-29"
        )
    )


@pytest.mark.asyncio
async def test_16j_updated_at_moves_on_a_real_edit_and_not_on_a_comment(
    world: ViewWorld,
) -> None:
    """**What the new dimension actually reports**, asserted rather than assumed.

    ``updated_at`` is the item row's own timestamp, so it moves when a column of
    ``pr_content_items`` changes - a revision here - and does **not** move for a
    write that only touches a child table. A comment is the clearest example.

    That is the existing meaning of the column, not something this filter chose,
    and the boundary is worth pinning: somebody reading *"Ngày cập nhật mới
    nhất"* as *"last activity of any kind"* would be wrong about comments and
    attachments.
    """
    row = await world.session.get(PrContentItem, world.writers_item)
    assert row is not None
    await _touched_at(world, world.writers_item, datetime(2026, 8, 1, 3, 0, tzinfo=UTC))
    stamped = row.updated_at

    # A child-table write: the item row is untouched. Inserted directly because
    # the claim is about the **row**, not about one service's permissions - any
    # write that does not name a `pr_content_items` column leaves it alone.
    world.session.add(
        PrContentComment(
            content_id=world.writers_item,
            author_user_id=world.head.id,
            body="Ghi chú",
        )
    )
    await world.session.flush()
    await world.session.refresh(row)
    # Normalised on the way out: SQLite drops the offset, so a bare comparison
    # against the aware value that was written would raise rather than fail.
    assert ensure_utc(row.updated_at) == stamped

    # A column on the item: it moves.
    row.topic = "Chủ đề mới"
    await world.session.flush()
    await world.session.refresh(row)
    assert ensure_utc(row.updated_at) > stamped


# ===========================================================================
# 17-19, 22-23: CHANNEL, PLATFORM AND RESPONSIBLE USER
# ===========================================================================


@pytest.mark.asyncio
async def test_17_the_channel_filter_goes_through_the_target_table(world: ViewWorld) -> None:
    """Requirement 17. Via ``pr_content_targets``, which is where the fact lives."""
    world.act_as(world.head)
    body = world.board(scope="ALL", channel_id=str(world.tiktok_a.id))
    assert _titles(body) == {"Bài của Nguyễn A", "Bài đa kênh", "Bài chờ duyệt"}

    # And a channel nothing but the team item targets.
    assert _titles(world.board(scope="ALL", channel_id=str(world.tiktok_b.id))) == {
        "Bài của team khác"
    }


@pytest.mark.asyncio
async def test_18_the_platform_filter_goes_through_channels_to_platforms(
    world: ViewWorld,
) -> None:
    """Requirement 18. Canonical path, never the channel's name.

    Both TikTok channels are matched, which is what makes this a platform filter
    rather than a channel filter with a different label - and "Apexmed Facebook"
    is excluded despite sharing the brand and most of its name with a TikTok one.
    """
    world.act_as(world.head)
    tiktok = world.board(scope="ALL", platform_id=str(world.tiktok.id))
    assert _titles(tiktok) == {
        "Bài của Nguyễn A",
        "Bài đa kênh",
        "Bài chờ duyệt",
        "Bài của team khác",
    }
    facebook = world.board(scope="ALL", platform_id=str(world.facebook.id))
    assert _titles(facebook) == {"Bài của trưởng nhóm", "Bài đa kênh", "Bài tháng trước"}


@pytest.mark.asyncio
async def test_19_the_responsible_filter_uses_the_same_model_as_the_scope(
    world: ViewWorld,
) -> None:
    """Requirement 19. "Người phụ trách = Nguyễn A" is "Của tôi" for Nguyễn A.

    Asserted as an equality between the two, because two definitions that
    *nearly* agree is the failure worth catching: a dropdown reading
    ``owner_user_id`` under a tab reading owner-or-assignee would give two
    different answers to "what is Nguyễn A working on".
    """
    world.act_as(world.head)
    by_dropdown = _titles(world.board(scope="ALL", responsible_user_id=str(world.writer.id)))

    world.act_as(world.writer)
    by_scope = _titles(world.board(scope="MY_CONTENT"))
    assert by_dropdown == by_scope == {"Bài của Nguyễn A", "Bài của trưởng nhóm"}


@pytest.mark.asyncio
async def test_19a_the_owner_filter_is_still_the_narrower_one(world: ViewWorld) -> None:
    """Requirement 20. The pre-existing ``owner_user_id`` filter is untouched.

    Kept beside the broader one rather than redefined under it: the Telegram
    ``pr.content.list`` tool and any existing script pass ``owner_user_id`` and
    mean the column.
    """
    world.act_as(world.head)
    assert _titles(world.board(scope="ALL", owner_user_id=str(world.writer.id))) == {
        "Bài của Nguyễn A"
    }


@pytest.mark.asyncio
async def test_20_the_brand_and_stage_filters_still_work(world: ViewWorld) -> None:
    """Requirement 20, for the two filters that predate the step."""
    world.act_as(world.head)
    assert _titles(world.board(scope="ALL", brand_id=str(world.brand_b))) == {"Bài của team khác"}
    assert _titles(world.board(scope="ALL", stage=PrWorkflowStage.TEAM_LEAD_REVIEW.value)) == {
        "Bài chờ duyệt"
    }
    # And the flat list route keeps its own shape - an array, not an envelope.
    flat = world.client.get("/api/pr/contents", params={"brand_id": str(world.brand_b)})
    assert flat.status_code == 200
    assert [row["title"] for row in flat.json()] == ["Bài của team khác"]


@pytest.mark.asyncio
async def test_22_a_multi_target_item_matches_its_channel_exactly_once(
    world: ViewWorld,
) -> None:
    """Requirement 22. The ``EXISTS``-not-``JOIN`` decision, made visible.

    ``multi_item`` holds **two** TikTok targets. A ``JOIN`` implementation would
    return it twice for a platform filter matching both, and the total beside the
    list would then exceed the list - a count larger than the thing it counts,
    which is the specific bug this shape prevents.
    """
    world.act_as(world.head)
    body = world.board(scope="ALL", platform_id=str(world.tiktok.id))
    titles = [row["title"] for row in body["items"]]
    assert titles.count("Bài đa kênh") == 1
    assert body["total"] == len(body["items"]) == 4


@pytest.mark.asyncio
async def test_23_a_multi_target_item_matches_any_of_its_platforms(world: ViewWorld) -> None:
    """Requirement 23. Any target, not every target.

    ``multi_item`` is on TikTok *and* Facebook, and appears under both - which is
    the truth about it. An ``ALL``-targets reading would make it appear under
    neither.
    """
    world.act_as(world.head)
    for platform in (world.tiktok, world.facebook):
        assert "Bài đa kênh" in _titles(world.board(scope="ALL", platform_id=str(platform.id)))
    for channel in (world.tiktok_a, world.tiktok_c, world.facebook_a):
        assert "Bài đa kênh" in _titles(world.board(scope="ALL", channel_id=str(channel.id)))


# ===========================================================================
# 21, 24, 25: COMPOSITION, COUNTS AND PAGING
# ===========================================================================


@pytest.mark.asyncio
async def test_21_filters_compose_as_an_intersection(world: ViewWorld) -> None:
    """Requirement 21, on the example from the specification.

    Each filter alone matches more than the combination does, which is what makes
    this a composition test rather than four filter tests in a row: an
    implementation that ORed them, or that let the last one win, would pass every
    test above and fail this one.
    """
    world.act_as(world.lead)
    assert _titles(
        world.board(
            scope="MY_ACTIONS",
            date_from="2026-08-10",
            date_to="2026-08-10",
            platform_id=str(world.tiktok.id),
            channel_id=str(world.tiktok_a.id),
            responsible_user_id=str(world.head.id),
        )
    ) == {"Bài chờ duyệt"}

    # Drop the two channel clauses and the lead's own draft comes back - it is
    # inside the date window and inside the scope, and was excluded only by the
    # platform. That is the intersection being real rather than one clause
    # happening to be selective enough on its own.
    assert _titles(
        world.board(scope="MY_ACTIONS", date_from="2026-08-10", date_to="2026-08-10")
    ) == {"Bài chờ duyệt", "Bài của trưởng nhóm"}
    assert _titles(world.board(scope="MY_ACTIONS")) == {
        "Bài chờ duyệt",
        "Bài của trưởng nhóm",
    }
    # One contradictory pair empties it, rather than falling back to either half.
    assert (
        world.board(
            scope="MY_ACTIONS",
            platform_id=str(world.facebook.id),
            channel_id=str(world.tiktok_a.id),
        )["total"]
        == 0
    )


@pytest.mark.asyncio
async def test_24_the_counts_are_about_the_current_filter(world: ViewWorld) -> None:
    """Requirement 24. The bug this step exists to close.

    ``/dashboard`` says four items are at ``IDEA`` because that is true of the
    department. The board, scoped and dated, says one - and both are right about
    their own question. What must never happen again is the board showing the
    department's number over its own list.
    """
    world.act_as(world.lead)
    department = world.client.get("/api/pr/dashboard").json()
    at_idea = {row["stage"]: row["count"] for row in department["stage_counts"]}
    assert at_idea[PrWorkflowStage.IDEA.value] == 5

    scoped = world.board(scope="MY_ACTIONS")
    assert _count_for(scoped, PrWorkflowStage.IDEA) == 1
    assert _count_for(scoped, PrWorkflowStage.TEAM_LEAD_REVIEW) == 1
    assert scoped["total"] == 2
    # Every stage is present, so a client renders a tile per stage without
    # distinguishing "none" from "missing from the response".
    assert len(scoped["stage_counts"]) == len(PrWorkflowStage)
    assert sum(row["count"] for row in scoped["stage_counts"]) == scoped["total"]


@pytest.mark.asyncio
async def test_24a_the_counts_describe_the_whole_filter_not_the_page(world: ViewWorld) -> None:
    """A count that shrank with the page size would be useless for paging."""
    world.act_as(world.head)
    body = world.board(scope="ALL", limit=2)
    assert len(body["items"]) == 2
    assert body["total"] == 6
    assert sum(row["count"] for row in body["stage_counts"]) == 6


@pytest.mark.asyncio
async def test_24b_the_counts_include_the_whole_review_queue(world: ViewWorld) -> None:
    """A count over the same set the list is, gate items and all.

    Three items at ``TEAM_LEAD_REVIEW``, none of them the lead's, so the tile
    says three. It would say one if the counts were computed against an
    ownership-narrowed set - and a "Chờ duyệt: 1" over three cards is precisely
    the disagreement between count and list the board exists to prevent.
    """
    await _put_at(world, PrWorkflowStage.TEAM_LEAD_REVIEW, world.writers_item, world.multi_item)
    world.act_as(world.lead)
    scoped = world.board(scope="MY_ACTIONS")
    assert _count_for(scoped, PrWorkflowStage.TEAM_LEAD_REVIEW) == 3
    assert _count_for(scoped, PrWorkflowStage.IDEA) == 1  # their own draft
    assert scoped["total"] == 4
    assert sum(row["count"] for row in scoped["stage_counts"]) == scoped["total"]
    assert len(scoped["items"]) == 4


# ===========================================================================
# 24c-24f: THE PRODUCTION SPLIT (STEP 1F.2.3c)
# ===========================================================================


@pytest.mark.asyncio
async def test_24c_approved_is_counted_by_who_is_on_it(world: ViewWorld) -> None:
    """Step 1F.2.3c. Two items at ``APPROVED`` are two different jobs.

    One has nobody on it - *chờ nhận sản xuất* - and the other has a producer -
    *sẵn sàng sản xuất*. They are the same stage, so ``stage_counts`` cannot
    label the board's two columns, and a browser splitting the number itself
    would be counting the cards that happened to fit on the page. Hence a second
    count, over the same conditions.
    """
    await _put_at(world, PrWorkflowStage.APPROVED, world.writers_item, world.leads_item)
    await _set_producer(world, world.leads_item, world.writer)
    world.act_as(world.head)

    body = world.board(scope="ALL")
    assert _count_for(body, PrWorkflowStage.APPROVED) == 2
    assert _handoff_count(body, PrProductionHandoff.WAITING_FOR_PRODUCER) == 1
    assert _handoff_count(body, PrProductionHandoff.READY_FOR_PRODUCTION) == 1
    # Every state is present, zeros included, for the same reason every stage is.
    assert len(body["production_state_counts"]) == len(PrProductionHandoff)


@pytest.mark.asyncio
async def test_24d_internal_review_is_counted_as_production(world: ViewWorld) -> None:
    """The correction the step is for, in the numbers rather than in the layout.

    A cut sitting with the internal reviewer is production work: it has a
    producer, it has a file, and the two script gates have already passed. It is
    counted as ``IN_INTERNAL_REVIEW`` and is nowhere near the team-lead and head
    counts, which is what lets the panel put "Chờ duyệt nội bộ" under *Sản xuất*
    without inventing a second grouping of its own.
    """
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.multi_item)
    await _set_producer(world, world.multi_item, world.writer)
    await _put_at(world, PrWorkflowStage.PRODUCTION, world.old_item)
    await _set_producer(world, world.old_item, world.writer)
    world.act_as(world.head)

    body = world.board(scope="ALL")
    assert _handoff_count(body, PrProductionHandoff.IN_INTERNAL_REVIEW) == 1
    assert _handoff_count(body, PrProductionHandoff.IN_PRODUCTION) == 1
    # The script gates are untouched by it: the one item still at
    # ``TEAM_LEAD_REVIEW`` is the fixture's, and the cut is not among them.
    assert _count_for(body, PrWorkflowStage.TEAM_LEAD_REVIEW) == 1
    assert _count_for(body, PrWorkflowStage.HEAD_REVIEW) == 0
    # And the production half of the board adds up to the three items in it.
    production = sum(
        _count_for(body, stage)
        for stage in (
            PrWorkflowStage.APPROVED,
            PrWorkflowStage.PRODUCTION,
            PrWorkflowStage.INTERNAL_REVIEW,
        )
    )
    assert production == sum(row["count"] for row in body["production_state_counts"])


@pytest.mark.asyncio
async def test_24e_the_production_counts_follow_the_filter_not_the_page(
    world: ViewWorld,
) -> None:
    """One request, one filter, both counts - including under a ``LIMIT``.

    The scoped case is the one that matters: a writer's ``MY_CONTENT`` view
    contains one of the two approved items - the other is the head's, with no
    task of the writer's on it - and its production counts say one rather than
    the department's two.
    """
    await _put_at(world, PrWorkflowStage.APPROVED, world.writers_item, world.multi_item)
    world.act_as(world.head)

    paged = world.board(scope="ALL", limit=1)
    assert len(paged["items"]) == 1
    assert _handoff_count(paged, PrProductionHandoff.WAITING_FOR_PRODUCER) == 2

    world.act_as(world.writer)
    scoped = world.board(scope="MY_CONTENT")
    assert _handoff_count(scoped, PrProductionHandoff.WAITING_FOR_PRODUCER) == 1


@pytest.mark.asyncio
async def test_24f_nothing_outside_the_handoff_is_counted_as_production(
    world: ViewWorld,
) -> None:
    """A draft has no production state, and saying it did would invent one.

    The fixture opens with five items at ``IDEA`` and one at a review gate. None
    of them has been approved, so every production figure is zero while the
    stage counts are not - which is the check that the second aggregate is
    reading the handoff rule rather than counting rows twice.
    """
    world.act_as(world.head)
    body = world.board(scope="ALL")
    assert body["total"] == 6
    assert sum(row["count"] for row in body["production_state_counts"]) == 0
    assert sum(row["count"] for row in body["stage_counts"]) == 6


@pytest.mark.asyncio
async def test_25_paging_keeps_every_filter(world: ViewWorld) -> None:
    """Requirement 25. Page 2 of a filter is page 2 *of that filter*.

    Walked with a page size of two over a five-item filter, asserting that the
    three pages partition it - no row seen twice, none missed, and the total
    steady throughout. A page that quietly dropped the filter would return the
    sixth item somewhere.
    """
    world.act_as(world.head)
    filters: dict[str, object] = {
        "scope": "ALL",
        "date_from": "2026-08-09",
        "date_to": "2026-08-10",
        "limit": 2,
    }
    seen: list[str] = []
    for page in range(3):
        body = world.board(**filters, offset=page * 2)
        assert body["total"] == 5, page
        assert body["limit"] == 2
        assert body["offset"] == page * 2
        assert body["scope"] == PrContentViewScope.ALL.value
        seen.extend(row["title"] for row in body["items"])

    assert len(seen) == 5
    assert len(set(seen)) == 5
    assert "Bài tháng trước" not in seen


@pytest.mark.asyncio
async def test_25a_search_composes_on_the_board_and_short_circuits_on_the_list(
    world: ViewWorld,
) -> None:
    """The one place the two content routes differ, asserted so it stays deliberate.

    On the board a filter bar is visibly switched on, so a search narrows within
    it. On the flat list, ``search`` is the "find me this code" path it has been
    since Step 1E, and it ignores the filters - somebody typing a code wants that
    item.
    """
    world.act_as(world.head)
    # Composed: the word matches two items, the platform narrows it to one.
    assert _titles(world.board(scope="ALL", search="Bài của")) == {
        "Bài của Nguyễn A",
        "Bài của trưởng nhóm",
        "Bài của team khác",
    }
    assert _titles(
        world.board(scope="ALL", search="Bài của", platform_id=str(world.facebook.id))
    ) == {"Bài của trưởng nhóm"}

    # Short-circuited: the flat route answers about the search alone.
    flat = world.client.get(
        "/api/pr/contents",
        params={"search": "Bài của", "platform_id": str(world.facebook.id)},
    )
    assert flat.status_code == 200
    assert len(flat.json()) == 3


# ===========================================================================
# 26: THE WORKFLOW GROUP IS A FILTER, NOT A PASS OVER THE PAGE (1F.2.3c1)
# ===========================================================================


def test_26_the_groups_partition_the_workflow() -> None:
    """Every stage in exactly one group, and no invented ones.

    A pure table check, and the one that keeps the rest of this section honest:
    a stage in two groups would be counted twice by two tabs, and a stage in
    none would be a card that exists, is counted in nothing and appears nowhere -
    the failure a filter built on the mapping makes silent rather than visible.
    """
    grouped = [stage for group in PrContentGroup for stage in stages_in_group(group)]
    # Step 1F.2.3f.5. **Every *active* stage**, which is now the honest form of
    # this claim: ``MEASURED`` is retired and belongs to no group on purpose.
    # Step 1F.2.3f.6c: and ``ARCHIVED`` is the archive view's, not a group's.
    # The property the test is really about is unchanged - no stage in two
    # groups, and no operational stage in none - and both sets are named rather
    # than hard-coded, so a second such stage does not need an edit here.
    assert sorted(grouped, key=lambda stage: stage.value) == sorted(
        board_stages(PrContentBoardView.ACTIVE), key=lambda stage: stage.value
    )
    assert sorted(grouped, key=lambda stage: stage.value) == sorted(
        set(PrWorkflowStage) - RETIRED_STAGES - ARCHIVE_STAGES, key=lambda stage: stage.value
    )
    assert len(set(grouped)) == len(grouped)
    # And a retired or archived stage really is in none of them - the other
    # half of the partition, which would otherwise be provable only by the
    # count above.
    for stage in RETIRED_STAGES | ARCHIVE_STAGES:
        assert all(stage not in stages_in_group(group) for group in PrContentGroup), stage
    assert board_stages(PrContentBoardView.ARCHIVE) == (PrWorkflowStage.ARCHIVED,)
    # The correction of Step 1F.2.3c, asserted where the mapping now lives:
    # internal review is the last step of production, not a third script gate.
    assert PrWorkflowStage.INTERNAL_REVIEW in stages_in_group(PrContentGroup.PRODUCTION)
    assert PrWorkflowStage.INTERNAL_REVIEW not in stages_in_group(PrContentGroup.EDITORIAL_REVIEW)


@pytest.mark.asyncio
async def test_26a_each_group_returns_its_own_stages_and_no_others(world: ViewWorld) -> None:
    """One item per group, then each group asked for by name.

    The base case, and the one that would pass by accident if the group were
    still applied in the browser - so every assertion below is about ``total``
    as much as about the titles: the total is the server's count of the *whole*
    matching set, and a group that narrowed nothing would report six every time.
    """
    await _put_at(world, PrWorkflowStage.SCRIPTING, world.writers_item)
    await _put_at(world, PrWorkflowStage.HEAD_REVIEW, world.leads_item)
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.multi_item)
    await _put_at(world, PrWorkflowStage.PUBLISHED, world.team_item)
    await _put_at(world, PrWorkflowStage.CANCELLED, world.old_item)
    world.act_as(world.head)

    expected = {
        PrContentGroup.PREPARATION: {"Bài của Nguyễn A"},
        # The fixture's own gated item is at ``TEAM_LEAD_REVIEW``, so this group
        # holds two - which is what makes it more than a rename of ``stage``.
        PrContentGroup.EDITORIAL_REVIEW: {"Bài của trưởng nhóm", "Bài chờ duyệt"},
        PrContentGroup.PRODUCTION: {"Bài đa kênh"},
        PrContentGroup.COMPLETED: {"Bài của team khác"},
        PrContentGroup.CANCELLED: {"Bài tháng trước"},
    }
    for group, titles in expected.items():
        body = world.board(scope="ALL", group=group.value)
        assert _titles(body) == titles, group.value
        assert body["total"] == len(titles), group.value


@pytest.mark.asyncio
async def test_26b_a_small_group_arrives_whole_on_the_first_page(world: ViewWorld) -> None:
    """The bug, in the shape it was reported in.

    Five items in *Chuẩn bị* out of a filtered set of six, a page size that
    comfortably holds them, and one item from another group sitting in the
    middle of the ordering. Paginated first and grouped afterwards - which is
    what the browser used to do - the page of five contains four of them and the
    fifth is on page 2, under a tab that says 5.

    Grouped first, the page of five *is* the group: all five titles, a total of
    five, and nothing on the page after it.
    """
    # ``gated_item`` is not a preparation item, so an ungrouped page of five
    # spends a slot on it and pushes one of the five over the boundary. *Which*
    # one is deliberately not asserted below: Step 1F.2.3d put priority and the
    # planned publish date ahead of ``created_at`` in the ordering, so the item
    # that falls off is whichever the sort puts last - and pinning it here would
    # be asserting the sort order in a test about pagination and grouping.
    await _put_at(world, PrWorkflowStage.PUBLISHED, world.gated_item)
    await _put_at(
        world,
        PrWorkflowStage.BRIEFING,
        world.writers_item,
        world.leads_item,
        world.multi_item,
        world.team_item,
        world.old_item,
    )
    world.act_as(world.head)

    preparation = {
        "Bài của Nguyễn A",
        "Bài của trưởng nhóm",
        "Bài đa kênh",
        "Bài của team khác",
        "Bài tháng trước",
    }

    # What the old behaviour saw: five rows, one of which is not preparation -
    # so only four of the five the tab counts are on the page, and the fifth is
    # stranded on page 2 under a tab that says 5.
    ungrouped = world.board(scope="ALL", limit=5)
    assert len(ungrouped["items"]) == 5
    assert "Bài chờ duyệt" in _titles(ungrouped)
    assert len(_titles(ungrouped) & preparation) == 4

    grouped = world.board(scope="ALL", group=PrContentGroup.PREPARATION.value, limit=5)
    assert grouped["total"] == 5
    assert len(grouped["items"]) == 5
    assert _titles(grouped) == preparation
    # No cards from another group consumed a slot, and there is no second page.
    assert "Bài chờ duyệt" not in _titles(grouped)
    assert (
        world.board(scope="ALL", group=PrContentGroup.PREPARATION.value, limit=5, offset=5)["items"]
        == []
    )


@pytest.mark.asyncio
async def test_26c_the_tab_counts_stay_global_while_the_total_follows_the_group(
    world: ViewWorld,
) -> None:
    """The two count scopes, which must not be mixed.

    ``total`` captions the pager and is the selected group's. ``stage_counts``
    captions the tabs, which are how somebody *leaves* the group they are in -
    so they describe the filtered set before the group narrows it. Counted under
    the group instead, *Chờ duyệt* would read 0 the moment *Chuẩn bị* was open,
    and the strip would be a row of zeros pointing at the work it was hiding.
    """
    await _put_at(world, PrWorkflowStage.HEAD_REVIEW, world.multi_item, world.team_item)
    await _put_at(world, PrWorkflowStage.PUBLISHED, world.old_item)
    world.act_as(world.head)

    body = world.board(scope="ALL", group=PrContentGroup.PREPARATION.value)
    # Two items left at ``IDEA``: the writer's and the lead's.
    assert body["total"] == 2
    assert _count_for(body, PrWorkflowStage.IDEA) == 2
    # And the other tabs still say what is waiting in them.
    assert _count_for(body, PrWorkflowStage.TEAM_LEAD_REVIEW) == 1
    assert _count_for(body, PrWorkflowStage.HEAD_REVIEW) == 2
    assert _count_for(body, PrWorkflowStage.PUBLISHED) == 1
    assert sum(row["count"] for row in body["stage_counts"]) == 6

    # The filters the person *did* choose still narrow the counts: a scope is not
    # a group, and dropping it here would make the tabs describe a different set
    # from the board under them. The writer owns one item and holds a task on a
    # second, and their tabs add up to those two rather than to the six.
    world.act_as(world.writer)
    scoped = world.board(scope="MY_CONTENT", group=PrContentGroup.PREPARATION.value)
    assert sum(row["count"] for row in scoped["stage_counts"]) == 2
    assert scoped["total"] == 2


@pytest.mark.asyncio
async def test_26d_the_stage_filter_intersects_with_the_group(world: ViewWorld) -> None:
    """Two narrowings compose; neither is silently ignored.

    Within a group the "Bước" filter is how somebody asks for one column of it,
    so ``PRODUCTION`` + ``INTERNAL_REVIEW`` is the cuts waiting for an internal
    reviewer. Across groups the pair is unsatisfiable, and the honest answer is
    an empty page - dropping either half would show rows the person excluded.
    """
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.multi_item)
    await _put_at(world, PrWorkflowStage.APPROVED, world.team_item)
    world.act_as(world.head)

    narrowed = world.board(
        scope="ALL",
        group=PrContentGroup.PRODUCTION.value,
        stage=PrWorkflowStage.INTERNAL_REVIEW.value,
    )
    assert _titles(narrowed) == {"Bài đa kênh"}
    assert narrowed["total"] == 1

    impossible = world.board(
        scope="ALL",
        group=PrContentGroup.PREPARATION.value,
        stage=PrWorkflowStage.HEAD_REVIEW.value,
    )
    assert impossible["items"] == []
    assert impossible["total"] == 0


@pytest.mark.asyncio
async def test_26e_the_group_composes_with_every_other_filter(world: ViewWorld) -> None:
    """Intersection semantics, with the group as one more term.

    Scope, date, platform, channel, responsible person and search each still
    narrow what the group left - which is the property that makes the board's
    filter bar mean anything while a tab is open.
    """
    await _put_at(world, PrWorkflowStage.APPROVED, world.multi_item, world.team_item)
    world.act_as(world.head)
    production = PrContentGroup.PRODUCTION.value

    # Platform: the multi-target item is on TikTok and Facebook, the other on
    # TikTok only, so Facebook narrows the production group to one.
    assert _titles(
        world.board(scope="ALL", group=production, platform_id=str(world.facebook.id))
    ) == {"Bài đa kênh"}
    # Channel, through the target table.
    assert _titles(
        world.board(scope="ALL", group=production, channel_id=str(world.tiktok_b.id))
    ) == {"Bài của team khác"}
    # Responsible person: both are the head's, and the writer is responsible for
    # neither - so their production group is empty rather than everybody's.
    assert (
        world.board(scope="ALL", group=production, responsible_user_id=str(world.writer.id))[
            "total"
        ]
        == 0
    )
    # Search, composed rather than short-circuiting, as on the board it always is.
    assert _titles(world.board(scope="ALL", group=production, search="đa kênh")) == {"Bài đa kênh"}
    # Date, in the business timezone, over the same group.
    assert (
        world.board(scope="ALL", group=production, date_from="2026-08-10", date_to="2026-08-10")[
            "total"
        ]
        == 2
    )
    assert world.board(scope="ALL", group=production, date_to="2026-08-09")["total"] == 0
    # Scope: the writer is responsible for neither approved item.
    world.act_as(world.writer)
    assert world.board(scope="MY_CONTENT", group=production)["total"] == 0


@pytest.mark.asyncio
async def test_26f_the_production_group_keeps_its_four_columns(world: ViewWorld) -> None:
    """The group is three stages; the columns are four derived states.

    Step 1F.2.3c's split survives the group becoming a filter, because it is not
    the same question: ``APPROVED`` with nobody on it and ``APPROVED`` with a
    producer are one stage and two jobs, and the server is still what says which
    is which. The group brings the rows back; ``production_state`` sorts them.
    """
    await _put_at(world, PrWorkflowStage.APPROVED, world.writers_item, world.leads_item)
    await _set_producer(world, world.leads_item, world.writer)
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.multi_item)
    await _set_producer(world, world.multi_item, world.writer)
    world.act_as(world.head)

    body = world.board(scope="ALL", group=PrContentGroup.PRODUCTION.value)
    assert body["total"] == 3
    states = {row["title"]: row["production_state"] for row in body["items"]}  # type: ignore[union-attr,index]
    assert states == {
        "Bài của Nguyễn A": PrProductionHandoff.WAITING_FOR_PRODUCER.value,
        "Bài của trưởng nhóm": PrProductionHandoff.READY_FOR_PRODUCTION.value,
        "Bài đa kênh": PrProductionHandoff.IN_INTERNAL_REVIEW.value,
    }
    assert _handoff_count(body, PrProductionHandoff.WAITING_FOR_PRODUCER) == 1
    assert _handoff_count(body, PrProductionHandoff.READY_FOR_PRODUCTION) == 1
    assert _handoff_count(body, PrProductionHandoff.IN_INTERNAL_REVIEW) == 1


@pytest.mark.asyncio
async def test_26g_the_group_is_sql_and_not_a_python_pass(world: ViewWorld) -> None:
    """A grouped page fetches only the rows it returns.

    The failure this guards against is the one the step is fixing, moved rather
    than removed: filtering the group after the ``LIMIT``, in Python this time.
    Asked for one row at a time out of a four-item group, it would return one
    row on page 1 and nothing at all after it.
    """
    await _put_at(
        world,
        PrWorkflowStage.SCRIPTING,
        world.writers_item,
        world.leads_item,
        world.multi_item,
        world.team_item,
    )
    await _put_at(world, PrWorkflowStage.PUBLISHED, world.old_item)
    world.act_as(world.head)
    preparation = PrContentGroup.PREPARATION.value

    seen: list[str] = []
    for offset in range(4):
        page = world.board(scope="ALL", group=preparation, limit=1, offset=offset)
        assert page["total"] == 4, offset
        assert len(page["items"]) == 1, offset
        seen.extend(row["title"] for row in page["items"])
    assert len(set(seen)) == 4
    assert "Bài tháng trước" not in seen
    assert "Bài chờ duyệt" not in seen


@pytest.mark.asyncio
async def test_26h_an_unknown_group_is_refused_with_the_options(world: ViewWorld) -> None:
    """A typo in the query string names the five values rather than guessing.

    The same treatment ``scope`` gets, and for the same reason: falling back to
    "every group" would answer a different question quietly, and at a hundred
    and forty-six items the difference is invisible until somebody counts.
    """
    world.act_as(world.head)
    response = world.client.get("/api/pr/contents/board", params={"group": "PREP"})
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["field"] == "group"
    assert details["allowed"] == sorted(group.value for group in PrContentGroup)


# ===========================================================================
# THE BOUNDARY
# ===========================================================================


def test_the_board_route_is_declared_before_the_content_id_route() -> None:
    """``/contents/board`` must not be parsed as a content id.

    FastAPI matches in declaration order, so this is a real ordering dependency
    rather than a style point: swapped, ``board`` reaches ``/contents/{content_id}``
    and fails with a 422 about a malformed UUID.
    """
    app = create_app(Settings(web_base_url="https://pr.example.com"))
    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths.index("/api/pr/contents/board") < paths.index("/api/pr/contents/{content_id}")


@pytest.mark.asyncio
async def test_the_scope_is_not_an_access_rule(world: ViewWorld) -> None:
    """The distinction the whole step rests on, asserted rather than asserted-in-prose.

    The writer may narrow to ``MY_CONTENT`` and still read a specific item they
    own nothing of, and list every item with ``ALL``. If a scope had become
    row-level security, one of these two would fail - and the failure would be a
    security model nobody designed, arrived at through a display preference.
    """
    world.act_as(world.writer)
    assert world.board(scope="MY_CONTENT")["total"] < 6
    assert world.board(scope="ALL")["total"] == 6
    detail = world.client.get(f"/api/pr/contents/{world.old_item}")
    assert detail.status_code == 200
    assert detail.json()["content"]["title"] == "Bài tháng trước"


@pytest.mark.asyncio
async def test_the_filters_are_not_applied_in_python(world: ViewWorld) -> None:
    """A filtered page fetches only the rows it returns.

    The failure mode this guards against is the easy one: fetch 200 rows and
    ``[row for row in rows if ...]``. It would pass every assertion above and
    stop working at a few months of history. Asserted by asking for one row of a
    six-row filter and checking that one row came back - which a Python filter
    over a ``LIMIT``ed fetch could not do correctly for page 2.
    """
    world.act_as(world.head)
    first = world.board(scope="ALL", limit=1)
    second = world.board(scope="ALL", limit=1, offset=1)
    assert len(first["items"]) == 1
    assert len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["total"] == second["total"] == 6

    # And the review queue is a database question too, not a page of rows the
    # server fetched and then sifted: a two-row page of the lead's four-item
    # queue reports four.
    await _put_at(world, PrWorkflowStage.TEAM_LEAD_REVIEW, world.writers_item, world.multi_item)
    world.act_as(world.lead)
    page = world.board(scope="MY_ACTIONS", limit=2)
    assert len(page["items"]) == 2
    assert page["total"] == 4


# ===========================================================================
# 27: THE LANE IS A FILTER TOO (STEP 1F.2.3c2)
# ===========================================================================
#
# Section 26 made the *group* narrow before ``LIMIT``, and that fixed a five-item
# "Chuẩn bị" arriving one card on page 1 and four on page 3. This section is the
# same correction one level deeper, and it comes from the same screenshot the
# step was reported with:
#
#     Sản xuất 170
#       Chờ nhận sản xuất   155
#       Sẵn sàng sản xuất     0
#       Đang sản xuất         3      <- and no cards under it
#       Chờ duyệt nội bộ     12
#
# Every one of those numbers was correct. The board was still wrong, because a
# group is not one queue: paginating it let the 155 take every slot on the first
# pages, so the three items somebody was actually cutting were on page three of a
# pager belonging to another column. A lane count that says 3 over an empty
# column is the board lying about the work.
#
# So the lane is a query dimension like the group, and these tests are about the
# two properties that makes true: a lane's rows are cut from the lane, and a
# lane's volume cannot reach into another lane's page.


#: One entry per row :func:`_bulk` has ever inserted, so the codes it allocates
#: are unique whatever it is asked to create. The ``BULK`` infix keeps them out
#: of the ``CNT-2026-nnnnnn`` space the code service allocates the fixture's six
#: items from; nothing asserts these codes, they only have to be unique.
_CODES: list[None] = []


async def _bulk(
    world: ViewWorld,
    count: int,
    *,
    stage: PrWorkflowStage,
    prefix: str,
    producer: User | None = None,
    priority: PrPriority = PrPriority.NORMAL,
    content_type: PrContentType | None = None,
    created_at: datetime = datetime(2026, 8, 10, 6, 0, tzinfo=UTC),
) -> list[str]:
    """``count`` bare content rows standing at ``stage``.

    Inserted rather than walked, for the reason :func:`_put_at` already gives:
    these tests are about *reading* queues at volume, and driving 155 items
    through briefing, scripting, AI review and two approvals would make the
    fixture the subject rather than the query. ``test_pr_web_admin`` and
    ``test_pr_production_lifecycle`` prove the walk.

    Returns the titles, so a test can assert *which* rows a lane returned rather
    than only how many.
    """
    titles: list[str] = []
    for index in range(count):
        title = f"{prefix} {index:03d}"
        titles.append(title)
        _CODES.append(None)
        world.session.add(
            PrContentItem(
                # Unique and shaped like the allocator's output without going
                # through it - the code service is tested where it lives. The
                # sequence is the suite's own counter rather than anything read
                # off the title, so two prefixes sharing three letters cannot
                # collide on the unique index.
                code=f"CNT-BULK-{len(_CODES):06d}",
                title=title,
                brand_id=world.brand_a,
                owner_user_id=world.head.id,
                created_by_user_id=world.head.id,
                workflow_stage=stage,
                producer_user_id=producer.id if producer else None,
                priority=priority,
                content_type=content_type,
                created_at=created_at,
            )
        )
    await world.session.flush()
    return titles


@pytest_asyncio.fixture
async def production_board(world: ViewWorld) -> ViewWorld:
    """The reported dataset, exactly: 155 / 0 / 3 / 12 in the production group.

    The ratio is the whole point. Three items in production among 170 in the
    group do not fit on any page of the group that a person would look at first,
    so a board that pages the group cannot show them - which is what the
    screenshot showed. Reproduced here so the fix is asserted against the shape
    that broke rather than against a tidy fixture where four columns of five
    would all fit anyway.
    """
    # The waiting items are the newest, which is both realistic - they were
    # approved most recently - and what makes them sort to the front of the
    # group under the third ordering key. That is the shape of the report: the
    # busy lane owned page 1, so the three items in production were nowhere on
    # it. Leaving the timestamps equal would have let the id tiebreaker decide,
    # and the test would have passed or failed on a random UUID.
    await _bulk(
        world,
        155,
        stage=PrWorkflowStage.APPROVED,
        prefix="Chờ nhận",
        created_at=datetime(2026, 8, 10, 8, 0, tzinfo=UTC),
    )
    await _bulk(
        world,
        3,
        stage=PrWorkflowStage.PRODUCTION,
        prefix="Đang dựng",
        producer=world.writer,
        created_at=datetime(2026, 8, 10, 6, 0, tzinfo=UTC),
    )
    await _bulk(
        world,
        12,
        stage=PrWorkflowStage.INTERNAL_REVIEW,
        prefix="Chờ nội bộ",
        created_at=datetime(2026, 8, 10, 5, 0, tzinfo=UTC),
    )
    world.act_as(world.head)
    return world


def _lane(world: ViewWorld, lane: PrContentLane, **params: object) -> dict[str, object]:
    """One lane of the production group, as the panel asks for it."""
    return world.board(
        **{
            "scope": "ALL",
            "group": PrContentGroup.PRODUCTION.value,
            "lane": lane.value,
            **params,
        }
    )


@pytest.mark.asyncio
async def test_27a_the_reported_board_shows_every_lane_on_the_first_render(
    production_board: ViewWorld,
) -> None:
    """Requirement 22, and the bug itself.

    155 / 0 / 3 / 12, and the board drawn as one request for the figures plus one
    per lane. What has to be true is not "the three items are reachable" - they
    always were, on page three - but that they are **on screen without anybody
    touching another lane's pagination**.
    """
    world = production_board

    # The figures, asked for with no rows at all. This is the request that
    # labels the tabs and the lane headers.
    figures = world.board(scope="ALL", group=PrContentGroup.PRODUCTION.value, limit=0)
    assert figures["items"] == []
    assert figures["total"] == 170
    assert _handoff_count(figures, PrProductionHandoff.WAITING_FOR_PRODUCER) == 155
    assert _handoff_count(figures, PrProductionHandoff.READY_FOR_PRODUCTION) == 0
    assert _handoff_count(figures, PrProductionHandoff.IN_PRODUCTION) == 3
    assert _handoff_count(figures, PrProductionHandoff.IN_INTERNAL_REVIEW) == 12

    # And one page per lane, twenty rows wide - what the panel opens with.
    waiting = _lane(world, PrContentLane.WAITING_FOR_PRODUCER, limit=20)
    ready = _lane(world, PrContentLane.READY_FOR_PRODUCTION, limit=20)
    producing = _lane(world, PrContentLane.IN_PRODUCTION, limit=20)
    internal = _lane(world, PrContentLane.IN_INTERNAL_REVIEW, limit=20)

    # The busy lane: its whole queue is 155 and the first twenty came back.
    assert waiting["total"] == 155
    assert len(waiting["items"]) == 20
    # The empty one is empty rather than absent, so a client draws a column that
    # says so instead of leaving a hole.
    assert ready["total"] == 0
    assert ready["items"] == []
    # **The three.** All of them, on the first request, with no offset anywhere.
    assert producing["total"] == 3
    assert len(producing["items"]) == 3
    assert _titles(producing) == {"Đang dựng 000", "Đang dựng 001", "Đang dựng 002"}
    # And twelve fit inside one lane page of twenty, so the internal reviewer
    # sees their whole queue too.
    assert internal["total"] == 12
    assert len(internal["items"]) == 12


@pytest.mark.asyncio
async def test_27b_a_busy_lane_cannot_take_another_lanes_page_slots(
    production_board: ViewWorld,
) -> None:
    """Requirement 18, stated as the negative it is.

    The old board's first page was sixty rows of the *group*, priority-ordered,
    and with 155 waiting items every one of those slots was a waiting item. This
    is that page, and then the same page asked for per lane.
    """
    world = production_board

    # What the old shape returned: sixty rows of the group, none of them the
    # three that mattered. Kept as an assertion rather than a comment, because it
    # is the thing that must never come back.
    grouped = world.board(scope="ALL", group=PrContentGroup.PRODUCTION.value, limit=60)
    assert len(grouped["items"]) == 60
    assert all(row["title"].startswith("Chờ nhận") for row in grouped["items"])  # type: ignore[union-attr,index]

    # The lane's page is cut from the lane. Sixty asked of a three-item lane is
    # three rows and no second page - the 155 are not in this query at all.
    producing = _lane(world, PrContentLane.IN_PRODUCTION, limit=60)
    assert len(producing["items"]) == 3
    assert _lane(world, PrContentLane.IN_PRODUCTION, limit=60, offset=3)["items"] == []

    # And the busy lane's own pagination stays its own: page two of *Chờ nhận
    # sản xuất* is more waiting items, not the production ones.
    second = _lane(world, PrContentLane.WAITING_FOR_PRODUCER, limit=20, offset=20)
    assert len(second["items"]) == 20
    assert all(row["title"].startswith("Chờ nhận") for row in second["items"])  # type: ignore[union-attr,index]


@pytest.mark.asyncio
async def test_27c_the_lane_is_sql_and_not_a_python_pass(production_board: ViewWorld) -> None:
    """Requirement 1. The lane narrows before ``LIMIT``/``OFFSET``.

    The failure this guards against is the one section 26 removed at the group
    level, moved down a level: fetch the group's page and keep the rows that
    belong to this column. Asked for one row at a time out of a three-item lane,
    that implementation returns one row, then nothing, then nothing - because
    rows 2 and 3 of the *group* are waiting items it would discard.
    """
    world = production_board
    seen: list[str] = []
    for offset in range(3):
        page = _lane(world, PrContentLane.IN_PRODUCTION, limit=1, offset=offset)
        assert len(page["items"]) == 1, offset
        assert page["total"] == 3, offset
        seen.append(page["items"][0]["title"])  # type: ignore[index]
    assert sorted(seen) == ["Đang dựng 000", "Đang dựng 001", "Đang dựng 002"]
    assert _lane(world, PrContentLane.IN_PRODUCTION, limit=1, offset=3)["items"] == []


@pytest.mark.asyncio
async def test_27d_the_lane_totals_reconcile_with_the_group(production_board: ViewWorld) -> None:
    """Requirements 15 and 17.

    Two claims, and the second is what stops the four columns quietly losing an
    item between them:

    * a lane's ``total`` is its **whole** queue, not the page that came back;
    * the four production lanes sum to the production group's total, under the
      same filters - so *Sản xuất 170* is 155 + 0 + 3 + 12 and not 170 items with
      four descriptions that happen to be near it.

    And the same figures arrive two ways - each lane's own ``total`` and the
    board's ``production_state_counts`` - which have to agree, because a lane
    header reads the second while the lane itself is cut by the first.
    """
    world = production_board
    figures = world.board(scope="ALL", group=PrContentGroup.PRODUCTION.value, limit=0)

    totals: dict[PrContentLane, int] = {}
    for lane in lanes_in_group(PrContentGroup.PRODUCTION):
        page = _lane(world, lane, limit=5)
        totals[lane] = int(page["total"])  # type: ignore[arg-type]
        # The page describes only this lane, and the total describes the queue.
        assert len(page["items"]) <= 5
        # A lane request answers about one column, so it does not carry the
        # board's figures - reading them there would be four copies of the same
        # two aggregates.
        assert page["stage_counts"] == []
        assert page["production_state_counts"] == []

    assert totals == {
        PrContentLane.WAITING_FOR_PRODUCER: 155,
        PrContentLane.READY_FOR_PRODUCTION: 0,
        PrContentLane.IN_PRODUCTION: 3,
        PrContentLane.IN_INTERNAL_REVIEW: 12,
    }
    assert sum(totals.values()) == figures["total"] == 170
    # And they are the same numbers the header reads.
    for lane, total in totals.items():
        assert _handoff_count(figures, PrProductionHandoff(lane.value)) == total, lane.value


@pytest.mark.asyncio
async def test_27e_the_production_lanes_are_the_handoff_rule_and_not_a_second_one(
    world: ViewWorld,
) -> None:
    """Requirements 3-6. Each production lane, against the domain's own reading.

    ``APPROVED`` is two lanes and three stages are four columns, which is the
    only reason the lane vocabulary is not ``PrWorkflowStage`` under a new name.
    The predicate for that split is read out of
    :func:`~meobot.domain.pr.production.handoff_state` rather than written beside
    the stage, and this asserts the two agree on every row - including the case a
    hand-written ``IS NULL`` gets wrong, ``IN_PRODUCTION`` with nobody named.
    """
    # One item in each of the four states, and an unclaimed one at ``PRODUCTION``
    # - which is a real state and must land in *Đang sản xuất*, not in *Chờ
    # nhận sản xuất*.
    await _put_at(world, PrWorkflowStage.APPROVED, world.writers_item, world.leads_item)
    await _set_producer(world, world.leads_item, world.writer)
    await _put_at(world, PrWorkflowStage.PRODUCTION, world.multi_item, world.team_item)
    await _set_producer(world, world.multi_item, world.writer)
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.old_item)
    world.act_as(world.head)

    expected = {
        PrContentLane.WAITING_FOR_PRODUCER: {"Bài của Nguyễn A"},
        PrContentLane.READY_FOR_PRODUCTION: {"Bài của trưởng nhóm"},
        # Both, claimed and unclaimed: the stage decides this lane on its own.
        PrContentLane.IN_PRODUCTION: {"Bài đa kênh", "Bài của team khác"},
        PrContentLane.IN_INTERNAL_REVIEW: {"Bài tháng trước"},
    }
    for lane, titles in expected.items():
        body = _lane(world, lane)
        assert _titles(body) == titles, lane.value
        assert body["total"] == len(titles), lane.value

    # And the split is the domain's: every row a lane returned reads back as the
    # production state that lane is named for.
    for lane, state in LANE_PRODUCTION_STATES.items():
        for row in _lane(world, lane)["items"]:  # type: ignore[union-attr]
            assert row["production_state"] == state.value, lane.value


@pytest.mark.asyncio
async def test_27f_a_stage_lane_is_its_stage(world: ViewWorld) -> None:
    """Requirement 2. The eleven lanes their stage decides on its own.

    Every one of them, so a lane added to the enum and to no table is a failure
    here rather than a column that silently matches everything.
    """
    world.act_as(world.head)
    others = (
        world.leads_item,
        world.multi_item,
        world.gated_item,
        world.team_item,
        world.old_item,
    )
    for lane in PrContentLane:
        if lane in LANE_PRODUCTION_STATES:
            # The four production lanes need the producer column as well, and
            # have their own test above.
            continue
        stage = lane_stage(lane)
        # The other five stand somewhere this lane is not, so a lane that failed
        # to narrow would return six rows rather than one.
        parking = (
            PrWorkflowStage.ARCHIVED
            if stage is PrWorkflowStage.MEASURED
            else PrWorkflowStage.MEASURED
        )
        await _put_at(world, parking, *others)
        await _put_at(world, stage, world.writers_item)
        # Step 1F.2.3f.6c: the archive lane is asked of the archive view.
        view = {"view": "ARCHIVE"} if lane in ARCHIVE_LANES else {}
        body = world.board(scope="ALL", lane=lane.value, **view)
        assert _titles(body) == {"Bài của Nguyễn A"}, lane.value
        assert body["total"] == 1, lane.value
        # And nothing else at another stage came with it.
        assert all(row["workflow_stage"] == stage.value for row in body["items"])  # type: ignore[union-attr,index]


@pytest.mark.asyncio
async def test_27g_the_lane_composes_with_every_other_filter(
    production_board: ViewWorld,
) -> None:
    """Requirements 7-13. The lane is one more term in the intersection.

    A lane that ignored the filter bar would be a second way of asking a
    question the board already has one way of asking - and the person who
    filtered to *Rất gấp* would get the ordinary items back, in a column that
    looked like it had been filtered.
    """
    world = production_board
    # Two of the three production items retagged, so each filter below has a row
    # it must exclude as well as one it must return.
    rows = (
        await world.session.execute(select(PrContentItem).order_by(PrContentItem.code))
    ).scalars()
    producing = [row for row in rows if row.title.startswith("Đang dựng")]
    producing[0].priority = PrPriority.CRITICAL
    producing[0].content_type = PrContentType.CORPORATE_TVC
    producing[1].owner_user_id = world.writer.id
    await world.session.flush()

    lane = PrContentLane.IN_PRODUCTION
    # Priority, and the busy lane is untouched by it.
    assert _lane(world, lane, priority=PrPriority.CRITICAL.value)["total"] == 1
    assert (
        _lane(world, PrContentLane.WAITING_FOR_PRODUCER, priority=PrPriority.CRITICAL.value)[
            "total"
        ]
        == 0
    )
    # Content type - the spec's "only TVCs awaiting internal review" shape.
    assert _lane(world, lane, content_type=PrContentType.CORPORATE_TVC.value)["total"] == 1
    assert _lane(world, lane, content_type=PrContentType.PRESS_ARTICLE.value)["total"] == 0
    # Người phụ trách.
    assert _lane(world, lane, responsible_user_id=str(world.writer.id))["total"] == 1
    # Search, over the same lane.
    assert _titles(_lane(world, lane, search="Đang dựng 002")) == {"Đang dựng 002"}
    # Dates, in the business timezone: the bulk rows are all created today.
    assert _lane(world, lane, date_from="2026-08-10", date_to="2026-08-10")["total"] == 3
    assert _lane(world, lane, date_to="2026-08-09")["total"] == 0
    # Platform and channel: the bulk rows have no targets at all, so a platform
    # filter empties the lane rather than passing everything through.
    assert _lane(world, lane, platform_id=str(world.tiktok.id))["total"] == 0
    assert _lane(world, lane, channel_id=str(world.tiktok_a.id))["total"] == 0
    # Scope. ``MY_CONTENT`` for the head is the one row still owned by them.
    assert _lane(world, lane, scope="MY_CONTENT")["total"] == 2
    world.act_as(world.writer)
    assert _lane(world, lane, scope="MY_CONTENT")["total"] == 1


@pytest.mark.asyncio
async def test_27h_the_lane_intersects_with_the_stage_filter(
    production_board: ViewWorld,
) -> None:
    """Requirement 13, and requirement 11 of the step's own numbering.

    Both filters apply. Neither is dropped, and neither is reinterpreted into
    agreement with the other - an impossible pair is empty, which is the answer
    the group already gives to the same question.
    """
    world = production_board
    lane = PrContentLane.IN_INTERNAL_REVIEW

    agreeing = _lane(world, lane, stage=PrWorkflowStage.INTERNAL_REVIEW.value)
    assert agreeing["total"] == 12

    contradicting = _lane(
        world, PrContentLane.IN_PRODUCTION, stage=PrWorkflowStage.INTERNAL_REVIEW.value
    )
    assert contradicting["total"] == 0
    assert contradicting["items"] == []


@pytest.mark.asyncio
async def test_27i_a_lane_outside_the_group_is_an_empty_intersection(
    production_board: ViewWorld,
) -> None:
    """Requirement 10. One behaviour, chosen and tested.

    ``group=PREPARATION`` with ``lane=IN_PRODUCTION`` is unsatisfiable. It
    returns nothing, which is what ``stage`` already does for the same shape of
    contradiction - and the important half is the second assertion: the server
    does **not** switch the group to the one the lane belongs to, so a client
    with a stale pair never gets rows it did not ask for.
    """
    world = production_board
    impossible = world.board(
        scope="ALL",
        group=PrContentGroup.PREPARATION.value,
        lane=PrContentLane.IN_PRODUCTION.value,
    )
    assert impossible["items"] == []
    assert impossible["total"] == 0

    # Not silently reinterpreted: the same lane inside its own group returns the
    # three rows, so the emptiness above is the intersection and not a mistake.
    assert _lane(world, PrContentLane.IN_PRODUCTION)["total"] == 3


@pytest.mark.asyncio
async def test_27j_an_unknown_lane_is_refused_with_the_options(world: ViewWorld) -> None:
    """A misspelt lane is a 422 naming the fifteen, like a misspelt group.

    "Every lane" would answer a different question quietly, and at a hundred and
    seventy rows the difference is a screenful.
    """
    response = world.client.get("/api/pr/contents/board", params={"lane": "PRODUCING"})
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["field"] == "lane"
    assert details["allowed"] == sorted(lane.value for lane in PrContentLane)


@pytest.mark.asyncio
async def test_27k_priority_orders_the_lane_before_it_is_paged(world: ViewWorld) -> None:
    """Requirement 14. The sort is the lane's, and it happens first.

    A critical item in a lane of ordinary ones is on that lane's **first** page,
    however far down the group it would have fallen. This is Step 1F.2.3d's
    property, re-asserted at the level pagination now happens at - the two are
    the same failure otherwise, one page turn apart.
    """
    await _bulk(world, 30, stage=PrWorkflowStage.PRODUCTION, prefix="Bình thường")
    urgent = await _bulk(
        world,
        1,
        stage=PrWorkflowStage.PRODUCTION,
        prefix="Rất gấp",
        priority=PrPriority.CRITICAL,
    )
    world.act_as(world.head)

    page = _lane(world, PrContentLane.IN_PRODUCTION, limit=5)
    assert page["total"] == 31
    assert page["items"][0]["title"] == urgent[0]  # type: ignore[index]
    # And ordering happened in SQL, not over the page: the second lane page does
    # not start with it again, and does not repeat a row from the first.
    second = _lane(world, PrContentLane.IN_PRODUCTION, limit=5, offset=5)
    assert urgent[0] not in _titles(second)
    assert not (_titles(page) & _titles(second))


@pytest.mark.asyncio
async def test_27l_every_group_paginates_its_lanes_independently(world: ViewWorld) -> None:
    """Requirements 23-26. The regressions for the other four groups.

    One high-volume lane and one or two small ones in each group, and the claim
    is the same every time: the small lanes arrive whole on the first render.
    The production group is not special, and neither is ``CANCELLED`` for having
    one lane - it pages like the rest rather than through a second architecture.
    """
    # Preparation: 100 ideas beside 2 briefs, 1 script and 3 in AI review.
    await _bulk(world, 100, stage=PrWorkflowStage.IDEA, prefix="Ý tưởng")
    await _bulk(world, 2, stage=PrWorkflowStage.BRIEFING, prefix="Brief")
    await _bulk(world, 1, stage=PrWorkflowStage.SCRIPTING, prefix="Kịch bản")
    await _bulk(world, 3, stage=PrWorkflowStage.AI_REVIEW, prefix="AI")
    # Editorial review: 100 at the lead's gate beside 4 at the head's.
    await _bulk(world, 100, stage=PrWorkflowStage.TEAM_LEAD_REVIEW, prefix="Trưởng nhóm")
    await _bulk(world, 4, stage=PrWorkflowStage.HEAD_REVIEW, prefix="Trưởng phòng")
    # Completed: a large published lane over a small ready-to-publish one, which
    # is the operationally important one - work waiting to go out.
    await _bulk(world, 100, stage=PrWorkflowStage.PUBLISHED, prefix="Đã đăng")
    await _bulk(world, 2, stage=PrWorkflowStage.READY_TO_PUBLISH, prefix="Sẵn sàng đăng")
    await _bulk(world, 7, stage=PrWorkflowStage.CANCELLED, prefix="Đã hủy")
    world.act_as(world.head)

    expected = {
        PrContentLane.BRIEFING: 2,
        PrContentLane.SCRIPTING: 1,
        PrContentLane.AI_REVIEW: 3,
        PrContentLane.HEAD_REVIEW: 4,
        PrContentLane.READY_TO_PUBLISH: 2,
        PrContentLane.CANCELLED: 7,
    }
    for lane, count in expected.items():
        group = group_of_lane(lane)
        body = world.board(scope="ALL", group=group.value, lane=lane.value, limit=20)
        assert body["total"] == count, lane.value
        # Whole, on the first page, with the noisy lane beside it untouched.
        assert len(body["items"]) == count, lane.value

    # And the noisy lanes are still there, paged on their own.
    for lane, count in (
        (PrContentLane.IDEA, 100),
        (PrContentLane.TEAM_LEAD_REVIEW, 100),
        (PrContentLane.PUBLISHED, 100),
    ):
        body = world.board(scope="ALL", group=group_of_lane(lane).value, lane=lane.value, limit=20)
        # At least, because the fixture's own six items are at some of these
        # stages - the claim is that the lane is long and pages twenty at a
        # time, not that this suite owns every row in it.
        assert body["total"] >= count, lane.value
        assert len(body["items"]) == 20, lane.value


@pytest.mark.asyncio
async def test_27m_my_actions_keeps_its_meaning_inside_a_lane(world: ViewWorld) -> None:
    """Requirement 27. The lane pages a queue; it does not decide who holds one.

    An internal reviewer's items reach them through ``MY_ACTIONS`` exactly as
    before, and asking for *Chờ duyệt nội bộ* inside that scope narrows the
    queue rather than widening it. What must not happen is a lane handing
    somebody rows their scope excluded.
    """
    await _grant(world, world.lead, PrCapability.PR_INTERNAL_REVIEW)
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.multi_item, world.team_item)
    # One more at the same stage, owned by nobody they are related to - still
    # theirs to act on, because the gate is what the queue is about.
    await _bulk(world, 5, stage=PrWorkflowStage.INTERNAL_REVIEW, prefix="Bản dựng")
    world.act_as(world.lead)

    lane = world.board(
        scope=PrContentViewScope.MY_ACTIONS.value,
        group=PrContentGroup.PRODUCTION.value,
        lane=PrContentLane.IN_INTERNAL_REVIEW.value,
        limit=20,
    )
    assert lane["total"] == 7
    assert len(lane["items"]) == 7

    # And the scope still bites: a lane the lead holds no gate for is empty for
    # them under ``MY_ACTIONS`` while being full under ``ALL``.
    await _bulk(world, 4, stage=PrWorkflowStage.HEAD_REVIEW, prefix="Chờ trưởng phòng")
    mine = world.board(
        scope=PrContentViewScope.MY_ACTIONS.value,
        group=PrContentGroup.EDITORIAL_REVIEW.value,
        lane=PrContentLane.HEAD_REVIEW.value,
    )
    assert mine["total"] == 0
    everything = world.board(
        scope="ALL",
        group=PrContentGroup.EDITORIAL_REVIEW.value,
        lane=PrContentLane.HEAD_REVIEW.value,
    )
    assert everything["total"] == 4


@pytest.mark.asyncio
async def test_27n_the_handoff_moves_an_item_between_lanes(world: ViewWorld) -> None:
    """Requirement 29. Naming a producer moves the card, and the stage does not.

    *Chờ nhận sản xuất* to *Sẵn sàng sản xuất* is the handoff, and it happens
    without ``APPROVED`` changing - which is exactly why the two are lanes and
    not stages. ``START_PRODUCTION`` then moves it to *Đang sản xuất*.
    """
    await _put_at(world, PrWorkflowStage.APPROVED, world.writers_item)
    world.act_as(world.head)

    assert _titles(_lane(world, PrContentLane.WAITING_FOR_PRODUCER)) == {"Bài của Nguyễn A"}
    assert _lane(world, PrContentLane.READY_FOR_PRODUCTION)["total"] == 0

    await _set_producer(world, world.writers_item, world.writer)
    assert _lane(world, PrContentLane.WAITING_FOR_PRODUCER)["total"] == 0
    assert _titles(_lane(world, PrContentLane.READY_FOR_PRODUCTION)) == {"Bài của Nguyễn A"}
    # The canonical stage did not move. The handoff is two columns, one stage.
    row = await world.session.get(PrContentItem, world.writers_item)
    assert row is not None and row.workflow_stage is PrWorkflowStage.APPROVED

    await _put_at(world, PrWorkflowStage.PRODUCTION, world.writers_item)
    assert _lane(world, PrContentLane.READY_FOR_PRODUCTION)["total"] == 0
    assert _titles(_lane(world, PrContentLane.IN_PRODUCTION)) == {"Bài của Nguyễn A"}


@pytest.mark.asyncio
async def test_27o_a_revision_reclassifies_the_lane_on_the_next_read(world: ViewWorld) -> None:
    """Requirement 28. Undo moves the card between lanes, with no stale answer.

    Nothing here caches a lane, so "after refresh" is simply "the next request".
    The assertion is that the card leaves *Chờ duyệt nội bộ* and appears in
    *Đang sản xuất* - which it does because a lane is a ``WHERE`` clause over the
    row's current stage rather than a bucket somebody was sorted into.
    """
    await _put_at(world, PrWorkflowStage.INTERNAL_REVIEW, world.writers_item)
    await _set_producer(world, world.writers_item, world.writer)
    world.act_as(world.head)
    assert _titles(_lane(world, PrContentLane.IN_INTERNAL_REVIEW)) == {"Bài của Nguyễn A"}

    # The undo: back to production for another cut.
    await _put_at(world, PrWorkflowStage.PRODUCTION, world.writers_item)
    assert _lane(world, PrContentLane.IN_INTERNAL_REVIEW)["total"] == 0
    assert _titles(_lane(world, PrContentLane.IN_PRODUCTION)) == {"Bài của Nguyễn A"}


def test_27p_the_lanes_partition_the_groups_and_the_stages() -> None:
    """The tables, asserted as tables.

    A sixteenth lane added to the enum and to no group, or a lane whose stage
    nobody wrote down, is a failure here rather than a column that renders empty
    for a week before anybody notices.
    """
    assert set(LANE_STAGES) == set(PrContentLane)
    grouped = [lane for group in PrContentGroup for lane in lanes_in_group(group)]
    # Step 1F.2.3f.6c: the archive lane is in no group - it is the archive
    # view's - and the two halves together are the whole enum.
    assert sorted(grouped) == sorted(set(PrContentLane) - ARCHIVE_LANES)
    assert len(set(grouped)) == len(grouped)
    assert {PrContentLane.ARCHIVED} == ARCHIVE_LANES
    with pytest.raises(KeyError):
        group_of_lane(PrContentLane.ARCHIVED)

    # A lane's stage is in its group's stages: the two tables describe one board.
    for lane in set(PrContentLane) - ARCHIVE_LANES:
        group = group_of_lane(lane)
        assert lane_stage(lane) in stages_in_group(group), lane.value

    # Every group's stages are covered by its lanes, so no stage is a column
    # nobody can ask for - which would be work with no queue.
    for group in PrContentGroup:
        covered = {lane_stage(lane) for lane in lanes_in_group(group)}
        assert covered == set(stages_in_group(group)), group.value

    # And the four production lanes are exactly the handoff states.
    assert set(LANE_PRODUCTION_STATES.values()) == set(PrProductionHandoff)
