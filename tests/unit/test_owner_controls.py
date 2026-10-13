"""The owner's controls (0054) and the task table's counts.

* Design / Dựng hand-ins finish the step without the Leader's review (the
  unit's default now);
* effort (token) mode: the OWNER switches it; off, no token is asked for,
  taken or shown;
* "Xoá task" and "Xoá thành viên": the OWNER only, for good - a deleted
  member's past work stays, named "<tên> (đã xoá)";
* ``GET /api/board/counts``: the numbers behind the quick filters.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from meobot.core.time import utcnow
from meobot.db.models.order import Order, OrderEvent, OrderNode, OrderTokenLedger
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from tests.unit.pr_world import World
from tests.unit.test_order_commands import Ads, act, ads_world, create, node, person
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio

SOON = (utcnow() + timedelta(days=2)).isoformat()


async def defaults(world: World) -> Ads:
    """ORD with the unit's own default settings (no review switched on)."""
    ads = await ads_world(world)
    ads.unit.settings = {"design_codes_enabled": False}  # see test_design_ban.py
    await world.session.flush()
    return ads


async def count(world: World, model: Any) -> int:
    return int(await world.session.scalar(select(func.count()).select_from(model)) or 0)


# --- Design / Dựng without review ---------------------------------------------------


async def test_01_design_and_cut_hand_ins_need_no_leader_review(world: World) -> None:
    ads = await defaults(world)
    detail = create(
        world,
        ads,
        video_type="TD",
        preassigned={"THIET_KE": str(ads.designer.id), "DUNG": str(ads.editor.id)},
    )
    detail = act(world, ads.head, "/approve", detail)
    tk = node(detail, "THIET_KE")["id"]
    detail = act(world, ads.designer, f"/nodes/{tk}/accept", detail)
    detail = act(world, ads.designer, f"/nodes/{tk}/submit", detail, link="https://e/design")
    # Handed in = done: the cut starts at once.
    assert node(detail, "THIET_KE")["status"] == "HOAN_THANH"
    assert detail["order"]["stage"] == "DUNG"
    dung = node(detail, "DUNG")["id"]
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/cut")
    # The last step's link goes straight to the orderer's final review.
    assert node(detail, "DUNG")["status"] == "HOAN_THANH"
    assert detail["order"]["stage"] == "FINAL_REVIEW"
    assert detail["order"]["product_link"] == "https://e/cut"


# --- effort (token) mode ---------------------------------------------------------------


async def test_02_only_the_owner_switches_effort_mode(world: World) -> None:
    ads = await ads_world(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    for refused_for in (admin, ads.head):
        world.act_as(refused_for)
        refused = world.client.patch("/api/units/ADS/settings", json={"effort_enabled": False})
        assert refused.status_code in (403, 404), refused.json()
    world.act_as(admin)
    refused = world.client.patch("/api/units/ADS/settings", json={"effort_enabled": False})
    assert refused.status_code == 403
    assert error_reason(refused.json()) == "effort_setting_forbidden"
    # An ADMIN still edits the rest.
    assert world.client.patch("/api/units/ADS/settings", json={"urgent_days": 9}).status_code == 200

    world.act_as(world.owner)
    off = world.client.patch("/api/units/ADS/settings", json={"effort_enabled": False})
    assert off.status_code == 200 and off.json()["effort_enabled"] is False
    assert world.client.get("/api/units/me").json()["effort_enabled"] is False
    world.act_as(ads.editor)
    assert world.client.get("/api/units/me").json()["effort_enabled"] is False


async def test_03_effort_off_asks_for_takes_and_shows_no_token(world: World) -> None:
    ads = await ads_world(world)
    world.act_as(world.owner)
    assert (
        world.client.patch("/api/units/ADS/settings", json={"effort_enabled": False}).status_code
        == 200
    )
    detail = create(world, ads, video_type="D", design_link="d")
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    dung = node(detail, "DUNG")["id"]
    world.act_as(ads.lead_dung)
    page = world.client.get(f"/api/tasks/{code}").json()
    assign = next(a for a in page["actions"] if a["key"].startswith("ads:ASSIGN:"))
    assert assign["inputs"] == ["assignee", "deadline"]
    assert assign["required_inputs"] == ["deadline"]
    assert all(option["tokens_left_today"] is None for option in assign["assignee_options"])
    plan = next(a for a in page["actions"] if a["key"].startswith("ads:SET_NODE_PLAN:"))
    assert plan["label"] == "Sửa deadline · Dựng" and plan["inputs"] == ["deadline"]
    # The deadline is still required; a token sent anyway is ignored.
    path = f"/api/orders/{detail['order']['id']}/nodes/{dung}"
    body = {"version": detail["order"]["version"], "assignee_user_id": str(ads.editor.id)}
    missing = world.client.post(f"{path}/assign", json=body)
    assert missing.status_code == 422 and error_reason(missing.json()) == "deadline_required"
    assigned = world.client.post(f"{path}/assign", json={**body, "deadline_at": SOON, "tokens": 5})
    assert assigned.status_code == 200, assigned.json()
    detail = assigned.json()
    assert node(detail, "DUNG")["token_estimate"] is None
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v1")
    # A return needs no "token sửa" either.
    world.act_as(ads.lead_dung)
    returned = world.client.post(
        f"{path}/return", json={"version": detail["order"]["version"], "note": "Nhạc"}
    )
    assert returned.status_code == 200, returned.json()
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", returned.json(), link="https://e/v2")
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    assert await count(world, OrderTokenLedger) == 0
    assert node(detail, "DUNG")["deadline_met"] is True  # deadlines still count

    world.act_as(ads.lead_dung)
    row = world.client.get("/api/board/tasks?unit=ADS").json()["items"][0]
    assert all(cell["tokens"] is None for cell in row["cells"])
    gone = world.client.get("/api/units/ADS/effort")
    assert gone.status_code == 404 and error_reason(gone.json()) == "effort_disabled"
    bans = world.client.get("/api/units/ADS/ban-stats").json()
    assert bans["effort_enabled"] is False
    assert all(ban["budget"] == 0 and ban["used"] == 0 for ban in bans["bans"])
    assert next(ban for ban in bans["bans"] if ban["role"] == "DUNG")["done"] == 1


# --- delete a task ---------------------------------------------------------------------


async def test_04_only_the_owner_deletes_a_task_for_good(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, video_type="D", design_link="d")
    code, order_id = detail["order"]["code"], uuid.UUID(detail["order"]["id"])
    detail = act(world, ads.head, "/approve", detail)
    dung = node(detail, "DUNG")["id"]
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", detail, assignee_user_id=str(ads.editor.id)
    )
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v1")
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    assert await count(world, OrderTokenLedger) == 1

    for refused_for in (ads.head, ads.orderer):
        world.act_as(refused_for)
        assert world.client.get(f"/api/tasks/{code}").json()["can_delete"] is False
        refused = world.client.delete(f"/api/tasks/{code}")
        assert refused.status_code == 403
        assert error_reason(refused.json()) == "task_delete_forbidden"

    world.act_as(world.owner)
    assert world.client.get(f"/api/tasks/{code}").json()["can_delete"] is True
    assert world.client.delete(f"/api/tasks/{code}").status_code == 204
    assert world.client.get(f"/api/tasks/{code}").status_code == 404
    assert world.client.get(f"/api/orders/{order_id}").status_code == 404
    for model in (Order, OrderNode, OrderEvent, OrderTokenLedger):
        assert await count(world, model) == 0, model.__name__
    assert world.client.get("/api/board/tasks?unit=ADS").json()["total"] == 0


async def test_05_the_owner_deletes_pr_content_by_prs_own_rules(world: World) -> None:
    item = await world.content(channels=(world.tiktok,))
    code = item.code
    world.act_as(world.lead)
    assert world.client.delete(f"/api/tasks/{code}").status_code == 403
    world.act_as(world.owner)
    assert world.client.delete(f"/api/tasks/{code}").status_code == 204
    assert world.client.get(f"/api/tasks/{code}").status_code == 404


# --- delete a member -------------------------------------------------------------------


async def test_06_only_the_owner_deletes_a_member_and_their_work_stays(world: World) -> None:
    ads = await ads_world(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    editor_id, editor_name = ads.editor.id, ads.editor.full_name
    ads.editor.telegram_user_id = 990077
    await world.session.flush()
    detail = create(world, ads, video_type="D", design_link="d")
    detail = act(world, ads.head, "/approve", detail)
    dung = node(detail, "DUNG")["id"]
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", detail, assignee_user_id=str(editor_id)
    )

    world.act_as(admin)
    refused = world.client.delete(f"/api/account/members/{editor_id}")
    assert refused.status_code == 403 and error_reason(refused.json()) == "member_delete_forbidden"
    world.act_as(world.owner)
    assert world.client.delete(f"/api/account/members/{world.owner.id}").status_code == 403
    # Open work is handed over first.
    busy = world.client.delete(f"/api/account/members/{editor_id}")
    assert busy.status_code == 422 and error_reason(busy.json()) == "member_has_open_work"

    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e/v1")
    detail = act(world, ads.lead_dung, f"/nodes/{dung}/approve", detail)
    detail = act(world, ads.orderer, "/final/approve", detail)
    world.act_as(world.owner)
    assert world.client.delete(f"/api/account/members/{editor_id}").status_code == 204

    gone = await world.session.scalar(
        select(User).where(User.id == editor_id).execution_options(populate_existing=True)
    )
    assert gone is not None and gone.deleted_at is not None and gone.active is False
    assert gone.full_name == f"{editor_name} (đã xoá)"
    assert gone.telegram_user_id is None and gone.password_hash is None
    open_tags = await world.session.scalar(
        select(func.count())
        .select_from(OrgUnitMember)
        .where(OrgUnitMember.user_id == editor_id, OrgUnitMember.left_at.is_(None))
    )
    assert open_tags == 0
    # Listed nowhere...
    listed = world.client.get("/api/account/members?include_inactive=true").json()["members"]
    assert str(editor_id) not in {row["user_id"] for row in listed}
    roster = world.client.get("/api/units/ADS/members").json()["members"]
    assert str(editor_id) not in {row["user_id"] for row in roster}
    directory = world.client.get("/api/units/directory").json()
    assert str(editor_id) not in {row["user_id"] for row in directory}
    # ...but the work they did keeps its history, under the marked name.
    order = world.client.get(f"/api/orders/{detail['order']['id']}").json()
    assert node(order, "DUNG")["assignee_name"] == f"{editor_name} (đã xoá)"
    again = world.client.delete(f"/api/account/members/{editor_id}")
    assert again.status_code == 404


# --- quick-filter counts ---------------------------------------------------------------


async def test_07_the_counts_behind_the_quick_filters(world: World) -> None:
    ads = await ads_world(world)
    first = create(world, ads, video_type="D", design_link="d")
    create(world, ads, video_type="D", design_link="d")
    first = act(world, ads.head, "/approve", first)
    act(world, ads.head, "/priority", first, is_priority=True)

    world.act_as(ads.orderer)
    counts = world.client.get("/api/board/counts?unit=ADS").json()
    assert counts == {"mine": 2, "awaiting_me": 0, "priority": 1, "urgent": 0, "overdue": 0}
    world.act_as(ads.head)
    # One order waits for the head's approval; the approved one is the
    # Dựng Leader's to hand out, not the head's.
    assert world.client.get("/api/board/counts?unit=ADS").json()["awaiting_me"] == 1
    world.act_as(ads.lead_dung)
    # The approved one was routed to the Dựng Leader to hand out.
    assert world.client.get("/api/board/counts?unit=ADS").json()["awaiting_me"] == 1
    world.act_as(ads.outsider)
    assert world.client.get("/api/board/counts?unit=ADS").status_code == 404
