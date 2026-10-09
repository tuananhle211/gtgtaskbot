"""ORD: a member's own Leader ("trưởng quản lý", 0050).

A ban may have several Leaders (and the stream several heads). Each staff
member can be given the one Leader they report to; an orderer, the one head.
Their hand-ins (and orders) are then named to, told to and "Cần làm" for that
person only. With nobody set - or once the link stops fitting - the whole ban
gets it, as before. The others keep the right to act (stand-ins).
"""

from __future__ import annotations

from typing import Any

import pytest

from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_order_commands import Ads, act, ads_world, create, inbox, node, person
from tests.unit.test_units_gating import error_reason, tag

pytestmark = pytest.mark.asyncio


def set_manager(world: World, member: User, manager: User | None) -> Any:
    world.act_as(world.owner)
    return world.client.patch(
        f"/api/units/ADS/members/{member.id}",
        json={"manager_user_id": None if manager is None else str(manager.id)},
    )


def row_of(world: World, user: User, code: str, **query: str) -> dict[str, Any] | None:
    world.act_as(user)
    params = "&".join(f"{key}={value}" for key, value in {"unit": "ADS", **query}.items())
    page = world.client.get(f"/api/board/tasks?{params}&limit=50").json()
    return next((row for row in page["items"] if row["code"] == code), None)


async def two_leads(world: World) -> tuple[Ads, User, User]:
    ads = await ads_world(world)
    head2 = await person(world, "Trưởng phòng 2", Role.TEAM_LEAD)
    await tag(world, ads.unit, head2, UnitMemberRole.HEAD)
    lead_dung2 = await person(world, "Editor Lead 2")
    await tag(world, ads.unit, lead_dung2, UnitMemberRole.DUNG, is_lead=True)
    return ads, head2, lead_dung2


async def test_01_the_manager_must_lead_the_members_own_ban(world: World) -> None:
    ads, head2, lead_dung2 = await two_leads(world)
    for member, wrong in (
        (ads.editor, ads.writer),  # not a Leader
        (ads.editor, ads.lead_bt),  # a Leader of another ban
        (ads.orderer, ads.lead_dung),  # an orderer reports to a head
        (ads.lead_dung, lead_dung2),  # a Leader has no Leader of their own
    ):
        refused = set_manager(world, member, wrong)
        assert refused.status_code == 422, refused.json()
        assert error_reason(refused.json()) == "unit_manager_invalid"

    done = set_manager(world, ads.editor, lead_dung2)
    assert done.status_code == 200 and done.json()["manager_user_id"] == str(lead_dung2.id)
    assert set_manager(world, ads.orderer, head2).status_code == 200
    listed = world.client.get("/api/units/ADS/members").json()["members"]
    assert {m["user_id"]: m["manager_user_id"] for m in listed}[str(ads.editor.id)] == str(
        lead_dung2.id
    )
    cleared = set_manager(world, ads.editor, None)
    assert cleared.status_code == 200 and cleared.json()["manager_user_id"] is None


async def test_02_an_order_waits_on_the_orderers_own_head_only(world: World) -> None:
    ads, head2, _ = await two_leads(world)
    assert set_manager(world, ads.orderer, head2).status_code == 200
    detail = create(world, ads)
    code = detail["order"]["code"]

    mine = row_of(world, head2, code)
    assert mine is not None and mine["awaiting_me"] is True
    assert mine["status_label"] == f"Chờ {head2.full_name} duyệt order"
    other = row_of(world, ads.head, code)
    assert other is not None and other["awaiting_me"] is False
    assert any(kind == "order_submitted" for kind, _ in await inbox(world, head2))
    assert all(kind != "order_submitted" for kind, _ in await inbox(world, ads.head))
    # The other head may still decide it (a stand-in).
    approved = act(world, ads.head, "/approve", detail)
    assert approved["order"]["stage"] != "ORDER_PENDING"


async def test_03_a_hand_in_waits_on_the_workers_own_leader_only(world: World) -> None:
    ads, _, lead_dung2 = await two_leads(world)
    assert set_manager(world, ads.editor, lead_dung2).status_code == 200
    detail = create(
        world, ads, video_type="D", design_link="d", preassigned={"DUNG": str(ads.editor.id)}
    )
    code = detail["order"]["code"]
    detail = act(world, ads.head, "/approve", detail)
    dung = node(detail, "DUNG")["id"]
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://e.com/cut")

    mine = row_of(world, lead_dung2, code)
    assert mine is not None and mine["awaiting_me"] is True
    assert mine["status_label"].endswith(f"Chờ {lead_dung2.full_name} duyệt")
    other = row_of(world, ads.lead_dung, code)
    assert other is not None and other["awaiting_me"] is False
    ready = "order_submission_ready"
    assert any(kind == ready for kind, _ in await inbox(world, lead_dung2))
    assert all(kind != ready for kind, _ in await inbox(world, ads.lead_dung))
    # The task page names the same person.
    world.act_as(lead_dung2)
    page = world.client.get(f"/api/tasks/{code}").json()
    assert page["task"]["current_person"]["name"] == lead_dung2.full_name

    # The manager stops leading: the link is dropped, the whole ban gets it.
    world.act_as(world.owner)
    demoted = world.client.patch(f"/api/units/ADS/members/{lead_dung2.id}", json={"is_lead": False})
    assert demoted.status_code == 200, demoted.json()
    listed = world.client.get("/api/units/ADS/members").json()["members"]
    assert {m["user_id"]: m["manager_user_id"] for m in listed}[str(ads.editor.id)] is None
    back = row_of(world, ads.lead_dung, code)
    assert back is not None and back["awaiting_me"] is True


async def test_04_without_a_manager_every_leader_is_named(world: World) -> None:
    ads, head2, _ = await two_leads(world)
    detail = create(world, ads)
    code = detail["order"]["code"]
    for head in (ads.head, head2):
        row = row_of(world, head, code)
        assert row is not None and row["awaiting_me"] is True
    row = row_of(world, ads.head, code)
    assert row is not None
    assert ads.head.full_name in row["status_label"] and head2.full_name in row["status_label"]
