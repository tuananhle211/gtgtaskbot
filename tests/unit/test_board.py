"""The shared board: both units on five phases, scoped, filtered, counted."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from meobot.db.models.order import Order
from meobot.db.models.org_unit import OrgUnitMember
from meobot.domain.board.models import ADS_STAGE_PHASE, PR_STAGE_PHASE, Phase
from meobot.domain.identity.models import Role
from meobot.domain.orders.labels import PROCESS_SEPARATOR as SEP
from meobot.domain.orders.models import OrderStage
from meobot.domain.pr.grants import GrantScope, PrGrantScopeMode
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.units.models import UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_order_commands import act, ads_world, create, inbox, node, person, tag
from tests.unit.test_units_gating import error_reason, seed_units

pytestmark = pytest.mark.asyncio


def test_01_every_stage_of_both_units_lands_in_a_phase() -> None:
    assert set(PR_STAGE_PHASE) == set(PrWorkflowStage)
    assert set(ADS_STAGE_PHASE) == set(OrderStage)
    assert PR_STAGE_PHASE[PrWorkflowStage.PRODUCTION] is Phase.PRODUCTION
    assert PR_STAGE_PHASE[PrWorkflowStage.INTERNAL_REVIEW] is Phase.FINAL_REVIEW
    assert ADS_STAGE_PHASE[OrderStage.ORDER_PENDING] is Phase.REVIEW
    assert ADS_STAGE_PHASE[OrderStage.GAN_LINK] is Phase.PRODUCTION


def tasks(world: World, **params: Any) -> dict[str, Any]:
    response = world.client.get("/api/board/tasks", params=params)
    assert response.status_code == 200, response.json()
    return response.json()


async def test_02_the_pr_board_shows_content_on_the_five_phases(world: World) -> None:
    item = await world.content()
    world.act_as(world.member)
    page = tasks(world, unit="PR")
    assert page["unit"] == "PR" and page["total"] == 1
    row = page["items"][0]
    assert row["code"] == item.code and row["unit"] == "PR"
    assert row["phase"] == "ORDER" and row["phase_label"] == "Order"
    assert row["status"] == "IDEA" and row["status_label"] == "Ý tưởng"
    assert [cell["key"] for cell in row["cells"]] == [
        "ORDER",
        "REVIEW",
        "PRODUCTION",
        "FINAL_REVIEW",
        "DONE",
    ]
    assert row["cells"][0]["is_current"] is True and row["cells"][0]["person_name"]
    assert row["detail_path"] == f"/tasks/{item.code}"
    assert [phase["value"] for phase in page["phases"]] == [
        "ORDER",
        "REVIEW",
        "PRODUCTION",
        "FINAL_REVIEW",
        "DONE",
    ]
    # A phase filter that matches nothing is honest about it.
    assert tasks(world, unit="PR", phase="DONE")["total"] == 0
    # The default unit for a PR-only person is PR.
    assert tasks(world)["unit"] == "PR"


async def test_03_the_ads_board_shows_orders_with_their_nodes(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, preassigned={"BIEN_TAP": str(ads.writer.id)})
    detail = act(world, ads.head, "/approve", detail)
    quick = create(
        world, ads, video_type="D", design_link="https://example.com/design", title="Nhanh"
    )

    world.act_as(ads.head)
    page = tasks(world, unit="ADS")
    assert page["total"] == 2
    by_code = {row["code"]: row for row in page["items"]}
    btd = by_code[detail["order"]["code"]]
    assert btd["phase"] == "PRODUCTION" and btd["status"] == "BIEN_TAP"
    # No video kind picked (the unit offers none): the kind reads as the process.
    assert btd["kind"] == "BTD" and btd["kind_label"] == f"Biên kịch{SEP}Design{SEP}Dựng"
    cells = {cell["key"]: cell for cell in btd["cells"]}
    # The chosen writer was handed the node and has not accepted it yet.
    assert [cell["key"] for cell in btd["cells"]] == ["BIEN_TAP", "THIET_KE", "DUNG", "FINAL"]
    assert cells["BIEN_TAP"]["status"] == "DA_GIAO" and cells["BIEN_TAP"]["is_current"]
    assert cells["BIEN_TAP"]["status_label"] == f"Đã giao {ads.writer.full_name}"
    assert cells["BIEN_TAP"]["person_name"] == ads.writer.full_name
    assert btd["status_label"] == f"Biên tập · Đã giao {ads.writer.full_name}"
    assert btd["state"] == "DA_GIAO"
    assert btd["current_person_name"] == ads.writer.full_name
    assert cells["THIET_KE"]["status_label"] == "Chưa tới"
    assert cells["FINAL"]["label"] == "Duyệt final" and cells["FINAL"]["status"] == "CHUA_TOI"
    assert btd["detail_path"] == f"/tasks/{btd['code']}"
    assert btd["owner_name"] == ads.orderer.full_name
    d = by_code[quick["order"]["code"]]
    assert d["phase"] == "REVIEW"
    assert d["status_label"] == f"Chờ {ads.head.full_name} duyệt order"
    assert d["state"] == "CHO_DUYET"
    assert d["current_person_name"] == ads.head.full_name
    assert {cell["key"]: cell["status"] for cell in d["cells"]}["BIEN_TAP"] == "BO_QUA"

    # Filters: phase, kind, status, search, awaiting-me (the head decides on D).
    assert tasks(world, unit="ADS", phase="REVIEW")["total"] == 1
    assert tasks(world, unit="ADS", kind="D")["total"] == 1
    assert tasks(world, unit="ADS", status="BIEN_TAP")["total"] == 1
    assert tasks(world, unit="ADS", q="nhanh")["total"] == 1
    assert tasks(world, unit="ADS", awaiting_me="true")["total"] == 1
    assert tasks(world, unit="ADS", owner=str(ads.orderer.id))["total"] == 2
    assert tasks(world, unit="ADS", assignee=str(ads.writer.id))["total"] == 1
    assert tasks(world, unit="ADS", limit=1)["total"] == 2
    assert len(tasks(world, unit="ADS", limit=1)["items"]) == 1
    assert len(tasks(world, unit="ADS", limit=1, offset=2)["items"]) == 0


async def test_04_each_person_sees_their_own_slice_of_ads(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, preassigned={"BIEN_TAP": str(ads.writer.id)})
    act(world, ads.head, "/approve", detail)
    create(world, ads, video_type="D", design_link="https://example.com/design")

    world.act_as(ads.writer)
    page = tasks(world, unit="ADS")
    assert page["total"] == 1 and page["items"][0]["code"] == detail["order"]["code"]
    assert tasks(world, unit="ADS", awaiting_me="true")["total"] == 1
    assert tasks(world, unit="ADS", mine="true")["total"] == 1

    world.act_as(ads.lead_tk)  # design lead: BTD orders pass through design
    assert tasks(world, unit="ADS")["total"] == 1
    world.act_as(ads.designer)  # not assigned anything yet
    assert tasks(world, unit="ADS")["total"] == 0
    world.act_as(ads.orderer)
    assert tasks(world, unit="ADS")["total"] == 2
    assert tasks(world, unit="ADS", awaiting_me="true")["total"] == 0

    # Outside: a PR person asking for Ads, and an Ads person asking for PR.
    world.act_as(ads.outsider)
    response = world.client.get("/api/board/tasks", params={"unit": "ADS"})
    assert response.status_code == 404 and error_reason(response.json()) == "unit_not_visible"
    world.act_as(ads.writer)
    assert world.client.get("/api/board/tasks", params={"unit": "PR"}).status_code == 404
    assert world.client.get("/api/board/tasks", params={"unit": "ALL"}).status_code == 404


async def test_05_priority_and_urgency_sort_and_filter(world: World) -> None:
    ads = await ads_world(world)
    old = create(world, ads, video_type="D", design_link="https://example.com/design", title="Cũ")
    new = create(world, ads, video_type="D", design_link="https://example.com/design", title="Mới")
    # Age the first one past the unit's limit.
    row = await world.session.get(Order, __import__("uuid").UUID(old["order"]["id"]))
    assert row is not None
    row.submitted_at = datetime.now(UTC) - timedelta(days=9)
    await world.session.flush()
    world.act_as(ads.head)
    page = tasks(world, unit="ADS")
    codes = [item["code"] for item in page["items"]]
    assert codes == [new["order"]["code"], old["order"]["code"]]
    assert [item["urgent"] for item in page["items"]] == [False, True]
    assert tasks(world, unit="ADS", urgent="true")["total"] == 1
    act(world, ads.head, "/priority", old, is_priority=True)
    page = tasks(world, unit="ADS")
    assert [item["code"] for item in page["items"]] == [old["order"]["code"], new["order"]["code"]]
    assert tasks(world, unit="ADS", priority="true")["total"] == 1


async def test_06_the_owner_sees_both_units_at_once(world: World) -> None:
    ads = await ads_world(world)
    create(world, ads, video_type="D", design_link="https://example.com/design")
    await world.content()
    world.act_as(world.owner)
    page = tasks(world, unit="ALL")
    assert page["unit"] == "ALL" and page["total"] == 2
    assert {row["unit"] for row in page["items"]} == {"PR", "ADS"}
    assert tasks(world, unit="ADS")["total"] == 1
    assert tasks(world, unit="PR")["total"] == 1


async def test_06b_the_merged_view_pages_without_repeats_or_gaps(world: World) -> None:
    ads = await ads_world(world)
    for _ in range(3):
        create(world, ads, video_type="D", design_link="https://example.com/design")
    for _ in range(2):
        await world.content()
    world.act_as(world.owner)
    everything = [row["code"] for row in tasks(world, unit="ALL", limit=20)["items"]]
    assert len(everything) == 5
    paged = [
        row["code"]
        for offset in (0, 2, 4)
        for row in tasks(world, unit="ALL", limit=2, offset=offset)["items"]
    ]
    assert paged == everything


async def test_07_the_dashboard_counts_what_the_table_shows(world: World) -> None:
    ads = await ads_world(world)
    done = create(world, ads, video_type="D", design_link="https://example.com/design")
    done = act(world, ads.head, "/approve", done)
    dung = node(done, "DUNG")["id"]
    done = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", done, assignee_user_id=str(ads.editor.id)
    )
    done = act(world, ads.editor, f"/nodes/{dung}/accept", done)
    done = act(world, ads.editor, f"/nodes/{dung}/submit", done, link="https://example.com/final")
    done = act(world, ads.lead_dung, f"/nodes/{dung}/approve", done)
    done = act(world, ads.head, "/final/approve", done)
    pending = create(world, ads, video_type="D", design_link="https://example.com/design")
    create(world, ads, title="Đang làm", preassigned={"BIEN_TAP": str(ads.writer.id)})

    world.act_as(ads.head)
    response = world.client.get("/api/board/dashboard", params={"unit": "ADS"})
    assert response.status_code == 200, response.json()
    summary = response.json()
    assert summary["unit"] == "ADS"
    assert summary["total"] == 3 and summary["completed"] == 1
    assert summary["pending_review"] == 2  # two orders waiting for the head
    assert summary["urgent"] == 0
    assert summary["progress_percent"] == 33
    assert {item["phase"]: item["count"] for item in summary["by_phase"]} == {
        "ORDER": 0,
        "REVIEW": 2,
        "PRODUCTION": 0,
        "FINAL_REVIEW": 0,
        "DONE": 1,
        "CANCELLED": 0,
    }
    assert summary["by_owner"][0]["name"] == ads.orderer.full_name
    assert summary["by_owner"][0]["opened"] == 3 and summary["by_owner"][0]["done"] == 1
    workers = {item["name"]: item for item in summary["by_worker"]}
    assert workers[ads.editor.full_name]["done"] == 1  # the edit (it carried the link)
    # The range defaults to this month, and a day range outside it is empty.
    today = datetime.now(UTC).date()
    assert summary["date_from"].endswith("-01")
    empty = world.client.get(
        "/api/board/dashboard",
        params={"unit": "ADS", "date_from": "2020-01-01", "date_to": "2020-01-31"},
    ).json()
    assert empty["total"] == 0 and empty["progress_percent"] is None
    assert pending["order"]["code"].startswith("TUAN-D-")
    assert today.year >= 2026


async def test_08_the_pr_dashboard_reads_the_same_rows(world: World) -> None:
    await seed_units(world)
    await world.content()
    world.act_as(world.member)
    summary = world.client.get("/api/board/dashboard", params={"unit": "PR"}).json()
    assert summary["unit"] == "PR" and summary["total"] == 1
    assert {item["phase"]: item["count"] for item in summary["by_phase"]}["ORDER"] == 1
    assert summary["by_owner"][0]["opened"] == 1


async def test_09_every_row_names_one_member_or_says_cho_giao(world: World) -> None:
    """No role is ever the holder: the head or Leader is named, or "Chờ giao"."""
    ads = await ads_world(world)
    quick = create(world, ads, video_type="D", design_link="https://example.com/design")
    world.act_as(ads.head)

    def row() -> dict[str, Any]:
        page = tasks(world, unit="ADS")
        return next(item for item in page["items"] if item["code"] == quick["order"]["code"])

    pending = row()
    assert pending["current_person_name"] == ads.head.full_name
    assert pending["current_person_user_id"] == str(ads.head.id)
    assert pending["awaiting_assignment"] is False

    # Approved, nobody chosen for the edit: routed to the Dựng Leader to hand out.
    quick = act(world, ads.head, "/approve", quick)
    routed = row()
    assert routed["current_person_name"] == ads.lead_dung.full_name
    assert routed["current_person_user_id"] == str(ads.lead_dung.id)
    assert routed["awaiting_assignment"] is False
    assert routed["status_label"] == f"Dựng · Chờ {ads.lead_dung.full_name} phân công"
    assert routed["state"] == "CHO_PHAN_CONG"
    cells = {cell["key"]: cell for cell in routed["cells"]}
    assert cells["DUNG"]["status"] == "CHO_PHAN_CONG"
    assert cells["DUNG"]["status_label"] == f"Chờ {ads.lead_dung.full_name} phân công"
    assert cells["DUNG"]["person_name"] == ads.lead_dung.full_name

    # Assigned, not accepted: "Đã giao", and the editor holds it.
    dung = node(quick, "DUNG")["id"]
    quick = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", quick, assignee_user_id=str(ads.editor.id)
    )
    assigned = row()
    assert assigned["current_person_name"] == ads.editor.full_name
    assert assigned["status_label"] == f"Dựng · Đã giao {ads.editor.full_name}"
    assert assigned["state"] == "DA_GIAO"
    cells = {cell["key"]: cell for cell in assigned["cells"]}
    assert cells["DUNG"]["status"] == "DA_GIAO"
    assert cells["DUNG"]["status_label"] == f"Đã giao {ads.editor.full_name}"
    world.act_as(ads.editor)
    assert quick["order"]["code"] in {
        item["code"] for item in tasks(world, unit="ADS", awaiting_me="true")["items"]
    }

    # Accepted: now it is being worked on.
    quick = act(world, ads.editor, f"/nodes/{dung}/accept", quick)
    world.act_as(ads.head)
    working = row()
    assert working["status_label"] == "Dựng · Đang làm" and working["state"] == "DANG_LAM"
    cells = {cell["key"]: cell for cell in working["cells"]}
    assert cells["DUNG"]["status"] == "DANG_LAM" and cells["DUNG"]["status_label"] == "Đang làm"

    # Handed in: waiting for the named Dựng Leader, who now holds it.
    quick = act(world, ads.editor, f"/nodes/{dung}/submit", quick, link="https://example.com/cut")
    review = row()
    assert review["status_label"] == f"Chờ {ads.lead_dung.full_name} duyệt"
    assert review["state"] == "CHO_DUYET"
    assert review["current_person_name"] == ads.lead_dung.full_name
    cells = {cell["key"]: cell for cell in review["cells"]}
    assert cells["DUNG"]["status_label"] == f"Chờ {ads.lead_dung.full_name} duyệt"
    assert cells["DUNG"]["status"] == "CHO_DUYET"

    # With no head tagged, a pending order is "Chờ giao", not "Trưởng phòng".
    await world.session.execute(
        update(OrgUnitMember)
        .where(OrgUnitMember.user_id == ads.head.id)
        .values(left_at=datetime.now(UTC))
    )
    await world.session.flush()
    other = create(world, ads, video_type="D", design_link="https://example.com/d2")
    world.act_as(world.owner)
    page = tasks(world, unit="ADS")
    orphan = next(item for item in page["items"] if item["code"] == other["order"]["code"])
    assert orphan["current_person_name"] == "Chờ giao"
    assert orphan["status_label"] == "Chờ giao"
    for item in page["items"]:
        assert item["current_person_name"] not in ("Trưởng phòng", "Leader Biên tập")


async def test_10_a_pr_review_names_the_granted_reviewer(world: World) -> None:
    """A PR gate names the person whose grant reaches the item, else "Chờ giao"."""
    item = await world.content()
    item.workflow_stage = PrWorkflowStage.TEAM_LEAD_REVIEW
    await world.session.flush()
    world.act_as(world.owner)

    def row() -> dict[str, Any]:
        return next(r for r in tasks(world, unit="PR")["items"] if r["code"] == item.code)

    # Nobody holds Team Lead review yet.
    nobody = row()
    assert nobody["current_person_name"] == "Chờ giao"
    assert nobody["awaiting_assignment"] is True
    assert nobody["status_label"] == "Chờ giao"

    await world.grant(
        world.lead,
        PrCapability.PR_TEAM_LEAD_REVIEW,
        GrantScope(
            content_type_scope=PrGrantScopeMode.ALL,
            channel_scope=PrGrantScopeMode.ALL,
        ),
    )
    named = row()
    assert named["current_person_name"] == world.lead.full_name
    assert named["current_person_user_id"] == str(world.lead.id)
    assert named["status_label"] == f"Chờ {world.lead.full_name} duyệt"
    cells = {cell["key"]: cell for cell in named["cells"]}
    assert cells["REVIEW"]["person_name"] == world.lead.full_name


async def test_11_the_ads_pha_filter_has_four_steps(world: World) -> None:
    ads = await ads_world(world)
    pending = create(world, ads, video_type="D", design_link="https://example.com/d")
    script = act(world, ads.head, "/approve", create(world, ads))
    design = act(world, ads.head, "/approve", create(world, ads, video_type="TD"))
    world.act_as(ads.head)

    page = tasks(world, unit="ADS")
    assert [(step["value"], step["label"]) for step in page["steps"]] == [
        ("ORDER", "Order"),
        ("BIEN_TAP", "Biên tập"),
        ("THIET_KE", "Thiết kế"),
        ("DUNG", "Dựng"),
    ]

    def codes(step: str) -> set[str]:
        return {row["code"] for row in tasks(world, unit="ADS", step=step)["items"]}

    assert codes("ORDER") == {pending["order"]["code"]}
    assert codes("BIEN_TAP") == {script["order"]["code"]}
    assert codes("thiet_ke") == {design["order"]["code"]}
    assert codes("DUNG") == set()
    assert codes("NOPE") == set()
    # PR and the merged view keep the five shared phases and no steps.
    world.act_as(world.owner)
    assert tasks(world, unit="PR")["steps"] == []
    assert tasks(world, unit="ALL")["steps"] == []


async def test_09b_several_people_on_one_step_are_all_named(world: World) -> None:
    """Two heads: the order waits for both by name, never for "Trưởng phòng"."""
    ads = await ads_world(world)
    second = await person(world, "Trần Minh Trang", Role.TEAM_LEAD)
    await tag(world, ads.unit, second, UnitMemberRole.HEAD)
    order = create(world, ads, video_type="D", design_link="https://example.com/design")
    world.act_as(ads.head)
    row = next(
        item for item in tasks(world, unit="ADS")["items"] if item["code"] == order["order"]["code"]
    )
    both = {ads.head.full_name, second.full_name}
    assert set(row["current_person_name"].split(", ")) == both
    assert row["status_label"].startswith("Chờ ") and row["status_label"].endswith(" duyệt order")
    assert all(name in row["status_label"] for name in both)
    # The task page lists them too.
    detail = world.client.get(f"/api/tasks/{order['order']['code']}").json()
    waiting = next(p for p in detail["people"] if p["role_label"] == "Chờ duyệt order")
    assert set(waiting["name"].split(", ")) == both


async def test_10b_an_approved_pr_piece_names_who_hands_out_production(world: World) -> None:
    """Approved with no producer: the people allowed to assign it are named."""
    item = await world.content()
    item.workflow_stage = PrWorkflowStage.APPROVED
    item.producer_user_id = None
    await world.session.flush()
    world.act_as(world.owner)
    row = next(r for r in tasks(world, unit="PR")["items"] if r["code"] == item.code)
    assert row["status_label"].endswith(" giao sản xuất")
    assert world.owner.full_name in row["current_person_name"]
    assert row["current_person_name"] != "Chờ giao"


async def test_09c_two_people_on_a_step_both_hold_it_and_one_decision_is_enough(
    world: World,
) -> None:
    """Two heads, two script Leaders: both are named, both are notified, both
    see the task under "Chờ tôi" and the buttons; the first decision moves
    the order on and the other person's buttons disappear."""
    ads = await ads_world(world)
    head2 = await person(world, "Trần Minh Trang", Role.TEAM_LEAD)
    await tag(world, ads.unit, head2, UnitMemberRole.HEAD)
    lead_bt2 = await person(world, "Leader BT Hai")
    await tag(world, ads.unit, lead_bt2, UnitMemberRole.BIEN_TAP, is_lead=True)
    ads.unit.settings = {"review_bien_tap": True}
    await world.session.flush()
    order = create(world, ads)  # BTD, nobody chosen for any node
    code = order["order"]["code"]

    def actions(user: Any) -> set[str]:
        world.act_as(user)
        detail = world.client.get(f"/api/orders/{order['order']['id']}").json()
        return {a["kind"] for a in detail["available_actions"]}

    def awaiting(user: Any) -> set[str]:
        world.act_as(user)
        return {r["code"] for r in tasks(world, unit="ADS", awaiting_me="true")["items"]}

    # Order approval: both heads notified, both see it waiting, both may decide.
    for head in (ads.head, head2):
        assert code in awaiting(head)
        assert {"APPROVE_ORDER", "RETURN_ORDER"} <= actions(head)
        assert ("order_submitted", "Có order mới cần duyệt") in await inbox(world, head)
    # One approves; the other's buttons go and a stale second click is refused.
    stale = order
    order = act(world, ads.head, "/approve", order)
    assert "APPROVE_ORDER" not in actions(head2)
    world.act_as(head2)
    second = world.client.post(
        f"/api/orders/{order['order']['id']}/approve", json={"version": stale["order"]["version"]}
    )
    assert second.status_code in (403, 409, 422)

    # Script node: routed to one Leader, yet both Leaders may hand it out.
    for lead in (ads.lead_bt, lead_bt2):
        assert code in awaiting(lead)
        assert "ASSIGN" in actions(lead)
    bt = node(order, "BIEN_TAP")["id"]
    order = act(world, lead_bt2, f"/nodes/{bt}/assign", order, assignee_user_id=str(ads.writer.id))
    order = act(world, ads.writer, f"/nodes/{bt}/accept", order)
    order = act(world, ads.writer, f"/nodes/{bt}/submit", order, script_text="Kịch bản")
    # Review: both Leaders notified and able to approve; one approval is enough.
    for lead in (ads.lead_bt, lead_bt2):
        assert code in awaiting(lead)
        assert "APPROVE_NODE" in actions(lead)
        assert ("order_submission_ready", "Có bài chờ bạn duyệt") in await inbox(world, lead)
    order = act(world, ads.lead_bt, f"/nodes/{bt}/approve", order)
    assert node(order, "BIEN_TAP")["status"] == "HOAN_THANH"
    assert order["order"]["stage"] == "THIET_KE"
    assert "APPROVE_NODE" not in actions(lead_bt2)


# --- the flexible process, the video kinds, the orderer's final review (0044) -------


async def final_review(world: World, ads: Any, **overrides: Any) -> dict[str, Any]:
    """A T order walked to the final review by the designer."""
    detail = create(
        world, ads, video_type="T", preassigned={"THIET_KE": str(ads.designer.id)}, **overrides
    )
    detail = act(world, ads.head, "/approve", detail)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    detail = act(world, ads.designer, f"/nodes/{tk}/submit", detail, link="https://e.com/final")
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    return detail


async def test_12_the_final_review_names_the_orderer_and_waits_on_them_only(
    world: World,
) -> None:
    ads = await ads_world(world)
    detail = await final_review(world, ads)
    code = detail["order"]["code"]

    def row(user: Any) -> dict[str, Any]:
        world.act_as(user)
        return next(r for r in tasks(world, unit="ADS")["items"] if r["code"] == code)

    def awaiting(user: Any) -> set[str]:
        world.act_as(user)
        return {r["code"] for r in tasks(world, unit="ADS", awaiting_me="true")["items"]}

    seen = row(ads.head)
    assert seen["status_label"] == f"Chờ {ads.orderer.full_name} duyệt final"
    assert seen["current_person_name"] == ads.orderer.full_name
    assert seen["current_person_user_id"] == str(ads.orderer.id)
    assert seen["state"] == "CHO_DUYET"
    assert seen["delivered_at"] is not None
    cells = {cell["key"]: cell for cell in seen["cells"]}
    assert "GAN_LINK" not in cells
    assert cells["THIET_KE"]["status"] == "HOAN_THANH"
    assert cells["FINAL"]["status"] == "CHO_DUYET" and cells["FINAL"]["is_current"]
    assert cells["FINAL"]["status_label"] == f"Chờ {ads.orderer.full_name} duyệt final"
    assert cells["FINAL"]["person_name"] == ads.orderer.full_name
    # The "Tất cả" tab's "Duyệt final" column says the same.
    world.act_as(world.owner)
    merged = next(r for r in tasks(world, unit="ALL")["items"] if r["code"] == code)
    final = {cell["key"]: cell for cell in merged["cells"]}["FINAL"]
    assert final["label"] == "Duyệt final" and final["status"] == "CHO_DUYET"
    assert final["status_label"] == f"Chờ {ads.orderer.full_name} duyệt final"
    # It waits on the orderer, not on the head who may only stand in.
    assert code in awaiting(ads.orderer)
    assert code not in awaiting(ads.head)
    # The task page says the same.
    world.act_as(ads.head)
    page = world.client.get(f"/api/tasks/{code}").json()
    assert page["task"]["current_person"] == {
        "user_id": str(ads.orderer.id),
        "name": ads.orderer.full_name,
    }
    waiting = next(p for p in page["people"] if p["role_label"] == "Chờ duyệt final")
    assert waiting["user_id"] == str(ads.orderer.id)
    # The head still has the stand-in buttons; the orderer's are the same.
    assert "ads:APPROVE_FINAL" in {a["key"] for a in page["actions"]}
    world.act_as(ads.orderer)
    mine = world.client.get(f"/api/tasks/{code}").json()
    assert {"ads:APPROVE_FINAL", "ads:RETURN_FINAL"} <= {a["key"] for a in mine["actions"]}


async def test_13_the_kind_column_reads_the_video_kind_then_the_process(world: World) -> None:
    from decimal import Decimal

    from meobot.db.models.org_unit import UnitVideoKind

    ads = await ads_world(world)
    kind = UnitVideoKind(unit_id=ads.unit.id, name="Short video", points=Decimal("1.50"))
    world.session.add(kind)
    await world.session.flush()
    kinded = create(world, ads, video_type="BD", design_link="d", video_kind_id=str(kind.id))
    kind.active = False
    await world.session.flush()
    plain = create(world, ads, video_type="BT")
    world.act_as(ads.head)
    by_code = {row["code"]: row for row in tasks(world, unit="ADS")["items"]}
    with_kind = by_code[kinded["order"]["code"]]
    assert with_kind["kind"] == "BD" and with_kind["kind_label"] == "Short video"
    extras = {item["label"]: item["value"] for item in with_kind["extras"]}
    assert extras["Loại video"] == "Short video" and extras["Điểm"] == "1.5"
    without = by_code[plain["order"]["code"]]
    assert without["kind"] == "BT" and without["kind_label"] == f"Biên kịch{SEP}Design"
    assert all(item["label"] not in ("Loại video", "Điểm") for item in without["extras"])

    # Filters: the process code (any case) and the video kind.
    assert {r["code"] for r in tasks(world, unit="ADS", kind="bd")["items"]} == {
        kinded["order"]["code"]
    }
    assert tasks(world, unit="ADS", kind="BT")["total"] == 1
    assert tasks(world, unit="ADS", kind="BTD")["total"] == 0
    assert {r["code"] for r in tasks(world, unit="ADS", video_kind_id=str(kind.id))["items"]} == {
        kinded["order"]["code"]
    }
    # Across both units, a video-kind filter leaves PR out.
    await world.content()
    world.act_as(world.owner)
    both = tasks(world, unit="ALL", video_kind_id=str(kind.id))
    assert [row["unit"] for row in both["items"]] == ["ADS"] and both["total"] == 1
    assert tasks(world, unit="ALL")["total"] == 3


async def test_14_the_all_tab_puts_both_units_on_the_same_five_columns(world: World) -> None:
    ads = await ads_world(world)
    create(world, ads, video_type="D", design_link="https://example.com/design")
    await world.content()
    world.act_as(world.owner)
    page = tasks(world, unit="ALL")
    columns = ["ORDER", "BIEN_TAP", "THIET_KE", "PRODUCTION", "FINAL"]
    labels = ["Order", "Biên kịch", "Thiết kế", "Dựng / Sản xuất", "Duyệt final"]
    for row in page["items"]:
        assert [cell["key"] for cell in row["cells"]] == columns
        assert [cell["label"] for cell in row["cells"]] == labels
    pr = next(row for row in page["items"] if row["unit"] == "PR")
    cells = {cell["key"]: cell for cell in pr["cells"]}
    assert cells["BIEN_TAP"]["status_label"] == "—" and cells["THIET_KE"]["status_label"] == "—"
    ads_row = next(row for row in page["items"] if row["unit"] == "ADS")
    order = {cell["key"]: cell for cell in ads_row["cells"]}["ORDER"]
    assert order["is_current"] is True and order["status_label"] == "Chờ duyệt order"
    final = {cell["key"]: cell for cell in ads_row["cells"]}["FINAL"]
    assert final["status"] == "CHUA_TOI" and final["status_label"] == "Chưa tới"
    # The per-unit tabs keep their own columns, with no link step.
    keys = [cell["key"] for cell in tasks(world, unit="ADS")["items"][0]["cells"]]
    assert keys == ["BIEN_TAP", "THIET_KE", "DUNG", "FINAL"]


async def test_15_the_dashboard_can_be_narrowed_to_one_person(world: World) -> None:
    ads = await ads_world(world)
    mine = create(world, ads, video_type="D", design_link="https://example.com/design")
    create(world, ads, by=ads.head, video_type="D", design_link="https://example.com/d2")
    # The editor works on the first order only.
    act(world, ads.head, "/approve", mine)
    world.act_as(world.owner)

    def summary(person: Any = None) -> dict[str, Any]:
        params = {"unit": "ADS"} | ({"person": str(person.id)} if person else {})
        response = world.client.get("/api/board/dashboard", params=params)
        assert response.status_code == 200, response.json()
        return response.json()

    assert summary()["total"] == 2
    assert summary(ads.orderer)["total"] == 1  # ordered one
    assert summary(ads.head)["total"] == 1  # ordered the other
    assert summary(ads.lead_dung)["total"] == 1  # holds the routed edit node
    assert summary(ads.writer)["total"] == 0
    rows = tasks(world, unit="ADS", person=str(ads.lead_dung.id))["items"]
    assert [row["code"] for row in rows] == [mine["order"]["code"]]
