"""The account screen's figures (0045): a small Ads + PR world, counted by month.

* every figure of :class:`MemberStats`, in the month asked for and only that
  month - a Vietnamese month, so 2026-08-31T18:00Z is September;
* one grouped query per figure, whether for one person or for the whole team;
* who may see the member list: OWNER (everyone), ADMIN (their units), the Ads
  HEAD (Ads), and nobody else (403 ``account_members_forbidden``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.engine import Connection

from meobot.application.account.stats_service import (
    AccountStatsService,
    MemberStats,
    resolve_month,
)
from meobot.db.models.order import Order, OrderEvent, OrderNode
from meobot.db.models.org_unit import OrgUnit
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.domain.account.errors import AccountValidationError
from meobot.domain.identity.models import Role
from meobot.domain.orders.models import (
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    OrderVideoType,
)
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrProductionArtifactType,
)
from meobot.domain.pr.work import PrWorkCountStatus
from meobot.domain.pr.work_results import PrWorkResultSource
from meobot.domain.units.models import UnitCode, UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_order_commands import person
from tests.unit.test_units_gating import seed_units, tag

HCM = ZoneInfo("Asia/Ho_Chi_Minh")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@dataclass
class Team:
    ads: OrgUnit
    head: User
    orderer: User
    writer: User
    editor: User
    admin: User


async def _order(
    world: World,
    team: Team,
    code: str,
    *,
    video_type: OrderVideoType,
    stage: OrderStage,
    submitted_at: str,
    points: str | None,
    completed_at: str | None = None,
) -> Order:
    order = Order(
        unit_id=team.ads.id,
        code=code,
        title=f"Order {code}",
        video_type=video_type,
        order_content="brief",
        design_link="https://example.com/design" if video_type is OrderVideoType.D else None,
        owner_user_id=team.orderer.id,
        stage=stage,
        submitted_at=at(submitted_at),
        completed_at=None if completed_at is None else at(completed_at),
        video_kind_points=None if points is None else Decimal(points),
        video_kind_name=None if points is None else "Short video",
    )
    world.session.add(order)
    await world.session.flush()
    return order


async def _node(
    world: World,
    order: Order,
    node_type: OrderNodeType,
    status: OrderNodeStatus,
    assignee: User | None,
    *,
    approved_at: str | None = None,
    revisions: int = 0,
) -> OrderNode:
    node = OrderNode(
        order_id=order.id,
        node_type=node_type,
        status=status,
        assignee_user_id=None if assignee is None else assignee.id,
        approved_at=None if approved_at is None else at(approved_at),
        revision_count=revisions,
    )
    world.session.add(node)
    await world.session.flush()
    return node


async def _event(
    world: World, order: Order, node: OrderNode, kind: OrderEventKind, actor: User, when: str
) -> None:
    world.session.add(
        OrderEvent(
            order_id=order.id,
            node_id=node.id,
            kind=kind,
            actor_user_id=actor.id,
            created_at=at(when),
        )
    )
    await world.session.flush()


async def build(world: World) -> Team:
    units = await seed_units(world)
    ads = units[UnitCode.ADS]
    head = await person(world, "Trưởng phòng Ads", Role.TEAM_LEAD)
    orderer = await person(world, "Marketing Tuấn")
    writer = await person(world, "Biên tập B")
    editor = await person(world, "Editor C")
    admin = await person(world, "Quản trị PR", Role.ADMIN)
    await tag(world, ads, head, UnitMemberRole.HEAD)
    await tag(world, ads, orderer, UnitMemberRole.ORDERER, member_code="TUAN")
    await tag(world, ads, writer, UnitMemberRole.BIEN_TAP)
    await tag(world, ads, editor, UnitMemberRole.DUNG)
    await tag(world, units[UnitCode.PR], admin, UnitMemberRole.MEMBER)
    team = Team(ads=ads, head=head, orderer=orderer, writer=writer, editor=editor, admin=admin)

    # --- Ads -------------------------------------------------------------------
    a = await _order(
        world,
        team,
        "TUAN-BTD-260902-01",
        video_type=OrderVideoType.BTD,
        stage=OrderStage.COMPLETED,
        submitted_at="2026-09-02T03:00:00",
        completed_at="2026-09-20T03:00:00",
        points="2.5",
    )
    a_script = await _node(
        world,
        a,
        OrderNodeType.BIEN_TAP,
        OrderNodeStatus.HOAN_THANH,
        writer,
        approved_at="2026-09-05T03:00:00",
    )
    # 18:00Z on 31 August is 01:00 on 1 September in Hồ Chí Minh City.
    a_design = await _node(
        world,
        a,
        OrderNodeType.THIET_KE,
        OrderNodeStatus.HOAN_THANH,
        editor,
        approved_at="2026-08-31T18:00:00",
        revisions=1,
    )
    # 17:30Z on 30 September is already October locally.
    await _node(
        world,
        a,
        OrderNodeType.DUNG,
        OrderNodeStatus.HOAN_THANH,
        editor,
        approved_at="2026-09-30T17:30:00",
    )
    a_link = await _node(
        world,
        a,
        OrderNodeType.GAN_LINK,
        OrderNodeStatus.HOAN_THANH,
        editor,
        approved_at="2026-09-21T03:00:00",
        revisions=1,
    )
    await _event(world, a, a_design, OrderEventKind.NODE_RETURNED, head, "2026-09-03T03:00:00")
    await _event(world, a, a_link, OrderEventKind.VIDEO_RETURNED, writer, "2026-09-25T03:00:00")
    await _event(world, a, a_script, OrderEventKind.NODE_RETURNED, head, "2026-08-15T03:00:00")
    # An approval is not a return.
    await _event(world, a, a_script, OrderEventKind.NODE_APPROVED, head, "2026-09-05T03:00:00")

    b = await _order(
        world,
        team,
        "TUAN-D-260820-01",
        video_type=OrderVideoType.D,
        stage=OrderStage.DUNG,
        submitted_at="2026-08-20T03:00:00",
        points="1",
    )
    await _node(world, b, OrderNodeType.DUNG, OrderNodeStatus.DANG_LAM, editor)

    c = await _order(
        world,
        team,
        "TUAN-D-260910-01",
        video_type=OrderVideoType.D,
        stage=OrderStage.CANCELLED,
        submitted_at="2026-09-10T03:00:00",
        points="1",
    )
    await _node(world, c, OrderNodeType.DUNG, OrderNodeStatus.DANG_LAM, editor)

    d = await _order(
        world,
        team,
        "TUAN-BTD-260911-01",
        video_type=OrderVideoType.BTD,
        stage=OrderStage.THIET_KE,
        submitted_at="2026-09-11T03:00:00",
        points=None,
    )
    await _node(
        world,
        d,
        OrderNodeType.BIEN_TAP,
        OrderNodeStatus.HOAN_THANH,
        writer,
        approved_at="2026-09-12T03:00:00",
    )
    await _node(world, d, OrderNodeType.THIET_KE, OrderNodeStatus.CHO_DUYET, editor)

    # --- PR --------------------------------------------------------------------
    september = await world.content()
    september_id = september.id
    august = await world.content()
    august_id = august.id
    await world.session.execute(
        update(PrContentItem)
        .where(PrContentItem.id == september_id)
        .values(created_at=at("2026-09-15T03:00:00"))
    )
    await world.session.execute(
        update(PrContentItem)
        .where(PrContentItem.id == august_id)
        .values(created_at=at("2026-08-10T03:00:00"))
    )
    version_id = await world.session.scalar(
        select(PrContentVersion.id).where(PrContentVersion.content_id == september_id)
    )
    assert version_id is not None
    for number, when in ((1, "2026-09-16T03:00:00"), (2, "2026-10-02T03:00:00")):
        world.session.add(
            PrProductionSubmission(
                content_id=september_id,
                content_version_id=version_id,
                submission_no=number,
                producer_user_id=world.member.id,
                submitted_by_user_id=world.member.id,
                artifact_type=PrProductionArtifactType.DRIVE_LINK,
                location="https://drive.example.com/file",
                created_at=at(when),
            )
        )
    for when in ("2026-09-17T03:00:00", "2026-08-17T03:00:00"):
        world.session.add(
            PrApprovalEvent(
                content_id=september_id,
                approval_stage=PrApprovalStage.TEAM_LEAD_REVIEW,
                reviewer_user_id=world.lead.id,
                decision=PrApprovalDecision.APPROVED,
                version_reviewed=1,
                decided_at=at(when),
            )
        )

    # --- the KPI ledger ------------------------------------------------------------
    for key, status, counted_at in (
        ("a", PrWorkCountStatus.COUNTED, "2026-09-05T03:00:00"),
        ("b", PrWorkCountStatus.COUNTED, "2026-09-12T03:00:00"),
        ("c", PrWorkCountStatus.PENDING, None),
        ("d", PrWorkCountStatus.COUNTED, "2026-08-12T03:00:00"),
    ):
        world.session.add(
            PrWorkResult(
                work_item_id=uuid.uuid4(),
                user_id=editor.id,
                quantity=Decimal(1),
                source_type=PrWorkResultSource.ORDER,
                source_key=f"order:{key}",
                status=status,
                reported_by_user_id=editor.id,
                reported_at=at("2026-09-01T03:00:00"),
                counted_at=None if counted_at is None else at(counted_at),
            )
        )
    await world.session.flush()
    return team


def _month(label: str) -> Any:
    return resolve_month(label, tz=HCM)


# --- the figures -----------------------------------------------------------------


async def test_01_every_figure_for_september(world: World) -> None:
    team = await build(world)
    service = AccountStatsService(world.session)
    ids = [team.writer.id, team.editor.id, team.orderer.id, world.owner.id, world.member.id]
    stats = await service.stats_for([*ids, world.lead.id], _month("2026-09"))

    assert stats[team.writer.id] == MemberStats(
        month="2026-09", points=2.5, nodes_done=2, on_time_rate=1.0
    )
    assert stats[team.editor.id] == MemberStats(
        month="2026-09",
        points=2.5,  # the design node only: the edit closed in October locally
        nodes_done=1,
        nodes_in_progress=2,  # B's edit and D's design; C was cancelled
        revisions=2,  # a design return and a video return on their link node
        work_items_counted=2,
        on_time_rate=0.0,
    )
    assert stats[team.orderer.id] == MemberStats(
        month="2026-09", orders_created=3, orders_completed=1
    )
    assert stats[world.owner.id] == MemberStats(month="2026-09", pr_contents_owned=1)
    assert stats[world.member.id] == MemberStats(month="2026-09", pr_productions_done=1)
    assert stats[world.lead.id] == MemberStats(month="2026-09", pr_approvals=1)


async def test_02_other_months_count_their_own(world: World) -> None:
    team = await build(world)
    service = AccountStatsService(world.session)
    august = await service.stats_for(
        [team.writer.id, team.editor.id, team.orderer.id, world.owner.id], _month("2026-08")
    )
    assert august[team.writer.id].revisions == 1 and august[team.writer.id].nodes_done == 0
    assert august[team.writer.id].on_time_rate is None
    assert august[team.editor.id].nodes_done == 0
    assert august[team.editor.id].nodes_in_progress == 2  # "now", whatever the month
    assert august[team.editor.id].work_items_counted == 1
    assert august[team.orderer.id].orders_created == 1
    assert august[team.orderer.id].orders_completed == 0
    assert august[world.owner.id].pr_contents_owned == 1

    october = await service.stats_for_one(team.editor.id, _month("2026-10"))
    assert october.nodes_done == 1 and october.points == 2.5 and october.on_time_rate == 1.0
    assert october.month == "2026-10"


async def test_03_one_query_per_figure_whoever_is_asked(world: World) -> None:
    team = await build(world)
    service = AccountStatsService(world.session)
    statements: list[str] = []

    def count(_conn: Connection, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    engine = world.session.bind
    assert engine is not None
    sync_engine = engine.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        await service.stats_for([team.writer.id], _month("2026-09"))
        one = len(statements)
        statements.clear()
        everyone = (await world.session.scalars(select(User.id))).all()
        statements.clear()
        await service.stats_for(everyone, _month("2026-09"))
        many = len(statements)
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)
    assert one == many == 8


def test_04_months_are_vietnamese_and_validated() -> None:
    september = _month("2026-09")
    assert september.start == at("2026-08-31T17:00:00")
    assert september.end == at("2026-09-30T17:00:00")
    december = _month("2026-12")
    assert december.end == at("2026-12-31T17:00:00")
    assert resolve_month(None, tz=HCM, now=at("2026-09-30T18:00:00")).label == "2026-10"
    for bad in ("2026-13", "2026-9", "26-09", "september", "2026-00"):
        with pytest.raises(AccountValidationError) as caught:
            resolve_month(bad, tz=HCM)
        assert caught.value.code == "invalid_month"


# --- the HTTP surface -----------------------------------------------------------


def _members(world: World, **params: str) -> dict[str, Any]:
    response = world.client.get("/api/account/members", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _names(body: dict[str, Any]) -> set[str]:
    return {row["full_name"] for row in body["members"]}


async def test_05_who_sees_the_member_list(world: World) -> None:
    team = await build(world)
    ads_people = {"Trưởng phòng Ads", "Marketing Tuấn", "Biên tập B", "Editor C"}
    pr_people = {world.owner.full_name, world.lead.full_name, world.member.full_name, "Quản trị PR"}

    # OWNER: everyone; or one unit at a time.
    world.act_as(world.owner)
    assert _names(_members(world, month="2026-09")) == ads_people | pr_people
    assert _names(_members(world, unit="ADS")) == ads_people
    assert _names(_members(world, unit="PR")) == pr_people

    # ADMIN tagged PR: the PR members (the untagged count as PR), never Ads.
    world.act_as(team.admin)
    assert _names(_members(world)) == pr_people
    refused = world.client.get("/api/account/members", params={"unit": "ADS"})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "account_members_forbidden"

    # The Ads HEAD: the Ads members.
    world.act_as(team.head)
    assert _names(_members(world)) == ads_people
    assert _names(_members(world, unit="ads")) == ads_people
    assert world.client.get("/api/account/members", params={"unit": "PR"}).status_code == 403

    # Anybody else: refused, whatever they ask.
    for outsider in (team.writer, world.member, world.lead):
        world.act_as(outsider)
        response = world.client.get("/api/account/members")
        assert response.status_code == 403, outsider.full_name
        assert response.json()["error"]["details"]["reason"] == "account_members_forbidden"

    world.act_as(world.owner)
    bad = world.client.get("/api/account/members", params={"unit": "HR"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_unit"


async def test_06_member_rows_carry_the_month_asked_for(world: World) -> None:
    team = await build(world)
    world.act_as(team.head)
    september = _members(world, month="2026-09")
    assert september["month"] == "2026-09"
    rows = {row["full_name"]: row for row in september["members"]}
    writer = rows["Biên tập B"]
    assert writer["user_id"] == str(team.writer.id)
    assert writer["units"] == ["ADS"]
    assert writer["role_label"] == "Biên tập"
    assert writer["has_custom_password"] is False and writer["locked"] is False
    assert writer["last_login_at"] is None
    assert writer["stats"]["nodes_done"] == 2 and writer["stats"]["points"] == 2.5
    assert rows["Trưởng phòng Ads"]["role_label"] == "Trưởng phòng Ads"
    assert set(writer["stats"]) == {
        "month",
        "points",
        "nodes_done",
        "nodes_in_progress",
        "revisions",
        "orders_created",
        "orders_completed",
        "pr_contents_owned",
        "pr_productions_done",
        "pr_approvals",
        "work_items_counted",
        "on_time_rate",
    }

    august = {row["full_name"]: row for row in _members(world, month="2026-08")["members"]}
    assert august["Biên tập B"]["stats"]["nodes_done"] == 0
    assert august["Biên tập B"]["stats"]["revisions"] == 1

    bad = world.client.get("/api/account/members", params={"month": "2026-13"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_month"


async def test_07_my_own_card_and_figures(world: World) -> None:
    team = await build(world)
    world.act_as(team.writer)
    stats = world.client.get("/api/account/me/stats", params={"month": "2026-09"})
    assert stats.status_code == 200, stats.text
    assert stats.json()["nodes_done"] == 2 and stats.json()["on_time_rate"] == 1.0

    me = world.client.get("/api/account/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user_id"] == str(team.writer.id)
    assert body["role"] == "EMPLOYEE" and body["role_label"] == "Nhân viên"
    assert body["units"] == [
        {"code": "ADS", "label": "Phòng Ads", "role_label": "Biên tập", "member_code": None}
    ]
    assert body["must_change_password"] is False
    assert body["has_custom_password"] is False and body["password_changed_at"] is None
    assert len(body["stats"]["month"]) == 7

    renamed = world.client.patch("/api/account/profile", json={"full_name": "Biên tập Bảo"})
    assert renamed.status_code == 200 and renamed.json()["full_name"] == "Biên tập Bảo"
