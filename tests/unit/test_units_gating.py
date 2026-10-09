"""The stream wall: who gets through to the PR routes and tools, and who does not.

* **Untagged means no stream.** A user with no open tag sees neither stream:
  the PR routes answer the usual "not visible" 404, ``/api/units/me`` lists no
  unit, the PR Telegram tools refuse. The PR test world's people are tagged PR
  (``tests/unit/streams.py``), as production tags them.
* **An ORD-only person is outside PR.** The PR routes answer the usual "not
  visible" 404, the PR Telegram tools refuse with the same sentence, and the
  notifications inbox - unit-agnostic - still works.
* **The OWNER and the ADMIN see both**, administer both and view "Tất cả".
* **Every ``/api/pr/*`` route is gated** except the three OAuth callbacks,
  which arrive by redirect with no session and authenticate from their state.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from meobot.api.deps import get_current_web_actor
from meobot.core.errors import ToolExecutionError
from meobot.core.time import utcnow
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitCode, UnitMemberRole
from meobot.tools.base import ToolContext
from meobot.tools.registry import build_default_registry
from tests.fakes import StubHealthService
from tests.unit.pr_world import World
from tests.unit.streams import ensure_units

pytestmark = pytest.mark.asyncio


async def seed_units(world: World) -> dict[UnitCode, OrgUnit]:
    """The two unit rows ``0042`` seeds, for a world built with ``create_all``."""
    return await ensure_units(world.session)


async def tag(
    world: World,
    unit: OrgUnit,
    user: User,
    role: UnitMemberRole,
    *,
    is_lead: bool = False,
    member_code: str | None = None,
) -> OrgUnitMember:
    """An open tag of ``user`` in ``unit`` with this role (reopened if closed)."""
    row = await world.session.scalar(
        select(OrgUnitMember).where(
            OrgUnitMember.unit_id == unit.id, OrgUnitMember.user_id == user.id
        )
    )
    if row is None:
        row = OrgUnitMember(
            unit_id=unit.id, user_id=user.id, role=role, is_lead=is_lead, member_code=member_code
        )
        world.session.add(row)
    else:
        row.role, row.is_lead, row.member_code, row.left_at = role, is_lead, member_code, None
    await world.session.flush()
    return row


async def untag(world: World, unit: OrgUnit, user: User) -> None:
    """Close ``user``'s tag in ``unit`` (the world tags its member PR)."""
    row = await world.session.scalar(
        select(OrgUnitMember).where(
            OrgUnitMember.unit_id == unit.id, OrgUnitMember.user_id == user.id
        )
    )
    if row is not None and row.left_at is None:
        row.left_at = utcnow()
        await world.session.flush()


async def newcomer(world: World, name: str = "Người mới", role: Role = Role.EMPLOYEE) -> User:
    """An account with no tag row at all - somebody who just redeemed an invite."""
    user = User(full_name=name, role=role)
    world.session.add(user)
    await world.session.flush()
    return user


def error_reason(body: dict[str, Any]) -> str | None:
    return body.get("error", {}).get("details", {}).get("reason")


# --- untagged = no stream -------------------------------------------------------


async def test_01_an_untagged_account_sees_no_stream(world: World) -> None:
    await seed_units(world)
    person = await newcomer(world)
    world.act_as(person)
    response = world.client.get("/api/pr/dashboard")
    assert response.status_code == 404
    assert error_reason(response.json()) == "unit_not_visible"
    assert world.client.get("/api/pr/contents").status_code == 404
    me = world.client.get("/api/units/me")
    assert me.status_code == 200
    body = me.json()
    assert body["units"] == []
    assert body["default_unit"] is None
    assert body["is_untagged"] is True
    assert body["can_view_all"] is False
    assert body["can_admin"] == [] and body["can_tag"] == []
    # The board refuses every stream, the inbox and the account still work.
    for unit in ("", "PR", "ADS", "ALL"):
        assert world.client.get(f"/api/board/tasks?unit={unit}").status_code == 404, unit
    assert world.client.get("/api/notifications").status_code == 200
    assert world.client.get("/api/account/me").json()["units"] == []


async def test_01b_the_tagged_pr_member_reaches_the_pr_routes(world: World) -> None:
    world.act_as(world.member)
    assert world.client.get("/api/pr/dashboard").status_code == 200
    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["PR"]
    assert me["units"][0]["label"] == "Luồng PR"
    assert me["units"][0]["short_label"] == "PR"
    assert me["is_untagged"] is False
    assert me["can_view_all"] is False
    assert me["can_admin"] == [] and me["can_tag"] == []


async def test_02_a_member_whose_tags_were_all_closed_is_outside_every_unit(world: World) -> None:
    units = await seed_units(world)
    await untag(world, units[UnitCode.PR], world.member)
    world.act_as(world.member)
    assert world.client.get("/api/pr/dashboard").status_code == 404
    me = world.client.get("/api/units/me").json()
    assert me["units"] == []
    assert me["is_untagged"] is True


# --- the wall -----------------------------------------------------------------


async def test_03_an_ads_only_person_is_told_nothing_by_the_pr_routes(world: World) -> None:
    units = await seed_units(world)
    await untag(world, units[UnitCode.PR], world.member)
    await tag(world, units[UnitCode.ADS], world.member, UnitMemberRole.ORDERER, member_code="HAO")
    world.act_as(world.member)

    response = world.client.get("/api/pr/dashboard")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unit_not_found"
    assert error_reason(response.json()) == "unit_not_visible"
    # A write, the content list and the work ledger read the same.
    assert world.client.get("/api/pr/contents").status_code == 404
    assert world.client.get("/api/pr/work").status_code == 404
    assert world.client.post("/api/pr/contents", json={}).status_code == 404

    # The inbox is unit-agnostic.
    assert world.client.get("/api/notifications").status_code == 200

    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["ADS"]
    assert me["units"][0]["label"] == "Luồng Order (ORD)"
    assert me["units"][0]["short_label"] == "ORD"
    assert me["units"][0]["function_tag"] is None
    assert me["units"][0]["role"] == "ORDERER"
    assert me["units"][0]["member_code"] == "HAO"
    assert me["units"][0]["settings"]["urgent_days"] == 7


async def test_04_a_pr_person_cannot_see_the_ads_unit(world: World) -> None:
    await seed_units(world)
    world.act_as(world.member)
    response = world.client.get("/api/units/ADS/members")
    assert response.status_code == 404
    assert error_reason(response.json()) == "unit_not_visible"
    # An unknown unit reads exactly the same.
    assert world.client.get("/api/units/HR/members").status_code == 404


async def test_05_the_owner_sees_both_units_and_administers_both(world: World) -> None:
    await seed_units(world)
    world.act_as(world.owner)
    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["ADS", "PR"]
    assert me["can_view_all"] is True
    assert me["can_admin"] == ["ADS", "PR"]
    assert me["can_tag"] == ["ADS", "PR"]
    assert me["units"][0]["role"] == "HEAD"
    assert me["units"][0]["role_label"] == "Trưởng phòng ORD"
    assert world.client.get("/api/pr/dashboard").status_code == 200
    assert world.client.get("/api/units/ADS/members").status_code == 200


async def test_06_a_person_in_both_units_sees_both_but_views_all_only_as_owner(
    world: World,
) -> None:
    units = await seed_units(world)
    await tag(world, units[UnitCode.PR], world.lead, UnitMemberRole.MEMBER)
    await tag(world, units[UnitCode.ADS], world.lead, UnitMemberRole.HEAD)
    world.act_as(world.lead)
    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["ADS", "PR"]
    assert me["can_view_all"] is False
    assert me["can_admin"] == []
    assert world.client.get("/api/pr/dashboard").status_code == 200


async def test_07_an_untagged_admin_sees_and_administers_every_stream(world: World) -> None:
    await seed_units(world)
    admin = await newcomer(world, "Phó phòng", Role.ADMIN)
    world.act_as(admin)
    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["ADS", "PR"]
    assert me["is_untagged"] is True
    assert me["can_view_all"] is True
    assert me["can_admin"] == ["ADS", "PR"]
    assert me["can_tag"] == ["ADS", "PR"]
    assert world.client.get("/api/pr/dashboard").status_code == 200
    assert world.client.get("/api/board/tasks?unit=ALL").status_code == 200
    assert world.client.get("/api/board/tasks?unit=ADS").status_code == 200
    response = world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(world.member.id), "role": "ORDERER"},
    )
    assert response.status_code == 201, response.json()
    settings = world.client.patch("/api/units/ADS/settings", json={"urgent_days": 5})
    assert settings.status_code == 200, settings.json()


# --- structure ------------------------------------------------------------------


def _has_pr_gate(route: APIRoute) -> bool:
    return any(
        getattr(dependant.call, "__name__", "") == "require_unit_pr"
        for dependant in route.dependant.dependencies
    )


async def test_08_every_pr_route_but_the_oauth_callbacks_is_behind_the_gate(world: World) -> None:
    app = world.client.app
    pr_routes = [
        route
        for route in app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.path.startswith("/api/pr")
    ]
    assert len(pr_routes) > 100
    ungated = sorted(route.path for route in pr_routes if not _has_pr_gate(route))
    assert ungated == [
        "/api/pr/channels/connections/meta/callback",
        "/api/pr/channels/connections/tiktok/callback",
        "/api/pr/channels/connections/youtube/callback",
    ]
    other = [
        route
        for route in app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute)
        and route.path.startswith(("/api/notifications", "/api/units", "/api/auth"))
    ]
    assert other and not any(_has_pr_gate(route) for route in other)


async def test_09_the_oauth_callbacks_still_run_without_a_session(world: World) -> None:
    world.client.app.dependency_overrides.pop(get_current_web_actor, None)  # type: ignore[attr-defined]
    world.client.cookies.clear()
    # A bad state is refused by the callback itself, not by a 401 or the gate.
    response = world.client.get(
        "/api/pr/channels/connections/tiktok/callback?state=nope&code=x", follow_redirects=False
    )
    assert response.status_code in (302, 303, 307, 400, 404, 422)
    assert response.status_code != 401


# --- Telegram -------------------------------------------------------------------


async def test_10_the_pr_telegram_tools_refuse_an_ads_only_person(world: World) -> None:
    units = await seed_units(world)
    await untag(world, units[UnitCode.PR], world.member)
    await tag(world, units[UnitCode.ADS], world.member, UnitMemberRole.ORDERER)
    registry = build_default_registry(health_service=StubHealthService())
    tool = registry.get("pr.content.create")
    context = ToolContext(
        actor=world.actor(world.member),
        request_id=uuid.uuid4(),
        settings=world.settings,
        session=world.session,
    )
    with pytest.raises(ToolExecutionError) as caught:
        await tool.handler(context, None)
    assert caught.value.details["reason"] == "unit_not_visible"
    assert "Không tìm thấy" in str(caught.value)


async def test_11_the_gate_wraps_every_pr_tool_and_nothing_else() -> None:
    registry = build_default_registry(health_service=StubHealthService())
    for tool in registry:
        wrapped = getattr(tool.handler, "__wrapped_by_unit__", None)
        if tool.name.startswith("pr."):
            assert wrapped == "PR", tool.name
        else:
            assert wrapped is None, tool.name


async def test_12_the_pr_telegram_tools_refuse_an_untagged_account(world: World) -> None:
    await seed_units(world)
    person = await newcomer(world)
    registry = build_default_registry(health_service=StubHealthService())
    tool = registry.get("pr.content.create")
    context = ToolContext(
        actor=world.actor(person),
        request_id=uuid.uuid4(),
        settings=world.settings,
        session=world.session,
    )
    with pytest.raises(ToolExecutionError) as caught:
        await tool.handler(context, None)
    assert caught.value.details["reason"] == "unit_not_visible"
