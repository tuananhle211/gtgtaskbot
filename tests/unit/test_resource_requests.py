"""Đề xuất tài nguyên (2026-10-21): the five-step request flow.

Tạo đề xuất -> Trưởng phòng duyệt -> Duyệt chi -> Mua & nhập kho -> Xác nhận nhận.
"""

from __future__ import annotations

from typing import Any

import pytest

from meobot.db.models.user import User
from tests.unit.pr_world import World
from tests.unit.test_departments import seat
from tests.unit.test_resources import cast, option_id, options
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio

FUTURE = "2099-01-01"


def body(catalog: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {
        "type_id": option_id(catalog, "TYPE", "VIA"),
        "quantity": 2,
        "site": "HANOI",
        "purpose": "Chạy chiến dịch tháng 11",
        "reason": "Thiếu tài khoản cho đợt chạy",
        "budget": 1_000_000,
        "needed_by": FUTURE,
        **over,
    }


def act(world: World, user: User, request: dict[str, Any], action: str, **extra: Any) -> Any:
    world.act_as(user)
    return world.client.post(
        f"/api/resources/requests/{request['id']}/actions", json={"action": action, **extra}
    )


async def test_01_the_whole_flow(world: World) -> None:
    people = await cast(world)
    ads = people.ads
    catalog = options(world, people.tn_head)
    assert seat(world, "ADS", ads.orderer, manager_user_id=str(ads.head.id)).status_code == 200

    # The form names the head who will decide.
    world.act_as(ads.orderer)
    form = world.client.get("/api/resources/requests/form").json()
    assert [p["full_name"] for p in form["approvers"]] == [ads.head.full_name]
    assert [s["label"] for s in form["sites"]] == ["Hà Nội", "Sài Gòn", "Toàn hệ thống"]
    created = world.client.post("/api/resources/requests", json=body(catalog))
    assert created.status_code == 201, created.json()
    request = created.json()
    assert request["code"].startswith("DX-") and request["stage"] == "HEAD_REVIEW"
    assert request["actions"] == ["cancel"]

    # Who sees it: the requester, their head, the Phòng Tài nguyên - not a stranger.
    world.act_as(ads.head)
    waiting = world.client.get("/api/resources/requests", params={"tab": "mine"}).json()
    assert [item["code"] for item in waiting["items"]] == [request["code"]]
    assert waiting["awaiting_count"] == 1
    world.act_as(ads.writer)
    assert world.client.get(f"/api/resources/requests/{request['id']}").status_code == 404

    # Step 2: the head approves; nobody else may.
    assert act(world, people.tn_staff, request, "approve").status_code == 403
    world.act_as(ads.orderer)
    waiting = world.client.get(f"/api/resources/requests/{request['id']}").json()["steps"][1]
    assert waiting["state"] == "current"
    assert waiting["status_label"] == f"Chờ {ads.head.full_name} duyệt"
    approved = act(world, ads.head, request, "approve", note="Ok")
    assert approved.status_code == 200 and approved.json()["stage"] == "FUNDING"

    # Step 3: "Duyệt chi" - the Phòng Tài nguyên.
    assert act(world, ads.head, request, "fund").status_code == 403
    funded = act(world, people.tn_staff, request, "fund", note="Phiếu chi PC-01")
    assert funded.json()["stage"] == "PURCHASING"

    # Step 4: one line per resource, exactly the quantity, no duplicate.
    short = act(
        world, people.tn_staff, request, "deliver", lines="111 | https://a", actual_cost=900_000
    )
    assert short.status_code == 422 and error_reason(short.json()) == "request_lines_invalid"
    twice = act(world, people.tn_staff, request, "deliver", lines="111\n111", actual_cost=900_000)
    assert twice.status_code == 422
    delivered = act(
        world,
        people.tn_staff,
        request,
        "deliver",
        lines="111 | https://fb.com/111\n\n222|tuỳ ý",
        actual_cost=900_000,
        supplier="NCC A",
    )
    assert delivered.status_code == 200, delivered.json()
    detail = delivered.json()
    assert detail["stage"] == "DELIVERED" and detail["delivered"] == 2
    assert [(r["external_id"], r["link"]) for r in detail["resources"]] == [
        ("111", "https://fb.com/111"),
        ("222", "tuỳ ý"),
    ]
    # The resources are the requester's, in their department.
    world.act_as(ads.orderer)
    held = world.client.get("/api/resources", params={"mine": "true"}).json()["items"]
    assert sorted(item["external_id"] for item in held) == ["111", "222"]
    assert {item["department"]["code"] for item in held} == {"ADS"}

    # Step 5: the requester confirms.
    assert act(world, ads.head, request, "confirm").status_code == 403
    done = act(world, ads.orderer, request, "confirm").json()
    assert done["stage"] == "COMPLETED"
    assert [step["state"] for step in done["steps"]] == ["done"] * 5
    head_step = done["steps"][1]
    assert head_step["person_name"] == ads.head.full_name and head_step["at"]
    assert head_step["status_label"] == "Đã duyệt" and head_step["note"] == "Ok"
    assert done["steps"][2]["note"] == "Phiếu chi PC-01"
    assert [event["kind"] for event in done["events"]] == [
        "CONFIRMED",
        "DELIVERED",
        "FUNDED",
        "HEAD_APPROVED",
        "SUBMITTED",
    ]


async def test_02_reject_cancel_and_skip(world: World) -> None:
    people = await cast(world)
    ads = people.ads
    catalog = options(world, people.tn_head)
    assert seat(world, "ADS", ads.orderer, manager_user_id=str(ads.head.id)).status_code == 200
    world.act_as(ads.orderer)
    first = world.client.post("/api/resources/requests", json=body(catalog)).json()
    second = world.client.post("/api/resources/requests", json=body(catalog)).json()
    # A refusal says why.
    assert act(world, ads.head, first, "reject").status_code == 422
    rejected = act(world, ads.head, first, "reject", note="Chưa cần").json()
    assert rejected["stage"] == "REJECTED"
    assert [step["state"] for step in rejected["steps"]][:2] == ["done", "stopped"]
    assert rejected["steps"][1]["status_label"] == "Từ chối"
    assert rejected["steps"][1]["note"] == "Chưa cần"
    assert act(world, ads.head, first, "approve").status_code == 409
    # The requester cancels their own.
    assert act(world, ads.orderer, second, "cancel").json()["stage"] == "CANCELLED"
    # A head has nobody above them: the head step is skipped.
    world.act_as(ads.head)
    own = world.client.post("/api/resources/requests", json=body(catalog, site="ALL")).json()
    assert own["stage"] == "FUNDING"
    assert [step["state"] for step in own["steps"]][:3] == ["done", "skipped", "current"]
    # The form refuses what makes no sense.
    world.act_as(ads.orderer)
    past = world.client.post("/api/resources/requests", json=body(catalog, needed_by="2000-01-01"))
    assert past.status_code == 422
    assert (
        world.client.post("/api/resources/requests", json=body(catalog, quantity=0)).status_code
        == 422
    )
