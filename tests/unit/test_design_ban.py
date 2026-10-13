"""The Design team's own rules (0058).

* the catalogue of design codes ("mã video"): everybody in ORD reads it, the
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
        ads.orderer: {"view": False, "manage_codes": False, "birthday": True},
        ads.head: {"view": False, "manage_codes": False, "birthday": True},
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
    assert any(entry["label"].startswith("Chốt mã video") for entry in page["timeline"])
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
    assert "design_codes" in submit["inputs"] and "design_codes" not in submit["required_inputs"]
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
    note = next(e for e in page["timeline"] if e["label"].startswith("Yêu cầu thay đổi mã video"))
    assert note["note"] == (
        "Nhân viên yêu cầu thay đổi mã video: TKBTS x3, CA x1 → TKBTS x2, O-TB x1"
    )
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
    assert error_reason(missing.json()) == "design_codes_required"
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

    made = birthday(world, ads.orderer)
    assert made.status_code == 201, made.json()
    code = made.json()["code"]
    assert code.startswith("TUAN-SN-")
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
