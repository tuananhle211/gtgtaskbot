"""Step 1F.2.3d: content priority, and the web notification centre.

Two features in one suite because they ship as one step, separated into two
sections below.

What the priority half is really testing
-----------------------------------------

Not "does the column store four values" - it stored four before this step. The
assertions that matter are about **where** the ordering and the filtering happen:
before ``LIMIT``, in SQL, over the whole filtered set. Section 62 builds more
content than fits on a page and proves a ``CRITICAL`` item on the last-created
row still arrives first, which is exactly what a browser sorting its own page
cannot do.

What the notification half is really testing
---------------------------------------------

Ownership, mostly. Section 64 is six assertions that one person cannot see,
read, or clear another person's notifications, written against the HTTP surface
rather than the service, because that is where somebody would attack it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
from meobot.application.pr_content_service import ContentTargetSpec, CreateContentCommand
from meobot.application.pr_services import build_pr_services
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrBrand, PrChannel, PrContentItem, PrPlatform
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.pr.models import (
    PrChannelCategory,
    PrDistributionMode,
    PrPriority,
    PrWorkflowStage,
)
from meobot.domain.pr.priority import PRIORITY_RANK
from tests.unit.streams import tag_pr

pytestmark = pytest.mark.asyncio


@dataclass
class World:
    """One brand, one channel, four people, and content to sort.

    The four roles exist because the priority rule has three outcomes and each
    needs a person who lands on it: a manager who may retriage anything, an
    owner who may retriage theirs, and a stranger who may retriage nothing
    despite holding the same ``PR_CONTENT_EDIT`` the owner does.
    """

    session: AsyncSession
    client: TestClient
    settings: Settings
    #: ``ADMIN``, so ``PR_CONTENT_CANCEL`` as well as ``PR_CONTENT_EDIT``.
    manager: User
    #: ``EMPLOYEE`` and the owner of every item below. Holds ``PR_CONTENT_EDIT``
    #: and **not** ``PR_CONTENT_CANCEL`` - the member case.
    owner: User
    #: ``EMPLOYEE``, related to nothing. Holds exactly what the owner holds,
    #: which is what makes the refusal about responsibility rather than about
    #: capability.
    stranger: User
    brand: PrBrand
    channel: PrChannel

    def act_as(self, user: User) -> None:
        self.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
            user_id=user.id, full_name=user.full_name, role=user.role, active=user.active
        )

    def actor(self, user: User) -> Actor:
        return Actor(user_id=user.id, full_name=user.full_name, role=user.role)

    async def content(
        self,
        title: str,
        *,
        priority: PrPriority = PrPriority.NORMAL,
        owner: User | None = None,
        created_at: datetime | None = None,
        planned: datetime | None = None,
    ) -> PrContentItem:
        services = build_pr_services(self.session, self.settings)
        snapshot = await services.content.create_content(
            actor=self.actor(self.manager),
            request_id=uuid.uuid4(),
            command=CreateContentCommand(
                title=title,
                brand_id=self.brand.id,
                owner_user_id=(owner or self.owner).id,
                priority=priority,
                script_text="Nội dung.",
                targets=(
                    ContentTargetSpec(
                        channel_id=self.channel.id,
                        distribution_mode=PrDistributionMode.ORGANIC,
                    ),
                ),
            ),
        )
        row = snapshot.content
        if created_at is not None:
            row.created_at = created_at
        row.planned_publish_at = planned
        await self.session.flush()
        return row

    def board(self, **params: object) -> dict[str, object]:
        response = self.client.get("/api/pr/contents/board", params=params)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def titles(self, **params: object) -> list[str]:
        """Board titles **in the order the server returned them**."""
        body = self.board(**params)
        return [item["title"] for item in body["items"]]  # type: ignore[index,union-attr]

    def set_priority(self, content_id: uuid.UUID, priority: str):  # type: ignore[no-untyped-def]
        return self.client.patch(
            f"/api/pr/contents/{content_id}/priority", json={"priority": priority}
        )

    async def notify(
        self,
        user: User,
        *,
        title: str = "Nội dung đã được duyệt",
        body: str = "“Bài A” đã được Trưởng phòng duyệt.",
        target: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> UserNotification:
        row = await UserNotificationService(self.session).record(
            recipient_user_id=user.id,
            event=NotificationEvent.PR_CONTENT_APPROVED,
            title=title,
            body=body,
            idempotency_key=f"test:{uuid.uuid4()}",
            target_kind="pr_content" if target else None,
            target_id=target,
        )
        assert row is not None
        if created_at is not None:
            row.created_at = created_at
            await self.session.flush()
        return row


@pytest_asyncio.fixture
async def world(session: AsyncSession) -> AsyncIterator[World]:
    manager = User(full_name="Ha Trưởng Phòng", role=Role.ADMIN)
    owner = User(full_name="Nguyễn A", role=Role.EMPLOYEE)
    stranger = User(full_name="Trần B", role=Role.EMPLOYEE)
    brand = PrBrand(code="BRND-A", name="Apexmed")
    platform = PrPlatform(code="TIKTOK", name="TikTok")
    session.add_all([manager, owner, stranger, brand, platform])
    await session.flush()
    await tag_pr(session, [manager, owner, stranger])  # untagged sees no stream

    settings = Settings(web_base_url="https://pr.example.com", web_cookie_secure=False)
    audit = AuditService(session)
    capabilities = PrCapabilityService(session, audit)
    granter = Actor(user_id=manager.id, full_name=manager.full_name, role=Role.OWNER)
    channels = PrChannelService(session, audit, capabilities, PrCodeService(session, settings))
    channel = await channels.create_channel(
        actor=granter,
        request_id=uuid.uuid4(),
        command=CreateChannelCommand(
            name="Apexmed TikTok",
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
            manager=manager,
            owner=owner,
            stranger=stranger,
            brand=brand,
            channel=channel,
        )
        built.act_as(manager)
        yield built
    app.dependency_overrides.clear()


# =============================================================================
# 61. The vocabulary
# =============================================================================


async def test_61a_new_content_defaults_to_normal(world: World) -> None:
    """Nobody has to choose a priority for ordinary work."""
    row = await world.content("Bài thường")
    assert row.priority is PrPriority.NORMAL


@pytest.mark.parametrize(
    "priority", [PrPriority.CRITICAL, PrPriority.URGENT, PrPriority.HIGH, PrPriority.NORMAL]
)
async def test_61b_create_accepts_every_level(world: World, priority: PrPriority) -> None:
    row = await world.content(f"Bài {priority.value}", priority=priority)
    assert row.priority is priority


async def test_61c_there_is_no_low_priority() -> None:
    """The explicit product decision, pinned so it cannot drift back in.

    ``NORMAL`` is the baseline and nothing sits below it. This is a test rather
    than a comment because "add a LOW for backlog items" is a reasonable-sounding
    request that would strand rows under a code the enum no longer has - see
    revision 0023, which had to move exactly such rows.
    """
    assert {member.value for member in PrPriority} == {"NORMAL", "HIGH", "URGENT", "CRITICAL"}
    assert not any(
        name in {member.name for member in PrPriority}
        for name in ("LOW", "MINOR", "OPTIONAL", "BACKLOG")
    )


async def test_61d_rank_orders_critical_highest(world: World) -> None:
    """The rank is derived from the enum, and it runs the way the product says."""
    assert (
        PRIORITY_RANK[PrPriority.CRITICAL]
        > PRIORITY_RANK[PrPriority.URGENT]
        > PRIORITY_RANK[PrPriority.HIGH]
        > PRIORITY_RANK[PrPriority.NORMAL]
    )


async def test_61e_an_invalid_priority_is_refused_with_the_options(world: World) -> None:
    """A 422 naming what is allowed, not a silent fallback to ``NORMAL``."""
    row = await world.content("Bài A")
    response = world.set_priority(row.id, "SUPER_URGENT")
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["field"] == "priority"
    assert details["allowed"] == ["CRITICAL", "HIGH", "NORMAL", "URGENT"]
    await world.session.refresh(row)
    assert row.priority is PrPriority.NORMAL


async def test_61f_the_filter_refuses_an_unknown_level(world: World) -> None:
    await world.content("Bài A")
    response = world.client.get("/api/pr/contents/board", params={"priority": "WHENEVER"})
    assert response.status_code == 422, response.text


# =============================================================================
# 62. Ordering, and the fact that it happens before the page is cut
# =============================================================================


async def test_62a_the_board_returns_most_urgent_first(world: World) -> None:
    base = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
    # Created oldest-first in *ascending* urgency, so ``created_at DESC`` alone
    # would return the exact reverse of the expected order.
    await world.content("Bình thường", priority=PrPriority.NORMAL, created_at=base)
    await world.content("Ưu tiên", priority=PrPriority.HIGH, created_at=base + timedelta(hours=1))
    await world.content("Gấp", priority=PrPriority.URGENT, created_at=base + timedelta(hours=2))
    await world.content(
        "Rất gấp", priority=PrPriority.CRITICAL, created_at=base + timedelta(hours=3)
    )

    assert world.titles(scope="ALL", group="PREPARATION") == [
        "Rất gấp",
        "Gấp",
        "Ưu tiên",
        "Bình thường",
    ]


async def test_62b_priority_beats_pagination(world: World) -> None:
    """**The assertion this step exists for.**

    Twelve ``NORMAL`` items and one ``CRITICAL``, and the critical one is created
    *last* - so under the old ``created_at DESC`` it led anyway, which would make
    a naive version of this test pass for the wrong reason. It is therefore
    created with the **oldest** timestamp: last in the previous ordering, and
    first in this one.

    Then the page size is five. A browser that sorted the page it was handed
    would sort five ``NORMAL`` rows and leave the critical item on page three,
    where somebody looking at "Rất gấp" work would never see it. Sorting in SQL
    before ``LIMIT`` is what puts it on page one.
    """
    base = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    for index in range(12):
        await world.content(f"Thường {index:02d}", created_at=base + timedelta(hours=index + 1))
    await world.content("Rất gấp", priority=PrPriority.CRITICAL, created_at=base)

    first_page = world.titles(scope="ALL", group="PREPARATION", limit=5, offset=0)
    assert first_page[0] == "Rất gấp"

    # And it is on page one *only* - not duplicated onto a later one, which is
    # what an unstable sort under OFFSET produces.
    later = world.titles(scope="ALL", group="PREPARATION", limit=5, offset=5)
    later += world.titles(scope="ALL", group="PREPARATION", limit=5, offset=10)
    assert "Rất gấp" not in later
    assert len(set(first_page + later)) == 13


async def test_62c_the_planned_date_breaks_a_priority_tie(world: World) -> None:
    """Within one level, soonest deadline first - and no date sorts last.

    "No planned date" is not "the most urgent deadline", which is what an
    unqualified ``ASC`` would make it on SQLite.
    """
    base = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    await world.content("Không hạn", priority=PrPriority.URGENT, created_at=base)
    await world.content(
        "Hạn muộn",
        priority=PrPriority.URGENT,
        created_at=base + timedelta(hours=1),
        planned=base + timedelta(days=9),
    )
    await world.content(
        "Hạn sớm",
        priority=PrPriority.URGENT,
        created_at=base + timedelta(hours=2),
        planned=base + timedelta(days=2),
    )

    assert world.titles(scope="ALL", group="PREPARATION") == ["Hạn sớm", "Hạn muộn", "Không hạn"]


async def test_62d_equal_rows_page_deterministically(world: World) -> None:
    """No row is shown twice or skipped when everything ties.

    Twelve items sharing a priority, a creation instant *and* a null planned
    date - every sort key equal but the last one. Without the ``id`` tiebreaker
    the database may order them differently per query, and paging through would
    repeat some rows and lose others. Read three pages and count.
    """
    same = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    for index in range(12):
        await world.content(f"Giống nhau {index:02d}", created_at=same)

    seen: list[str] = []
    for offset in (0, 4, 8):
        seen += world.titles(scope="ALL", group="PREPARATION", limit=4, offset=offset)
    assert len(seen) == 12
    assert len(set(seen)) == 12


async def test_62e_priority_does_not_move_content_between_groups(world: World) -> None:
    """Ordering is not routing. A critical item sits where its stage puts it."""
    row = await world.content("Rất gấp", priority=PrPriority.CRITICAL)
    row.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.session.flush()

    assert world.titles(scope="ALL", group="EDITORIAL_REVIEW") == ["Rất gấp"]
    assert world.titles(scope="ALL", group="PREPARATION") == []


# =============================================================================
# 63. The filter, and what it composes with
# =============================================================================


async def test_63a_the_filter_selects_one_level_exactly(world: World) -> None:
    """Equality, not "this and everything above it"."""
    await world.content("Rất gấp", priority=PrPriority.CRITICAL)
    await world.content("Gấp", priority=PrPriority.URGENT)
    await world.content("Bình thường")

    assert world.titles(scope="ALL", group="PREPARATION", priority="URGENT") == ["Gấp"]
    assert world.titles(scope="ALL", group="PREPARATION", priority="NORMAL") == ["Bình thường"]


async def test_63b_the_filter_narrows_before_the_page(world: World) -> None:
    """``total`` counts the filtered set, and the page is cut from it."""
    base = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    for index in range(9):
        await world.content(f"Thường {index}", created_at=base + timedelta(hours=index))
    await world.content("Rất gấp", priority=PrPriority.CRITICAL, created_at=base)

    body = world.board(scope="ALL", group="PREPARATION", priority="CRITICAL", limit=5)
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["Rất gấp"]  # type: ignore[index,union-attr]


async def test_63c_the_filter_composes_with_the_group(world: World) -> None:
    critical_review = await world.content("Rất gấp chờ duyệt", priority=PrPriority.CRITICAL)
    critical_review.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.content("Rất gấp chuẩn bị", priority=PrPriority.CRITICAL)
    await world.session.flush()

    assert world.titles(scope="ALL", group="EDITORIAL_REVIEW", priority="CRITICAL") == [
        "Rất gấp chờ duyệt"
    ]
    assert world.titles(scope="ALL", group="PREPARATION", priority="CRITICAL") == [
        "Rất gấp chuẩn bị"
    ]


async def test_63d_the_filter_composes_with_every_other_dimension(world: World) -> None:
    """Conjunction throughout: each added filter narrows what the last left."""
    row = await world.content(
        "Rất gấp của Nguyễn A",
        priority=PrPriority.CRITICAL,
        created_at=datetime(2026, 8, 10, 3, 0, tzinfo=UTC),
    )
    await world.content("Rất gấp của người khác", priority=PrPriority.CRITICAL, owner=world.manager)
    await world.content("Bình thường của Nguyễn A")

    common: dict[str, object] = {
        "scope": "ALL",
        "group": "PREPARATION",
        "priority": "CRITICAL",
        "responsible_user_id": str(world.owner.id),
    }
    assert world.titles(**common) == ["Rất gấp của Nguyễn A"]
    assert world.titles(**common, channel_id=str(world.channel.id)) == ["Rất gấp của Nguyễn A"]
    assert world.titles(**common, search="Nguyễn") == ["Rất gấp của Nguyễn A"]
    assert world.titles(**common, stage="IDEA") == ["Rất gấp của Nguyễn A"]
    assert (
        world.titles(**common, date_from="2026-08-10", date_to="2026-08-10")[0]
        == "Rất gấp của Nguyễn A"
    )
    # And the one that must exclude it, so the clause is proved to be applied.
    assert world.titles(**common, date_from="2026-09-01", date_to="2026-09-02") == []
    assert row.priority is PrPriority.CRITICAL


async def test_63e_the_filter_does_not_change_the_tab_counts(world: World) -> None:
    """The tabs describe the filtered set; they are how somebody leaves a group.

    The priority filter is part of that set - unlike ``group``, which the counts
    deliberately ignore - so filtering to *Rất gấp* makes the tabs count critical
    items rather than making four of them read zero.
    """
    critical = await world.content("Rất gấp", priority=PrPriority.CRITICAL)
    critical.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.content("Bình thường")
    await world.session.flush()

    body = world.board(scope="ALL", group="PREPARATION", priority="CRITICAL")
    counts = {row["stage"]: row["count"] for row in body["stage_counts"]}  # type: ignore[index,union-attr]
    assert counts["HEAD_REVIEW"] == 1
    assert counts["IDEA"] == 0
    assert body["total"] == 0


# =============================================================================
# 64. Who may retriage
# =============================================================================


async def test_64a_management_may_retriage_anything(world: World) -> None:
    row = await world.content("Bài A")
    world.act_as(world.manager)

    response = world.set_priority(row.id, "CRITICAL")
    assert response.status_code == 200, response.text
    assert response.json()["content"]["priority"] == "CRITICAL"
    await world.session.refresh(row)
    assert row.priority is PrPriority.CRITICAL


async def test_64b_a_responsible_member_may_retriage_their_own(world: World) -> None:
    row = await world.content("Bài A")
    world.act_as(world.owner)

    response = world.set_priority(row.id, "URGENT")
    assert response.status_code == 200, response.text
    await world.session.refresh(row)
    assert row.priority is PrPriority.URGENT


async def test_64c_an_unrelated_member_may_not(world: World) -> None:
    """Same capability as the owner, different answer - the check is ownership.

    The stranger holds ``PR_CONTENT_EDIT`` exactly as the owner does, which is
    what makes this test about the responsibility clause rather than about a
    role. Hidden controls are not the enforcement; this calls the route.
    """
    row = await world.content("Bài A", priority=PrPriority.HIGH)
    world.act_as(world.stranger)

    response = world.set_priority(row.id, "CRITICAL")
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "not_responsible"
    await world.session.refresh(row)
    assert row.priority is PrPriority.HIGH


async def test_64d_retriaging_is_offered_only_where_it_is_allowed(world: World) -> None:
    """``available_actions`` and the write agree, because they share a predicate."""
    row = await world.content("Bài A")

    def offered() -> bool:
        response = world.client.get(f"/api/pr/contents/{row.id}/available-actions")
        assert response.status_code == 200, response.text
        return any(
            action["action"] == "SET_PRIORITY" for action in response.json()["available_actions"]
        )

    world.act_as(world.manager)
    assert offered() is True
    world.act_as(world.owner)
    assert offered() is True
    world.act_as(world.stranger)
    assert offered() is False


async def test_64e_retriaging_stays_available_past_the_editable_stages(world: World) -> None:
    """The gap this step closed: escalation matters most after work starts.

    ``revise_content`` refuses outside ``EDITABLE_STAGES``, and priority used to
    ride on it - so a piece in production could not be marked urgent, which is
    precisely when somebody needs to.
    """
    row = await world.content("Bài A")
    row.workflow_stage = PrWorkflowStage.PRODUCTION
    await world.session.flush()
    world.act_as(world.manager)

    assert world.set_priority(row.id, "CRITICAL").status_code == 200
    await world.session.refresh(row)
    assert row.priority is PrPriority.CRITICAL


async def test_64f_retriaging_writes_no_new_version(world: World) -> None:
    """Priority is queue metadata, not a draft of the script."""
    services = build_pr_services(world.session, world.settings)
    row = await world.content("Bài A")
    before = await services.content.current_version(row.id)
    assert before is not None

    world.act_as(world.manager)
    assert world.set_priority(row.id, "URGENT").status_code == 200

    after = await services.content.current_version(row.id)
    assert after is not None
    assert after.version_no == before.version_no


async def test_64g_retriaging_does_not_move_the_stage(world: World) -> None:
    row = await world.content("Bài A")
    row.workflow_stage = PrWorkflowStage.HEAD_REVIEW
    await world.session.flush()
    world.act_as(world.manager)

    assert world.set_priority(row.id, "CRITICAL").status_code == 200
    await world.session.refresh(row)
    assert row.workflow_stage is PrWorkflowStage.HEAD_REVIEW


async def test_64h_a_missing_content_item_is_a_404(world: World) -> None:
    world.act_as(world.manager)
    assert world.set_priority(uuid.uuid4(), "URGENT").status_code == 404


# =============================================================================
# 65. The audit trail
# =============================================================================


async def _priority_events(session: AsyncSession) -> list[AuditLog]:
    rows = await session.execute(
        select(AuditLog)
        .where(AuditLog.action == "pr.content.priority_changed")
        .order_by(AuditLog.created_at.asc())
    )
    return list(rows.scalars().all())


async def test_65a_a_change_is_audited_with_both_levels(world: World) -> None:
    row = await world.content("Bài A", priority=PrPriority.HIGH)
    world.act_as(world.manager)
    assert world.set_priority(row.id, "CRITICAL").status_code == 200

    events = await _priority_events(world.session)
    assert len(events) == 1
    assert events[0].entity_type == "pr_content_item"
    assert events[0].entity_id == str(row.id)
    assert events[0].actor_user_id == world.manager.id
    assert events[0].before_data == {"priority": "HIGH"}
    assert events[0].after_data == {"content_code": row.code, "priority": "CRITICAL"}


async def test_65b_the_audit_row_carries_no_content_body(world: World) -> None:
    """Codes and levels, never the script. Versions are where the text lives."""
    row = await world.content("Bài A")
    world.act_as(world.manager)
    assert world.set_priority(row.id, "URGENT").status_code == 200

    payload = str((await _priority_events(world.session))[0].after_data)
    assert "Nội dung." not in payload


async def test_65c_setting_the_same_level_records_nothing(world: World) -> None:
    """An audit trail full of "URGENT -> URGENT" is one nobody reads."""
    row = await world.content("Bài A", priority=PrPriority.URGENT)
    world.act_as(world.manager)
    assert world.set_priority(row.id, "URGENT").status_code == 200

    assert await _priority_events(world.session) == []


async def test_65d_a_refused_change_records_nothing(world: World) -> None:
    row = await world.content("Bài A")
    world.act_as(world.stranger)
    assert world.set_priority(row.id, "CRITICAL").status_code == 403

    assert await _priority_events(world.session) == []


async def test_65e_a_priority_change_sends_no_notification(world: World) -> None:
    """The default, and the reason is noise.

    A manager retriaging a backlog would otherwise produce one notification per
    item. The change is audited and visible on the card; that is enough.
    """
    row = await world.content("Bài A")
    world.act_as(world.manager)
    assert world.set_priority(row.id, "CRITICAL").status_code == 200

    rows = await world.session.execute(select(UserNotification))
    assert list(rows.scalars().all()) == []


# =============================================================================
# 66. The notification centre: ownership
# =============================================================================


async def test_66a_a_user_lists_their_own_notifications(world: World) -> None:
    await world.notify(world.owner, title="Của Nguyễn A")
    world.act_as(world.owner)

    response = world.client.get("/api/notifications")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["title"] for item in body["items"]] == ["Của Nguyễn A"]
    assert body["unread_count"] == 1


async def test_66b_a_user_never_sees_another_persons(world: World) -> None:
    await world.notify(world.owner, title="Của Nguyễn A")
    world.act_as(world.stranger)

    body = world.client.get("/api/notifications").json()
    assert body["items"] == []
    assert body["unread_count"] == 0


async def test_66c_reading_another_persons_by_id_is_a_404(world: World) -> None:
    """Not a 403: that would confirm the notification exists."""
    row = await world.notify(world.owner)
    world.act_as(world.stranger)

    response = world.client.post(f"/api/notifications/{row.id}/read")
    assert response.status_code == 404, response.text
    await world.session.refresh(row)
    assert row.read_at is None


async def test_66d_an_invented_id_is_the_same_404(world: World) -> None:
    """Indistinguishable from a stranger's, which is the point."""
    world.act_as(world.stranger)
    assert world.client.post(f"/api/notifications/{uuid.uuid4()}/read").status_code == 404


async def test_66e_mark_all_read_touches_only_the_caller(world: World) -> None:
    mine = await world.notify(world.owner)
    theirs = await world.notify(world.stranger)
    world.act_as(world.owner)

    response = world.client.post("/api/notifications/read-all")
    assert response.status_code == 200, response.text
    assert response.json() == {"marked": 1, "unread_count": 0}

    await world.session.refresh(mine)
    await world.session.refresh(theirs)
    assert mine.read_at is not None
    assert theirs.read_at is None, "another person's bell must never be cleared"


# =============================================================================
# 67. The notification centre: behaviour
# =============================================================================


async def test_67a_the_unread_count_is_its_own_cheap_endpoint(world: World) -> None:
    for _ in range(3):
        await world.notify(world.owner)
    world.act_as(world.owner)

    assert world.client.get("/api/notifications/unread-count").json() == {"unread_count": 3}


async def test_67b_marking_one_read_decrements_the_count(world: World) -> None:
    first = await world.notify(world.owner)
    await world.notify(world.owner)
    world.act_as(world.owner)

    response = world.client.post(f"/api/notifications/{first.id}/read")
    assert response.status_code == 200, response.text
    assert response.json()["read_at"] is not None
    assert world.client.get("/api/notifications/unread-count").json() == {"unread_count": 1}


async def test_67c_marking_read_twice_is_not_an_error(world: World) -> None:
    """And the first-seen timestamp is not moved by the second call."""
    row = await world.notify(world.owner)
    world.act_as(world.owner)

    first = world.client.post(f"/api/notifications/{row.id}/read").json()
    second = world.client.post(f"/api/notifications/{row.id}/read")
    assert second.status_code == 200, second.text
    assert second.json()["read_at"] == first["read_at"]


async def test_67d_notifications_come_back_newest_first(world: World) -> None:
    base = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    await world.notify(world.owner, title="Cũ nhất", created_at=base)
    await world.notify(world.owner, title="Giữa", created_at=base + timedelta(hours=1))
    await world.notify(world.owner, title="Mới nhất", created_at=base + timedelta(hours=2))
    world.act_as(world.owner)

    body = world.client.get("/api/notifications").json()
    assert [item["title"] for item in body["items"]] == ["Mới nhất", "Giữa", "Cũ nhất"]


async def test_67e_the_list_is_bounded_and_pages(world: World) -> None:
    """A bell never loads an entire history, however it is asked."""
    base = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    for index in range(8):
        await world.notify(
            world.owner, title=f"Thông báo {index}", created_at=base + timedelta(hours=index)
        )
    world.act_as(world.owner)

    first = world.client.get("/api/notifications", params={"limit": 3}).json()
    assert len(first["items"]) == 3
    assert first["has_more"] is True
    # The badge counts the whole inbox, not the page.
    assert first["unread_count"] == 8

    last = world.client.get("/api/notifications", params={"limit": 3, "offset": 6}).json()
    assert len(last["items"]) == 2
    assert last["has_more"] is False

    # And the ceiling is the server's, not the caller's.
    assert world.client.get("/api/notifications", params={"limit": 500}).status_code == 422


async def test_67f_a_notification_deep_links_to_its_content(world: World) -> None:
    """Structured target, never a URL and never a uuid in the words."""
    row = await world.content("Bài A")
    notification = await world.notify(world.owner, target=row.id)
    world.act_as(world.owner)

    item = world.client.get("/api/notifications").json()["items"][0]
    assert item["target_kind"] == "pr_content"
    assert item["target_id"] == str(row.id)
    assert str(row.id) not in item["title"]
    assert str(row.id) not in item["body"]
    assert notification.target_id == row.id


async def test_67g_an_actor_with_no_user_row_gets_an_empty_bell(world: World) -> None:
    """The bootstrap owner. An empty inbox, not an error box on every page."""
    world.client.app.dependency_overrides[get_current_web_actor] = lambda: Actor(  # type: ignore[attr-defined]
        user_id=None, full_name="Bootstrap", role=Role.OWNER
    )
    await world.notify(world.owner)

    assert world.client.get("/api/notifications").json()["items"] == []
    assert world.client.get("/api/notifications/unread-count").json() == {"unread_count": 0}
    assert world.client.post("/api/notifications/read-all").json() == {
        "marked": 0,
        "unread_count": 0,
    }


async def test_67h_recording_the_same_event_twice_makes_one_row(world: World) -> None:
    """Idempotent on the key, like the outbox it is written beside."""
    service = UserNotificationService(world.session)
    for _ in range(2):
        await service.record(
            recipient_user_id=world.owner.id,
            event=NotificationEvent.PR_CONTENT_APPROVED,
            title="Nội dung đã được duyệt",
            body="“Bài A” đã được duyệt.",
            idempotency_key="pr_content_approved:fixed",
        )

    rows = await world.session.execute(
        select(UserNotification).where(UserNotification.recipient_user_id == world.owner.id)
    )
    assert len(list(rows.scalars().all())) == 1
