"""Task codes with points on every production step (0074).

* "Danh mục điểm": each phase's Trưởng phòng keeps that phase's codes (points,
  unit, examples); the OWNER / an ADMIN keeps the forms ("hình thức");
* the order **suggests** a code per ticked step (optional); the hand-in says
  what was **actually** done - the same moves on as before, a different one
  waits for the phase's Trưởng phòng (who is told how it differs) unless that
  Trưởng phòng did the step;
* the Trưởng phòng approves it as entered, or says why not (three reasons);
* a done step gives every person on it the points of their own codes.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from meobot.db.models.order import Order, OrderNode, OrderNodePoints
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.units.models import UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_design_ban import action, birthday, design_world, do, task
from tests.unit.test_order_commands import Ads, act, create, node, person
from tests.unit.test_units_gating import error_reason, tag

pytestmark = pytest.mark.asyncio


async def coded_world(world: World) -> tuple[Ads, dict[str, str]]:
    """The design world (codes on, N3 120 / D3 240 suggested), plus D4 480,
    a Design code worth 90, N1 15 and the Dựng step without review."""
    ads, codes = await design_world(world)
    ads.unit.settings = {**(ads.unit.settings or {}), "review_dung": False}
    await world.session.flush()
    world.act_as(world.owner)
    for phase, code, points in (
        ("DUNG", "D4", 480),
        ("THIET_KE", "T1", 90),
        ("BIEN_TAP", "N1", 15),
    ):
        made = world.client.post(
            "/api/units/ADS/task-codes",
            json={"code": code, "phase": phase, "points": points, "examples": f"{code} mẫu"},
        )
        assert made.status_code == 201, made.json()
        codes[code] = made.json()["id"]
    free = world.client.post(
        "/api/units/ADS/task-codes",
        json={"code": "D0", "name": "Việc khác", "phase": "DUNG", "free_points": True},
    )
    assert free.status_code == 201, free.json()
    codes["D0"] = free.json()["id"]
    world.task_codes = codes  # type: ignore[attr-defined]
    return ads, codes


async def at_dung(world: World, ads: Ads, **overrides: Any) -> tuple[str, dict[str, Any]]:
    """A Biên kịch > Dựng order at the Dựng step, the editor having accepted."""
    detail = create(
        world,
        ads,
        video_type="BD",
        preassigned={"BIEN_TAP": str(ads.writer.id), "DUNG": str(ads.editor.id)},
        **overrides,
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
        task_codes=[{"code_id": codes_of(world)["N3"]}],
    )
    if detail["order"]["stage"] == "BIEN_TAP":
        # The script step is reviewed (or its code waits) in this world.
        detail = act(world, ads.lead_bt, f"/nodes/{bt}/approve", detail)
    assert detail["order"]["stage"] == "DUNG", detail["order"]["stage"]
    dung = node(detail, "DUNG")["id"]
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    return code, detail


def codes_of(world: World) -> dict[str, str]:
    return world.task_codes  # type: ignore[attr-defined,no-any-return]


async def points_of(world: World, order_id: str) -> dict[str, int]:
    rows = (
        await world.session.scalars(
            select(OrderNodePoints).where(OrderNodePoints.order_id == uuid.UUID(order_id))
        )
    ).all()
    return {str(row.user_id): row.points for row in rows}


async def bodies(world: World, user: User) -> list[str]:
    rows = (
        await world.session.scalars(
            select(UserNotification).where(UserNotification.recipient_user_id == user.id)
        )
    ).all()
    return [row.body for row in rows]


# --- the catalogue ------------------------------------------------------------------


async def test_01_each_phase_keeps_its_own_codes_and_the_owner_the_forms(world: World) -> None:
    ads, codes = await coded_world(world)
    # Everybody in ORD reads a phase's codes, with their points and the forms.
    world.act_as(ads.orderer)
    listed = world.client.get("/api/units/ADS/task-codes", params={"phase": "DUNG"})
    assert listed.status_code == 200, listed.json()
    rows = {row["code"]: row for row in listed.json()["codes"]}
    assert set(rows) == {"D3", "D4", "D0"}
    assert rows["D4"]["points"] == 480 and rows["D0"]["free_points"] is True
    forms = {form["key"]: form for form in listed.json()["forms"]}
    assert forms["AFTER_EFFECTS"]["pct"] == 150 and forms["AFTER_EFFECTS"]["phases"] == ["DUNG"]
    assert forms["RESIZE"]["phases"] == ["THIET_KE", "DUNG"]
    # A Design code has no name of its own: the examples stand in.
    world.act_as(ads.designer)
    design = world.client.get("/api/units/ADS/task-codes", params={"phase": "THIET_KE"})
    t1 = next(row for row in design.json()["codes"] if row["code"] == "T1")
    assert t1["title"] is None and t1["name"] == "T1 mẫu"
    # The OWNER alone keeps it (2026-10-23): no Trưởng phòng, not even the step's.
    for refused_for in (ads.lead_tk, ads.lead_dung, ads.head):
        world.act_as(refused_for)
        refused = world.client.post(
            "/api/units/ADS/task-codes", json={"code": "D9", "name": "x", "phase": "DUNG"}
        )
        assert refused.status_code == 403
        assert error_reason(refused.json()) == "design_code_forbidden"
        edit = world.client.patch(f"/api/units/ADS/task-codes/{codes['D4']}", json={"points": 1})
        assert edit.status_code == 403
        me = world.client.get("/api/units/me").json()
        assert me["design"]["task_phases"] == [] and me["design"]["manage_forms"] is False
        forms = world.client.get("/api/units/ADS/task-forms").json()["forms"]
        denied = world.client.put("/api/units/ADS/task-forms", json={"forms": forms})
        assert denied.status_code == 403
    world.act_as(world.owner)
    edited = world.client.patch(f"/api/units/ADS/task-codes/{codes['D4']}", json={"points": 500})
    assert edited.status_code == 200 and edited.json()["points"] == 500
    me = world.client.get("/api/units/me").json()
    assert me["design"]["task_phases"] == ["BIEN_TAP", "THIET_KE", "DUNG"]
    assert me["design"]["manage_forms"] is True
    # "Làm mới" stays, at 100%.
    current = world.client.get("/api/units/ADS/task-forms").json()["forms"]
    without_new = [form for form in current if form["key"] != "NEW"]
    bad = world.client.put("/api/units/ADS/task-forms", json={"forms": without_new})
    assert bad.status_code == 422 and error_reason(bad.json()) == "task_form_invalid"
    cheaper = [{**form, "pct": 40} if form["key"] == "ADAPT" else form for form in current]
    saved = world.client.put("/api/units/ADS/task-forms", json={"forms": cheaper})
    assert saved.status_code == 200, saved.json()
    assert next(f for f in saved.json()["forms"] if f["key"] == "ADAPT")["pct"] == 40


# --- the suggestion on the order ------------------------------------------------------


async def test_02_the_order_suggests_a_code_per_step_with_its_points(world: World) -> None:
    ads, codes = await coded_world(world)
    world.act_as(ads.orderer)
    base = {
        "title": "Video A",
        "video_type": "BD",
        "order_content": "Ý tưởng.",
        "script_source": "AI",
        "desired_deadline_at": "2030-01-01T00:00:00+00:00",
    }
    # One code per single-code step, in a form that step offers.
    for task_codes, reason in (
        ({"DUNG": [{"code_id": codes["D3"]}, {"code_id": codes["D4"]}]}, "task_code_single"),
        ({"BIEN_TAP": [{"code_id": codes["N3"], "form": "ADAPT"}]}, "task_form_invalid"),
        ({"DUNG": [{"code_id": codes["N3"]}]}, "design_code_invalid"),
        ({"DUNG": [{"code_id": codes["D0"]}]}, "task_other_invalid"),
    ):
        refused = world.client.post("/api/orders", json={**base, "task_codes": task_codes})
        assert refused.status_code == 422, refused.json()
        assert error_reason(refused.json()) == reason
    # Biên kịch takes several codes, each with a quantity (2026-10-27).
    several = world.client.post(
        "/api/orders",
        json={
            **base,
            "task_codes": {
                "BIEN_TAP": [
                    {"code_id": codes["N3"]},
                    {"code_id": codes["N1"], "quantity": 2},
                ]
            },
        },
    )
    assert several.status_code == 201, several.json()
    page = task(world, ads.orderer, several.json()["order"]["code"])
    bien_tap = next(step for step in page["task_codes"] if step["phase"] == "BIEN_TAP")
    assert [(item["code"], item["quantity"]) for item in bien_tap["suggested"]] == [
        ("N3", 1),
        ("N1", 2),
    ]
    assert bien_tap["suggested_points"] == 120 + 2 * 15
    # No suggestion at all is fine; "Loại video" / "Thời lượng" are not asked.
    assert world.client.post("/api/orders", json={**base, "task_codes": {}}).status_code == 201
    made = world.client.post(
        "/api/orders",
        json={
            **base,
            "task_codes": {"DUNG": [{"code_id": codes["D3"], "form": "AFTER_EFFECTS"}]},
        },
    )
    assert made.status_code == 201, made.json()
    page = task(world, ads.orderer, made.json()["order"]["code"])
    steps = {step["phase"]: step for step in page["task_codes"]}
    assert set(steps) == {"BIEN_TAP", "DUNG"}
    dung = steps["DUNG"]
    assert dung["suggested"][0]["code"] == "D3"
    assert dung["suggested"][0]["form_label"] == "Video có After Effects"
    assert dung["suggested_points"] == 360 and dung["current_points"] == 360
    assert steps["BIEN_TAP"]["suggested"] == []


# --- the hand-in ----------------------------------------------------------------------


async def test_03_same_as_suggested_moves_on_a_different_code_waits(world: World) -> None:
    ads, codes = await coded_world(world)
    # Suggested D3; handed in as D3: Dựng without review moves straight on.
    code, detail = await at_dung(world, ads)
    submit = action(task(world, ads.editor, code), "ads:SUBMIT_WORK:")
    assert "task_codes" in submit["inputs"] and "task_codes" not in submit["required_inputs"]
    assert submit["task_phase"] == "DUNG" and submit["task_codes"][0]["code"] == "D3"
    done = do(
        world,
        ads.editor,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/v",
        task_codes=[{"code_id": codes["D3"]}],
    )
    assert done.status_code == 200, done.json()
    assert done.json()["task"]["stage"] == "FINAL_REVIEW"
    assert (await points_of(world, detail["order"]["id"]))[str(ads.editor.id)] == 240

    # Handed in as D4 + After Effects: it waits for the Dựng Trưởng phòng,
    # who is told how it differs, whatever the review setting.
    code, detail = await at_dung(world, ads)
    waiting = do(
        world,
        ads.editor,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/v",
        task_codes=[{"code_id": codes["D4"], "form": "AFTER_EFFECTS"}],
    )
    assert waiting.status_code == 200, waiting.json()
    page = waiting.json()
    assert page["task"]["stage"] == "DUNG" and page["task"]["state"] == "CHO_DUYET"
    dung = next(step for step in page["task_codes"] if step["phase"] == "DUNG")
    assert dung["proposed_points"] == 720 and "Chênh: +480 điểm" in dung["change"]
    told = await bodies(world, ads.lead_dung)
    assert any("Chênh: +480 điểm" in body for body in told), told
    # Approved as entered: the points are the editor's.
    approve = action(task(world, ads.lead_dung, code), "ads:APPROVE_NODE:")
    assert approve["inputs"] == ["note"] and "Đề xuất: D3" in approve["hint"]
    approved = do(world, ads.lead_dung, code, "ads:APPROVE_NODE:")
    assert approved.status_code == 200, approved.json()
    assert approved.json()["task"]["stage"] == "FINAL_REVIEW"
    assert (await points_of(world, detail["order"]["id"]))[str(ads.editor.id)] == 720


async def test_04_with_nothing_suggested_the_worker_must_pick(world: World) -> None:
    ads, codes = await coded_world(world)
    code, _ = await at_dung(world, ads, task_codes={})
    submit = action(task(world, ads.editor, code), "ads:SUBMIT_WORK:")
    assert "task_codes" in submit["required_inputs"] and submit["task_codes"] == []
    missing = do(world, ads.editor, code, "ads:SUBMIT_WORK:", link="https://e/v")
    assert missing.status_code == 422 and error_reason(missing.json()) == "task_codes_required"
    # A first pick where nothing was suggested is a change: it waits.
    picked = do(
        world,
        ads.editor,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/v",
        task_codes=[{"code_id": codes["D3"]}],
    )
    assert picked.status_code == 200, picked.json()
    assert picked.json()["task"]["state"] == "CHO_DUYET"
    dung = next(step for step in picked.json()["task_codes"] if step["phase"] == "DUNG")
    assert dung["change"].startswith("Đề xuất: (trống)")


async def test_05_a_trưởng_phòng_who_does_the_step_sets_the_code_himself(world: World) -> None:
    ads, codes = await coded_world(world)
    detail = create(world, ads, video_type="BD", preassigned={"BIEN_TAP": str(ads.writer.id)})
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    detail = act(world, ads.writer, f"/nodes/{bt}/submit", detail, link="https://e/script")
    if detail["order"]["stage"] == "BIEN_TAP":
        detail = act(world, ads.lead_bt, f"/nodes/{bt}/approve", detail)
    dung = node(detail, "DUNG")["id"]
    # Routed to the Dựng Trưởng phòng, who takes it and hands in D4.
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/accept", detail)
    done = do(
        world,
        ads.lead_dung,
        detail["order"]["code"],
        "ads:SUBMIT_WORK:",
        link="https://e/v",
        task_codes=[{"code_id": codes["D4"]}],
    )
    assert done.status_code == 200, done.json()
    page = done.json()
    assert page["task"]["stage"] == "FINAL_REVIEW"
    assert any(
        (entry["note"] or "").startswith("Trưởng phòng tự chốt mã task Dựng")
        for entry in page["timeline"]
    )
    assert (await points_of(world, detail["order"]["id"]))[str(ads.lead_dung.id)] == 480


async def test_06_a_free_line_needs_its_work_and_points(world: World) -> None:
    ads, codes = await coded_world(world)
    code, detail = await at_dung(world, ads)
    for line in (
        {"code_id": codes["D0"]},
        {"code_id": codes["D0"], "note": "Tách nền"},
        {"code_id": codes["D0"], "points": 90},
    ):
        refused = do(
            world, ads.editor, code, "ads:SUBMIT_WORK:", link="https://e/v", task_codes=[line]
        )
        assert refused.status_code == 422 and error_reason(refused.json()) == "task_other_invalid"
    sent = do(
        world,
        ads.editor,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/v",
        task_codes=[{"code_id": codes["D0"], "note": "Tách nền 20 ảnh", "points": 90}],
    )
    assert sent.status_code == 200, sent.json()
    dung = next(step for step in sent.json()["task_codes"] if step["phase"] == "DUNG")
    assert dung["proposed"][0]["free"] is True and dung["proposed"][0]["note"] == "Tách nền 20 ảnh"
    assert "Việc khác (90đ): Tách nền 20 ảnh" in dung["change"]
    do(world, ads.lead_dung, code, "ads:APPROVE_NODE:")
    assert (await points_of(world, detail["order"]["id"]))[str(ads.editor.id)] == 90


# --- sending back ---------------------------------------------------------------------


async def test_07_three_reasons_to_send_back_and_what_each_counts(world: World) -> None:
    ads, _ = await coded_world(world)
    ads.unit.settings = {**(ads.unit.settings or {}), "review_dung": True}
    await world.session.flush()
    code, detail = await at_dung(world, ads)
    dung_id = node(detail, "DUNG")["id"]

    async def counters() -> tuple[int, int, int]:
        row = await world.session.get(OrderNode, uuid.UUID(dung_id))
        assert row is not None
        await world.session.refresh(row)
        return row.revision_count, row.returns_fault, row.returns_other

    def hand_in() -> None:
        sent = do(world, ads.editor, code, "ads:SUBMIT_WORK:", link="https://e/v")
        assert sent.status_code == 200, sent.json()

    returns = action(task(world, ads.editor, code), "ads:SUBMIT_WORK:")
    assert returns["link_default"] is None
    hand_in()
    page = task(world, ads.lead_dung, code)
    assert "return_reason" in action(page, "ads:RETURN_NODE:")["required_inputs"]
    for reason, expected in (
        ("WORKER_FAULT", (1, 1, 0)),
        ("CHANGED_REQUEST", (2, 1, 1)),
        ("WRONG_CODES", (2, 1, 1)),
    ):
        sent_back = do(
            world,
            ads.lead_dung,
            code,
            "ads:RETURN_NODE:",
            note="Xem lại",
            tokens=0,
            return_reason=reason,
        )
        assert sent_back.status_code == 200, sent_back.json()
        assert await counters() == expected, reason
        hand_in()


# --- points ------------------------------------------------------------------------------


async def test_08_two_designers_each_earn_their_own_points(world: World) -> None:
    ads, codes = await coded_world(world)
    second = await person(world, "Thiết kế B")
    await tag(world, ads.unit, second, UnitMemberRole.THIET_KE)
    t1 = codes["T1"]
    detail = create(
        world,
        ads,
        preassigned={"BIEN_TAP": str(ads.writer.id), "DUNG": str(ads.editor.id)},
        task_codes={
            **world.task_suggestions,  # type: ignore[attr-defined]
            "THIET_KE": [{"code_id": t1, "quantity": 6, "form": "RESIZE"}],
        },
    )
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    bt = node(detail, "BIEN_TAP")["id"]
    detail = act(world, ads.writer, f"/nodes/{bt}/accept", detail)
    detail = act(world, ads.writer, f"/nodes/{bt}/submit", detail, link="https://e/s")
    if detail["order"]["stage"] == "BIEN_TAP":
        detail = act(world, ads.lead_bt, f"/nodes/{bt}/approve", detail)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(
        world,
        ads.lead_tk,
        f"/nodes/{tk}/assign",
        detail,
        assignee_user_ids=[str(ads.designer.id), str(second.id)],
    )
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    detail = act(world, second, f"/nodes/{tk}/accept", detail)
    # Split 4 / 2 of the suggested six resizes: the total matches, it moves on.
    first = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/a",
        design_codes=[{"code_id": t1, "quantity": 4, "form": "RESIZE"}],
    )
    assert first.status_code == 200, first.json()
    last = do(
        world,
        second,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/b",
        design_codes=[{"code_id": t1, "quantity": 2, "form": "RESIZE"}],
    )
    assert last.status_code == 200, last.json()
    assert last.json()["task"]["stage"] == "DUNG"
    earned = await points_of(world, detail["order"]["id"])
    # 90 x 20% x 4 = 72; 90 x 20% x 2 = 36.
    assert earned[str(ads.designer.id)] == 72 and earned[str(second.id)] == 36
    assert earned[str(ads.writer.id)] == 120  # the script step's N3
    step = next(s for s in last.json()["task_codes"] if s["phase"] == "THIET_KE")
    assert {person["points"] for person in step["people"]} == {72, 36}
    assert all(person["earned"] for person in step["people"])
    # The month's figures read them.
    world.act_as(ads.designer)
    me = world.client.get("/api/account/me")
    assert me.status_code == 200


async def _birthday_done(world: World, ads: Ads, code: str) -> dict[str, Any]:
    assigned = do(
        world,
        ads.lead_tk,
        code,
        "ads:ASSIGN:",
        assignee_user_id=str(ads.designer.id),
        tokens=1,
        deadline_at="2030-01-01T00:00:00+00:00",
    )
    assert assigned.status_code == 200, assigned.json()
    assert do(world, ads.designer, code, "ads:ACCEPT:").status_code == 200
    submit = action(task(world, ads.designer, code), "ads:SUBMIT_WORK:")
    assert "design_codes" not in submit["inputs"] and "task_codes" not in submit["inputs"]
    done = do(world, ads.designer, code, "ads:SUBMIT_WORK:", link="https://e/sn")
    assert done.status_code == 200, done.json()
    page: dict[str, Any] = done.json()
    return page


async def test_09_a_birthday_order_carries_t1_and_scores_it(world: World) -> None:
    ads, _ = await coded_world(world)
    # Even with the Design step reviewed, a birthday card does not wait.
    ads.unit.settings = {**(ads.unit.settings or {}), "review_thiet_ke": True}
    await world.session.flush()
    made = birthday(world, ads.lead_tk)
    assert made.status_code == 201, made.json()
    code = made.json()["code"]
    # 2026-10-23: a birthday card is Design's T1 x1, nobody picks it.
    page = task(world, ads.lead_tk, code)
    (design,) = page["task_codes"]
    assert design["phase"] == "THIET_KE"
    assert [(item["code"], item["quantity"]) for item in design["current"]] == [("T1", 1)]
    assert design["current_points"] == 90
    done = await _birthday_done(world, ads, code)
    assert done["task"]["stage"] == "COMPLETED"
    order_id = done["task"]["source"]["id"]
    assert (await points_of(world, order_id)) == {str(ads.designer.id): 90}


async def test_09b_a_birthday_order_placed_before_t1_still_scores_it(world: World) -> None:
    ads, _ = await coded_world(world)
    made = birthday(world, ads.lead_tk)
    assert made.status_code == 201, made.json()
    order = await world.session.get(Order, uuid.UUID(made.json()["id"]))
    assert order is not None
    order.design_items = None  # as placed before birthday orders carried T1
    order.suggested_task_items = None
    await world.session.flush()
    done = await _birthday_done(world, ads, made.json()["code"])
    assert done["task"]["stage"] == "COMPLETED"
    assert (await points_of(world, str(order.id))) == {str(ads.designer.id): 90}
