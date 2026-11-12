"""Ghim menu (2026-11-12): a person's pinned nav branches, kept on the server."""

from __future__ import annotations

from meobot.application.account.nav_pins import MAX_PINS
from tests.unit.pr_world import World


async def test_01_pins_are_saved_in_order_and_come_back_on_units_me(world: World) -> None:
    world.act_as(world.member)
    assert world.client.get("/api/units/me").json()["nav_pins"] == []

    saved = world.client.put("/api/account/nav-pins", json={"pins": ["pr", "hr", "pr"]})
    assert saved.status_code == 200 and saved.json() == {"pins": ["pr", "hr"]}
    assert world.client.get("/api/units/me").json()["nav_pins"] == ["pr", "hr"]

    # Unpinning everything empties the list.
    assert world.client.put("/api/account/nav-pins", json={"pins": []}).json() == {"pins": []}
    assert world.client.get("/api/units/me").json()["nav_pins"] == []


async def test_02_each_person_has_their_own_pins(world: World) -> None:
    world.act_as(world.member)
    world.client.put("/api/account/nav-pins", json={"pins": ["ads"]})
    world.act_as(world.owner)
    assert world.client.get("/api/units/me").json()["nav_pins"] == []
    world.client.put("/api/account/nav-pins", json={"pins": ["media", "resources"]})
    world.act_as(world.member)
    assert world.client.get("/api/units/me").json()["nav_pins"] == ["ads"]


async def test_03_a_malformed_list_is_refused(world: World) -> None:
    world.act_as(world.member)
    for pins in (["Phòng PR"], ["pr; drop"], [f"k{n}" for n in range(MAX_PINS + 1)]):
        response = world.client.put("/api/account/nav-pins", json={"pins": pins})
        assert response.status_code == 422, pins
    assert world.client.get("/api/units/me").json()["nav_pins"] == []
