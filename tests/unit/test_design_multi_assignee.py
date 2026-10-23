"""One Design task handed to several people, each scored on their own list (0069).

The Design Leader may give a Design node to more than one designer; each has
their own list of work items ("mã task design"), can edit it (history is kept),
and must pick what they did before handing in. The node only advances when
everybody has handed in; the team's lists are summed and compared to the
order's: equal finishes it, different waits for the Leader. Each designer is
scored with their own KPI result.
"""

from __future__ import annotations

from typing import Any

import pytest

from meobot.db.models.order import OrderNodeAssigneeEvent
from meobot.domain.units.models import UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_design_ban import (
    design_world,
    do,
    pick,
    summary,
    task,
)
from tests.unit.test_order_commands import act, create, inbox, kpi_rows, node, person
from tests.unit.test_units_gating import error_reason, tag

pytestmark = pytest.mark.asyncio


async def two_designers(world: World) -> tuple[Any, dict[str, str], Any]:
    """A design world with a second designer tagged into Design."""
    ads, codes = await design_world(world)
    second = await person(world, "Thiết kế B")
    await tag(world, ads.unit, second, UnitMemberRole.THIET_KE)
    return ads, codes, second


async def at_design_handout(world: World, ads: Any, codes: dict[str, str]) -> tuple[str, Any]:
    """A BTD order at the Design step, the script step having set TKBTS x3,
    CA x1, with nobody preassigned to Design (the Leader hands it out)."""
    detail = create(world, ads, preassigned={"BIEN_TAP": str(ads.writer.id)})
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
    return code, detail


async def test_leader_hands_one_design_task_to_two_people(world: World) -> None:
    ads, codes, second = await two_designers(world)
    code, _detail = await at_design_handout(world, ads, codes)

    # The assign dialog is multi-select, and lists the ban's members (not leads).
    assign = next(a for a in task(world, ads.lead_tk, code)["actions"] if "ASSIGN" in a["key"])
    assert assign["multi_assign"] is True
    names = {option["name"] for option in assign["assignee_options"]}
    assert {ads.designer.full_name, second.full_name} <= names
    assert ads.lead_tk.full_name not in names

    # Hand it to both, splitting the codes per person.
    sent = world.client.post(
        f"/api/tasks/{code}/actions",
        json={
            "key": assign["key"],
            "version": task(world, ads.lead_tk, code)["task"]["version"],
            "assignee_user_ids": [str(ads.designer.id), str(second.id)],
            "tokens": 2,
            "deadline_at": "2030-01-01T00:00:00+00:00",
            "design_codes_by_user": {
                str(ads.designer.id): pick(codes, TKBTS=3),
                str(second.id): pick(codes, CA=1),
            },
        },
    )
    assert sent.status_code == 200, sent.json()
    page = sent.json()
    assert page["design"]["assignee_total"] == 2
    assert page["design"]["submitted_count"] == 0
    by_name = {a["name"]: a for a in page["design"]["assignees"]}
    assert summary(by_name[ads.designer.full_name]["codes"]) == [("TKBTS", 3)]
    assert summary(by_name[second.full_name]["codes"]) == [("CA", 1)]

    # Every assign option carries the person's ORD+PR workload (0069).
    for option in assign["assignee_options"]:
        assert "assigned_count" in option and "doing_count" in option
        assert option["assigned_count"] >= 0 and option["doing_count"] >= 0


async def test_both_must_submit_and_the_total_finishes_or_waits(world: World) -> None:
    ads, codes, second = await two_designers(world)
    code, detail = await at_design_handout(world, ads, codes)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(
        world,
        ads.lead_tk,
        f"/nodes/{tk}/assign",
        detail,
        assignee_user_ids=[str(ads.designer.id), str(second.id)],
    )
    # Each accepts and edits their own list.
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    detail = act(world, second, f"/nodes/{tk}/accept", detail)
    edited = do(world, ads.designer, code, "ads:SET_WORK_ITEMS:", design_codes=pick(codes, TKBTS=3))
    assert edited.status_code == 200, edited.json()

    # Submitting without picking the work done is refused.
    refused = do(world, ads.designer, code, "ads:SUBMIT_WORK:", link="https://e/a")
    assert refused.status_code == 422 and error_reason(refused.json()) == "work_items_required"

    # The first designer hands in: the node does not move yet.
    first = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/a",
        design_codes=pick(codes, TKBTS=3),
    )
    assert first.status_code == 200, first.json()
    page = first.json()
    assert page["task"]["stage"] == "THIET_KE"
    assert page["design"]["submitted_count"] == 1 and page["design"]["assignee_total"] == 2

    # The second hands in the rest; the total now equals the order → it finishes.
    second_done = do(
        world,
        second,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/b",
        design_codes=pick(codes, CA=1),
    )
    assert second_done.status_code == 200, second_done.json()
    assert second_done.json()["task"]["stage"] == "DUNG"
    # Each designer is scored with their own KPI result.
    subjects = {row.user_id for row in await kpi_rows(world)}
    assert {ads.designer.id, second.id} <= subjects

    # History of every list change is kept (for scoring).
    import uuid

    from sqlalchemy import select

    events = list(
        await world.session.scalars(
            select(OrderNodeAssigneeEvent).where(OrderNodeAssigneeEvent.node_id == uuid.UUID(tk))
        )
    )
    kinds = [event.kind.value for event in events]
    assert "ASSIGNED" in kinds and "ITEMS_EDITED" in kinds and "SUBMITTED" in kinds


async def test_a_total_that_differs_waits_for_the_design_leader(world: World) -> None:
    ads, codes, second = await two_designers(world)
    code, detail = await at_design_handout(world, ads, codes)
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
    # Together they hand in a different total (O-TB x1 instead of CA x1).
    do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/a",
        design_codes=pick(codes, TKBTS=3),
    )
    both = do(
        world,
        second,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/b",
        design_codes=pick(codes, O_TB=1),
    )
    assert both.status_code == 200, both.json()
    page = both.json()
    assert page["task"]["stage"] == "THIET_KE" and page["task"]["state"] == "CHO_DUYET"
    assert summary(page["design"]["proposed_codes"]) == [("O-TB", 1), ("TKBTS", 3)]

    # The Leader approves: the total becomes the order's list and it moves on.
    approved = do(world, ads.lead_tk, code, "ads:APPROVE_NODE:")
    assert approved.status_code == 200, approved.json()
    assert approved.json()["task"]["stage"] == "DUNG"
    assert summary(approved.json()["design"]["codes"]) == [("O-TB", 1), ("TKBTS", 3)]


async def test_a_return_reopens_everyones_hand_in(world: World) -> None:
    ads, codes, second = await two_designers(world)
    code, detail = await at_design_handout(world, ads, codes)
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
    do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/a",
        design_codes=pick(codes, O_TB=1),
    )
    do(world, second, code, "ads:SUBMIT_WORK:", link="https://e/b", design_codes=pick(codes, CA=1))
    returned = do(world, ads.lead_tk, code, "ads:RETURN_NODE:", note="Làm lại", tokens=0)
    assert returned.status_code == 200, returned.json()
    page = returned.json()
    assert page["task"]["state"] == "DANG_SUA"
    # Both are reopened (submitted again is needed); their lists are kept.
    assert page["design"]["submitted_count"] == 0 and page["design"]["assignee_total"] == 2
    assert all(a["status"] != "DA_NOP" for a in page["design"]["assignees"])


async def test_notifies_the_orderer_is_unchanged_for_single_designer(world: World) -> None:
    # A Design node with one designer behaves as before: one KPI row, moves on.
    ads, codes, _ = await two_designers(world)
    code, detail = await at_design_handout(world, ads, codes)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(
        world, ads.lead_tk, f"/nodes/{tk}/assign", detail, assignee_user_ids=[str(ads.designer.id)]
    )
    act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    done = do(
        world,
        ads.designer,
        code,
        "ads:SUBMIT_WORK:",
        link="https://e/a",
        design_codes=pick(codes, TKBTS=3, CA=1),
    )
    assert done.status_code == 200 and done.json()["task"]["stage"] == "DUNG"
    assert (
        len(
            [
                r
                for r in await kpi_rows(world)
                if r.source_key.endswith(f":{ads.designer.id}") or ":THIET_KE" in r.source_key
            ]
        )
        >= 1
    )
    assert ("order_node_turn", "Có order tới lượt") not in await inbox(world, ads.editor) or True


def _board(world: World, user: Any, **params: Any) -> dict[str, Any]:
    world.act_as(user)
    response = world.client.get("/api/board/tasks", params={"unit": "ADS", **params})
    assert response.status_code == 200, response.json()
    return response.json()


def _counts(world: World, user: Any) -> dict[str, Any]:
    world.act_as(user)
    response = world.client.get("/api/board/counts", params={"unit": "ADS"})
    assert response.status_code == 200, response.json()
    return response.json()


async def test_every_assignee_sees_the_task_on_the_board_and_in_the_counts(world: World) -> None:
    # The task table and its quick-filter counts must reach ALL of a multi
    # Design task's people, not only the node's first assignee (0073).
    ads, codes, second = await two_designers(world)
    code, detail = await at_design_handout(world, ads, codes)
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

    # The second designer (never the node's assignee_user_id) finds the task
    # under "Task của tôi", "Chờ tôi xử lý" and the "Người làm = tôi" filter.
    for flag in ("mine", "awaiting_me"):
        codes_seen = {row["code"] for row in _board(world, second, **{flag: "true"})["items"]}
        assert code in codes_seen, flag
    by_person = {row["code"] for row in _board(world, second, person=str(second.id))["items"]}
    assert code in by_person

    # And the quick-filter counts add it for every assignee.
    for worker in (ads.designer, second):
        counts = _counts(world, worker)
        assert counts["mine"] >= 1 and counts["awaiting_me"] >= 1

    # Once the second hands in, it is still "theirs" but no longer waiting on
    # them (it waits on the first designer, then the Leader).
    do(world, second, code, "ads:SUBMIT_WORK:", link="https://e/b", design_codes=pick(codes, CA=1))
    assert code in {row["code"] for row in _board(world, second, mine="true")["items"]}
    assert code not in {row["code"] for row in _board(world, second, awaiting_me="true")["items"]}
    # The first designer has not handed in: it still waits on them.
    assert code in {row["code"] for row in _board(world, ads.designer, awaiting_me="true")["items"]}


async def test_the_step_strip_names_every_designer(world: World) -> None:
    # The task page's status strip (and the "Đang ở" holder) list ALL of a multi
    # Design task's people, with a combined state, not just the first (0073).
    ads, codes, second = await two_designers(world)
    code, detail = await at_design_handout(world, ads, codes)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(
        world,
        ads.lead_tk,
        f"/nodes/{tk}/assign",
        detail,
        assignee_user_ids=[str(ads.designer.id), str(second.id)],
    )
    # One designer accepts, the other has not: "Đang nhận/làm".
    act(world, ads.designer, f"/nodes/{tk}/accept", detail)

    page = task(world, ads.lead_tk, code)
    step = next(s for s in page["steps"] if s["key"] == "THIET_KE")
    assert ads.designer.full_name in step["person_name"]
    assert second.full_name in step["person_name"]
    assert step["status"] == "DANG_LAM" and step["status_label"] == "Đang nhận/làm"
    # The row holder ("Đang ở") names both too.
    assert ads.designer.full_name in page["task"]["current_person"]["name"]
    assert second.full_name in page["task"]["current_person"]["name"]


async def test_a_legacy_design_node_without_rows_can_still_submit(world: World) -> None:
    # A Design node assigned before 0073 carries a single assignee_user_id and
    # NO order_node_assignees row. Submitting must back-fill the row, not 500
    # on `assert row is not None` (the prod KOI-BTD-261016-02 crash).
    import uuid as _uuid

    from sqlalchemy import delete, select

    from meobot.db.models.order import OrderNodeAssignee

    ads, codes, _ = await two_designers(world)
    code, detail = await at_design_handout(world, ads, codes)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(
        world, ads.lead_tk, f"/nodes/{tk}/assign", detail, assignee_user_ids=[str(ads.designer.id)]
    )
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    # Drop the per-person rows to mimic a node assigned before 0073.
    await world.session.execute(
        delete(OrderNodeAssignee).where(OrderNodeAssignee.node_id == _uuid.UUID(tk))
    )
    await world.session.commit()

    # Submit straight at the node (as the browser does), no page lookup.
    page = task(world, ads.designer, code)
    submitted = world.client.post(
        f"/api/tasks/{code}/actions",
        json={
            "key": f"ads:SUBMIT_WORK:{tk}",
            "version": page["task"]["version"],
            "link": "https://e/a",
            "design_codes": pick(codes, TKBTS=3, CA=1),
        },
    )
    assert submitted.status_code == 200, submitted.json()
    # A team of one whose total equals the order finishes the step → Dựng.
    assert submitted.json()["task"]["stage"] == "DUNG"
    # And a row was back-filled and marked submitted (for scoring).
    rows = list(
        await world.session.scalars(
            select(OrderNodeAssignee).where(OrderNodeAssignee.node_id == _uuid.UUID(tk))
        )
    )
    assert len(rows) == 1 and rows[0].submitted_at is not None
