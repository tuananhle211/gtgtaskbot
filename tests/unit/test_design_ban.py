"""The Design team's own rules (0058).

* the catalogue of design codes ("mã task design"): everybody in ORD reads it, the
  Design Leader keeps it;
* the script step picks the codes when it hands in to Design; a designer who
  hands in with a different list waits for the Design Leader, and on an order
  with no script step the designer's first list always does;
* birthday orders ("Order sinh nhật"): straight to Design, done at Design's
  hand-in, on a board of their own that the whole Design team sees.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from meobot.core.time import utcnow
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_order_commands import (
    FAR_DEADLINE,
    Ads,
    act,
    ads_world,
    create,
    node,
    person,
)
from tests.unit.test_units_gating import error_reason, tag

pytestmark = pytest.mark.asyncio


async def design_world(world: World) -> tuple[Ads, dict[str, str]]:
    """ORD with design codes on, and three codes in the catalogue."""
    ads = await ads_world(world)
    ads.unit.settings = {**(ads.unit.settings or {}), "design_codes_enabled": True}
    await world.session.flush()
    world.act_as(ads.lead_tk)
    codes: dict[str, str] = {}
    for code, name in (
        ("TKBTS", "SOCIAL - Thiết kế Banner, Thumb Video, Avatar"),
        ("CA", "SOCIAL - Retouch ảnh phức tạp"),
        ("O-TB", "OFFLINE - Thiết kế Brochure"),
    ):
        made = world.client.post("/api/units/ADS/design-codes", json={"code": code, "name": name})
        assert made.status_code == 201, made.json()
        codes[code] = made.json()["id"]
    return ads, codes


def pick(codes: dict[str, str], **quantities: int) -> list[dict[str, Any]]:
    return [
        {"code_id": codes[code.replace("_", "-")], "quantity": quantity}
        for code, quantity in quantities.items()
    ]


def task(world: World, user: User, code: str) -> dict[str, Any]:
    world.act_as(user)
    response = world.client.get(f"/api/tasks/{code}")
    assert response.status_code == 200, response.json()
    return response.json()


def action(page: dict[str, Any], prefix: str) -> dict[str, Any]:
    return next(item for item in page["actions"] if item["key"].startswith(prefix))


def do(world: World, user: User, code: str, prefix: str, **body: Any) -> Any:
    """Run the task page's action whose key starts with ``prefix``."""
    page = task(world, user, code)
    return world.client.post(
        f"/api/tasks/{code}/actions",
        json={"key": action(page, prefix)["key"], "version": page["task"]["version"], **body},
    )


def board(world: World, user: User, **params: str) -> dict[str, Any]:
    world.act_as(user)
    response = world.client.get("/api/board/tasks", params={"unit": "ADS", **params})
    assert response.status_code == 200, response.json()
    return response.json()


def summary(items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return sorted((item["code"], item["quantity"]) for item in items)


async def to_design(world: World, ads: Ads, codes: dict[str, str]) -> str:
    """A BTD order whose script step handed in with ``TKBTS x3, CA x1``; the
    designer has accepted. Returns the order code."""
    detail = create(
        world,
        ads,
        preassigned={"BIEN_TAP": str(ads.writer.id), "THIET_KE": str(ads.designer.id)},
    )
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    detail = act(
        world,
        ads.writer,
        f"/nodes/{bt}/submit",
        detail,
        link="https://e/script",
        design_codes=pick(codes, TKBTS=3, CA=1),
    )
    assert detail["order"]["stage"] == "THIET_KE"
    tk = node(detail, "THIET_KE")["id"]
    act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    return code


# --- the catalogue ------------------------------------------------------------------


async def test_01_the_design_leader_keeps_the_catalogue_everybody_reads_it(world: World) -> None:
    ads, codes = await design_world(world)
    # Anybody in ORD reads the active codes, in the order they were added.
    for reader in (ads.designer, ads.writer, ads.orderer, ads.head):
        world.act_as(reader)
        listed = world.client.get("/api/units/ADS/design-codes")
        assert listed.status_code == 200
        assert [row["code"] for row in listed.json()["codes"]] == ["TKBTS", "CA", "O-TB"]
    # Outside ORD there is nothing behind the route.
    world.act_as(ads.outsider)
    assert world.client.get("/api/units/ADS/design-codes").status_code == 404
    # Only the Design Leader (and OWNER / ADMIN) writes or sees the retired ones.
    for refused_for in (ads.designer, ads.head, ads.lead_bt):
        world.act_as(refused_for)
        made = world.client.post("/api/units/ADS/design-codes", json={"code": "X", "name": "x"})
        assert made.status_code == 403 and error_reason(made.json()) == "design_code_forbidden"
        retired = world.client.get("/api/units/ADS/design-codes?include_inactive=true")
        assert retired.status_code == 403
    world.act_as(ads.lead_tk)
    taken = world.client.post("/api/units/ADS/design-codes", json={"code": "ca", "name": "Khác"})
    assert taken.status_code == 422 and error_reason(taken.json()) == "design_code_taken"
    renamed = world.client.patch(
        f"/api/units/ADS/design-codes/{codes['CA']}", json={"name": "Retouch", "active": False}
    )
    assert renamed.status_code == 200 and renamed.json()["active"] is False
    assert [
        row["code"] for row in world.client.get("/api/units/ADS/design-codes").json()["codes"]
    ] == ["TKBTS", "O-TB"]
    everything = world.client.get("/api/units/ADS/design-codes?include_inactive=true").json()
    assert len(everything["codes"]) == 3
    world.act_as(world.owner)
    made = world.client.post("/api/units/ADS/design-codes", json={"code": " tkv ", "name": "KV"})
    assert made.status_code == 201 and made.json()["code"] == "TKV"
    # The catalogue is ORD's alone.
    assert world.client.get("/api/units/PR/design-codes").status_code == 404


async def test_02_units_me_says_what_the_design_screens_are_for_each_person(world: World) -> None:
    ads, _ = await design_world(world)
    expected = {
        ads.designer: {"view": True, "manage_codes": False, "birthday": True},
        ads.lead_tk: {"view": True, "manage_codes": True, "birthday": True},
        # The birthday board is the Design team's; others need the grant (0059).
        ads.orderer: {"view": False, "manage_codes": False, "birthday": False},
        ads.head: {"view": False, "manage_codes": False, "birthday": False},
        ads.writer: {"view": False, "manage_codes": False, "birthday": False},
        ads.editor: {"view": False, "manage_codes": False, "birthday": False},
        world.owner: {"view": False, "manage_codes": True, "birthday": True},
        ads.outsider: {"view": False, "manage_codes": False, "birthday": False},
    }
    for user, flags in expected.items():
        world.act_as(user)
        design = world.client.get("/api/units/me").json()["design"]
        assert {key: design[key] for key in flags} == flags, user.full_name


# --- the script step picks the codes ---------------------------------------------------


async def test_03_the_script_step_must_pick_design_codes_for_design(world: World) -> None:
    ads, codes = await design_world(world)
    detail = create(world, ads, preassigned={"BIEN_TAP": str(ads.writer.id)})
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    # The task page asks for them.
    submit = action(task(world, ads.writer, code), "ads:SUBMIT_WORK:")
    assert "design_codes" in submit["inputs"] and "design_codes" in submit["required_inputs"]
    assert submit["design_codes"] == [] and submit["hint"]

    path = f"/api/orders/{detail['order']['id']}/nodes/{bt}/submit"
    base = {"version": detail["order"]["version"], "link": "https://e/script"}
    for body, reason in (
        ({}, "design_codes_required"),
        ({"design_codes": []}, "design_codes_required"),
        (
            {"design_codes": [{"code_id": "00000000-0000-0000-0000-000000000001", "quantity": 1}]},
            "design_code_invalid",
        ),
        ({"design_codes": pick(codes, CA=1) + pick(codes, CA=2)}, "design_code_invalid"),
    ):
        refused = world.client.post(path, json={**base, **body})
        assert refused.status_code == 422, refused.json()
        assert error_reason(refused.json()) == reason
    done = do(
        world,
        ads.writer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/script",
        design_codes=pick(codes, TKBTS=3, CA=1),
    )
    assert done.status_code == 200, done.json()
    page = done.json()
    assert page["task"]["stage"] == "THIET_KE"
    assert summary(page["design"]["codes"]) == [("CA", 1), ("TKBTS", 3)]
    assert page["design"]["proposed_codes"] is None
    assert any(entry["label"].startswith("Chốt mã task design") for entry in page["timeline"])
    # The Design team's table reads the script link, who wrote it and the codes.
    row = next(item for item in board(world, ads.lead_tk)["items"] if item["code"] == code)
    assert row["category"] == "STANDARD" and row["birthday"] is None
    assert row["design"]["script_link"] == "https://e/script"
    assert row["design"]["script_by"] == ads.writer.full_name
    assert summary(row["design"]["codes"]) == [("CA", 1), ("TKBTS", 3)]
    assert row["design"]["design_link"] is None


async def test_04_an_order_with_no_design_step_asks_for_no_codes(world: World) -> None:
    ads, _ = await design_world(world)
    detail = create(
        world, ads, video_type="BD", design_link="d", preassigned={"BIEN_TAP": str(ads.writer.id)}
    )
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    submit = action(task(world, ads.writer, code), "ads:SUBMIT_WORK:")
    assert "design_codes" not in submit["inputs"]
    detail = act(world, ads.writer, f"/nodes/{bt}/submit", detail, link="https://e/script")
    assert detail["order"]["stage"] == "DUNG"


# --- the designer's hand-in -------------------------------------------------------------


async def test_05_a_designer_who_keeps_the_codes_moves_on_without_review(world: World) -> None:
    ads, codes = await design_world(world)
    code = await to_design(world, ads, codes)
    submit = action(task(world, ads.designer, code), "ads:SUBMIT_WORK:")
    assert "design_codes" in submit["inputs"] and "design_codes" in submit["required_inputs"]
    assert summary(submit["design_codes"]) == [("CA", 1), ("TKBTS", 3)]
    # Sent back as they are (in another order): not a change.
    done = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/design",
        design_codes=pick(codes, CA=1, TKBTS=3),
    )
    assert done.status_code == 200, done.json()
    page = done.json()
    assert page["task"]["stage"] == "DUNG"
    assert page["design"]["proposed_codes"] is None
    assert page["design"]["design_link"] == "https://e/design"
    assert page["design"]["designer"] == ads.designer.full_name


async def test_06_a_changed_list_waits_for_the_design_leader(world: World) -> None:
    ads, codes = await design_world(world)
    code = await to_design(world, ads, codes)
    done = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/design",
        design_codes=pick(codes, TKBTS=2, O_TB=1),
    )
    assert done.status_code == 200, done.json()
    page = done.json()
    # Not finished: the Leader decides, whatever the unit's review setting.
    assert page["task"]["stage"] == "THIET_KE" and page["task"]["state"] == "CHO_DUYET"
    assert summary(page["design"]["codes"]) == [("CA", 1), ("TKBTS", 3)]
    assert summary(page["design"]["proposed_codes"]) == [("O-TB", 1), ("TKBTS", 2)]
    asked = "Yêu cầu thay đổi mã task design"
    note = next(e for e in page["timeline"] if e["label"].startswith(asked))
    assert note["note"] == ("Nhóm Design nộp khác mã order: TKBTS x3, CA x1 → TKBTS x2, O-TB x1")
    waiting = board(world, ads.lead_tk, awaiting_me="true")
    assert [item["code"] for item in waiting["items"]] == [code]
    assert summary(waiting["items"][0]["design"]["proposed_codes"]) == [("O-TB", 1), ("TKBTS", 2)]

    approve = action(task(world, ads.lead_tk, code), "ads:APPROVE_NODE:")
    assert approve["inputs"] == ["note", "design_codes"]
    assert summary(approve["design_codes"]) == [("O-TB", 1), ("TKBTS", 2)]
    approved = do(world, ads.lead_tk, code, "ads:APPROVE_NODE:")
    assert approved.status_code == 200, approved.json()
    page = approved.json()
    assert page["task"]["stage"] == "DUNG"
    assert summary(page["design"]["codes"]) == [("O-TB", 1), ("TKBTS", 2)]
    assert page["design"]["proposed_codes"] is None


async def test_07_the_leader_may_fix_the_list_or_send_the_hand_in_back(world: World) -> None:
    ads, codes = await design_world(world)
    code = await to_design(world, ads, codes)
    changed = pick(codes, TKBTS=9)
    assert (
        do(
            world,
            ads.designer,
            code,
            "ads:SUBMIT_WORK:",
            link="https://e/v1",
            design_codes=changed,
        ).status_code
        == 200
    )
    # Sent back: the request goes with it, the order keeps its codes.
    returned = do(world, ads.lead_tk, code, "ads:RETURN_NODE:", note="Sai số lượng", tokens=0)
    assert returned.status_code == 200, returned.json()
    page = returned.json()
    assert page["task"]["state"] == "DANG_SUA"
    assert page["design"]["proposed_codes"] is None
    assert summary(page["design"]["codes"]) == [("CA", 1), ("TKBTS", 3)]
    # Handed in again with a change; this time the Leader decides the list.
    assert (
        do(
            world,
            ads.designer,
            code,
            "ads:SUBMIT_WORK:",
            link="https://e/v2",
            design_codes=changed,
        ).status_code
        == 200
    )
    empty = do(world, ads.lead_tk, code, "ads:APPROVE_NODE:", design_codes=[])
    assert empty.status_code == 422 and error_reason(empty.json()) == "design_codes_required"
    approved = do(
        world, ads.lead_tk, code, "ads:APPROVE_NODE:", design_codes=pick(codes, TKBTS=4, CA=1)
    )
    assert approved.status_code == 200, approved.json()
    assert summary(approved.json()["design"]["codes"]) == [("CA", 1), ("TKBTS", 4)]


async def test_08_with_no_script_step_the_designer_picks_and_always_waits(world: World) -> None:
    ads, codes = await design_world(world)
    detail = create(
        world,
        ads,
        video_type="TD",
        preassigned={"THIET_KE": str(ads.designer.id), "DUNG": str(ads.editor.id)},
    )
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    tk = node(detail, "THIET_KE")["id"]
    act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    submit = action(task(world, ads.designer, code), "ads:SUBMIT_WORK:")
    assert "design_codes" in submit["required_inputs"] and submit["design_codes"] == []
    missing = do(world, ads.designer, code, "ads:SUBMIT_WORK:", link="https://e/design")
    assert missing.status_code == 422
    assert error_reason(missing.json()) == "work_items_required"
    done = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/design",
        design_codes=pick(codes, O_TB=2),
    )
    assert done.status_code == 200, done.json()
    page = done.json()
    assert page["task"]["stage"] == "THIET_KE" and page["task"]["state"] == "CHO_DUYET"
    assert page["design"]["codes"] == []
    assert summary(page["design"]["proposed_codes"]) == [("O-TB", 2)]
    approved = do(world, ads.lead_tk, code, "ads:APPROVE_NODE:")
    assert approved.status_code == 200, approved.json()
    assert approved.json()["task"]["stage"] == "DUNG"
    assert summary(approved.json()["design"]["codes"]) == [("O-TB", 2)]


async def test_09_a_retired_code_stays_on_the_order_that_carries_it(world: World) -> None:
    ads, codes = await design_world(world)
    code = await to_design(world, ads, codes)
    world.act_as(ads.lead_tk)
    assert (
        world.client.patch(
            f"/api/units/ADS/design-codes/{codes['CA']}", json={"active": False}
        ).status_code
        == 200
    )
    # Kept as it is: still not a change, and still accepted.
    done = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/design",
        design_codes=pick(codes, TKBTS=3, CA=1),
    )
    assert done.status_code == 200, done.json()
    assert done.json()["task"]["stage"] == "DUNG"


# --- birthday orders --------------------------------------------------------------------

BIRTHDAY = {
    "full_name": "Trần Diễm My",
    "branch": "VP - Hà Nội",
    "gender": "FEMALE",
    "birthday_on": "2026-10-01",
    "reference_link": "https://drive.example/my.jpg",
    "note": "Giữ đường nét mặt.",
}


def birthday(world: World, user: User, **overrides: Any) -> Any:
    world.act_as(user)
    return world.client.post("/api/orders/birthday", json={**BIRTHDAY, **overrides})


def grant_birthday(
    world: World, code: str = "ADS", position: str = "MEMBER", scope: str = "OWN"
) -> dict[str, Any]:
    """The OWNER grants a department's position the birthday board (0062)."""
    world.act_as(world.owner)
    departments = world.client.get("/api/departments").json()["departments"]
    target = next(item for item in departments if item["code"] == code)
    response = world.client.put(
        f"/api/departments/{target['id']}/grants",
        json={"cells": {position: {"BIRTHDAY": scope}}},
    )
    assert response.status_code == 200, response.json()
    return dict(response.json()["cells"])


async def test_10_a_birthday_order_goes_straight_to_design_and_ends_there(world: World) -> None:
    ads, _ = await design_world(world)
    # The script and edit teams have nothing to do with it.
    for refused_for in (ads.writer, ads.editor):
        refused = birthday(world, refused_for)
        assert refused.status_code == 403 and error_reason(refused.json()) == "birthday_forbidden"
    for body, reason in (
        ({"full_name": "  "}, "birthday_name_missing"),
        ({"branch": " "}, "birthday_branch_missing"),
        ({"gender": "X"}, "birthday_gender_invalid"),
    ):
        refused = birthday(world, ads.designer, **body)
        assert refused.status_code == 422 and error_reason(refused.json()) == reason

    # Neither does an orderer - until the birthday board is granted to them.
    refused = birthday(world, ads.orderer)
    assert refused.status_code == 403 and error_reason(refused.json()) == "birthday_forbidden"
    grant_birthday(world)  # ADS members: their own birthday orders
    made = birthday(world, ads.orderer)
    assert made.status_code == 201, made.json()
    code = made.json()["code"]
    assert len(code) == 5 and "-" not in code  # the short code, birthday too
    page = task(world, ads.orderer, code)
    assert page["category"] == "BIRTHDAY"
    assert page["birthday"] == {
        "full_name": "Trần Diễm My",
        "branch": "VP - Hà Nội",
        "gender": "FEMALE",
        "birthday_on": "2026-10-01",
    }
    # Nobody approves it: it is already at Design, waiting to be handed out.
    assert page["task"]["stage"] == "THIET_KE" and page["task"]["kind_label"] == "Sinh nhật"
    assert page["task"]["state"] == "CHO_PHAN_CONG"
    assert page["design"]["reference_link"] == "https://drive.example/my.jpg"
    assert {field["key"]: field["value"] for field in page["fields"]}["birthday_on"] == "01/10/2026"

    assigned = do(
        world,
        ads.lead_tk,
        code,
        "ads:ASSIGN:",
        assignee_user_id=str(ads.designer.id),
        tokens=2,
        deadline_at=FAR_DEADLINE,
    )
    assert assigned.status_code == 200, assigned.json()
    assert do(world, ads.designer, code, "ads:ACCEPT:").status_code == 200
    submit = action(task(world, ads.designer, code), "ads:SUBMIT_WORK:")
    # No design codes on a birthday order; the link is the product.
    assert submit["inputs"] == ["link", "note"] and submit["required_inputs"] == ["link"]
    done = do(world, ads.designer, code, "ads:SUBMIT_WORK:", link="https://e/my-birthday")
    assert done.status_code == 200, done.json()
    page = done.json()
    # Handed in = finished: no final review by anybody.
    assert page["task"]["stage"] == "COMPLETED"
    assert page["task"]["product_link"] == "https://e/my-birthday"
    assert page["actions"] == [] or all(
        not item["key"].startswith("ads:APPROVE_FINAL") for item in page["actions"]
    )


async def test_11_the_birthday_board_is_its_own_and_the_design_team_sees_all_of_it(
    world: World,
) -> None:
    ads, _ = await design_world(world)
    other_orderer = await person(world, "Marketing Hai")
    await tag(world, ads.unit, other_orderer, UnitMemberRole.ORDERER, member_code="HAI")
    ordinary = create(world, ads)["order"]["code"]
    today = utcnow().astimezone(world.settings.timezone).date()

    def born(days: int, year: int) -> str:
        day = today + timedelta(days=days)
        return f"{year}-{day:%m-%d}"

    def coming(day: str) -> tuple[bool, str]:
        """The board's order: this year's coming birthdays, then the past ones."""
        return (day[5:] < f"{today:%m-%d}", day[5:])

    dates = {"late": born(3, 1990), "soon": born(1, 1996), "past": born(-2, 2000)}
    grant_birthday(world)  # ADS members: their own birthday orders
    late = birthday(world, ads.orderer, full_name="Muộn hơn", birthday_on=dates["late"]).json()
    soon = birthday(world, ads.designer, full_name="Sắp tới", birthday_on=dates["soon"]).json()
    past = birthday(world, other_orderer, full_name="Đã qua", birthday_on=dates["past"]).json()
    made = {"late": late, "soon": soon, "past": past}
    in_order = [made[name]["code"] for name in sorted(dates, key=lambda n: coming(dates[n]))]

    def codes_of(user: User, **params: str) -> list[str]:
        return [item["code"] for item in board(world, user, **params)["items"]]

    # "Order tổng" lists no birthday order; the birthday board only them,
    # the nearest coming birthday first, whatever the year of birth.
    assert codes_of(world.owner) == [ordinary]
    assert codes_of(world.owner, category="BIRTHDAY", order="birthday") == in_order
    # The whole Design team sees every birthday order...
    for member in (ads.designer, ads.lead_tk):
        assert len(codes_of(member, category="BIRTHDAY")) == 3
    # ...an orderer their own, the script and edit teams none.
    assert codes_of(ads.orderer, category="BIRTHDAY") == [late["code"]]
    assert codes_of(other_orderer, category="BIRTHDAY") == [past["code"]]
    for stranger in (ads.writer, ads.editor):
        assert codes_of(stranger, category="BIRTHDAY") == []
        world.act_as(stranger)
        assert world.client.get(f"/api/tasks/{late['code']}").status_code == 404
    # On "Order tổng" a designer still sees only their own work.
    assert codes_of(ads.designer) == []
    rows = board(world, ads.lead_tk, category="BIRTHDAY", order="birthday")["items"]
    row = next(item for item in rows if item["code"] == soon["code"])
    assert row["birthday"]["full_name"] == "Sắp tới" and row["birthday"]["gender"] == "FEMALE"
    assert row["birthday"]["birthday_on"] == dates["soon"] and row["title"] == "Sắp tới"
    assert row["design"]["reference_link"] == BIRTHDAY["reference_link"]
    assert row["design"]["note"] == BIRTHDAY["note"]
    # The quick-filter counts follow the board they are on.
    world.act_as(ads.lead_tk)
    counts = world.client.get("/api/board/counts", params={"unit": "ADS", "category": "BIRTHDAY"})
    assert counts.status_code == 200 and counts.json()["awaiting_me"] == 3
    world.act_as(ads.designer)
    options = world.client.get("/api/orders/birthday/options")
    assert options.status_code == 200 and options.json()["branches"] == ["VP - Hà Nội"]
    bad = world.client.get("/api/board/tasks", params={"unit": "ADS", "category": "NOPE"})
    assert bad.status_code == 422 and error_reason(bad.json()) == "invalid_category"


async def test_12_a_birthday_order_is_edited_by_its_creator_or_the_design_leader(
    world: World,
) -> None:
    ads, _ = await design_world(world)
    grant_birthday(world)  # ADS members: their own birthday orders
    made = birthday(world, ads.orderer).json()
    path = f"/api/orders/{made['id']}/birthday"
    world.act_as(ads.designer)
    refused = world.client.patch(path, json={"version": made["version"], "branch": "CS - Sài Gòn"})
    assert refused.status_code == 403 and error_reason(refused.json()) == "birthday_forbidden"
    world.act_as(ads.orderer)
    stale = world.client.patch(path, json={"version": made["version"] + 5, "branch": "X"})
    assert stale.status_code == 409 and error_reason(stale.json()) == "order_stale_version"
    edited = world.client.patch(
        path,
        json={"version": made["version"], "branch": "CS - Sài Gòn", "birthday_on": "2026-10-02"},
    )
    assert edited.status_code == 200, edited.json()
    world.act_as(ads.lead_tk)
    again = world.client.patch(
        path,
        json={"version": edited.json()["version"], "full_name": "Trần Diễm Mi", "gender": "MALE"},
    )
    assert again.status_code == 200, again.json()
    page = task(world, ads.orderer, made["code"])
    assert page["task"]["title"] == "Trần Diễm Mi"
    assert page["birthday"] == {
        "full_name": "Trần Diễm Mi",
        "branch": "CS - Sài Gòn",
        "gender": "MALE",
        "birthday_on": "2026-10-02",
    }
    # Whoever placed it may take it back; a finished one is not edited.
    cancelled = do(world, ads.orderer, made["code"], "ads:CANCEL", note="Nhập nhầm")
    assert cancelled.status_code == 200, cancelled.json()
    world.act_as(ads.orderer)
    closed = world.client.patch(path, json={"version": cancelled.json()["task"]["version"]})
    assert closed.status_code == 409
    # An ordinary order has no birthday to edit.
    ordinary = create(world, ads)
    world.act_as(ads.orderer)
    wrong = world.client.patch(
        f"/api/orders/{ordinary['order']['id']}/birthday",
        json={"version": ordinary["order"]["version"], "branch": "X"},
    )
    assert wrong.status_code == 404


async def test_13_with_design_codes_switched_off_nothing_asks_for_them(world: World) -> None:
    ads = await ads_world(world)  # this world keeps them off
    detail = create(
        world,
        ads,
        preassigned={"BIEN_TAP": str(ads.writer.id), "THIET_KE": str(ads.designer.id)},
    )
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    assert "design_codes" not in action(task(world, ads.writer, code), "ads:SUBMIT_WORK:")["inputs"]
    detail = act(world, ads.writer, f"/nodes/{bt}/submit", detail, link="https://e/script")
    assert detail["order"]["stage"] == "THIET_KE"
    world.act_as(world.owner)
    settings = world.client.get("/api/units/me").json()
    ads_entry = next(unit for unit in settings["units"] if unit["code"] == "ADS")
    assert ads_entry["settings"]["design_codes_enabled"] is False
    # An ADMIN-level switch like the reviews.
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    on = world.client.patch("/api/units/ADS/settings", json={"design_codes_enabled": True})
    assert on.status_code == 200 and on.json()["design_codes_enabled"] is True


async def test_14_a_row_says_what_its_viewer_may_do_and_when_design_handed_in(
    world: World,
) -> None:
    ads, codes = await design_world(world)
    code = await to_design(world, ads, codes)

    def row_of(user: User) -> dict[str, Any]:
        return next(item for item in board(world, user)["items"] if item["code"] == code)

    # The designer may hand in from the list; the Leader has nothing to approve yet.
    mine = row_of(ads.designer)
    assert "SUBMIT_WORK" in mine["actions"] and "APPROVE_NODE" not in mine["actions"]
    assert mine["design"]["design_submitted_at"] is None
    assert "APPROVE_NODE" not in row_of(ads.lead_tk)["actions"]
    done = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/design",
        design_codes=pick(codes, TKBTS=1),
    )
    assert done.status_code == 200, done.json()
    # A changed list waits for the Leader: the approval is theirs, on the row.
    waiting = row_of(ads.lead_tk)
    assert "APPROVE_NODE" in waiting["actions"] and "RETURN_NODE" in waiting["actions"]
    assert waiting["design"]["design_submitted_at"] is not None
    assert "SUBMIT_WORK" not in row_of(ads.designer)["actions"]
    # A birthday row: the Leader hands it out, then the designer accepts and submits.
    grant_birthday(world)  # ADS members: their own birthday orders
    made = birthday(world, ads.orderer).json()
    rows = board(world, ads.lead_tk, category="BIRTHDAY")["items"]
    assert "ASSIGN" in next(r for r in rows if r["code"] == made["code"])["actions"]
    assert (
        next(
            r
            for r in board(world, ads.designer, category="BIRTHDAY")["items"]
            if r["code"] == made["code"]
        )["actions"]
        == []
    )


# --- the Design team's library ---------------------------------------------------------


async def test_15_the_design_leader_sets_up_the_library_the_design_team_sees(world: World) -> None:
    ads, _ = await design_world(world)
    wanted = [
        {"name": "  Nhập   môn ", "url": "https://www.figma.com/file/abc", "icon": "N"},
        {"name": "Brand Guideline", "url": "https://www.figma.com/file/brand", "icon": "🎨"},
        {"name": "Template media", "url": "http://nas.local/templates"},
    ]
    # Staff and the other teams may not set it up.
    for refused_for in (ads.designer, ads.head, ads.lead_bt):
        world.act_as(refused_for)
        refused = world.client.put("/api/units/ADS/library", json={"links": wanted})
        assert refused.status_code == 403 and error_reason(refused.json()) == "library_forbidden"
    world.act_as(ads.lead_tk)
    for links, reason in (
        ([{"name": " ", "url": "https://a.example"}], "library_name_missing"),
        ([{"name": "X", "url": "javascript:alert(1)"}], "library_url_invalid"),
        ([{"name": "X", "url": "figma.com/file"}], "library_url_invalid"),
        ([{"name": "X", "url": "https://a.example/a b"}], "library_url_invalid"),
        ([{"name": f"Mục {n}", "url": "https://a.example"} for n in range(31)], "library_too_many"),
    ):
        refused = world.client.put("/api/units/ADS/library", json={"links": links})
        assert refused.status_code == 422, refused.json()
        assert error_reason(refused.json()) == reason
    saved = world.client.put("/api/units/ADS/library", json={"links": wanted})
    assert saved.status_code == 200, saved.json()
    links = saved.json()["links"]
    assert [(link["name"], link["url"], link["icon"]) for link in links] == [
        ("Nhập môn", "https://www.figma.com/file/abc", "N"),
        ("Brand Guideline", "https://www.figma.com/file/brand", "🎨"),
        ("Template media", "http://nas.local/templates", None),
    ]
    assert len({link["id"] for link in links}) == 3

    # The Design team has it in the menu; the Leader (and the OWNER) may edit.
    def menu(user: User) -> dict[str, Any]:
        world.act_as(user)
        return world.client.get("/api/units/me").json()["design"]

    assert [link["name"] for link in menu(ads.designer)["library"]] == [
        "Nhập môn",
        "Brand Guideline",
        "Template media",
    ]
    assert menu(ads.designer)["manage_library"] is False
    assert menu(ads.lead_tk)["manage_library"] is True
    assert menu(world.owner)["manage_library"] is True and len(menu(world.owner)["library"]) == 3
    # Nobody else in ORD - nor outside it - sees the library.
    for stranger in (ads.orderer, ads.head, ads.writer, ads.outsider):
        assert menu(stranger)["library"] == [] and menu(stranger)["manage_library"] is False
    world.act_as(ads.writer)
    assert world.client.get("/api/units/ADS/library").status_code == 403
    world.act_as(ads.designer)
    read = world.client.get("/api/units/ADS/library")
    assert read.status_code == 200 and read.json()["can_manage"] is False
    assert len(read.json()["links"]) == 3

    # Saved whole and in order: an entry keeps its id, a dropped one is gone,
    # and the unit's other settings are untouched.
    world.act_as(ads.lead_tk)
    reordered = world.client.put(
        "/api/units/ADS/library",
        json={
            "links": [{**links[2], "name": "Template"}, {"name": "Mới", "url": "https://x.example"}]
        },
    )
    assert reordered.status_code == 200, reordered.json()
    after = reordered.json()["links"]
    assert [link["name"] for link in after] == ["Template", "Mới"]
    assert after[0]["id"] == links[2]["id"] and after[1]["id"] not in {link["id"] for link in links}
    world.act_as(world.owner)
    me = world.client.get("/api/units/me").json()
    ads_entry = next(unit for unit in me["units"] if unit["code"] == "ADS")
    assert ads_entry["settings"]["design_codes_enabled"] is True
    # ...and the settings form saving something else keeps the library.
    assert world.client.patch("/api/units/ADS/settings", json={"urgent_days": 5}).status_code == 200
    assert [link["name"] for link in menu(ads.designer)["library"]] == ["Template", "Mới"]


# --- only once the order has reached your step ------------------------------------------


async def test_16_a_team_sees_an_order_only_once_it_reaches_their_step(world: World) -> None:
    ads, codes = await design_world(world)
    detail = create(
        world,
        ads,
        preassigned={
            "BIEN_TAP": str(ads.writer.id),
            "THIET_KE": str(ads.designer.id),
            "DUNG": str(ads.editor.id),
        },
    )
    code = detail["order"]["code"]

    def sees(user: User) -> bool:
        listed = code in [item["code"] for item in board(world, user)["items"]]
        world.act_as(user)
        opened = world.client.get(f"/api/tasks/{code}").status_code
        assert opened == (200 if listed else 404), (user.full_name, opened)
        return listed

    script, design, cut = (
        (ads.lead_bt, ads.writer),
        (ads.lead_tk, ads.designer),
        (ads.lead_dung, ads.editor),
    )
    # Waiting for the head: no production team has it yet, chosen or not.
    assert sees(ads.orderer) and sees(ads.head)
    assert not any(sees(person) for team in (script, design, cut) for person in team)
    detail = act(world, ads.head, "/approve", detail)
    # At the script step: the script team only.
    assert all(sees(person) for person in script)
    assert not any(sees(person) for team in (design, cut) for person in team)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    detail = act(
        world,
        ads.writer,
        f"/nodes/{bt}/submit",
        detail,
        link="https://e/script",
        design_codes=pick(codes, CA=1),
    )
    # At Design: Design joins, the script team keeps what it has worked on,
    # the edit team still waits its turn.
    assert all(sees(person) for team in (script, design) for person in team)
    assert not any(sees(person) for person in cut)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    detail = act(
        world,
        ads.designer,
        f"/nodes/{tk}/submit",
        detail,
        link="https://e/design",
        design_codes=pick(codes, CA=1),
    )
    assert detail["order"]["stage"] == "DUNG"
    assert all(sees(person) for team in (script, design, cut) for person in team)


async def test_17_the_design_team_filters_by_where_the_design_step_stands(world: World) -> None:
    ads, codes = await design_world(world)

    def states() -> dict[str, list[str]]:
        found = {}
        for state in (
            "CHO_PHAN_CONG",
            "DA_GIAO",
            "DANG_LAM",
            "CHO_DUYET",
            "DANG_SUA",
            "HOAN_THANH",
        ):
            page = board(world, ads.lead_tk, design_status=state)
            found[state] = sorted(item["title"] for item in page["items"])
        return found

    async def at_design(title: str, *, chosen: bool) -> tuple[str, dict[str, Any]]:
        picked = {"BIEN_TAP": str(ads.writer.id)}
        if chosen:
            picked["THIET_KE"] = str(ads.designer.id)
        detail = create(world, ads, title=title, preassigned=picked)
        detail = act(world, ads.head, "/approve", detail)
        bt = node(detail, "BIEN_TAP")["id"]
        detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
        detail = act(
            world,
            ads.writer,
            f"/nodes/{bt}/submit",
            detail,
            link="https://e/s",
            design_codes=pick(codes, CA=1),
        )
        return detail["order"]["code"], detail

    await at_design("Chờ phân công", chosen=False)  # routed to the Leader
    await at_design("Đã giao", chosen=True)  # chosen on the form, not taken yet
    _, doing = await at_design("Đang làm", chosen=True)
    act(world, ads.designer, f"/nodes/{node(doing, 'THIET_KE')['id']}/accept", doing)
    review_code, review = await at_design("Chờ duyệt", chosen=True)
    tk = node(review, "THIET_KE")["id"]
    act(world, ads.designer, f"/nodes/{tk}/accept", review)
    changed = pick(codes, TKBTS=2)
    assert (
        do(
            world,
            ads.designer,
            review_code,
            "ads:SUBMIT_WORK:",
            link="https://e/d",
            design_codes=changed,
        ).status_code
        == 200
    )
    fix_code, fixing = await at_design("Đang sửa", chosen=True)
    act(world, ads.designer, f"/nodes/{node(fixing, 'THIET_KE')['id']}/accept", fixing)
    assert (
        do(
            world,
            ads.designer,
            fix_code,
            "ads:SUBMIT_WORK:",
            link="https://e/d",
            design_codes=changed,
        ).status_code
        == 200
    )
    assert (
        do(world, ads.lead_tk, fix_code, "ads:RETURN_NODE:", note="Sửa", tokens=0).status_code
        == 200
    )
    done_code, done = await at_design("Hoàn thành", chosen=True)
    act(world, ads.designer, f"/nodes/{node(done, 'THIET_KE')['id']}/accept", done)
    assert (
        do(
            world,
            ads.designer,
            done_code,
            "ads:SUBMIT_WORK:",
            link="https://e/d",
            design_codes=pick(codes, CA=1),
        ).status_code
        == 200
    )

    assert states() == {
        "CHO_PHAN_CONG": ["Chờ phân công"],
        "DA_GIAO": ["Đã giao"],
        "DANG_LAM": ["Đang làm"],
        "CHO_DUYET": ["Chờ duyệt"],
        "DANG_SUA": ["Đang sửa"],
        "HOAN_THANH": ["Hoàn thành"],
    }
    # The filter's states match what each row's Design cell says.
    for state, titles in states().items():
        row = next(
            item for item in board(world, ads.lead_tk)["items"] if item["title"] == titles[0]
        )
        cell = next(cell for cell in row["cells"] if cell["key"] == "THIET_KE")
        assert cell["status"] == state, (state, cell)
    page = board(world, ads.lead_tk)
    assert [option["value"] for option in page["design_states"]] == list(states())
    world.act_as(ads.lead_tk)
    bad = world.client.get("/api/board/tasks", params={"unit": "ADS", "design_status": "X"})
    assert bad.status_code == 422 and error_reason(bad.json()) == "invalid_design_status"


# --- who the birthday board is for --------------------------------------------------------


async def test_18_the_birthday_board_is_a_permission_like_any_other(world: World) -> None:
    ads, _ = await design_world(world)

    def has(user: User) -> bool:
        world.act_as(user)
        return bool(world.client.get("/api/units/me").json()["design"]["birthday"])

    def listed(user: User) -> list[str]:
        return [item["code"] for item in board(world, user, category="BIRTHDAY")["items"]]

    # Thiết Kế is granted it by default (all of it); the OWNER has everything.
    assert has(ads.designer) and has(ads.lead_tk) and has(world.owner)
    assert not any(has(user) for user in (ads.orderer, ads.head, ads.writer, ads.editor))
    cells = grant_birthday(world, "TK", "MEMBER", "ALL")
    assert cells["HEAD"]["BIRTHDAY"] == "ALL" and cells["MEMBER"]["BIRTHDAY"] == "ALL"

    # Granted to Biên Tập's members, own scope: the tab, and their own orders.
    grant_birthday(world, "BT", "MEMBER", "OWN")
    assert has(ads.writer) and not has(ads.lead_bt) and not has(ads.editor)
    mine = birthday(world, ads.writer)
    assert mine.status_code == 201, mine.json()
    theirs = birthday(world, ads.designer).json()
    assert listed(ads.writer) == [mine.json()["code"]]
    assert sorted(listed(ads.designer)) == sorted([mine.json()["code"], theirs["code"]])
    world.act_as(ads.writer)
    assert world.client.get(f"/api/tasks/{theirs['code']}").status_code == 404
    # "ALL" for them: every birthday order.
    grant_birthday(world, "BT", "MEMBER", "ALL")
    assert sorted(listed(ads.writer)) == sorted([mine.json()["code"], theirs["code"]])

    # Taken back: no tab, no new order, and the board lists nothing - the
    # order they placed is still theirs to open.
    grant_birthday(world, "BT", "MEMBER", "NONE")
    assert not has(ads.writer)
    refused = birthday(world, ads.writer)
    assert refused.status_code == 403 and error_reason(refused.json()) == "birthday_forbidden"
    assert listed(ads.writer) == []
    world.act_as(ads.writer)
    assert world.client.get(f"/api/tasks/{mine.json()['code']}").status_code == 200
    # Only the OWNER grants it (a head reads the screen since 2026-10-17, a
    # member does not); the per-person switch is gone.
    world.act_as(ads.designer)
    assert world.client.get("/api/departments").status_code == 403
    world.act_as(world.owner)
    ignored = world.client.patch(
        f"/api/units/ADS/members/{ads.editor.id}", json={"birthday_orders": True}
    )
    assert ignored.status_code == 200 and "birthday_orders" not in ignored.json()
    assert not has(ads.editor)


async def test_19_a_birthday_order_carries_several_reference_links(world: World) -> None:
    ads, _ = await design_world(world)
    first, second = "https://drive.example/a.jpg", "\\\\10.10.40.45\\Marketing\\b.jpg"
    made = birthday(
        world,
        ads.designer,
        reference_link=None,
        reference_links=[f"  {first} ", "", second, first],
    )
    assert made.status_code == 201, made.json()
    body = made.json()
    page = task(world, ads.designer, body["code"])
    # Blanks and repeats are dropped, the order kept; the first is "the" link.
    assert page["design"]["reference_links"] == [first, second]
    assert page["design"]["reference_link"] == first
    shown = [(f["label"], f["value"]) for f in page["fields"] if f["key"].startswith("design_link")]
    assert shown == [("Ảnh minh hoạ", first), ("Ảnh minh hoạ 2", second)]
    row = board(world, ads.designer, category="BIRTHDAY")["items"][0]
    assert row["design"]["reference_links"] == [first, second]

    path = f"/api/orders/{body['id']}/birthday"
    world.act_as(ads.designer)
    too_many = world.client.patch(
        path,
        json={
            "version": page["task"]["version"],
            "reference_links": [f"https://e/{n}" for n in range(21)],
        },
    )
    assert too_many.status_code == 422, too_many.json()
    assert error_reason(too_many.json()) == "birthday_links_too_many"
    # An edit that does not mention the links leaves them alone...
    kept = world.client.patch(
        path, json={"version": page["task"]["version"], "branch": "CS - Sài Gòn"}
    )
    assert kept.status_code == 200, kept.json()
    assert task(world, ads.designer, body["code"])["design"]["reference_links"] == [first, second]
    # ...the list replaces them, and an empty one clears them.
    world.act_as(ads.designer)
    third = "https://drive.example/c.jpg"
    replaced = world.client.patch(
        path, json={"version": kept.json()["version"], "reference_links": [second, third]}
    )
    assert replaced.status_code == 200, replaced.json()
    assert task(world, ads.designer, body["code"])["design"]["reference_links"] == [second, third]
    world.act_as(ads.designer)
    cleared = world.client.patch(
        path, json={"version": replaced.json()["version"], "reference_links": []}
    )
    assert cleared.status_code == 200, cleared.json()
    page = task(world, ads.designer, body["code"])
    assert page["design"]["reference_links"] == [] and page["design"]["reference_link"] is None
    # The older single field still works.
    single = birthday(world, ads.designer, reference_link=first)
    assert single.status_code == 201
    assert task(world, ads.designer, single.json()["code"])["design"]["reference_links"] == [first]


# --- a Design-only order ("T") ---------------------------------------------------------


def design_only(world: World, ads: Ads, **overrides: Any) -> Any:
    """POST a Design-only order as the orderer; the raw response."""
    world.act_as(ads.orderer)
    body = {
        "title": "Banner tháng 11",
        "process": ["THIET_KE"],
        "order_content": "Banner khuyến mãi, tông đỏ.",
        "desired_deadline_at": FAR_DEADLINE,
        **overrides,
    }
    return world.client.post("/api/orders", json=body)


async def test_design_only_order_skips_the_head_and_lists_its_tasks(world: World) -> None:
    ads, codes = await design_world(world)
    # Its "Task công việc" are the design codes: required, and the video
    # kind / platform / duration are not asked for.
    refused = design_only(world, ads)
    assert refused.status_code == 422 and error_reason(refused.json()) == "design_codes_required"
    made = design_only(world, ads, design_codes=pick(codes, TKBTS=2))
    assert made.status_code == 201, made.json()
    detail = made.json()
    order = detail["order"]
    # No ORD approval: straight to Design, waiting on its Leader to hand out.
    assert order["stage"] == "THIET_KE"
    assert order.get("approver_user_id") is None
    tk = node(detail, "THIET_KE")
    assert tk["assignee_user_id"] == str(ads.lead_tk.id)
    assert not any(a["gate"] == "ORDER" for a in detail["approvals"])
    page = task(world, ads.lead_tk, order["code"])
    assert summary(page["design"]["codes"]) == [("TKBTS", 2)]
    keys = {field["key"] for field in page["fields"]}
    assert not keys & {"video_kind", "video_kind_points", "script_source"}
    # The ORD head is never asked to approve it.
    world.act_as(ads.head)
    seen = world.client.get(f"/api/tasks/{order['code']}")
    if seen.status_code == 200:
        assert not any("APPROVE_ORDER" in item["key"] for item in seen.json()["actions"])
    # Not approved yet: the Leader's hand-out is the approval.
    assert detail["order"]["order_approved_at"] is None
    # The designer's hand-in without a new list keeps the order's tasks.
    detail = act(
        world,
        ads.lead_tk,
        f"/nodes/{tk['id']}/assign",
        detail,
        assignee_user_id=str(ads.designer.id),
    )
    assert detail["order"]["order_approved_at"] is not None
    gates = [(a["gate"], a["decision"], a["actor_user_id"]) for a in detail["approvals"]]
    assert ("ORDER", "APPROVED", str(ads.lead_tk.id)) in gates
    detail = act(world, ads.designer, f"/nodes/{tk['id']}/accept", detail)
    detail = act(
        world,
        ads.designer,
        f"/nodes/{tk['id']}/submit",
        detail,
        link="https://e/tk",
        design_codes=pick(codes, TKBTS=2),
    )
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    # Approved once: later takes do not add another approval.
    assert sum(1 for a in detail["approvals"] if a["gate"] == "ORDER") == 1


async def test_design_only_content_is_not_read_by_the_ai(world: World) -> None:
    ads, codes = await design_world(world)
    # Words the (fake) AI refuses in a script pass in a Design-only "Nội dung".
    made = design_only(
        world, ads, order_content="Cam kết hiệu quả 100%", design_codes=pick(codes, CA=1)
    )
    assert made.status_code == 201, made.json()
    world.act_as(ads.orderer)
    scripted = world.client.post(
        "/api/orders",
        json={
            "title": "Video",
            "video_type": "BTD",
            "order_content": "Cam kết hiệu quả 100%",
            "desired_deadline_at": FAR_DEADLINE,
        },
    )
    assert scripted.status_code == 422
    assert error_reason(scripted.json()) == "script_review_failed"


async def test_design_only_the_design_leader_approves_by_taking_or_cancels(world: World) -> None:
    from tests.unit.test_order_commands import inbox

    ads, codes = await design_world(world)

    def kinds(user: User, code: str) -> set[str]:
        return {item["key"].split(":")[1] for item in task(world, user, code)["actions"]}

    # The Leader takes it: that is the approval too.
    taken = design_only(world, ads, design_codes=pick(codes, CA=1)).json()
    tk = node(taken, "THIET_KE")["id"]
    assert "CANCEL" in kinds(ads.lead_tk, taken["order"]["code"])
    # Before that the orderer may still take it back; afterwards not.
    assert "CANCEL" in kinds(ads.orderer, taken["order"]["code"])
    taken = act(world, ads.lead_tk, f"/nodes/{tk}/accept", taken)
    assert taken["order"]["order_approved_at"] is not None
    assert "CANCEL" not in kinds(ads.orderer, taken["order"]["code"])
    assert "CANCEL" in kinds(ads.lead_tk, taken["order"]["code"])
    # The Leader turns one down: cancelled, and the orderer is told why.
    refused = design_only(world, ads, title="Không làm", design_codes=pick(codes, CA=1)).json()
    refused = act(world, ads.lead_tk, "/cancel", refused, note="Trùng order khác")
    assert refused["order"]["stage"] == "CANCELLED"
    assert ("order_cancelled", "Order đã bị huỷ") in await inbox(world, ads.orderer)
    # A Design member is no stand-in for the Leader.
    other = design_only(world, ads, title="Khác", design_codes=pick(codes, CA=1)).json()
    world.act_as(ads.designer)
    seen = world.client.get(f"/api/tasks/{other['order']['code']}")
    if seen.status_code == 200:
        assert not any("CANCEL" in item["key"] for item in seen.json()["actions"])
