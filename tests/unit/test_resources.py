"""Resources ("Quản lý tài nguyên", đợt 1, 0065), over HTTP.

* access follows the departments' ``RESOURCES`` grants: the Phòng Tài
  nguyên runs the module, Ban Giám đốc sees all, a head sees their
  department's, a member what they hold, an outsider nothing;
* codes are allocated per department (``ADS-001``) and never change; no two
  resources of one type share an id;
* every change leaves its line: handovers, statuses, field changes - and the
  database refuses to rewrite them;
* the alerts: a leaver, a mover, an overdue review, a loss;
* pick-lists and the review threshold are the Phòng TN head's; the look-up
  by employee and the summary report count right;
* the Excel export keeps ids as text; the import previews, refuses errors,
  then writes codes, holders and month statuses.
"""

from __future__ import annotations

import base64
import io
import uuid
from datetime import timedelta
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from meobot.core.time import utcnow
from meobot.db.models.resource import Resource, ResourceHandover
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.resources.models import looks_like_secret
from tests.unit.pr_world import World
from tests.unit.test_departments import department, owner, seat
from tests.unit.test_order_commands import Ads, ads_world, person
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio


# --- the cast ---------------------------------------------------------------------------


class Cast:
    def __init__(self, ads: Ads, tn_head: User, tn_staff: User, director: User) -> None:
        self.ads = ads
        self.tn_head = tn_head
        self.tn_staff = tn_staff
        self.director = director


async def cast(world: World) -> Cast:
    """The order world plus the Phòng Tài nguyên (a head and a member) and a
    director in Ban Giám đốc."""
    ads = await ads_world(world)
    tn_head = await person(world, "Trưởng phòng Tài nguyên")
    tn_staff = await person(world, "Nhân viên Tài nguyên")
    director = await person(world, "Giám đốc")
    assert seat(world, "TN", tn_head, position="HEAD").status_code == 200
    assert seat(world, "TN", tn_staff).status_code == 200
    assert seat(world, "BGD", director, position="HEAD").status_code == 200
    return Cast(ads, tn_head, tn_staff, director)


def options(world: World, user: User) -> dict[str, Any]:
    world.act_as(user)
    response = world.client.get("/api/resources/options")
    assert response.status_code == 200, response.json()
    return response.json()


def option_id(catalog: dict[str, Any], kind: str, code: str) -> str:
    kinds = next(item for item in catalog["kinds"] if item["kind"] == kind)
    return next(item["id"] for item in kinds["options"] if item["code"] == code)


def department_id(catalog: dict[str, Any], code: str) -> str:
    return next(item["id"] for item in catalog["departments"] if item["code"] == code)


def create(
    world: World, user: User, catalog: dict[str, Any], *, department: str = "ADS", **body: Any
) -> dict[str, Any]:
    world.act_as(user)
    payload = {
        "department_id": department_id(catalog, department),
        "type_id": option_id(catalog, "TYPE", "VIA"),
        "external_id": body.pop("external_id", "100012345678901"),
        "name": body.pop("name", "Via chạy ads"),
        **body,
    }
    response = world.client.post("/api/resources", json=payload)
    assert response.status_code == 201, response.json()
    return response.json()


def me(world: World, user: User) -> dict[str, Any]:
    world.act_as(user)
    response = world.client.get("/api/units/me")
    assert response.status_code == 200, response.json()
    return response.json()["resources"]


# --- access ---------------------------------------------------------------------------


async def test_01_access_follows_the_departments(world: World) -> None:
    people = await cast(world)
    ads = people.ads
    # The Phòng TN head: everything. Its member: view all, edit, report.
    assert me(world, people.tn_head) == {
        "view": True,
        "view_all": True,
        "edit": True,
        "import_excel": True,
        "settings": True,
        "report": True,
        "hr": True,
    }
    staff = me(world, people.tn_staff)
    assert staff["view_all"] and staff["edit"] and staff["report"]
    assert not staff["import_excel"] and not staff["settings"] and not staff["hr"]
    # Ban Giám đốc: sees everything and the reports, edits nothing.
    director = me(world, people.director)
    assert director["view_all"] and director["report"] and not director["edit"]
    # A head of another department: their own department; a member: what they hold.
    head = me(world, ads.head)
    assert head["view"] and not head["view_all"] and not head["edit"]
    orderer = me(world, ads.orderer)
    assert orderer["view"] and not orderer["view_all"]
    # Somebody with no seat: nothing, and the listing is refused.
    nobody = await person(world, "Người ngoài")
    assert me(world, nobody)["view"] is False
    world.act_as(nobody)
    refused = world.client.get("/api/resources")
    assert refused.status_code == 403 and error_reason(refused.json()) == "resource_forbidden"
    # The OWNER sees and does everything without a seat.
    assert me(world, world.owner)["settings"] is True


async def test_02_grants_are_edited_per_module(world: World) -> None:
    await cast(world)
    tn = department(world, "TN")
    cells = owner(world).get(f"/api/departments/{tn['id']}/grants?module=RESOURCES").json()
    assert cells["module"] == "RESOURCES"
    assert (
        cells["cells"]["HEAD"]["IMPORT"] == "ALL" and cells["cells"]["MEMBER"]["IMPORT"] == "NONE"
    )
    # The order flow's cells are untouched by the module's defaults.
    order_cells = owner(world).get(f"/api/departments/{tn['id']}/grants").json()["cells"]
    assert order_cells["HEAD"]["VIEW"] == "NONE"
    changed = owner(world).put(
        f"/api/departments/{tn['id']}/grants",
        json={"module": "RESOURCES", "cells": {"MEMBER": {"IMPORT": "ALL"}}},
    )
    assert changed.status_code == 200 and changed.json()["cells"]["MEMBER"]["IMPORT"] == "ALL"
    bad = owner(world).put(
        f"/api/departments/{tn['id']}/grants",
        json={"module": "RESOURCES", "cells": {"MEMBER": {"EDIT": "OWN"}}},
    )
    assert bad.status_code == 422 and error_reason(bad.json()) == "department_grant_invalid"
    unknown = owner(world).put(
        f"/api/departments/{tn['id']}/grants", json={"module": "HR", "cells": {}}
    )
    assert unknown.status_code == 422
    catalog = owner(world).get("/api/departments/catalog").json()
    assert [item["module"] for item in catalog["modules"]] == ["ORDER", "PR", "RESOURCES"]
    # A department created later has the module's defaults from the start.
    made = owner(world).post("/api/departments", json={"code": "HR", "name": "Nhân sự"})
    assert made.status_code == 201
    fresh = owner(world).get(f"/api/departments/{made.json()['id']}/grants?module=RESOURCES").json()
    assert fresh["cells"]["HEAD"]["VIEW"] == "OWN" and fresh["cells"]["MEMBER"]["VIEW"] == "OWN"


# --- codes and ids ----------------------------------------------------------------------


async def test_03_codes_are_per_department_and_ids_unique_per_type(world: World) -> None:
    people = await cast(world)
    catalog = options(world, people.tn_head)
    first = create(world, people.tn_head, catalog, external_id="1001")["resource"]
    second = create(world, people.tn_head, catalog, external_id="1002")["resource"]
    third = create(world, people.tn_head, catalog, department="BT", external_id="1003")["resource"]
    assert (first["code"], second["code"], third["code"]) == ("ADS-001", "ADS-002", "BT-001")
    assert first["status"]["code"] == "ACTIVE" and first["handover"]["code"] == "NOT_YET"
    assert first["reviewed_at"] is not None
    # Moving to another department keeps the code.
    moved = world.client.patch(
        f"/api/resources/{first['id']}", json={"department_id": department_id(catalog, "BT")}
    )
    assert moved.status_code == 200 and moved.json()["resource"]["code"] == "ADS-001"
    assert moved.json()["resource"]["department"]["code"] == "BT"
    # The same id in the same type is refused; in another type it is fine.
    duplicate = world.client.post(
        "/api/resources",
        json={
            "department_id": department_id(catalog, "ADS"),
            "type_id": option_id(catalog, "TYPE", "VIA"),
            "external_id": " 1001 ",
            "name": "Trùng",
        },
    )
    assert duplicate.status_code == 422 and error_reason(duplicate.json()) == "resource_id_taken"
    assert duplicate.json()["error"]["details"]["field"] == "external_id"
    other_type = create(
        world,
        people.tn_head,
        catalog,
        external_id="1001",
        type_id=option_id(catalog, "TYPE", "PAGE"),
    )
    assert other_type["resource"]["code"] == "ADS-003"
    # A long id and a leading zero stay as typed.
    phone = create(
        world, people.tn_head, catalog, external_id="104000000000000", phone="0912000111"
    )
    assert phone["resource"]["external_id"] == "104000000000000"
    assert phone["resource"]["phone"] == "0912000111"
    # A member who may not edit is refused; the code and the status are not editable fields.
    world.act_as(people.ads.head)
    refused = world.client.post(
        "/api/resources",
        json={
            "department_id": department_id(catalog, "ADS"),
            "type_id": option_id(catalog, "TYPE", "VIA"),
            "external_id": "9",
            "name": "x",
        },
    )
    assert refused.status_code == 403 and error_reason(refused.json()) == "resource_forbidden"
    world.act_as(people.tn_head)
    locked = world.client.patch(
        f"/api/resources/{first['id']}", json={"status_id": option_id(catalog, "STATUS", "DIE")}
    )
    assert locked.status_code == 422
    assert error_reason(locked.json()) == "resource_field_not_editable"


# --- scope ----------------------------------------------------------------------------


async def test_04_everyone_sees_exactly_their_share(world: World) -> None:
    people = await cast(world)
    ads = people.ads
    catalog = options(world, people.tn_head)
    held = create(
        world, people.tn_head, catalog, external_id="a1", holder_user_id=str(ads.orderer.id)
    )["resource"]
    other_ads = create(world, people.tn_head, catalog, external_id="a2")["resource"]
    bt = create(world, people.tn_head, catalog, department="BT", external_id="b1")["resource"]

    def codes(user: User, **params: Any) -> list[str]:
        world.act_as(user)
        response = world.client.get("/api/resources", params=params)
        assert response.status_code == 200, response.json()
        return [item["code"] for item in response.json()["items"]]

    assert codes(people.tn_staff) == [held["code"], other_ads["code"], bt["code"]]
    assert codes(people.director) == [held["code"], other_ads["code"], bt["code"]]
    # The ADS head: ADS's resources only.
    assert codes(ads.head) == [held["code"], other_ads["code"]]
    # The orderer: what they hold - "Tài nguyên của tôi" says the same.
    assert codes(ads.orderer) == [held["code"]]
    assert codes(ads.orderer, mine="true") == [held["code"]]
    assert codes(people.tn_staff, mine="true") == []
    # Design's Leader: nothing in Thiết Kế yet.
    assert codes(ads.lead_tk) == []
    # Outside one's share, a resource is not found - and cannot be acted on.
    world.act_as(ads.head)
    assert world.client.get(f"/api/resources/{bt['id']}").status_code == 404
    world.act_as(ads.orderer)
    assert world.client.get(f"/api/resources/{other_ads['id']}").status_code == 404
    hidden = world.client.post(f"/api/resources/{other_ads['id']}/review", json={"note": "x"})
    assert hidden.status_code in (403, 404)
    # Filters: holder, department, search.
    assert codes(people.tn_head, holder=str(ads.orderer.id)) == [held["code"]]
    assert codes(people.tn_head, holder="none") == [other_ads["code"], bt["code"]]
    assert codes(people.tn_head, department_id=department_id(catalog, "BT")) == [bt["code"]]
    assert codes(people.tn_head, q="b1") == [bt["code"]]
    assert codes(people.tn_head, sort="code", desc="true")[0] == bt["code"]


# --- histories --------------------------------------------------------------------------


async def test_05_every_change_leaves_its_line_and_history_cannot_be_rewritten(
    world: World,
) -> None:
    people = await cast(world)
    ads = people.ads
    catalog = options(world, people.tn_head)
    made = create(world, people.tn_head, catalog, external_id="h1", note="dùng cho TKQC")
    resource_id = made["resource"]["id"]
    assert [line["note"] for line in made["statuses"]] == ["Tạo mới"]
    assert made["handovers"] == []
    # Hand it to the orderer: one handover line, the change log names the person.
    handed = world.client.post(
        f"/api/resources/{resource_id}/holder",
        json={"holder_user_id": str(ads.orderer.id), "reason": "NEW", "note": "Cấp cho MKT"},
    )
    assert handed.status_code == 200, handed.json()
    line = handed.json()["handovers"][0]
    assert line["from_person"] is None and line["to_person"]["user_id"] == str(ads.orderer.id)
    assert line["reason"] == "NEW" and line["reason_label"] == "Cấp mới"
    assert line["actor"]["user_id"] == str(people.tn_head.id)
    # Then to the writer, then back to the Phòng TN.
    moved = world.client.post(
        f"/api/resources/{resource_id}/holder",
        json={"holder_user_id": str(ads.writer.id), "reason": "TRANSFER"},
    )
    assert moved.status_code == 200
    same = world.client.post(
        f"/api/resources/{resource_id}/holder",
        json={"holder_user_id": str(ads.writer.id), "reason": "TRANSFER"},
    )
    assert same.status_code == 422 and error_reason(same.json()) == "resource_holder_unchanged"
    returned = world.client.post(
        f"/api/resources/{resource_id}/holder",
        json={
            "holder_user_id": None,
            "reason": "RETURN",
            "handover_id": option_id(catalog, "HANDOVER", "DONE"),
        },
    )
    assert returned.status_code == 200
    assert returned.json()["resource"]["holder"] is None
    assert returned.json()["resource"]["handover"]["code"] == "DONE"
    assert [item["reason"] for item in returned.json()["handovers"]] == [
        "RETURN",
        "TRANSFER",
        "NEW",
    ]
    # A status update: a line, and the review date moves.
    before = returned.json()["resource"]["reviewed_at"]
    checkpoint = world.client.post(
        f"/api/resources/{resource_id}/status",
        json={"status_id": option_id(catalog, "STATUS", "CHECKPOINT"), "note": "Bị FB hỏi giấy tờ"},
    )
    assert checkpoint.status_code == 200, checkpoint.json()
    top = checkpoint.json()["statuses"][0]
    assert top["from_status"]["code"] == "ACTIVE" and top["to_status"]["code"] == "CHECKPOINT"
    assert top["review_only"] is False and top["note"] == "Bị FB hỏi giấy tờ"
    assert checkpoint.json()["resource"]["reviewed_at"] >= before
    # A review that changes nothing still writes "Đã rà soát".
    reviewed = world.client.post(f"/api/resources/{resource_id}/review", json={})
    assert reviewed.status_code == 200
    assert reviewed.json()["statuses"][0]["review_only"] is True
    assert reviewed.json()["statuses"][0]["note"] == "Đã rà soát"
    # An edit: one change line per field, old → new in words.
    edited = world.client.patch(
        f"/api/resources/{resource_id}",
        json={
            "name": "Via chạy ads (mới)",
            "role_id": option_id(catalog, "ROLE", "ADVERTISER"),
            "two_factor_id": option_id(catalog, "TWO_FACTOR", "YES"),
            "two_factor_holder_user_id": str(people.tn_staff.id),
        },
    )
    assert edited.status_code == 200, edited.json()
    changes: dict[str, Any] = {}
    for item in edited.json()["changes"]:  # newest first: the first line per field
        changes.setdefault(item["field"], item)
    assert changes["name"]["old_value"] == "Via chạy ads"
    assert changes["name"]["new_value"] == "Via chạy ads (mới)"
    assert changes["role_id"]["new_value"] == "Nhà quảng cáo"
    assert changes["two_factor_holder_user_id"]["new_value"] == people.tn_staff.full_name
    assert changes["holder_user_id"]["old_value"] == ads.writer.full_name
    assert changes["holder_user_id"]["new_value"] is None
    assert changes["status_id"]["new_value"] == "Checkpoint"
    assert "created" in changes
    # The note looks like a password: a warning, not a refusal.
    assert looks_like_secret("pass: abc") and looks_like_secret("Mật khẩu: 123")
    assert not looks_like_secret("dùng cho TKQC")
    warned = world.client.patch(f"/api/resources/{resource_id}", json={"note": "mật khẩu 123456"})
    assert warned.status_code == 200 and warned.json()["resource"]["secret_warning"] is True
    # No delete route: a resource is never removed.
    assert world.client.delete(f"/api/resources/{resource_id}").status_code == 405
    # The database refuses to rewrite history.
    with pytest.raises(DBAPIError):
        await world.session.execute(update(ResourceHandover).values(note="đã sửa"))
    await world.session.rollback()


# --- alerts ---------------------------------------------------------------------------


async def test_06_a_leaver_a_mover_an_overdue_review_and_a_loss_warn(world: World) -> None:
    people = await cast(world)
    ads = people.ads
    catalog = options(world, people.tn_head)
    at_orderer = create(
        world, people.tn_head, catalog, external_id="w1", holder_user_id=str(ads.orderer.id)
    )["resource"]
    at_writer = create(
        world,
        people.tn_head,
        catalog,
        department="BT",
        external_id="w2",
        holder_user_id=str(ads.writer.id),
    )["resource"]
    stale = create(world, people.tn_head, catalog, external_id="w3")["resource"]
    lost = create(world, people.tn_head, catalog, external_id="w4")["resource"]
    moved = create(
        world, people.tn_head, catalog, external_id="w5", holder_user_id=str(ads.writer.id)
    )["resource"]

    def alerts(resource_id: str) -> tuple[list[str], bool]:
        world.act_as(people.tn_head)
        detail = world.client.get(f"/api/resources/{resource_id}").json()["resource"]
        return [item["kind"] for item in detail["alerts"]], detail["flagged"]

    assert alerts(at_orderer["id"]) == ([], False)
    # The orderer leaves: the Phòng TN head says so (HR_STATUS); the row turns red.
    world.act_as(people.tn_head)
    left = world.client.put(
        f"/api/account/members/{ads.orderer.id}/employment-status",
        json={"employment_status": "LEFT"},
    )
    assert left.status_code == 200 and left.json()["employment_status_label"] == "Đã nghỉ việc"
    assert alerts(at_orderer["id"]) == (["HOLDER_LEFT"], True)
    # The writer holds a Biên Tập resource and an ADS one: the ADS one is a "mover".
    assert alerts(at_writer["id"]) == ([], False)
    assert alerts(moved["id"]) == (["HOLDER_MOVED"], True)
    # A review older than the threshold.
    row = await world.session.get(Resource, uuid.UUID(stale["id"]))
    assert row is not None
    row.reviewed_at = utcnow() - timedelta(days=31)
    await world.session.flush()
    assert alerts(stale["id"]) == (["REVIEW_OVERDUE"], False)
    world.act_as(people.tn_head)
    assert world.client.put("/api/resources/settings", json={"review_days": 60}).status_code == 200
    assert alerts(stale["id"]) == ([], False)
    # Die: a loss, red.
    world.act_as(people.tn_head)
    assert (
        world.client.post(
            f"/api/resources/{lost['id']}/status",
            json={"status_id": option_id(catalog, "STATUS", "DIE")},
        ).status_code
        == 200
    )
    assert alerts(lost["id"]) == (["STATUS_LOST"], True)
    # The warning list, and the "alerts" filter, hold exactly those.
    warnings = world.client.get("/api/resources/reports/warnings").json()
    assert {item["code"] for item in warnings["items"]} == {
        at_orderer["code"],
        moved["code"],
        lost["code"],
    }
    counts = {item["kind"]: item["count"] for item in warnings["counts"]}
    assert counts["HOLDER_LEFT"] == 1 and counts["STATUS_LOST"] == 1 and counts["HOLDER_MOVED"] == 1
    flagged = world.client.get("/api/resources", params={"alerts": "true"}).json()
    assert flagged["total"] == 3
    by_employee = world.client.get("/api/resources", params={"employment_status": "LEFT"}).json()
    assert [item["code"] for item in by_employee["items"]] == [at_orderer["code"]]
    # Only OWNER / ADMIN / HR_STATUS holders set the employment status.
    world.act_as(people.tn_staff)
    refused = world.client.put(
        f"/api/account/members/{ads.writer.id}/employment-status",
        json={"employment_status": "ON_LEAVE"},
    )
    assert refused.status_code == 403
    assert error_reason(refused.json()) == "employment_status_forbidden"
    world.act_as(world.owner)
    members = world.client.get("/api/account/members").json()["members"]
    row_of = next(item for item in members if item["user_id"] == str(ads.orderer.id))
    assert row_of["employment_status"] == "LEFT"


# --- pick-lists and settings --------------------------------------------------------------


async def test_07_the_phong_tn_head_keeps_the_pick_lists(world: World) -> None:
    people = await cast(world)
    world.act_as(people.tn_staff)
    refused = world.client.post("/api/resources/options", json={"kind": "TYPE", "name": "Zalo OA"})
    assert refused.status_code == 403 and error_reason(refused.json()) == "resource_forbidden"
    world.act_as(people.tn_head)
    made = world.client.post("/api/resources/options", json={"kind": "TYPE", "name": "Zalo OA"})
    assert made.status_code == 201, made.json()
    assert made.json()["code"] == "ZALO_OA" and made.json()["active"] is True
    again = world.client.post("/api/resources/options", json={"kind": "TYPE", "name": "Zalo OA"})
    assert again.status_code == 201 and again.json()["code"] == "ZALO_OA_2"
    no_group = world.client.post(
        "/api/resources/options", json={"kind": "STATUS", "name": "Tạm khoá"}
    )
    assert no_group.status_code == 422
    assert error_reason(no_group.json()) == "resource_status_group_missing"
    status = world.client.post(
        "/api/resources/options",
        json={"kind": "STATUS", "name": "Tạm khoá", "status_group": "ATTENTION"},
    )
    assert status.status_code == 201 and status.json()["status_group_label"] == "Cần chú ý"
    renamed = world.client.patch(
        f"/api/resources/options/{made.json()['id']}",
        json={"name": "Zalo OA (chính thức)", "active": False},
    )
    assert renamed.status_code == 200 and renamed.json()["active"] is False
    listed = world.client.get("/api/resources/options").json()
    types = next(item for item in listed["kinds"] if item["kind"] == "TYPE")["options"]
    assert "ZALO_OA" not in {item["code"] for item in types}
    with_inactive = world.client.get(
        "/api/resources/options", params={"include_inactive": "true"}
    ).json()
    types = next(item for item in with_inactive["kinds"] if item["kind"] == "TYPE")["options"]
    assert "ZALO_OA" in {item["code"] for item in types}
    assert listed["can_manage"] is True and listed["review_days"] == 30
    # Reorder.
    ids = [item["id"] for item in types]
    reordered = world.client.put(
        "/api/resources/options/order", json={"kind": "TYPE", "ids": ids[::-1]}
    )
    assert reordered.status_code == 200
    assert [item["id"] for item in reordered.json()] == ids[::-1]
    # The threshold.
    assert world.client.put("/api/resources/settings", json={"review_days": 0}).status_code == 422
    assert world.client.put("/api/resources/settings", json={"review_days": 45}).status_code == 200
    assert world.client.get("/api/resources/settings").json() == {"review_days": 45}


# --- look-up and report -----------------------------------------------------------------


async def test_08_the_look_up_by_employee_and_the_summary(world: World) -> None:
    people = await cast(world)
    ads = people.ads
    catalog = options(world, people.tn_head)
    via = create(
        world, people.tn_head, catalog, external_id="r1", holder_user_id=str(ads.orderer.id)
    )
    page = create(
        world,
        people.tn_head,
        catalog,
        external_id="r2",
        type_id=option_id(catalog, "TYPE", "PAGE"),
        holder_user_id=str(ads.orderer.id),
        handover_id=option_id(catalog, "HANDOVER", "DONE"),
    )
    gone = create(
        world, people.tn_head, catalog, external_id="r3", holder_user_id=str(ads.orderer.id)
    )
    world.client.post(
        f"/api/resources/{gone['resource']['id']}/holder",
        json={"holder_user_id": str(ads.writer.id), "reason": "TRANSFER"},
    )
    create(world, people.tn_head, catalog, department="BT", external_id="r4")
    world.client.post(
        f"/api/resources/{via['resource']['id']}/status",
        json={"status_id": option_id(catalog, "STATUS", "RESTRICTED")},
    )
    # The look-up.
    world.act_as(people.director)
    lookup = world.client.get(f"/api/resources/people/{ads.orderer.id}")
    assert lookup.status_code == 200, lookup.json()
    body = lookup.json()
    assert body["person"]["full_name"] == ads.orderer.full_name
    assert body["person"]["employment_status_label"] == "Đang làm"
    assert [item["code"] for item in body["person"]["departments"]] == ["ADS"]
    assert body["total"] == 2
    assert {(item["type"]["code"], item["count"]) for item in body["by_type"]} == {
        ("VIA", 1),
        ("PAGE", 1),
    }
    assert {item["code"] for item in body["holding"]} == {
        via["resource"]["code"],
        page["resource"]["code"],
    }
    assert [item["code"] for item in body["held_before"]] == [gone["resource"]["code"]]
    # One's own look-up is open to anybody; somebody else's needs REPORT.
    world.act_as(ads.orderer)
    assert world.client.get(f"/api/resources/people/{ads.orderer.id}").status_code == 200
    assert world.client.get(f"/api/resources/people/{ads.writer.id}").status_code == 403
    # The people list for the pickers.
    world.act_as(people.tn_staff)
    listed = world.client.get("/api/resources/people", params={"q": "Thùy"}).json()["people"]
    assert [item["full_name"] for item in listed] == [ads.designer.full_name]
    # The summary.
    month = utcnow().strftime("%Y-%m")
    world.act_as(people.director)
    summary = world.client.get("/api/resources/reports/summary", params={"month": month})
    assert summary.status_code == 200, summary.json()
    body = summary.json()
    assert body["total"] == 4 and body["handed_over"] == 1 and body["handover_rate"] == 0.25
    lines = {item["department"]["code"]: item for item in body["departments"]}
    assert lines["ADS"]["total"] == 3 and lines["ADS"]["handed_over"] == 1
    assert lines["ADS"]["by_type"][option_id(catalog, "TYPE", "VIA")] == 2
    assert lines["BT"]["total"] == 1 and lines["BT"]["handover_rate"] == 0.0
    statuses = {item["status"]["code"]: item["count"] for item in body["by_status"]}
    assert statuses["ACTIVE"] == 3 and statuses["RESTRICTED"] == 1
    groups = {item["group"]: item["count"] for item in body["by_group"]}
    assert groups["NORMAL"] == 3 and groups["ATTENTION"] == 1
    # A month before anything existed: nothing stood anywhere.
    earlier = world.client.get("/api/resources/reports/summary", params={"month": "2020-01"}).json()
    assert sum(item["count"] for item in earlier["by_status"]) == 0
    # The report needs REPORT (or seeing everything).
    world.act_as(ads.head)
    assert world.client.get("/api/resources/reports/summary").status_code == 403
    assert (
        world.client.get("/api/resources/reports/summary", params={"month": "abc"}).status_code
        == 422
    )


# --- Excel -----------------------------------------------------------------------------


def workbook(rows: list[list[Any]], *, sheet: str = "ADS", months: list[str] | None = None) -> str:
    """An inventory file in the requirement's shape: headers on row 5, data
    from row 6, a "Người sử dụng" tab that must be skipped; base64."""
    book = Workbook()
    first = book.active
    assert first is not None
    first.title = sheet
    first["A1"] = "Apexmed - Khai báo tài nguyên"
    headers = [
        "STT",
        "Mã tài nguyên",
        "Loại tài nguyên",
        "ID tài nguyên",
        "Tên tài nguyên",
        "Link",
        "Người được bàn giao (đang quản lý)",
        "Trạng thái người dùng",
        "Quyền đang có",
        "Email",
        "SĐT",
        "Bảo mật 2 lớp",
        "Ai đang giữ mã 2 lớp",
        "Nguồn gốc",
        "Ngày bắt đầu dùng",
        "Mục đích",
        "Ghi chú",
        "Bàn giao về Tài nguyên",
        *(months or ["T10/2026", "T11/2026", "T12/2026"]),
        # The one key between the file and the people (2026-10-19): a row's
        # 22nd value, after the months.
        "Mã NV",
    ]
    for column, header in enumerate(headers, start=1):
        first.cell(row=5, column=column, value=header)
    for offset, row in enumerate(rows, start=6):
        for column, value in enumerate(row, start=1):
            first.cell(row=offset, column=column, value=value)
    people = book.create_sheet("Người sử dụng")
    people["A1"] = "Họ tên"
    buffer = io.BytesIO()
    book.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


async def test_09_export_keeps_ids_as_text_and_import_previews_then_writes(world: World) -> None:
    people = await cast(world)
    ads = people.ads
    catalog = options(world, people.tn_head)
    create(
        world,
        people.tn_head,
        catalog,
        external_id="104000000000000",
        phone="0912000111",
        holder_user_id=str(ads.orderer.id),
    )
    # Export: the id and the phone are text cells.
    world.act_as(ads.head)
    exported = world.client.get("/api/resources/export.xlsx")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/vnd.openxmlformats")
    sheet = load_workbook(io.BytesIO(exported.content)).active
    assert sheet is not None
    headers = [cell.value for cell in sheet[1]]
    assert headers[:4] == ["Mã tài nguyên", "Loại", "ID tài nguyên", "Tên tài nguyên"]
    id_cell = sheet.cell(row=2, column=3)
    assert id_cell.value == "104000000000000" and id_cell.number_format == "@"
    phone_cell = sheet.cell(row=2, column=headers.index("SĐT đăng ký") + 1)
    assert phone_cell.value == "0912000111" and phone_cell.number_format == "@"
    assert (
        sheet.cell(row=2, column=headers.index("Người được bàn giao") + 1).value
        == ads.orderer.full_name
    )
    # The orderer's export holds only what they hold; an outsider gets nothing.
    world.act_as(ads.orderer)
    assert world.client.get("/api/resources/export.xlsx").status_code == 200
    nobody = await person(world, "Người ngoài")
    world.act_as(nobody)
    assert world.client.get("/api/resources/export.xlsx").status_code == 403

    # Import, step 1: the preview reports every row's errors and writes nothing.
    bad_file = workbook(
        [
            [
                1,
                "ADS-010",
                "Via",
                104000000000000,
                "Via A",
                "",
                ads.writer.full_name,
                "",
                "Nhà quảng cáo",
                "",
                "0901",
                "Có",
                "",
                "Công ty cấp",
                "01/09/2026",
                "",
                "",
                "Đã bàn giao",
                "Hoạt động",
                "Checkpoint",
                "",
            ],
            [
                2,
                "",
                "Page",
                "77",
                "Page B",
                "",
                "Người Không Có",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "Hoạt động",
                "",
                "",
            ],
            [
                3,
                "",
                "Máy bay",
                "78",
                "Thứ lạ",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            [
                4,
                "",
                "Via",
                "79",
                "Via C",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]
    )
    world.act_as(people.tn_staff)
    refused = world.client.post("/api/resources/import/preview", json={"data": bad_file})
    assert refused.status_code == 403
    world.act_as(people.tn_head)
    preview = world.client.post(
        "/api/resources/import/preview", json={"filename": "kiem-ke.xlsx", "data": bad_file}
    )
    assert preview.status_code == 200, preview.json()
    body = preview.json()
    assert [sheet["name"] for sheet in body["sheets"]] == ["ADS", "Người sử dụng"]
    assert body["sheets"][1]["skipped"] is True
    assert body["sheets"][0]["department"]["code"] == "ADS"
    rows = {row["row_no"]: row for row in body["rows"]}
    assert rows[6]["errors"] == ["ID '104000000000000' đã có trong hệ thống ở cùng loại."]
    assert rows[6]["raw"]["external_id"] == "104000000000000" and rows[6]["raw"]["phone"] == "0901"
    assert [item["status"] for item in rows[6]["months"]] == ["Hoạt động", "Checkpoint"]
    # A holder named without a "Mã NV" is nobody: a warning, not an error.
    assert rows[7]["errors"] == []
    assert any("Không có Mã NV: bỏ qua người giữ" in item for item in rows[7]["warnings"])
    assert any("không có trong danh sách lựa chọn" in item for item in rows[8]["errors"])
    # No month filled: the status defaults to "Hoạt động" with a note, not an error.
    assert rows[9]["errors"] == []
    assert rows[9]["warnings"] == [
        "Chưa ghi bàn giao: lấy 'Chưa bàn giao'.",
        "Chưa có trạng thái tháng nào: lấy 'Hoạt động'.",
    ]
    assert body["error_count"] == 2 and body["ok_count"] == 2
    assert [item["name"] for item in body["missing_options"]] == ["Máy bay"]
    assert (await world.session.scalar(select(Resource).where(Resource.code == "ADS-010"))) is None
    commit = world.client.post("/api/resources/import/commit", json={"data": bad_file})
    assert commit.status_code == 422 and error_reason(commit.json()) == "import_has_errors"

    # Step 2: a clean file is written whole - codes kept, holders handed by
    # their member code, months as history.
    world.act_as(world.owner)
    assert (
        world.client.put(
            f"/api/account/members/{ads.writer.id}/member-code", json={"member_code": "WRT"}
        ).status_code
        == 200
    )
    world.act_as(people.tn_head)
    good_file = workbook(
        [
            [
                1,
                "ADS-010",
                "Via",
                "555000111222333",
                "Via A",
                "https://fb.com/a",
                ads.writer.full_name,
                "",
                "Nhà quảng cáo",
                "a@x.vn",
                "0901",
                "Có",
                people.tn_staff.full_name,
                "Công ty cấp",
                "01/09/2026",
                "Chạy ads",
                "",
                "Đã bàn giao",
                "Hoạt động",
                "Checkpoint",
                "Hoạt động",
                "wrt",
            ],
            [
                2,
                "",
                "Page",
                "77",
                "Page B",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "Hoạt động",
                "",
                "",
            ],
        ]
    )
    written = world.client.post("/api/resources/import/commit", json={"data": good_file})
    assert written.status_code == 200, written.json()
    assert written.json()["written"] is True and written.json()["ok_count"] == 2
    listed = world.client.get("/api/resources", params={"sort": "code"}).json()["items"]
    codes = [item["code"] for item in listed]
    # The old code is kept; a row without one is numbered past it, and so is the next.
    assert "ADS-010" in codes and "ADS-011" in codes
    nxt = create(world, people.tn_head, catalog, external_id="new-after-import")["resource"]
    assert nxt["code"] == "ADS-012"
    via_a = next(item for item in listed if item["code"] == "ADS-010")
    assert via_a["holder"]["full_name"] == ads.writer.full_name
    assert via_a["status"]["code"] == "ACTIVE" and via_a["handover"]["code"] == "DONE"
    assert via_a["two_factor_holder"]["full_name"] == people.tn_staff.full_name
    assert via_a["started_on"] == "2026-09-01" and via_a["phone"] == "0901"
    detail = world.client.get(f"/api/resources/{via_a['id']}").json()
    assert [line["to_status"]["code"] for line in detail["statuses"]] == [
        "ACTIVE",
        "CHECKPOINT",
        "ACTIVE",
    ]
    assert [line["at"][:7] for line in detail["statuses"]] == ["2026-12", "2026-11", "2026-10"]
    assert detail["handovers"][0]["reason"] == "NEW"
    # October 2026's report counts the imported statuses as they stood.
    world.act_as(people.tn_head)
    october = world.client.get("/api/resources/reports/summary", params={"month": "2026-11"}).json()
    statuses = {item["status"]["code"]: item["count"] for item in october["by_status"]}
    assert statuses["CHECKPOINT"] == 1
    # The same file again: every id is now taken.
    again = world.client.post("/api/resources/import/preview", json={"data": good_file}).json()
    assert again["error_count"] == 2
    # A file that is not Excel.
    garbage = world.client.post(
        "/api/resources/import/preview", json={"data": base64.b64encode(b"hello").decode()}
    )
    assert garbage.status_code == 422 and error_reason(garbage.json()) == "import_file_invalid"


async def test_09d_the_template_is_one_sheet_and_the_ban_follows_the_holder(
    world: World,
) -> None:
    """One sheet (Mã NV, loại, ID, tên, trạng thái, link, ghi chú), no ban
    column: Mã NV is required and the resource takes the holder's ban - ADS,
    then Dựng, Thiết kế, Biên tập, then the first of any other."""
    people = await cast(world)
    ads = people.ads
    world.act_as(people.tn_staff)
    assert world.client.get("/api/resources/import/template.xlsx").status_code == 403
    world.act_as(people.tn_head)
    response = world.client.get("/api/resources/import/template.xlsx")
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["Mẫu nhập"]
    sheet = book["Mẫu nhập"]
    assert [c.value for c in sheet[1]] == [
        "Mã NV",
        "Loại tài nguyên",
        "ID tài nguyên",
        "Tên tài nguyên",
        "Trạng thái",
        "Link",
        "Ghi chú",
    ]
    assert sheet.cell(row=2, column=3).number_format == "@"
    untouched = base64.b64encode(response.content).decode()
    empty = world.client.post("/api/resources/import/preview", json={"data": untouched}).json()
    assert empty["rows"] == [] and empty["skipped_rows"] == 2

    # Member codes, and a second seat for two of the people.
    world.act_as(world.owner)
    for user, code in ((ads.orderer, "ORD"), (ads.editor, "EDT"), (ads.writer, "WRT")):
        r = world.client.put(
            f"/api/account/members/{user.id}/member-code", json={"member_code": code}
        )
        assert r.status_code == 200
    assert seat(world, "BT", ads.editor).status_code == 200  # Dựng + Biên Tập -> Dựng
    assert seat(world, "D", ads.orderer).status_code == 200  # ADS + Dựng -> ADS
    world.act_as(people.tn_head)
    sheet.append(["ORD", "Via", "1", "Của ADS", "Hoạt động", "https://a", ""])
    sheet.append(["edt", "Gmail", "2", "Của Dựng", "Hoạt động", "tuỳ ý, không phải link", ""])
    sheet.append(["WRT", "Gmail", "3", "Của Biên Tập", "Hoạt động", "", ""])
    sheet.append(["", "Via", "4", "Thiếu mã", "Hoạt động", "", ""])
    sheet.append(["NOPE", "Via", "5", "Mã lạ", "Hoạt động", "", ""])
    buffer = io.BytesIO()
    book.save(buffer)
    data = base64.b64encode(buffer.getvalue()).decode()
    preview = world.client.post("/api/resources/import/preview", json={"data": data}).json()
    rows = {row["raw"]["external_id"]: row for row in preview["rows"]}
    assert rows["1"]["department"]["code"] == "ADS"
    assert rows["2"]["department"]["code"] == "D"
    assert rows["3"]["department"]["code"] == "BT"
    assert "Thiếu Mã NV" in rows["4"]["errors"][0]
    assert "chưa có trong hệ thống" in rows["5"]["errors"][0]
    assert preview["ok_count"] == 3 and preview["error_count"] == 2


async def test_09b_the_holder_is_matched_by_member_code_alone(world: World) -> None:
    """2026-10-19: "Mã NV" = the account's member code is the only link. A
    code the system lacks leaves the resource unassigned with a warning -
    no account is created from the file, whoever imports."""
    people = await cast(world)
    taken = await person(world, "Người Có Mã")
    world.act_as(world.owner)
    assert (
        world.client.put(
            f"/api/account/members/{taken.id}/member-code", json={"member_code": "NCM"}
        ).status_code
        == 200
    )
    # Unknown code: a red line and nothing is written, even for the OWNER.
    unknown = _with_holder_code("Nhân Viên Mới", "NVM", "x1")
    preview = world.client.post("/api/resources/import/preview", json={"data": unknown}).json()
    assert preview["error_count"] == 1 and preview["ok_count"] == 0
    assert (
        "Mã NV 'NVM' chưa có trong hệ thống. (file ghi 'Nhân Viên Mới')"
        in preview["rows"][0]["errors"]
    )
    refused = world.client.post("/api/resources/import/commit", json={"data": unknown})
    assert refused.status_code == 422 and error_reason(refused.json()) == "import_has_errors"
    assert await world.session.scalar(select(User).where(User.full_name == "Nhân Viên Mới")) is None
    assert world.client.get("/api/resources", params={"q": "x1"}).json()["items"] == []
    # A known code names its person, whatever the row calls them - the head
    # of Tài nguyên importing too.
    world.act_as(people.tn_head)
    written = world.client.post(
        "/api/resources/import/commit", json={"data": _with_holder_code("Ai Đó", "ncm", "x3")}
    )
    assert written.status_code == 200, written.json()
    listed = world.client.get("/api/resources", params={"q": "x3"}).json()["items"]
    assert listed[0]["holder"]["full_name"] == "Người Có Mã"


def _with_holder_code(name: str, code: str, external_id: str) -> str:
    """An inventory file whose one row names ``name`` (member code ``code``)
    as the holder."""
    row = ["", "", "Via", external_id, "Via X", "", name] + [""] * 11 + ["Hoạt động", "", "", code]
    return workbook([row])


async def test_09c_a_resource_without_an_id_takes_its_code(world: World) -> None:
    """The id is the platform's, not ours: a blank one (form, API or Excel)
    is filled with the resource code, which is unique on its own."""
    people = await cast(world)
    catalog = options(world, people.tn_head)
    made = create(world, people.tn_head, catalog, external_id="")["resource"]
    assert made["external_id"] == made["code"] == "ADS-001"
    # A second one with no id is no duplicate.
    again = create(world, people.tn_head, catalog, external_id="")["resource"]
    assert again["external_id"] == again["code"] == "ADS-002"
    # Blanking the id on edit falls back to the code too.
    world.act_as(people.tn_head)
    edited = world.client.patch(f"/api/resources/{made['id']}", json={"external_id": "123"})
    assert edited.status_code == 200 and edited.json()["resource"]["external_id"] == "123"
    blanked = world.client.patch(f"/api/resources/{made['id']}", json={"external_id": " "})
    assert blanked.status_code == 200 and blanked.json()["resource"]["external_id"] == "ADS-001"
    # Excel: a row without an id is a warning, not an error.
    row = ["", "", "Page", "", "Page HN"] + [""] * 13 + ["Hoạt động", "", ""]
    preview = world.client.post("/api/resources/import/preview", json={"data": workbook([row])})
    assert preview.status_code == 200, preview.json()
    body = preview.json()
    assert body["error_count"] == 0 and body["ok_count"] == 1
    assert "Không có ID: hệ thống lấy mã tài nguyên làm ID." in body["rows"][0]["warnings"]
    written = world.client.post("/api/resources/import/commit", json={"data": workbook([row])})
    assert written.status_code == 200, written.json()
    listed = world.client.get("/api/resources", params={"q": "Page HN"}).json()["items"]
    assert listed[0]["external_id"] == listed[0]["code"] == "ADS-003"
    # Nor are the type and the name: "Khác" and a made-up name stand in,
    # and a line holding nothing but a reserved person's name is skipped.
    no_type = ["", "", "", "555", ""] + [""] * 13 + ["Hoạt động", "", ""]
    no_name = ["", "", "TKQC", "777", ""] + [""] * 13 + ["Hoạt động", "", ""]
    reserved = ["", "", "", "", "", "", people.tn_staff.full_name] + [""] * 14
    body = world.client.post(
        "/api/resources/import/preview", json={"data": workbook([no_type, no_name, reserved])}
    ).json()
    assert body["error_count"] == 0 and body["ok_count"] == 2 and len(body["rows"]) == 2
    assert "Thiếu loại: tạm để 'Khác', bổ sung sau trên màn hình." in body["rows"][0]["warnings"]
    assert "Thiếu tên: tạm đặt 'Khác 555', bổ sung sau." in body["rows"][0]["warnings"]
    assert "Thiếu tên: tạm đặt 'TKQC 777', bổ sung sau." in body["rows"][1]["warnings"]
    # Two lines of the file numbered alike: the later one gets a fresh code.
    twice = [
        ["", "ADS-050", "Via", "901", "Via A"] + [""] * 13 + ["Hoạt động", "", ""],
        ["", "ADS-050", "Via", "902", "Via B"] + [""] * 13 + ["Hoạt động", "", ""],
    ]
    body = world.client.post("/api/resources/import/preview", json={"data": workbook(twice)}).json()
    assert body["error_count"] == 0 and body["ok_count"] == 2
    assert body["rows"][1]["code"] is None
    assert any(
        item.startswith("Mã ADS-050 trùng với tab ADS dòng 6")
        for item in body["rows"][1]["warnings"]
    )
    written = world.client.post("/api/resources/import/commit", json={"data": workbook(twice)})
    assert written.status_code == 200, written.json()
    codes = {
        item["name"]: item["code"] for item in world.client.get("/api/resources").json()["items"]
    }
    assert codes["Via A"] == "ADS-050" and codes["Via B"] == "ADS-051"


async def test_10_the_owner_and_an_admin_need_no_seat(world: World) -> None:
    await cast(world)
    catalog = options(world, world.owner)
    made = create(world, world.owner, catalog, external_id="o1")
    assert made["resource"]["code"] == "ADS-001"
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    assert world.client.get("/api/resources").json()["total"] == 1
    assert world.client.get("/api/resources/reports/warnings").status_code == 200
    # The SQLite trigger is what the offline suite runs on; the PostgreSQL one
    # is the migration's. Both refuse an UPDATE and a DELETE.
    with pytest.raises(DBAPIError):
        await world.session.execute(text("DELETE FROM resource_status_history"))
    await world.session.rollback()
