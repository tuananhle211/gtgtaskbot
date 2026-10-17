"""Departments, seats and grants (0061).

* only the OWNER shapes departments, seats people and grants permissions;
* a person may sit in several departments, and a department may have several
  heads; a member reports to one of them;
* the order flow's stream tags follow the seats;
* any department may order once it is granted to, and an order goes to the
  orderer's own head - or, when no head of theirs approves orders, to an
  approver they must choose;
* Ban Giám đốc sees every order and report.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitCode, unit_seed_id
from tests.unit.pr_world import World
from tests.unit.test_order_commands import FAR_DEADLINE, Ads, act, ads_world, person
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio


def owner(world: World) -> Any:
    world.act_as(world.owner)
    return world.client


def department(world: World, code: str) -> dict[str, Any]:
    listed = owner(world).get("/api/departments").json()["departments"]
    return next(item for item in listed if item["code"] == code)


def seat(world: World, code: str, user: User, **body: Any) -> Any:
    target = department(world, code)
    return owner(world).put(
        f"/api/departments/{target['id']}/members", json={"user_id": str(user.id), **body}
    )


def grant(world: World, code: str, cells: dict[str, dict[str, str]]) -> Any:
    target = department(world, code)
    return owner(world).put(f"/api/departments/{target['id']}/grants", json={"cells": cells})


async def tag_of(world: World, user: User, unit: UnitCode) -> tuple[str, bool, Any] | None:
    row = await world.session.scalar(
        select(OrgUnitMember).where(
            OrgUnitMember.unit_id == unit_seed_id(unit), OrgUnitMember.user_id == user.id
        )
    )
    await world.session.refresh(row) if row is not None else None
    if row is None or row.left_at is not None:
        return None
    return (row.role.value, row.is_lead, row.manager_user_id)


def order(world: World, user: User, **body: Any) -> Any:
    world.act_as(user)
    return world.client.post(
        "/api/orders",
        json={
            "title": "Video tuyển dụng",
            "video_type": "D",
            "design_link": "https://e/d",
            "order_content": "Nội dung.",
            "desired_deadline_at": FAR_DEADLINE,
            **body,
        },
    )


async def hcns(world: World, ads: Ads) -> tuple[dict[str, Any], User, User]:
    """A department that orders: HCNS, with a head who approves its orders."""
    made = owner(world).post(
        "/api/departments", json={"code": "hc ns", "name": "Hành chính nhân sự"}
    )
    assert made.status_code == 201, made.json()
    assert made.json()["code"] == "HCNS" and made.json()["function"] == "NONE"
    head = await person(world, "Trưởng HCNS")
    staff = await person(world, "Nhân viên HCNS")
    assert seat(world, "HCNS", head, position="HEAD").status_code == 200
    assert seat(world, "HCNS", staff, manager_user_id=str(head.id)).status_code == 200
    return made.json(), head, staff


# --- the OWNER's alone -----------------------------------------------------------------


async def test_01_only_the_owner_shapes_departments(world: World) -> None:
    ads = await ads_world(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    target = department(world, "ADS")
    for refused_for in (admin, ads.head, ads.lead_tk):
        world.act_as(refused_for)
        for call in (
            lambda: world.client.post("/api/departments", json={"code": "X", "name": "X"}),
            lambda: world.client.patch(f"/api/departments/{target['id']}", json={"name": "Y"}),
            lambda: world.client.put(
                f"/api/departments/{target['id']}/members", json={"user_id": str(admin.id)}
            ),
            lambda: world.client.put(
                f"/api/departments/{target['id']}/grants", json={"cells": {"HEAD": {}}}
            ),
        ):
            refused = call()
            assert refused.status_code == 403, refused.json()
            assert error_reason(refused.json()) == "department_forbidden"
    # An ADMIN reads it, and so does a head (test_09); a plain member does not.
    world.act_as(admin)
    assert world.client.get("/api/departments").status_code == 200
    world.act_as(ads.writer)
    assert world.client.get("/api/departments").status_code == 403
    # The starting departments, with the order flow's people already seated.
    listed = {
        item["code"]: item for item in owner(world).get("/api/departments").json()["departments"]
    }
    assert list(listed) == ["ADS", "BT", "TK", "D", "PR", "TN", "BGD"]
    assert listed["BGD"]["function"] == "BOARD" and listed["TK"]["function"] == "THIET_KE"
    roster = owner(world).get(f"/api/departments/{listed['ADS']['id']}/members").json()["members"]
    assert {(item["full_name"], item["position"]) for item in roster} == {
        (ads.head.full_name, "HEAD"),
        (ads.orderer.full_name, "MEMBER"),
    }
    taken = owner(world).post("/api/departments", json={"code": "ads", "name": "Trùng"})
    assert taken.status_code == 422 and error_reason(taken.json()) == "department_code_taken"


# --- seats and the streams that follow them ---------------------------------------------


async def test_02_seats_decide_the_stream_tags(world: World) -> None:
    ads = await ads_world(world)
    newcomer = await person(world, "Quỳnh Như")
    assert await tag_of(world, newcomer, UnitCode.ADS) is None
    # Thiết Kế, member: Design staff in the order flow.
    assert seat(world, "TK", newcomer).status_code == 200
    assert await tag_of(world, newcomer, UnitCode.ADS) == ("THIET_KE", False, None)
    # Reporting to the Design Leader: their own Leader in the flow too.
    assert seat(world, "TK", newcomer, manager_user_id=str(ads.lead_tk.id)).status_code == 200
    assert await tag_of(world, newcomer, UnitCode.ADS) == ("THIET_KE", False, ads.lead_tk.id)
    # A second department at once: PR Thương Hiệu.
    assert seat(world, "PR", newcomer).status_code == 200
    assert await tag_of(world, newcomer, UnitCode.PR) == ("MEMBER", False, None)
    assert (await tag_of(world, newcomer, UnitCode.ADS) or ("",))[0] == "THIET_KE"
    # Head of Thiết Kế: a second Design Leader (one per branch); no manager.
    assert seat(world, "TK", newcomer, position="HEAD").status_code == 200
    assert await tag_of(world, newcomer, UnitCode.ADS) == ("THIET_KE", True, None)
    # A manager must head the same department.
    wrong = seat(world, "TK", ads.designer, manager_user_id=str(ads.lead_dung.id))
    assert wrong.status_code == 422 and error_reason(wrong.json()) == "department_manager_invalid"
    assert seat(world, "TK", ads.designer, manager_user_id=str(newcomer.id)).status_code == 200
    # Out of Thiết Kế: out of the order flow, still in PR. Their staff's link drops.
    target = department(world, "TK")
    gone = owner(world).delete(f"/api/departments/{target['id']}/members/{newcomer.id}")
    assert gone.status_code == 204
    assert await tag_of(world, newcomer, UnitCode.ADS) is None
    assert await tag_of(world, newcomer, UnitCode.PR) == ("MEMBER", False, None)
    assert await tag_of(world, ads.designer, UnitCode.ADS) == ("THIET_KE", False, None)
    world.act_as(newcomer)
    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["PR"]
    assert [(item["code"], item["position"]) for item in me["departments"]] == [("PR", "MEMBER")]


async def test_03_somebody_with_open_order_work_is_not_taken_out_of_the_flow(world: World) -> None:
    ads = await ads_world(world)
    placed = order(world, ads.orderer)
    assert placed.status_code == 201, placed.json()
    target = department(world, "ADS")
    refused = owner(world).delete(f"/api/departments/{target['id']}/members/{ads.orderer.id}")
    assert refused.status_code == 422 and error_reason(refused.json()) == "member_has_open_work"


# --- any department may order; its own head approves -------------------------------------


async def test_04_another_department_orders_once_granted_and_its_head_approves(
    world: World,
) -> None:
    ads = await ads_world(world)
    _, head, staff = await hcns(world, ads)
    # Seated, but granted nothing: not in the order flow.
    assert await tag_of(world, staff, UnitCode.ADS) is None
    assert order(world, staff).status_code == 404
    assert (
        grant(
            world,
            "HCNS",
            {
                "HEAD": {"VIEW": "OWN", "ORDER_CREATE": "ALL", "ORDER_APPROVE": "OWN"},
                "MEMBER": {"VIEW": "OWN", "ORDER_CREATE": "ALL"},
            },
        ).status_code
        == 200
    )
    assert await tag_of(world, staff, UnitCode.ADS) == ("ORDERER", False, head.id)
    assert await tag_of(world, head, UnitCode.ADS) == ("HEAD", False, None)
    world.act_as(staff)
    routes = world.client.get("/api/orders/approvers").json()
    assert routes["own_gate"] is False and routes["required"] is False
    assert [item["name"] for item in routes["default"]] == [head.full_name]
    placed = order(world, staff)
    assert placed.status_code == 201, placed.json()
    detail = placed.json()
    code = detail["order"]["code"]
    # It waits on the HCNS head - not on the ADS head - who sees and approves it.
    world.act_as(head)
    waiting = world.client.get(
        "/api/board/tasks", params={"unit": "ADS", "awaiting_me": "true"}
    ).json()["items"]
    assert [item["code"] for item in waiting] == [code]
    assert waiting[0]["status_label"] == f"Chờ {head.full_name} duyệt order"
    world.act_as(ads.head)
    assert world.client.get(f"/api/tasks/{code}").status_code == 404
    detail = act(world, head, "/approve", detail)
    assert detail["order"]["stage"] == "DUNG"
    # The HCNS head's own order needs no approval.
    own = order(world, head)
    assert own.status_code == 201 and own.json()["order"]["stage"] == "DUNG"


async def test_05_with_no_head_of_theirs_approving_the_orderer_must_choose(world: World) -> None:
    ads = await ads_world(world)
    _, head, staff = await hcns(world, ads)
    # HCNS may order, but its head does not approve orders.
    assert (
        grant(
            world,
            "HCNS",
            {"HEAD": {"ORDER_CREATE": "ALL"}, "MEMBER": {"VIEW": "OWN", "ORDER_CREATE": "ALL"}},
        ).status_code
        == 200
    )
    world.act_as(staff)
    routes = world.client.get("/api/orders/approvers").json()
    assert routes["required"] is True and routes["default"] == []
    assert ads.head.full_name in [item["name"] for item in routes["candidates"]]
    assert head.full_name not in [item["name"] for item in routes["candidates"]]
    refused = order(world, staff)
    assert refused.status_code == 422 and error_reason(refused.json()) == "approver_required"
    wrong = order(world, staff, approver_user_id=str(head.id))
    assert wrong.status_code == 422 and error_reason(wrong.json()) == "approver_invalid"
    placed = order(world, staff, approver_user_id=str(ads.head.id))
    assert placed.status_code == 201, placed.json()
    act(world, ads.head, "/approve", placed.json())


async def test_06_several_heads_each_approve_the_staff_who_report_to_them(world: World) -> None:
    ads = await ads_world(world)
    second = await person(world, "Trưởng phòng ADS chi nhánh 2", Role.EMPLOYEE)
    branch_staff = await person(world, "Nhân viên chi nhánh 2")
    assert seat(world, "ADS", second, position="HEAD").status_code == 200
    assert seat(world, "ADS", branch_staff, manager_user_id=str(second.id)).status_code == 200
    placed = order(world, branch_staff)
    assert placed.status_code == 201, placed.json()
    code = placed.json()["order"]["code"]

    def waiting(user: User) -> list[str]:
        world.act_as(user)
        rows = world.client.get(
            "/api/board/tasks", params={"unit": "ADS", "awaiting_me": "true"}
        ).json()["items"]
        return [row["code"] for row in rows]

    assert waiting(second) == [code]
    assert code not in waiting(ads.head)
    world.act_as(ads.head)
    assert world.client.get(f"/api/tasks/{code}").status_code == 404
    # Without a manager, the orderer's order waits on every ADS head.
    loose = order(world, ads.orderer)
    assert loose.status_code == 201
    assert loose.json()["order"]["code"] in waiting(second)
    assert loose.json()["order"]["code"] in waiting(ads.head)


# --- Ban Giám đốc --------------------------------------------------------------------------


async def test_07_the_board_sees_every_order_and_report(world: World) -> None:
    ads = await ads_world(world)
    director = await person(world, "Giám đốc")
    placed = order(world, ads.orderer)
    assert placed.status_code == 201
    code = placed.json()["order"]["code"]
    world.act_as(director)
    assert world.client.get("/api/board/tasks", params={"unit": "ADS"}).status_code == 404
    assert seat(world, "BGD", director).status_code == 200
    world.act_as(director)
    rows = world.client.get("/api/board/tasks", params={"unit": "ADS"}).json()["items"]
    assert code in [row["code"] for row in rows]
    page = world.client.get(f"/api/tasks/{code}").json()
    # Sees, decides nothing.
    assert page["actions"] == []
    assert world.client.get("/api/board/tasks", params={"unit": "PR"}).status_code == 200
    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["ADS", "PR"]


# --- grants ---------------------------------------------------------------------------------


async def test_08_grants_are_read_whole_and_written_cell_by_cell(world: World) -> None:
    await ads_world(world)
    target = department(world, "ADS")
    cells = owner(world).get(f"/api/departments/{target['id']}/grants").json()["cells"]
    assert cells["HEAD"]["ORDER_APPROVE"] == "OWN" and cells["HEAD"]["NODE_ASSIGN"] == "NONE"
    assert cells["MEMBER"]["ORDER_CREATE"] == "ALL" and cells["MEMBER"]["ORDER_APPROVE"] == "NONE"
    for bad in (
        {"BOSS": {"VIEW": "ALL"}},
        {"HEAD": {"FLY": "ALL"}},
        {"HEAD": {"ORDER_CREATE": "OWN"}},
    ):
        refused = grant(world, "ADS", bad)
        assert refused.status_code == 422, bad
        assert error_reason(refused.json()) == "department_grant_invalid"
    changed = grant(world, "ADS", {"MEMBER": {"ORDER_CREATE": "NONE"}})
    assert changed.status_code == 200
    assert changed.json()["cells"]["MEMBER"]["ORDER_CREATE"] == "NONE"
    assert changed.json()["cells"]["MEMBER"]["VIEW"] == "OWN"  # left alone
    catalog = owner(world).get("/api/departments/catalog").json()
    assert [item["value"] for item in catalog["positions"]] == ["HEAD", "MEMBER"]
    approve = next(item for item in catalog["permissions"] if item["key"] == "ORDER_APPROVE")
    assert approve["scopes"] == ["NONE", "OWN", "ALL"]


# --- a head and their own department ---------------------------------------------------


async def test_09_a_head_seats_the_members_of_their_own_department(world: World) -> None:
    """2026-10-17: a department's head reads the department list and their own
    roster, and adds or removes *members* of their own department - never a
    head, never another department, never an OWNER / ADMIN account, never
    themselves. Everything else here stays the OWNER's."""
    ads = await ads_world(world)
    tk = department(world, "TK")
    bt = department(world, "BT")
    newcomer = await person(world, "Designer mới")
    admin = await person(world, "Quản trị 2", Role.ADMIN)

    world.act_as(ads.lead_tk)  # Trưởng phòng Design = HEAD of TK
    client = world.client
    assert client.get("/api/departments").status_code == 200
    assert client.get("/api/departments/catalog").status_code == 200
    assert client.get(f"/api/departments/{tk['id']}/members").status_code == 200
    assert client.get(f"/api/departments/{tk['id']}/grants").status_code == 200
    assert client.get(f"/api/departments/{bt['id']}/members").status_code == 403
    # ...and the account directory, to pick people from outside their roster.
    directory = client.get("/api/units/directory")
    assert directory.status_code == 200, directory.text
    assert any(
        user["full_name"] == newcomer.full_name and user["departments"] == []
        for user in directory.json()
    )

    seated = client.put(f"/api/departments/{tk['id']}/members", json={"user_id": str(newcomer.id)})
    assert seated.status_code == 200, seated.json()
    assert seated.json()["position"] == "MEMBER"
    # ...who reports to them from then on.
    assert seated.json()["manager_user_id"] == str(ads.lead_tk.id)
    # Somebody already seated elsewhere is the OWNER's to move (one person in
    # several departments), and a head's directory offers only the unseated.
    elsewhere = client.put(
        f"/api/departments/{tk['id']}/members", json={"user_id": str(ads.writer.id)}
    )
    assert elsewhere.status_code == 403 and error_reason(elsewhere.json()) == "department_forbidden"
    assert all(user["departments"] == [] for user in client.get("/api/units/directory").json())
    # The seat decides the stream tag, as for the OWNER's own seating.
    tagged = await tag_of(world, newcomer, UnitCode.ADS)
    assert tagged is not None and tagged[:2] == ("THIET_KE", False)

    for body, target in (
        ({"user_id": str(newcomer.id), "position": "HEAD"}, tk),
        ({"user_id": str(newcomer.id)}, bt),
        ({"user_id": str(admin.id)}, tk),
        ({"user_id": str(ads.lead_tk.id), "position": "MEMBER"}, tk),
    ):
        refused = client.put(f"/api/departments/{target['id']}/members", json=body)
        assert refused.status_code == 403, (body, refused.json())
        assert error_reason(refused.json()) == "department_forbidden"
    for call in (
        lambda: client.post("/api/departments", json={"code": "X", "name": "X"}),
        lambda: client.patch(f"/api/departments/{tk['id']}", json={"name": "Y"}),
        lambda: client.put(f"/api/departments/{tk['id']}/grants", json={"cells": {"HEAD": {}}}),
    ):
        refused = call()
        assert refused.status_code == 403, refused.json()

    gone = client.delete(f"/api/departments/{tk['id']}/members/{newcomer.id}")
    assert gone.status_code == 204, gone.text
    assert await tag_of(world, newcomer, UnitCode.ADS) is None
    # Their own seat, a head's seat, is not theirs to close.
    assert client.delete(f"/api/departments/{tk['id']}/members/{ads.lead_tk.id}").status_code == 403

    # The head also lists their own people on the account roster - the members
    # reporting to them (the designer reports to nobody, and TK has one head) -
    # with the seats every roster now shows instead of stream tags.
    listed = client.get("/api/account/members")
    assert listed.status_code == 200, listed.text
    rows = {row["full_name"]: row for row in listed.json()["members"]}
    assert ads.designer.full_name in rows and ads.lead_tk.full_name in rows
    assert ads.writer.full_name not in rows and ads.head.full_name not in rows
    assert [
        (seat["code"], seat["position"]) for seat in rows[ads.lead_tk.full_name]["departments"]
    ] == [("TK", "HEAD")]
    assert rows[ads.designer.full_name]["departments"][0]["name"]

    # A plain member reads nothing here.
    world.act_as(ads.designer)
    assert world.client.get("/api/departments").status_code == 403
    assert world.client.get("/api/account/members").status_code == 403
