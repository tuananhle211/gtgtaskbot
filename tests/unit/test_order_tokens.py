"""ORD token effort and deadlines (0053, docs/ord/ORD_TOKEN_DEADLINE_PLAN.md).

* a node is planned (tokens + deadline) when it is handed out - required the
  first time; a deadline past the orderer's wish is only counted;
* approval takes the tokens off the worker's day (one ledger row, the
  Vietnamese day); a return adds "token sửa", taken when the fix is approved;
  a final return leaves the Leader a "Nhập token sửa" to-do;
* the effort grid shows budget / used / left per person and day, to whom it
  may; the board flags overdue steps; the month's stats score it.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, update

from meobot.core.time import utcnow
from meobot.db.models.order import OrderNode, OrderTokenLedger
from meobot.domain.orders.deadlines import work_day
from tests.unit.pr_world import World
from tests.unit.test_order_commands import Ads, ads_world, create, node
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio

SOON = (utcnow() + timedelta(days=2)).isoformat()
LATER = (utcnow() + timedelta(days=5)).isoformat()


def call(world: World, user: Any, path: str, detail: dict[str, Any], **body: Any) -> Any:
    world.act_as(user)
    return world.client.post(
        f"/api/orders/{detail['order']['id']}{path}",
        json={"version": detail["order"]["version"], **body},
    )


def ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.json()
    result: dict[str, Any] = response.json()
    return result


async def ledger(world: World) -> list[OrderTokenLedger]:
    return list(
        await world.session.scalars(select(OrderTokenLedger).order_by(OrderTokenLedger.created_at))
    )


async def at_the_cut(world: World, ads: Ads, *, desired: str = LATER) -> dict[str, Any]:
    """A Dựng-only order, approved: routed to the Dựng Leader to hand out."""
    detail = create(world, ads, video_type="D", design_link="d", desired_deadline_at=desired)
    return ok(call(world, ads.head, "/approve", detail))


async def test_01_handing_out_needs_tokens_and_a_deadline(world: World) -> None:
    ads = await ads_world(world)
    detail = await at_the_cut(world, ads)
    dung = node(detail, "DUNG")["id"]
    path = f"/nodes/{dung}/assign"
    bare = call(world, ads.lead_dung, path, detail, assignee_user_id=str(ads.editor.id))
    assert bare.status_code == 422 and error_reason(bare.json()) == "tokens_required"
    no_deadline = call(
        world, ads.lead_dung, path, detail, assignee_user_id=str(ads.editor.id), tokens=3
    )
    assert error_reason(no_deadline.json()) == "deadline_required"
    past = call(
        world,
        ads.lead_dung,
        path,
        detail,
        assignee_user_id=str(ads.editor.id),
        tokens=3,
        deadline_at=(utcnow() - timedelta(hours=1)).isoformat(),
    )
    assert error_reason(past.json()) == "deadline_in_past"
    bad = call(world, ads.lead_dung, path, detail, assignee_user_id=str(ads.editor.id), tokens=-1)
    assert bad.status_code == 422

    done = ok(
        call(
            world,
            ads.lead_dung,
            path,
            detail,
            assignee_user_id=str(ads.editor.id),
            tokens=3,
            deadline_at=SOON,
        )
    )
    cut = node(done, "DUNG")
    assert cut["token_estimate"] == 3.0 and cut["deadline_at"] is not None
    assert cut["deadline_status"] == "ON_TRACK"
    assert done["order"]["over_deadline_count"] == 0
    assert any(event["kind"] == "PLAN_SET" for event in done["events"])


async def test_02_a_deadline_past_the_wish_is_counted_not_refused(world: World) -> None:
    ads = await ads_world(world)
    detail = await at_the_cut(world, ads, desired=SOON)
    dung = node(detail, "DUNG")["id"]
    done = ok(
        call(
            world,
            ads.lead_dung,
            f"/nodes/{dung}/assign",
            detail,
            assignee_user_id=str(ads.editor.id),
            tokens=2,
            deadline_at=LATER,
        )
    )
    assert done["order"]["over_deadline_count"] == 1
    exceeded = [event for event in done["events"] if event["kind"] == "DEADLINE_EXCEEDED"]
    assert len(exceeded) == 1 and "vượt deadline mong muốn 3 ngày" in exceeded[0]["note"]
    # Moving it later again counts again; the task page shows the count.
    moved = ok(
        call(
            world,
            ads.lead_dung,
            f"/nodes/{dung}/plan",
            done,
            deadline_at=(utcnow() + timedelta(days=6)).isoformat(),
        )
    )
    assert moved["order"]["over_deadline_count"] == 2
    world.act_as(ads.lead_dung)
    page = world.client.get(f"/api/tasks/{moved['order']['code']}").json()
    assert page["task"]["over_deadline_count"] == 2
    fields = {field["key"]: field["value"] for field in page["fields"]}
    assert fields["over_deadline_count"] == "2 lần" and fields["desired_deadline"]


async def test_03_approval_takes_the_tokens_off_the_workers_day(world: World) -> None:
    ads = await ads_world(world)
    detail = await at_the_cut(world, ads)
    dung = node(detail, "DUNG")["id"]
    detail = ok(
        call(
            world,
            ads.lead_dung,
            f"/nodes/{dung}/assign",
            detail,
            assignee_user_id=str(ads.editor.id),
            tokens=3,
            deadline_at=SOON,
        )
    )
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/accept", detail))
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v1"))
    # Sent back once with 1.5 revision tokens.
    detail = ok(
        call(world, ads.lead_dung, f"/nodes/{dung}/return", detail, note="Nhạc", tokens=1.5)
    )
    assert node(detail, "DUNG")["token_revision"] == 1.5
    assert await ledger(world) == []  # nothing approved yet
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v2"))
    detail = ok(call(world, ads.lead_dung, f"/nodes/{dung}/approve", detail))
    rows = await ledger(world)
    assert [(row.kind.value, row.tokens, row.revision_no) for row in rows] == [
        ("ESTIMATE", Decimal("3.00"), 0),
        ("REVISION", Decimal("1.50"), 1),
    ]
    assert {row.user_id for row in rows} == {ads.editor.id}
    assert {row.work_date for row in rows} == {work_day(utcnow())}
    assert node(detail, "DUNG")["deadline_met"] is True
    assert node(detail, "DUNG")["deadline_status"] == "MET"

    # The orderer sends the product back: the Leader owes the revision tokens.
    detail = ok(call(world, ads.orderer, "/final/return", detail, note="Đổi logo"))
    assert node(detail, "DUNG")["revision_tokens_pending"] is True
    world.act_as(ads.lead_dung)
    page = world.client.get(f"/api/tasks/{detail['order']['code']}").json()
    plan = next(a for a in page["actions"] if a["key"].startswith("ads:SET_NODE_PLAN:"))
    assert plan["label"] == "Nhập token sửa · Dựng"
    assert plan["required_inputs"] == ["tokens"] and plan["plan_mode"] == "REVISION"
    todo = world.client.get("/api/board/tasks?unit=ADS&awaiting_me=true").json()["items"]
    assert [row["code"] for row in todo] == [detail["order"]["code"]]
    detail = ok(call(world, ads.lead_dung, f"/nodes/{dung}/plan", detail, tokens=2))
    assert node(detail, "DUNG")["revision_tokens_pending"] is False
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v3"))
    detail = ok(call(world, ads.lead_dung, f"/nodes/{dung}/approve", detail))
    rows = await ledger(world)
    assert [(row.kind.value, row.tokens, row.revision_no) for row in rows][-1] == (
        "REVISION",
        Decimal("2.00"),
        2,
    )
    assert sum(row.tokens for row in rows) == Decimal("6.50")


async def test_04_a_leader_taking_a_routed_node_plans_it(world: World) -> None:
    ads = await ads_world(world)
    detail = await at_the_cut(world, ads)
    dung = node(detail, "DUNG")
    assert dung["assignee_user_id"] == str(ads.lead_dung.id)  # routed to hand out
    world.act_as(ads.lead_dung)
    page = world.client.get(f"/api/tasks/{detail['order']['code']}").json()
    accept = next(a for a in page["actions"] if a["key"].startswith("ads:ACCEPT:"))
    assert accept["inputs"] == ["tokens", "deadline"]
    assert accept["required_inputs"] == ["tokens", "deadline"]
    refused = call(world, ads.lead_dung, f"/nodes/{dung['id']}/accept", detail)
    assert error_reason(refused.json()) == "tokens_required"
    taken = ok(
        call(
            world,
            ads.lead_dung,
            f"/nodes/{dung['id']}/accept",
            detail,
            tokens=4,
            deadline_at=SOON,
        )
    )
    assert node(taken, "DUNG")["token_estimate"] == 4.0


async def test_05_overdue_steps_are_flagged_and_finish_missed(world: World) -> None:
    ads = await ads_world(world)
    detail = await at_the_cut(world, ads)
    dung = node(detail, "DUNG")["id"]
    detail = ok(
        call(
            world,
            ads.lead_dung,
            f"/nodes/{dung}/assign",
            detail,
            assignee_user_id=str(ads.editor.id),
            tokens=3,
            deadline_at=SOON,
        )
    )
    await world.session.execute(
        update(OrderNode)
        .where(OrderNode.order_id == uuid.UUID(detail["order"]["id"]))
        .values(deadline_at=utcnow() - timedelta(hours=2))
    )
    await world.session.flush()
    world.act_as(ads.lead_dung)
    late = world.client.get("/api/board/tasks?unit=ADS&overdue=true").json()["items"]
    assert [row["code"] for row in late] == [detail["order"]["code"]]
    assert late[0]["deadline_status"] == "OVERDUE"
    cell = next(c for c in late[0]["cells"] if c["key"] == "DUNG")
    assert cell["deadline_status"] == "OVERDUE" and cell["tokens"] == 3.0
    dashboard = world.client.get("/api/board/dashboard?unit=ADS").json()
    assert dashboard["overdue"] == 1
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/accept", detail))
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v1"))
    detail = ok(call(world, ads.lead_dung, f"/nodes/{dung}/approve", detail))
    assert node(detail, "DUNG")["deadline_met"] is False
    assert node(detail, "DUNG")["deadline_status"] == "MISSED"
    world.act_as(ads.editor)
    month = f"{utcnow():%Y-%m}"
    stats = world.client.get("/api/account/me/stats", params={"month": month}).json()
    assert stats["on_time_rate"] == 0.0 and stats["late_count"] == 1
    assert stats["tokens_used"] == 3.0 and stats["tokens_budget"] > 0
    assert stats["performance_score"] is not None


async def test_06_the_effort_grid_and_who_sees_it(world: World) -> None:
    ads = await ads_world(world)
    world.act_as(world.owner)
    assert (
        world.client.patch(
            f"/api/units/ADS/members/{ads.editor.id}", json={"daily_tokens": 2}
        ).json()["daily_tokens"]
        == 2.0
    )
    detail = await at_the_cut(world, ads)
    dung = node(detail, "DUNG")["id"]
    detail = ok(
        call(
            world,
            ads.lead_dung,
            f"/nodes/{dung}/assign",
            detail,
            assignee_user_id=str(ads.editor.id),
            tokens=3,
            deadline_at=SOON,
        )
    )
    world.act_as(ads.lead_dung)
    page = world.client.get(f"/api/tasks/{detail['order']['code']}").json()
    options = next(a for a in page["actions"] if a["key"].startswith("ads:ASSIGN:"))[
        "assignee_options"
    ]
    editor = next(o for o in options if o["user_id"] == str(ads.editor.id))
    assert editor["tokens_open"] == 3.0 and editor["open_tasks"] == 1
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/accept", detail))
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v1"))
    ok(call(world, ads.lead_dung, f"/nodes/{dung}/approve", detail))

    today = work_day(utcnow())
    query = {"date_from": today.isoformat(), "date_to": today.isoformat()}
    world.act_as(ads.lead_dung)
    grid = world.client.get("/api/units/ADS/effort", params=query).json()
    people = {person["full_name"]: person for person in grid["people"]}
    # The Dựng Leader sees their ban only.
    assert set(people) == {ads.lead_dung.full_name, ads.editor.full_name}
    day = people[ads.editor.full_name]["days"][0]
    # A full day Monday to Friday, Saturday morning is half, Sunday none.
    share = 1.0 if today.weekday() < 5 else 0.5 if today.weekday() == 5 else 0.0
    assert day["used"] == 3.0 and day["budget"] == 2.0 * share
    assert day["left"] == day["budget"] - 3.0  # over effort: negative
    # Staff see only themselves, and nobody else on asking.
    world.act_as(ads.editor)
    mine = world.client.get("/api/units/ADS/effort", params=query).json()
    assert [person["full_name"] for person in mine["people"]] == [ads.editor.full_name]
    other = world.client.get(
        "/api/units/ADS/effort", params={**query, "user_id": str(ads.designer.id)}
    )
    assert other.status_code == 403 and error_reason(other.json()) == "effort_forbidden"
    # The head sees every ban.
    world.act_as(ads.head)
    everyone = world.client.get("/api/units/ADS/effort", params=query).json()
    assert ads.designer.full_name in {person["full_name"] for person in everyone["people"]}
    too_long = world.client.get(
        "/api/units/ADS/effort",
        params={"date_from": "2026-01-01", "date_to": "2026-06-01"},
    )
    assert too_long.status_code == 422


async def test_07_an_order_needs_a_wished_deadline(world: World) -> None:
    ads = await ads_world(world)
    world.act_as(ads.orderer)
    base = {"title": "X", "video_type": "D", "order_content": "y", "design_link": "d"}
    missing = world.client.post("/api/orders", json=base)
    assert missing.status_code == 422 and error_reason(missing.json()) == "deadline_required"
    past = world.client.post(
        "/api/orders",
        json={**base, "desired_deadline_at": (utcnow() - timedelta(days=1)).isoformat()},
    )
    assert error_reason(past.json()) == "deadline_in_past"
    made = world.client.post("/api/orders", json={**base, "desired_deadline_at": LATER})
    assert made.status_code == 201
    assert made.json()["order"]["desired_deadline_at"] is not None


async def test_08_the_dashboard_counts_tokens_per_ban(world: World) -> None:
    ads = await ads_world(world)
    detail = await at_the_cut(world, ads)
    dung = node(detail, "DUNG")["id"]
    detail = ok(
        call(
            world,
            ads.lead_dung,
            f"/nodes/{dung}/assign",
            detail,
            assignee_user_id=str(ads.editor.id),
            tokens=3,
            deadline_at=SOON,
        )
    )
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/accept", detail))
    detail = ok(call(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v1"))
    ok(call(world, ads.lead_dung, f"/nodes/{dung}/approve", detail))

    # A member of a ban opens on their ban; every ORD member may read them all.
    world.act_as(ads.editor)
    stats = world.client.get("/api/units/ADS/ban-stats").json()
    assert stats["my_ban"] == "DUNG"
    bans = {ban["role"]: ban for ban in stats["bans"]}
    assert set(bans) == {"BIEN_TAP", "THIET_KE", "DUNG"}
    cut = bans["DUNG"]
    assert cut["label"] == "Dựng" and cut["used"] == 3.0 and cut["done"] == 1
    assert cut["budget"] > 0 and cut["left"] == cut["budget"] - 3.0
    people = {member["full_name"]: member for member in cut["members"]}
    assert set(people) == {ads.lead_dung.full_name, ads.editor.full_name}
    assert people[ads.editor.full_name]["done"] == 1
    assert people[ads.editor.full_name]["used"] == 3.0
    assert bans["BIEN_TAP"]["used"] == 0.0
    world.act_as(ads.head)
    assert world.client.get("/api/units/ADS/ban-stats").json()["my_ban"] is None
    world.act_as(ads.outsider)
    assert world.client.get("/api/units/ADS/ban-stats").status_code == 404


def test_09_saturday_morning_carries_half_the_budget() -> None:
    from datetime import date

    from meobot.application.orders.effort_service import budget_on, working_share
    from meobot.domain.units.models import UnitSettings

    settings = UnitSettings()
    friday, saturday, sunday = date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)
    assert budget_on(friday, 8, settings) == 8
    assert budget_on(saturday, 8, settings) == 4
    assert budget_on(sunday, 8, settings) == 0
    # October 2026: 22 weekdays and 5 Saturdays.
    assert working_share(date(2026, 10, 1), date(2026, 10, 31), settings) == 24.5
