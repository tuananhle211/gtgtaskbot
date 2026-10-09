"""'Quản trị viên' in the stream picker: the OWNER makes an account an ADMIN,
who then sees every task of both streams. Nobody else may."""

from __future__ import annotations

import pytest

from meobot.domain.identity.models import Role
from tests.unit.pr_world import World
from tests.unit.test_order_commands import person
from tests.unit.test_units_gating import error_reason, seed_units

pytestmark = pytest.mark.asyncio


async def test_the_owner_makes_an_admin_who_sees_both_streams(world: World) -> None:
    await seed_units(world)
    someone = await person(world, "Người xem tổng quan")
    world.act_as(someone)
    assert world.client.get("/api/units/me").json()["can_view_all"] is False

    world.act_as(world.owner)
    made = world.client.post(f"/api/account/members/{someone.id}/role", json={"role": "ADMIN"})
    assert made.status_code == 204, made.text
    await world.session.refresh(someone)
    assert someone.role is Role.ADMIN

    world.act_as(someone)
    me = world.client.get("/api/units/me").json()
    assert me["can_view_all"] is True
    for unit in ("PR", "ADS", "ALL"):
        assert world.client.get(f"/api/board/tasks?unit={unit}").status_code == 200


async def test_only_the_owner_changes_a_system_role(world: World) -> None:
    await seed_units(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    someone = await person(world, "Nhân viên")
    for actor in (admin, world.lead):
        world.act_as(actor)
        refused = world.client.post(
            f"/api/account/members/{someone.id}/role", json={"role": "ADMIN"}
        )
        assert refused.status_code == 403, refused.text
        assert error_reason(refused.json()) in {"member_manage_forbidden", "role_change_forbidden"}
