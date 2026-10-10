"""The order engine end to end, over HTTP, for every video type.

Every write goes through ``/api/orders`` with the version the caller last
saw, exactly as the screens will send it, and every assertion reads the
detail the same route returns. What the tests pin:

* the full pipeline for D, TD and BTD, including who is told what;
* a chosen person is handed the node at once, an unchosen node waits for its
  Leader, and a Leader may only hand work to somebody in their own function;
* handed out is not accepted: the assignee presses "Nhận việc" before they
  may hand in;
* there is no link step: the last production node hands in the product link
  (required), its completion opens the gates, and a gate sending it back
  reopens that node for the same person;
* a Leader's first approval lands one result in the KPI ledger, counted when
  the Leader is not the worker, pending when they are; a second approval
  after a return adds nothing;
* a stale version is a 409, a wrong stage a 409, a wrong person a 403, an
  outsider a 404;
* cancel keeps what was counted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from meobot.db.models.order import OrderWorkRule
from meobot.db.models.org_unit import OrgUnit, UnitVideoKind
from meobot.db.models.pr_work import PrWorkType
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.identity.models import Role
from meobot.domain.orders.labels import PROCESS_SEPARATOR as SEP
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.pr.work import PrWorkCategory
from meobot.domain.pr.work_results import PrWorkResultSource
from meobot.domain.units.member_code import derive_member_code
from meobot.domain.units.models import UnitCode, UnitMemberRole, unit_seed_id
from tests.unit.pr_world import World
from tests.unit.test_units_gating import error_reason, seed_units, tag, untag

pytestmark = pytest.mark.asyncio


@dataclass
class Ads:
    unit: OrgUnit
    head: User
    orderer: User
    lead_bt: User
    writer: User
    lead_tk: User
    designer: User
    lead_dung: User
    editor: User
    outsider: User


async def person(world: World, name: str, role: Role = Role.EMPLOYEE) -> User:
    user = User(full_name=name, role=role)
    world.session.add(user)
    await world.session.flush()
    return user


async def ads_world(world: World) -> Ads:
    units = await seed_units(world)
    ads = units[UnitCode.ADS]
    head = await person(world, "Trưởng phòng MKT", Role.TEAM_LEAD)
    orderer = world.member
    lead_bt = await person(world, "Hiền Lương")
    writer = await person(world, "Biên tập B")
    lead_tk = await person(world, "Thiết kế A")
    designer = await person(world, "Thùy Anh")
    lead_dung = await person(world, "Editor A")
    editor = await person(world, "Editor C")
    outsider = world.lead  # tagged PR by the world, outside Ads
    await tag(world, ads, head, UnitMemberRole.HEAD)
    # The orderer works in Ads only: the world tagged them PR, so close it.
    await untag(world, units[UnitCode.PR], orderer)
    await tag(world, ads, orderer, UnitMemberRole.ORDERER, member_code="TUAN")
    await tag(world, ads, lead_bt, UnitMemberRole.BIEN_TAP, is_lead=True)
    await tag(world, ads, writer, UnitMemberRole.BIEN_TAP)
    await tag(world, ads, lead_tk, UnitMemberRole.THIET_KE, is_lead=True)
    await tag(world, ads, designer, UnitMemberRole.THIET_KE)
    await tag(world, ads, lead_dung, UnitMemberRole.DUNG, is_lead=True)
    await tag(world, ads, editor, UnitMemberRole.DUNG)
    for node_type, code, name in (
        (OrderNodeType.BIEN_TAP, "ADS_BIEN_TAP", "Biên tập (Ads)"),
        (OrderNodeType.THIET_KE, "ADS_THIET_KE", "Thiết kế (Ads)"),
        (OrderNodeType.DUNG, "ADS_DUNG", "Dựng (Ads)"),
    ):
        work_type = PrWorkType(code=code, name=name, category=PrWorkCategory.PRODUCTION)
        world.session.add(work_type)
        await world.session.flush()
        world.session.add(
            OrderWorkRule(unit_id=ads.id, node_type=node_type, work_type_id=work_type.id)
        )
    await world.session.flush()
    return Ads(
        unit=ads,
        head=head,
        orderer=orderer,
        lead_bt=lead_bt,
        writer=writer,
        lead_tk=lead_tk,
        designer=designer,
        lead_dung=lead_dung,
        editor=editor,
        outsider=outsider,
    )


def create(world: World, ads: Ads, *, by: User | None = None, **overrides: Any) -> dict[str, Any]:
    world.act_as(by or ads.orderer)
    body = {
        "title": "Kịch bản A",
        "video_type": "BTD",
        "order_content": "Ý tưởng, hook, câu từ.",
        "script_source": "AI",
        "reference_link": "https://example.com/ref",
        "desired_deadline_at": FAR_DEADLINE,
    }
    body.update(overrides)
    response = world.client.post("/api/orders", json=body)
    assert response.status_code == 201, response.json()
    return response.json()


#: A desired / node deadline no test clock reaches (0053 makes them required).
FAR_DEADLINE = "2030-01-01T00:00:00+00:00"


def act(world: World, user: User, path: str, detail: dict[str, Any], **body: Any) -> dict[str, Any]:
    world.act_as(user)
    if "/nodes/" in path and path.rsplit("/", 1)[-1] in ("assign", "accept", "return"):
        # Tokens / a deadline are required here since 0053 (on accept: for a
        # Leader taking a routed node); tests about something else get a plan.
        body.setdefault("tokens", 1)
        if not path.endswith("/return"):
            body.setdefault("deadline_at", FAR_DEADLINE)
    response = world.client.post(
        f"/api/orders/{detail['order']['id']}{path}",
        json={"version": detail["order"]["version"], **body},
    )
    assert response.status_code == 200, (path, response.json())
    return response.json()


def node(detail: dict[str, Any], node_type: str) -> dict[str, Any]:
    return next(item for item in detail["nodes"] if item["node_type"] == node_type)


def statuses(detail: dict[str, Any]) -> dict[str, str]:
    return {item["node_type"]: item["status"] for item in detail["nodes"]}


async def inbox(world: World, user: User) -> list[tuple[str, str]]:
    rows = (
        await world.session.scalars(
            select(UserNotification)
            .where(UserNotification.recipient_user_id == user.id)
            .order_by(UserNotification.created_at, UserNotification.id)
        )
    ).all()
    return [(row.event_type, row.title) for row in rows]


async def kpi_rows(world: World) -> list[PrWorkResult]:
    return list(
        (
            await world.session.scalars(
                select(PrWorkResult).where(PrWorkResult.source_type == PrWorkResultSource.ORDER)
            )
        ).all()
    )


# --- create ---------------------------------------------------------------------


async def test_01_an_order_is_submitted_with_its_code_and_three_nodes(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads)
    order = detail["order"]
    assert order["code"].startswith("TUAN-BTD-") and order["code"].endswith("-01")
    assert order["stage"] == "ORDER_PENDING" and order["stage_label"] == "Chờ duyệt"
    assert order["version"] == 1 and order["urgent"] is False
    assert order["owner_name"] == ads.orderer.full_name
    assert statuses(detail) == {
        "BIEN_TAP": "CHUA_TOI",
        "THIET_KE": "CHUA_TOI",
        "DUNG": "CHUA_TOI",
    }
    assert [event["kind"] for event in detail["events"]] == ["SUBMITTED"]
    assert {action["kind"] for action in detail["available_actions"]} == {"CANCEL"}
    assert await inbox(world, ads.head) == [("order_submitted", "Có order mới cần duyệt")]
    # The second order of the day gets the next number; another type too.
    second = create(world, ads, video_type="D", design_link="https://example.com/design")
    assert second["order"]["code"].endswith("-02")
    assert statuses(second) == {
        "BIEN_TAP": "BO_QUA",
        "THIET_KE": "BO_QUA",
        "DUNG": "CHUA_TOI",
    }


async def test_02_create_validates_the_type_the_link_and_the_chosen_people(world: World) -> None:
    ads = await ads_world(world)
    world.act_as(ads.orderer)
    base = {
        "title": "X",
        "video_type": "D",
        "order_content": "y",
        "desired_deadline_at": FAR_DEADLINE,
    }
    response = world.client.post("/api/orders", json=base)
    assert response.status_code == 422 and error_reason(response.json()) == "design_link_required"
    response = world.client.post("/api/orders", json={**base, "video_type": "XL"})
    assert response.status_code == 422 and error_reason(response.json()) == "invalid_video_type"
    # A designer chosen for a quick edit, which has no design node.
    response = world.client.post(
        "/api/orders",
        json={**base, "design_link": "d", "preassigned": {"THIET_KE": str(ads.designer.id)}},
    )
    assert response.status_code == 422 and error_reason(response.json()) == "preassign_not_in_plan"
    # A designer chosen as the editor.
    response = world.client.post(
        "/api/orders",
        json={**base, "design_link": "d", "preassigned": {"DUNG": str(ads.designer.id)}},
    )
    assert response.status_code == 422
    assert error_reason(response.json()) == "not_a_unit_function_member"
    # Somebody with no member code cannot get a code.
    world.act_as(ads.writer)
    response = world.client.post("/api/orders", json={**base, "design_link": "d"})
    assert response.status_code == 422 and error_reason(response.json()) == "not_an_orderer"
    # An outsider is told nothing.
    world.act_as(ads.outsider)
    assert world.client.post("/api/orders", json={**base, "design_link": "d"}).status_code == 404


# --- the full pipeline ------------------------------------------------------------


async def test_03_a_full_pipeline_order_runs_from_submission_to_the_product_link(
    world: World,
) -> None:
    ads = await ads_world(world)
    # Every review switched on: this walks the longest path the unit allows.
    ads.unit.settings = {
        "review_bien_tap": True,
        "review_thiet_ke": True,
        "review_video_by_script_lead": True,
    }
    await world.session.flush()
    detail = create(world, ads, preassigned={"BIEN_TAP": str(ads.writer.id)})
    order_id = detail["order"]["id"]

    # The head approves: the script node is handed to the chosen writer.
    detail = act(world, ads.head, "/approve", detail)
    assert detail["order"]["stage"] == "BIEN_TAP"
    assert node(detail, "BIEN_TAP")["status"] == "DANG_LAM"
    assert node(detail, "BIEN_TAP")["assignee_user_id"] == str(ads.writer.id)
    assert node(detail, "BIEN_TAP")["accepted_at"] is None
    assert node(detail, "BIEN_TAP")["is_current"] is True
    assert [a["gate"] + ":" + a["decision"] for a in detail["approvals"]] == ["ORDER:APPROVED"]
    assert await inbox(world, ads.lead_bt) == [("order_approved", "Có order mới")]
    assert await inbox(world, ads.writer) == [("order_approved", "Có order mới")]

    # The writer accepts, then hands in.
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    assert node(detail, "BIEN_TAP")["accepted_at"] is not None
    assert await inbox(world, ads.orderer) == [("order_node_accepted", "Đã có người nhận việc")]
    detail = act(
        world, ads.writer, f"/nodes/{bt}/submit", detail, script_text="Kịch bản…", note="Frame 1-5"
    )
    assert node(detail, "BIEN_TAP")["status"] == "CHO_DUYET"
    assert detail["submissions"][0]["label"].endswith("_V1")
    # Rows written within one second share a timestamp, so compare as a set.
    assert set(await inbox(world, ads.lead_bt)) == {
        ("order_approved", "Có order mới"),
        ("order_submission_ready", "Có bài chờ bạn duyệt"),
    }

    # The lead sends it back once, then approves. One KPI result, counted.
    detail = act(world, ads.lead_bt, f"/nodes/{bt}/return", detail, note="Hook còn dài")
    assert node(detail, "BIEN_TAP")["status"] == "DANG_SUA"
    assert node(detail, "BIEN_TAP")["revision_count"] == 1
    assert ("order_node_returned", "Bài của bạn cần sửa") in await inbox(world, ads.writer)
    # Already accepted: the fix goes straight in.
    detail = act(world, ads.writer, f"/nodes/{bt}/submit", detail, script_text="Kịch bản v2")
    assert node(detail, "BIEN_TAP")["submission_count"] == 2
    detail = act(world, ads.lead_bt, f"/nodes/{bt}/approve", detail)
    assert node(detail, "BIEN_TAP")["status"] == "HOAN_THANH"
    assert detail["order"]["stage"] == "THIET_KE"
    # Nobody was chosen for design: it goes to the design Leader to hand out.
    assert node(detail, "THIET_KE")["status"] == "DANG_LAM"
    assert node(detail, "THIET_KE")["assignee_user_id"] == str(ads.lead_tk.id)
    assert node(detail, "THIET_KE")["accepted_at"] is None
    assert ("order_node_turn", "Có order tới lượt") in await inbox(world, ads.lead_tk)
    results = await kpi_rows(world)
    assert len(results) == 1
    assert results[0].user_id == ads.writer.id
    assert results[0].status.value == "COUNTED"
    assert results[0].source_key == f"order:{bt}:BIEN_TAP"

    # The design lead assigns the designer, who accepts and hands in.
    tk = node(detail, "THIET_KE")["id"]
    detail = act(
        world, ads.lead_tk, f"/nodes/{tk}/assign", detail, assignee_user_id=str(ads.designer.id)
    )
    assert node(detail, "THIET_KE")["status"] == "DANG_LAM"
    assert node(detail, "THIET_KE")["accepted_at"] is None
    assert ("order_node_assigned", "Bạn được giao một công đoạn") in await inbox(
        world, ads.designer
    )
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    detail = act(world, ads.designer, f"/nodes/{tk}/submit", detail, link="https://example.com/tk")
    detail = act(world, ads.lead_tk, f"/nodes/{tk}/approve", detail)
    assert detail["order"]["stage"] == "DUNG"

    # The editing lead does the edit themselves: the result waits for the head.
    # The cut is the last node's hand-in: it is the product, link and all.
    dung = node(detail, "DUNG")["id"]
    detail = act(
        world,
        ads.lead_dung,
        f"/nodes/{dung}/assign",
        detail,
        assignee_user_id=str(ads.lead_dung.id),
    )
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/accept", detail)
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/submit", detail, link="https://example.com/final_V1"
    )
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    # No link step: the approved cut goes to the script lead first (BTD).
    assert detail["order"]["stage"] == "DUYET_VIDEO_BT"
    assert detail["order"]["product_link"] == "https://example.com/final_V1"
    assert node(detail, "DUNG")["status"] == "HOAN_THANH"
    assert [n["node_type"] for n in detail["nodes"]] == ["BIEN_TAP", "THIET_KE", "DUNG"]
    assert ("order_submission_ready", "Có bài chờ bạn duyệt") in await inbox(world, ads.lead_bt)
    by_node = {row.source_key.split(":")[-1]: row for row in await kpi_rows(world)}
    assert set(by_node) == {"BIEN_TAP", "THIET_KE", "DUNG"}
    assert by_node["DUNG"].status.value == "PENDING"
    assert by_node["THIET_KE"].status.value == "COUNTED"

    # The script lead sends the cut back: the edit reopens for the same editor.
    detail = act(world, ads.lead_bt, "/video/return", detail, note="Thiếu logo")
    assert detail["order"]["stage"] == "DUNG"
    assert node(detail, "DUNG")["status"] == "DANG_SUA"
    assert node(detail, "DUNG")["assignee_user_id"] == str(ads.lead_dung.id)
    assert node(detail, "DUNG")["revision_count"] == 1
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/submit", detail, link="https://example.com/final_V2"
    )
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    assert detail["order"]["stage"] == "DUYET_VIDEO_BT"
    detail = act(world, ads.lead_bt, "/video/approve", detail)
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    # The final review is the orderer's: they are told, not the head.
    assert ("order_submission_ready", "Có bài chờ bạn duyệt") in await inbox(world, ads.orderer)
    assert ("order_submission_ready", "Có bài chờ bạn duyệt") not in await inbox(world, ads.head)

    # The head, a stand-in by the default matrix, returns once: back to the
    # last production node, the same editor, one more revision.
    detail = act(world, ads.head, "/final/return", detail, note="Đổi nhạc")
    assert detail["order"]["stage"] == "DUNG"
    assert node(detail, "DUNG")["status"] == "DANG_SUA"
    assert node(detail, "DUNG")["assignee_user_id"] == str(ads.lead_dung.id)
    assert node(detail, "DUNG")["revision_count"] == 2
    assert ("order_final_returned", "Sản phẩm cần sửa lại") in await inbox(world, ads.lead_dung)
    # A fresh cut goes through review and the script lead again.
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/submit", detail, link="https://example.com/final_V3"
    )
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    assert detail["order"]["stage"] == "DUYET_VIDEO_BT"
    detail = act(world, ads.lead_bt, "/video/approve", detail)
    detail = act(world, ads.head, "/final/approve", detail)
    order = detail["order"]
    assert order["stage"] == "COMPLETED" and order["completed_at"] is not None
    assert order["product_link"] == "https://example.com/final_V3"
    assert node(detail, "DUNG")["status"] == "HOAN_THANH"
    assert ("order_completed", "Order đã hoàn thành") in await inbox(world, ads.orderer)
    assert detail["available_actions"] == []
    gates = [
        a["gate"] + ":" + str(a["round_no"]) + ":" + a["decision"] for a in detail["approvals"]
    ]
    assert gates == [
        "ORDER:1:APPROVED",
        "VIDEO_BT:1:RETURNED",
        "VIDEO_BT:2:APPROVED",
        "FINAL:1:RETURNED",
        "VIDEO_BT:3:APPROVED",
        "FINAL:2:APPROVED",
    ]
    # Still three results: a node approved again after a return adds nothing.
    assert len(await kpi_rows(world)) == 3
    assert "LINK_ATTACHED" not in {event["kind"] for event in detail["events"]}
    # The detail is also reachable by its code.
    world.act_as(ads.orderer)
    assert world.client.get(f"/api/orders/{order['code']}").json()["order"]["id"] == order_id


async def test_04_a_quick_edit_skips_two_nodes_and_needs_no_script_lead(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, video_type="D", design_link="https://example.com/design")
    detail = act(world, ads.head, "/approve", detail)
    assert detail["order"]["stage"] == "DUNG"
    assert statuses(detail)["BIEN_TAP"] == "BO_QUA"
    dung = node(detail, "DUNG")["id"]
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", detail, assignee_user_id=str(ads.editor.id)
    )
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://example.com/cut")
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    # No script lead on a D order: the approved cut goes straight to the orderer.
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    assert detail["order"]["product_link"] == "https://example.com/cut"
    detail = act(world, ads.head, "/final/approve", detail)
    assert detail["order"]["stage"] == "COMPLETED"
    assert detail["order"]["product_link"] == "https://example.com/cut"
    assert len(await kpi_rows(world)) == 1


async def test_05_design_plus_edit_runs_the_two_production_nodes(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, video_type="TD", preassigned={"THIET_KE": str(ads.designer.id)})
    detail = act(world, ads.head, "/approve", detail)
    assert detail["order"]["stage"] == "THIET_KE"
    assert node(detail, "THIET_KE")["assignee_user_id"] == str(ads.designer.id)
    tk = node(detail, "THIET_KE")["id"]
    # Design is not reviewed by default: handing in finishes the node and the
    # edit starts at once, with nothing waiting on the design Leader.
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    detail = act(world, ads.designer, f"/nodes/{tk}/submit", detail, link="https://example.com/tk")
    assert detail["order"]["stage"] == "DUNG"
    assert statuses(detail)["THIET_KE"] == "HOAN_THANH"
    assert statuses(detail)["BIEN_TAP"] == "BO_QUA"
    assert {a["kind"] for a in detail["available_actions"]}.isdisjoint({"APPROVE_NODE"})
    # The edit is still reviewed by its Leader.
    detail = act(
        world,
        ads.lead_dung,
        f"/nodes/{node(detail, 'DUNG')['id']}/assign",
        detail,
        assignee_user_id=str(ads.editor.id),
    )
    dung = node(detail, "DUNG")["id"]
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://example.com/cut")
    assert statuses(detail)["DUNG"] == "CHO_DUYET"
    # The strip reads in pipeline order; there is no link node.
    assert [n["node_type"] for n in detail["nodes"]] == ["BIEN_TAP", "THIET_KE", "DUNG"]


# --- refusals ---------------------------------------------------------------------


async def test_06_a_stale_button_a_wrong_stage_a_wrong_person_an_outsider(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads)
    order_id = detail["order"]["id"]
    world.act_as(ads.head)
    stale = world.client.post(f"/api/orders/{order_id}/approve", json={"version": 7})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "order_stale_version"
    wrong_stage = world.client.post(f"/api/orders/{order_id}/final/approve", json={"version": 1})
    assert wrong_stage.status_code == 409
    assert wrong_stage.json()["error"]["code"] == "order_invalid_state"
    # The orderer sees their order but may not approve it.
    world.act_as(ads.orderer)
    wrong_person = world.client.post(f"/api/orders/{order_id}/approve", json={"version": 1})
    assert wrong_person.status_code == 403
    assert wrong_person.json()["error"]["code"] == "order_forbidden"
    # The writer is not on this order yet, so they cannot even see it.
    world.act_as(ads.writer)
    assert world.client.get(f"/api/orders/{order_id}").status_code == 404
    world.act_as(ads.outsider)
    assert world.client.get(f"/api/orders/{order_id}").status_code == 404
    # A return needs a reason.
    world.act_as(ads.head)
    no_reason = world.client.post(f"/api/orders/{order_id}/return", json={"version": 1})
    assert no_reason.status_code == 422 and error_reason(no_reason.json()) == "note_required"


async def test_07_a_returned_order_is_fixed_and_resubmitted_under_the_same_code(
    world: World,
) -> None:
    ads = await ads_world(world)
    detail = create(world, ads)
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/return", detail, note="Thiếu source")
    assert detail["order"]["stage"] == "ORDER_RETURNED"
    assert detail["order"]["returned_reason"] == "Thiếu source"
    assert ("order_returned", "Order cần sửa, gửi lại") in await inbox(world, ads.orderer)
    detail = act(world, ads.orderer, "/resubmit", detail, source_link="https://example.com/src")
    assert detail["order"]["stage"] == "ORDER_PENDING"
    assert detail["order"]["code"] == code
    assert detail["order"]["source_link"] == "https://example.com/src"
    assert detail["order"]["returned_reason"] is None
    assert [e["kind"] for e in detail["events"]] == ["SUBMITTED", "ORDER_RETURNED", "RESUBMITTED"]
    assert ("order_submitted", "Có order mới cần duyệt") in await inbox(world, ads.head)


async def test_08_a_leader_may_only_hand_work_to_their_own_function(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, video_type="TD")
    detail = act(world, ads.head, "/approve", detail)
    tk = node(detail, "THIET_KE")["id"]
    world.act_as(ads.lead_tk)
    response = world.client.post(
        f"/api/orders/{detail['order']['id']}/nodes/{tk}/assign",
        json={"version": detail["order"]["version"], "assignee_user_id": str(ads.editor.id)},
    )
    assert response.status_code == 422
    assert error_reason(response.json()) == "not_a_unit_function_member"
    # The editing lead has no say over the design node.
    world.act_as(ads.lead_dung)
    response = world.client.post(
        f"/api/orders/{detail['order']['id']}/nodes/{tk}/assign",
        json={"version": detail["order"]["version"], "assignee_user_id": str(ads.designer.id)},
    )
    assert response.status_code in (403, 404)


async def test_09_priority_is_a_flag_and_cancel_keeps_what_was_counted(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, video_type="D", design_link="https://example.com/design")
    detail = act(world, ads.head, "/priority", detail, is_priority=True)
    assert detail["order"]["is_priority"] is True and detail["order"]["stage"] == "ORDER_PENDING"
    assert detail["events"][-1]["kind"] == "PRIORITY_SET"
    detail = act(world, ads.head, "/approve", detail)
    dung = node(detail, "DUNG")["id"]
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", detail, assignee_user_id=str(ads.editor.id)
    )
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://example.com/cut")
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    assert len(await kpi_rows(world)) == 1
    detail = act(world, ads.head, "/cancel", detail, note="Khách đổi ý")
    assert detail["order"]["stage"] == "CANCELLED"
    assert detail["order"]["cancelled_reason"] == "Khách đổi ý"
    assert detail["available_actions"] == []
    assert len(await kpi_rows(world)) == 1
    # The orderer may cancel their own order only before it is approved.
    other = create(world, ads, video_type="D", design_link="https://example.com/design")
    other = act(world, ads.orderer, "/cancel", other, note="Nhầm")
    assert other["order"]["stage"] == "CANCELLED"
    third = create(world, ads, video_type="D", design_link="https://example.com/design")
    third = act(world, ads.head, "/approve", third)
    world.act_as(ads.orderer)
    refused = world.client.post(
        f"/api/orders/{third['order']['id']}/cancel",
        json={"version": third["order"]["version"], "note": "Thôi"},
    )
    assert refused.status_code == 403


async def test_10_every_order_write_is_audited(world: World) -> None:
    from meobot.db.models.audit_log import AuditLog

    ads = await ads_world(world)
    detail = create(world, ads, video_type="D", design_link="https://example.com/design")
    detail = act(world, ads.head, "/approve", detail)
    actions = (
        await world.session.scalars(
            select(AuditLog.action).where(AuditLog.action.like("order.%")).order_by(AuditLog.id)
        )
    ).all()
    assert sorted(actions) == ["order.approved", "order.submitted"]
    assert uuid.UUID(detail["order"]["id"])


async def test_11_a_head_without_a_member_code_gets_one_from_their_name(world: World) -> None:
    ads = await ads_world(world)
    # The head was tagged with no code: the first order derives one and keeps it.
    first = create(world, ads, by=ads.head)
    assert first["order"]["code"].startswith("TRUONG-BTD-") and first["order"]["code"].endswith(
        "-01"
    )
    assert create(world, ads, by=ads.head)["order"]["code"].endswith("-02")
    members = world.client.get("/api/units/ADS/members").json()["members"]
    assert next(m["member_code"] for m in members if m["user_id"] == str(ads.head.id)) == "TRUONG"
    # A name that folds to a code somebody already holds gets a numeric suffix.
    other = await person(world, "Trương Văn B")
    await tag(world, ads.unit, other, UnitMemberRole.ORDERER)
    assert create(world, ads, by=other)["order"]["code"].startswith("TRUONG2-BTD-")
    # The owner acts without a tag: there is no row to keep the code on, so it
    # is derived the same way each time.
    prefix = derive_member_code(world.owner.full_name)
    assert create(world, ads, by=world.owner)["order"]["code"].startswith(f"{prefix}-BTD-")
    assert create(world, ads, by=world.owner)["order"]["code"].startswith(f"{prefix}-BTD-")


# --- the flexible process and the video kinds (0044) ------------------------------


async def add_kind(
    world: World, ads: Ads, name: str, points: str = "1", *, active: bool = True
) -> UnitVideoKind:
    kind = UnitVideoKind(unit_id=ads.unit.id, name=name, points=Decimal(points), active=active)
    world.session.add(kind)
    await world.session.flush()
    return kind


PROCESS_NODES = {
    "B": ["BIEN_TAP"],
    "T": ["THIET_KE"],
    "D": ["DUNG"],
    "BT": ["BIEN_TAP", "THIET_KE"],
    "BD": ["BIEN_TAP", "DUNG"],
    "TD": ["THIET_KE", "DUNG"],
    "BTD": ["BIEN_TAP", "THIET_KE", "DUNG"],
}


async def test_12_every_process_combination_plans_its_nodes_and_its_code(world: World) -> None:
    ads = await ads_world(world)
    for number, (code, nodes) in enumerate(PROCESS_NODES.items(), start=1):
        # Ticked in reverse: the server puts them in pipeline order.
        detail = create(
            world,
            ads,
            video_type=None,
            process=[name.lower() for name in reversed(nodes)],
            design_link="https://example.com/design",
        )
        order = detail["order"]
        assert order["video_type"] == code
        assert order["code"].startswith(f"TUAN-{code}-") and order["code"].endswith(
            f"-{number:02d}"
        )
        assert order["process"] == nodes
        assert statuses(detail) == {
            node_type: ("CHUA_TOI" if node_type in nodes else "BO_QUA")
            for node_type in ("BIEN_TAP", "THIET_KE", "DUNG")
        }
        # The approval starts the first ticked node.
        detail = act(world, ads.head, "/approve", detail)
        assert detail["order"]["stage"] == nodes[0]
    # The code alone still works, and agreeing forms are accepted.
    agreed = create(world, ads, video_type="bd", process=["DUNG", "BIEN_TAP"], design_link="x")
    assert agreed["order"]["video_type"] == "BD"
    assert agreed["order"]["video_type_label"] == f"Biên kịch{SEP}Dựng"


async def test_13_the_process_is_validated(world: World) -> None:
    ads = await ads_world(world)
    world.act_as(ads.orderer)
    base = {
        "title": "X",
        "order_content": "y",
        "design_link": "d",
        "desired_deadline_at": FAR_DEADLINE,
    }

    def reason(**body: Any) -> tuple[int, str | None]:
        response = world.client.post("/api/orders", json={**base, **body})
        return response.status_code, error_reason(response.json())

    assert reason(process=[]) == (422, "process_empty")
    assert reason() == (422, "process_empty")
    assert reason(video_type="") == (422, "process_empty")
    assert reason(process=["GAN_LINK"]) == (422, "invalid_process")
    assert reason(process=["NOPE"]) == (422, "invalid_process")
    assert reason(video_type="TD", process=["DUNG"]) == (422, "process_mismatch")
    assert reason(video_type="XL") == (422, "invalid_video_type")
    # The design-link rule: an edit with no design node needs it, others do not.
    without = {"title": "X", "order_content": "y", "desired_deadline_at": FAR_DEADLINE}
    for code in ("D", "BD"):
        response = world.client.post("/api/orders", json={**without, "video_type": code})
        assert response.status_code == 422, code
        assert error_reason(response.json()) == "design_link_required"
    for code in ("B", "T", "BT", "TD", "BTD"):
        response = world.client.post("/api/orders", json={**without, "video_type": code})
        assert response.status_code == 201, (code, response.json())


async def test_14_the_last_production_node_hands_in_the_product_link(world: World) -> None:
    ads = await ads_world(world)
    # Script only: the writer's hand-in is the product, so it needs a link.
    detail = create(world, ads, video_type="B", preassigned={"BIEN_TAP": str(ads.writer.id)})
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    world.act_as(ads.writer)
    no_link = world.client.post(
        f"/api/orders/{detail['order']['id']}/nodes/{bt}/submit",
        json={"version": detail["order"]["version"], "script_text": "Kịch bản"},
    )
    assert no_link.status_code == 422 and error_reason(no_link.json()) == "link_required"
    detail = act(
        world,
        ads.writer,
        f"/nodes/{bt}/submit",
        detail,
        script_text="Kịch bản",
        link="https://example.com/final",
    )
    # Script review is off by default: the hand-in finishes it - final review.
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    assert detail["order"]["product_link"] == "https://example.com/final"
    assert ("order_submission_ready", "Có bài chờ bạn duyệt") in await inbox(world, ads.orderer)
    assert all(action["kind"] != "ATTACH_LINK" for action in detail["available_actions"])
    # The old link route is gone.
    world.act_as(ads.writer)
    gone = world.client.post(
        f"/api/orders/{detail['order']['id']}/link",
        json={"version": detail["order"]["version"], "link": "https://example.com/x"},
    )
    assert gone.status_code in (404, 405)

    # Script then design: the script needs no link, the design (last) does.
    detail = create(world, ads, video_type="BT", preassigned={"THIET_KE": str(ads.designer.id)})
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(
        world, ads.lead_bt, f"/nodes/{bt}/assign", detail, assignee_user_id=str(ads.writer.id)
    )
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    detail = act(world, ads.writer, f"/nodes/{bt}/submit", detail, script_text="Kịch bản")
    assert detail["order"]["stage"] == "THIET_KE"
    tk = node(detail, "THIET_KE")["id"]
    assert node(detail, "THIET_KE")["assignee_user_id"] == str(ads.designer.id)
    # Handed to the chosen designer, who has not accepted: no hand-in yet.
    world.act_as(ads.designer)
    early = world.client.post(
        f"/api/orders/{detail['order']['id']}/nodes/{tk}/submit",
        json={"version": detail["order"]["version"], "link": "https://example.com/tk"},
    )
    assert early.status_code == 403
    mine = world.client.get(f"/api/orders/{detail['order']['id']}").json()
    assert {a["kind"] for a in mine["available_actions"]} == {"ACCEPT"}
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    world.act_as(ads.designer)
    text_only = world.client.post(
        f"/api/orders/{detail['order']['id']}/nodes/{tk}/submit",
        json={"version": detail["order"]["version"], "script_text": "Ghi chú thiết kế"},
    )
    assert text_only.status_code == 422 and error_reason(text_only.json()) == "link_required"
    detail = act(world, ads.designer, f"/nodes/{tk}/submit", detail, link="https://e.com/tk_V1")
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    assert detail["order"]["product_link"] == "https://e.com/tk_V1"

    # The orderer sends it back: the design reopens for the same designer,
    # who fixes it without accepting again.
    detail = act(world, ads.orderer, "/final/return", detail, note="Đổi màu")
    assert detail["order"]["stage"] == "THIET_KE"
    assert node(detail, "THIET_KE")["status"] == "DANG_SUA"
    assert node(detail, "THIET_KE")["assignee_user_id"] == str(ads.designer.id)
    assert node(detail, "THIET_KE")["revision_count"] == 1
    assert ("order_final_returned", "Sản phẩm cần sửa lại") in await inbox(world, ads.designer)
    detail = act(world, ads.designer, f"/nodes/{tk}/submit", detail, link="https://e.com/tk_V2")
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    detail = act(world, ads.orderer, "/final/approve", detail)
    assert detail["order"]["stage"] == "COMPLETED"
    assert detail["order"]["product_link"] == "https://e.com/tk_V2"
    # One result per node, recorded on the first completion only.
    assert len(await kpi_rows(world)) == 3


async def test_15_the_orderer_decides_the_final_review(world: World) -> None:
    ads = await ads_world(world)
    ads.unit.settings = {"review_video_by_script_lead": True}
    await world.session.flush()
    # BD has a script node: the script lead watches the cut first.
    detail = create(
        world,
        ads,
        video_type="BD",
        design_link="https://example.com/design",
        preassigned={"BIEN_TAP": str(ads.writer.id), "DUNG": str(ads.editor.id)},
    )
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    detail = act(world, ads.writer, f"/nodes/{bt}/submit", detail, script_text="Kịch bản")
    dung = node(detail, "DUNG")["id"]
    assert node(detail, "DUNG")["assignee_user_id"] == str(ads.editor.id)
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(
        world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://example.com/final_V1"
    )
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    assert detail["order"]["stage"] == "DUYET_VIDEO_BT"
    detail = act(world, ads.lead_bt, "/video/approve", detail)
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    # The orderer is told and holds the buttons; the head is not told.
    assert ("order_submission_ready", "Có bài chờ bạn duyệt") in await inbox(world, ads.orderer)
    assert all(kind != "order_submission_ready" for kind, _ in await inbox(world, ads.head))
    world.act_as(ads.orderer)
    mine = world.client.get(f"/api/orders/{detail['order']['id']}").json()
    assert {"APPROVE_FINAL", "RETURN_FINAL"} <= {a["kind"] for a in mine["available_actions"]}
    final = next(a for a in mine["available_actions"] if a["kind"] == "APPROVE_FINAL")
    assert final["node_id"] == dung
    # The orderer sends it back; the editor fixes it; the orderer approves.
    detail = act(world, ads.orderer, "/final/return", detail, note="Đổi nhạc")
    assert detail["order"]["stage"] == "DUNG"
    assert node(detail, "DUNG")["status"] == "DANG_SUA"
    assert ("order_final_returned", "Sản phẩm cần sửa lại") in await inbox(world, ads.editor)
    detail = act(
        world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://example.com/final_V2"
    )
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    detail = act(world, ads.lead_bt, "/video/approve", detail)
    detail = act(world, ads.orderer, "/final/approve", detail)
    assert detail["order"]["stage"] == "COMPLETED"
    assert detail["order"]["product_link"] == "https://example.com/final_V2"
    assert [a["actor_user_id"] for a in detail["approvals"] if a["gate"] == "FINAL"] == [
        str(ads.orderer.id),
        str(ads.orderer.id),
    ]
    # Another marketer may not decide it.
    other = await person(world, "Marketing khác")
    await tag(world, ads.unit, other, UnitMemberRole.ORDERER, member_code="KHAC")
    second = create(world, ads, video_type="T", preassigned={"THIET_KE": str(ads.designer.id)})
    second = act(world, ads.head, "/approve", second)
    tk = node(second, "THIET_KE")["id"]
    second = act(world, ads.designer, f"/nodes/{tk}/accept", second)
    second = act(world, ads.designer, f"/nodes/{tk}/submit", second, link="https://example.com/t")
    assert second["order"]["stage"] == "FINAL_REVIEW"
    world.act_as(other)
    refused = world.client.post(
        f"/api/orders/{second['order']['id']}/final/approve",
        json={"version": second["order"]["version"]},
    )
    assert refused.status_code in (403, 404)


async def test_16_the_video_kind_is_required_valid_and_snapshotted(world: World) -> None:
    ads = await ads_world(world)
    full = await add_kind(world, ads, "Video full diễn hoạt", "2.5")
    retired = await add_kind(world, ads, "Quay khác", active=False)
    world.act_as(ads.orderer)
    base = {
        "title": "X",
        "video_type": "TD",
        "order_content": "y",
        "desired_deadline_at": FAR_DEADLINE,
    }
    missing = world.client.post("/api/orders", json=base)
    assert missing.status_code == 422 and error_reason(missing.json()) == "video_kind_required"
    for bad in (retired.id, uuid.uuid4()):
        response = world.client.post("/api/orders", json={**base, "video_kind_id": str(bad)})
        assert response.status_code == 422 and error_reason(response.json()) == "video_kind_invalid"
    # A kind of another unit is not this unit's.
    pr_unit = await world.session.get(OrgUnit, unit_seed_id(UnitCode.PR))
    assert pr_unit is not None
    foreign = UnitVideoKind(unit_id=pr_unit.id, name="PR kind", points=Decimal("1"))
    world.session.add(foreign)
    await world.session.flush()
    response = world.client.post("/api/orders", json={**base, "video_kind_id": str(foreign.id)})
    assert response.status_code == 422 and error_reason(response.json()) == "video_kind_invalid"

    detail = create(world, ads, video_type="TD", video_kind_id=str(full.id))
    order = detail["order"]
    assert order["video_kind_id"] == str(full.id)
    assert order["video_kind_name"] == "Video full diễn hoạt"
    assert order["video_kind_points"] == 2.5
    # Editing the catalogue does not rewrite the order.
    full.name = "Video full (mới)"
    full.points = Decimal("4")
    await world.session.flush()
    world.act_as(ads.orderer)
    again = world.client.get(f"/api/orders/{order['id']}").json()["order"]
    assert again["video_kind_name"] == "Video full diễn hoạt"
    assert again["video_kind_points"] == 2.5
    # A resubmission may pick another kind, snapshotted afresh.
    short = await add_kind(world, ads, "Short video", "0.5")
    detail = act(world, ads.head, "/return", detail, note="Đổi loại")
    detail = act(world, ads.orderer, "/resubmit", detail, video_kind_id=str(short.id))
    assert detail["order"]["video_kind_name"] == "Short video"
    assert detail["order"]["video_kind_points"] == 0.5
    # With no active kind in the unit, nothing is required.
    for kind in (full, short):
        kind.active = False
    await world.session.flush()
    plain = create(world, ads, video_type="TD")
    assert plain["order"]["video_kind_id"] is None and plain["order"]["video_kind_points"] is None


# --- no link step: orders created before it was folded in --------------------------


async def legacy_link_node(world: World, detail: dict[str, Any], **values: Any) -> None:
    """Give an order the old "Gắn link" node, as orders created before the
    link step was folded into the last production node have."""
    from meobot.db.models.order import Order, OrderNode
    from meobot.domain.orders.models import OrderNodeStatus, OrderStage

    order = await world.session.get(Order, uuid.UUID(detail["order"]["id"]))
    assert order is not None
    status = values.pop("status", OrderNodeStatus.DANG_LAM)
    stage = values.pop("stage", OrderStage.GAN_LINK)
    world.session.add(
        OrderNode(order_id=order.id, node_type=OrderNodeType.GAN_LINK, status=status, **values)
    )
    order.stage = stage
    await world.session.flush()


async def test_17_a_legacy_order_at_the_old_link_step_hides_it_and_still_finishes(
    world: World,
) -> None:
    from meobot.core.time import utcnow
    from meobot.db.models.order import Order, OrderNode
    from meobot.domain.orders.models import OrderNodeStatus, OrderStage

    ads = await ads_world(world)
    detail = create(
        world,
        ads,
        video_type="D",
        design_link="https://example.com/design",
        preassigned={"DUNG": str(ads.editor.id)},
    )
    detail = act(world, ads.head, "/approve", detail)
    dung = node(detail, "DUNG")["id"]
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e.com/cut")
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    # Rewind it to what the old engine left: the link node up, nothing attached.
    order = await world.session.get(Order, uuid.UUID(detail["order"]["id"]))
    assert order is not None
    order.product_link = None
    now = utcnow()
    await legacy_link_node(
        world,
        detail,
        assignee_user_id=ads.editor.id,
        activated_at=now,
        assigned_at=now,
    )
    world.act_as(ads.editor)
    detail = world.client.get(f"/api/orders/{order.id}").json()
    assert detail["order"]["stage"] == "GAN_LINK"
    # Hidden on every screen: no node in the detail, no cell on the board.
    assert [n["node_type"] for n in detail["nodes"]] == ["BIEN_TAP", "THIET_KE", "DUNG"]
    rows = world.client.get("/api/board/tasks", params={"unit": "ADS"}).json()["items"]
    row = next(item for item in rows if item["code"] == detail["order"]["code"])
    assert [cell["key"] for cell in row["cells"]] == ["BIEN_TAP", "THIET_KE", "DUNG", "FINAL"]
    assert "Gắn link" not in row["status_label"]
    # Its holder takes it and hands the link in like the last node would.
    assert {a["kind"] for a in detail["available_actions"]} == {"ACCEPT"}
    link = next(a for a in detail["available_actions"] if a["kind"] == "ACCEPT")["node_id"]
    detail = act(world, ads.editor, f"/nodes/{link}/accept", detail)
    world.act_as(ads.editor)
    empty = world.client.post(
        f"/api/orders/{order.id}/nodes/{link}/submit",
        json={"version": detail["order"]["version"], "script_text": "x"},
    )
    assert empty.status_code == 422 and error_reason(empty.json()) == "link_required"
    detail = act(world, ads.editor, f"/nodes/{link}/submit", detail, link="https://e.com/old")
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    assert detail["order"]["product_link"] == "https://e.com/old"
    # Sent back: the edit reopens; the old link step never comes back.
    detail = act(world, ads.orderer, "/final/return", detail, note="Sửa")
    assert detail["order"]["stage"] == "DUNG" and node(detail, "DUNG")["status"] == "DANG_SUA"
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e.com/new")
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    detail = act(world, ads.orderer, "/final/approve", detail)
    assert detail["order"]["stage"] == "COMPLETED"
    assert detail["order"]["product_link"] == "https://e.com/new"
    assert len(await kpi_rows(world)) == 1

    # One waiting at the final review with its link on the old node: the
    # orderer returns it (the old node is skipped for good), then approves.
    second = create(
        world,
        ads,
        video_type="D",
        design_link="https://example.com/design",
        preassigned={"DUNG": str(ads.editor.id)},
    )
    second = act(world, ads.head, "/approve", second)
    dung2 = node(second, "DUNG")["id"]
    second = act(world, ads.editor, f"/nodes/{dung2}/accept", second)
    second = act(world, ads.editor, f"/nodes/{dung2}/submit", second, link="https://e.com/c2")
    second = act(world, ads.lead_dung, f"/nodes/{dung2}/approve", second)
    assert second["order"]["stage"] == "FINAL_REVIEW"
    await legacy_link_node(
        world,
        second,
        status=OrderNodeStatus.CHO_DUYET,
        assignee_user_id=ads.editor.id,
        stage=OrderStage.FINAL_REVIEW,
    )
    world.act_as(ads.orderer)
    second = world.client.get(f"/api/orders/{second['order']['id']}").json()
    second = act(world, ads.orderer, "/final/return", second, note="Sửa")
    assert second["order"]["stage"] == "DUNG"
    legacy = (
        await world.session.scalars(
            select(OrderNode).where(
                OrderNode.order_id == uuid.UUID(second["order"]["id"]),
                OrderNode.node_type == OrderNodeType.GAN_LINK,
            )
        )
    ).one()
    await world.session.refresh(legacy)
    assert legacy.status is OrderNodeStatus.BO_QUA
