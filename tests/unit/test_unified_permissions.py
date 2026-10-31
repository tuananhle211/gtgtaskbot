"""One permission model (0066).

* the registry lists the three modules; the PR module's position defaults
  equal what the role matrix gave a Trưởng nhóm / Nhân viên, so nobody's
  access changed;
* an ADMIN holds every ORD permission; the stored Admin column is ignored;
* a person's page shows what they hold in every module and where from;
* personal grants add to the seats: given by the OWNER, an ADMIN, or a head
  holding ``MANAGE_PERMISSIONS`` over their own members - never to oneself,
  never wider than one holds, never the review gates nor the right to grant;
* a revoked grant stops at once; the PR head grants review gates to their
  members on the PR screen.
"""

from __future__ import annotations

from typing import Any

import pytest

from meobot.application.org.bot_access import with_unified_access
from meobot.bot.commands import has_full_bot
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.org.models import DepartmentFunction, Position
from meobot.domain.org.modules import MODULES, CommonPermission, default_pr_grants
from meobot.domain.permissions.matrix import Permission
from meobot.domain.pr.membership import role_capabilities
from meobot.domain.pr.policy import GRANT_BACKED, PrCapability
from tests.unit.pr_world import World
from tests.unit.test_departments import department, owner, seat
from tests.unit.test_order_commands import Ads, ads_world, person
from tests.unit.test_resources import create as create_resource
from tests.unit.test_resources import options
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio


def page(world: World, viewer: User, target: User) -> Any:
    world.act_as(viewer)
    return world.client.get(f"/api/permissions/people/{target.id}")


def give(world: World, viewer: User, target: User, **body: Any) -> Any:
    world.act_as(viewer)
    return world.client.post(f"/api/permissions/people/{target.id}/grants", json=body)


def rows_of(body: dict[str, Any], module: str) -> dict[str, dict[str, Any]]:
    found = next(item for item in body["modules"] if item["module"] == module)
    return {row["permission"]: row for row in found["rows"]}


# --- the registry ---------------------------------------------------------------------


async def test_01_the_registry_and_the_pr_defaults_mirror_the_old_role_matrix() -> None:
    assert list(MODULES) == ["ORDER", "PR", "RESOURCES", "PERFORMANCE", "ASSISTANT"]
    for spec in MODULES.values():
        assert CommonPermission.MANAGE_PERMISSIONS.value in spec.permission_keys()
        assert spec.allowed(CommonPermission.MANAGE_PERMISSIONS.value) == {"NONE", "OWN", "ALL"}
    pr = default_pr_grants(DepartmentFunction.PR)
    head = {key for key, scope in pr[Position.HEAD].items() if scope == "ALL"}
    member = {key for key, scope in pr[Position.MEMBER].items() if scope == "ALL"}
    assert head == {cap.value for cap in role_capabilities(Role.TEAM_LEAD)} | {
        PrCapability.PR_MEMBERS_VIEW.value,
        PrCapability.PR_MEMBERS_MANAGE.value,
    }
    assert member == {cap.value for cap in role_capabilities(Role.EMPLOYEE)}
    assert pr[Position.HEAD][CommonPermission.MANAGE_PERMISSIONS.value] == "OWN"
    # The review gates are never department cells.
    gates = {cap.value for cap in GRANT_BACKED}
    assert not gates & set(MODULES["PR"].department_keys())
    assert gates <= set(MODULES["PR"].personal_only)
    assert default_pr_grants(DepartmentFunction.NONE) == {}


# --- the person's page ------------------------------------------------------------------


async def test_02_the_page_shows_every_module_with_its_sources(world: World) -> None:
    ads = await ads_world(world)
    # The ADS head, seen by the OWNER: ORD from the ADS seat, PR nothing, resources OWN.
    body = page(world, world.owner, ads.head).json()
    assert body["sees_all"] is False
    assert [(item["code"], item["position"]) for item in body["seats"]] == [("ADS", "HEAD")]
    order = rows_of(body, "ORDER")
    assert order["ORDER_APPROVE"]["scope"] == "OWN"
    assert order["ORDER_APPROVE"]["sources"][0] == {
        **order["ORDER_APPROVE"]["sources"][0],
        "source": "DEPARTMENT",
        "department_code": "ADS",
        "position": "HEAD",
    }
    assert rows_of(body, "RESOURCES")["VIEW"]["scope"] == "OWN"
    assert rows_of(body, "PR")["PR_CONTENT_CREATE"]["scope"] == "NONE"
    assert all(item["can_manage"] for item in body["modules"])
    # The PR lead (head of PR Thương Hiệu): PR from the seat, and may shape members.
    body = page(world, world.owner, world.lead).json()
    pr = rows_of(body, "PR")
    assert (
        pr["PR_CONTENT_CANCEL"]["scope"] == "ALL"
        and pr["PR_CONTENT_CANCEL"]["sources"][0]["department_code"] == "PR"
    )
    assert pr["MANAGE_PERMISSIONS"]["scope"] == "OWN"
    assert pr["PR_TEAM_LEAD_REVIEW"]["personal_only"] is True
    assert pr["PR_TEAM_LEAD_REVIEW"]["scope"] == "NONE"
    # One reads one's own page; a colleague's is refused.
    assert page(world, ads.orderer, ads.orderer).status_code == 200
    refused = page(world, ads.orderer, ads.writer)
    assert refused.status_code == 403 and error_reason(refused.json()) == "permission_forbidden"
    # An ADMIN: everything, by role.
    admin = await person(world, "Quản trị", Role.ADMIN)
    body = page(world, world.owner, admin).json()
    assert body["sees_all"] is True
    assert rows_of(body, "ORDER")["NODE_ASSIGN"]["scope"] == "ALL"


# --- personal grants --------------------------------------------------------------------


async def test_03_a_personal_grant_adds_to_the_seat_and_a_revocation_stops_it(
    world: World,
) -> None:
    ads = await ads_world(world)
    catalog = options(world, world.owner)
    # The orderer sees no resource of ADS beyond what they hold...
    held = create_resource(world, world.owner, catalog, external_id="p1")["resource"]
    world.act_as(ads.orderer)
    assert world.client.get(f"/api/resources/{held['id']}").status_code == 404
    # ...until the OWNER grants them VIEW ALL in Tài nguyên.
    made = give(
        world,
        world.owner,
        ads.orderer,
        module="RESOURCES",
        permission="VIEW",
        scope="ALL",
        note="Kiểm kê tháng 10",
    )
    assert made.status_code == 201, made.json()
    assert made.json()["permission_label"] == "Xem tài nguyên" and made.json()["scope"] == "ALL"
    world.act_as(ads.orderer)
    assert world.client.get(f"/api/resources/{held['id']}").status_code == 200
    assert world.client.get("/api/units/me").json()["resources"]["view_all"] is True
    body = page(world, world.owner, ads.orderer).json()
    row = rows_of(body, "RESOURCES")["VIEW"]
    assert row["scope"] == "ALL" and row["sources"][-1]["source"] == "PERSONAL"
    assert row["sources"][-1]["note"] == "Kiểm kê tháng 10"
    assert [item["permission"] for item in body["personal"]] == ["VIEW"]
    # The same grant twice is refused; so is a review gate, or granting the right to grant.
    again = give(
        world, world.owner, ads.orderer, module="RESOURCES", permission="VIEW", scope="ALL"
    )
    assert again.status_code == 422 and error_reason(again.json()) == "permission_duplicate"
    gate = give(
        world, world.owner, ads.orderer, module="PR", permission="PR_HEAD_REVIEW", scope="ALL"
    )
    assert gate.status_code == 422 and error_reason(gate.json()) == "permission_use_pr_grant"
    manage = give(
        world, world.owner, ads.orderer, module="PR", permission="MANAGE_PERMISSIONS", scope="ALL"
    )
    assert manage.status_code == 422 and error_reason(manage.json()) == "permission_not_grantable"
    unknown = give(world, world.owner, ads.orderer, module="PR", permission="FLY", scope="ALL")
    assert unknown.status_code == 422 and error_reason(unknown.json()) == "permission_invalid"
    # Revoked: gone at once.
    grant_id = body["personal"][0]["id"]
    world.act_as(world.owner)
    assert (
        world.client.delete(
            f"/api/permissions/people/{ads.orderer.id}/grants/{grant_id}"
        ).status_code
        == 204
    )
    world.act_as(ads.orderer)
    assert world.client.get(f"/api/resources/{held['id']}").status_code == 404
    assert page(world, world.owner, ads.orderer).json()["personal"] == []
    # An ORD grant too: ORDER_APPROVE ALL makes the orderer an approver of every order.
    made = give(
        world, world.owner, ads.orderer, module="ORDER", permission="ORDER_APPROVE", scope="ALL"
    )
    assert made.status_code == 201
    world.act_as(ads.orderer)
    assert world.client.get("/api/orders/approvers").json()["own_gate"] is True


async def test_04_a_head_grants_only_their_members_only_what_they_hold(world: World) -> None:
    ads = await ads_world(world)
    tn_head = await person(world, "Trưởng phòng Tài nguyên")
    tn_staff = await person(world, "Nhân viên Tài nguyên")
    assert seat(world, "TN", tn_head, position="HEAD").status_code == 200
    assert seat(world, "TN", tn_staff).status_code == 200
    # Without MANAGE_PERMISSIONS the TN head manages nobody in Tài nguyên.
    refused = give(world, tn_head, tn_staff, module="RESOURCES", permission="IMPORT", scope="ALL")
    assert refused.status_code == 403 and error_reason(refused.json()) == "permission_forbidden"
    tn = department(world, "TN")
    turned_on = owner(world).put(
        f"/api/departments/{tn['id']}/grants",
        json={"module": "RESOURCES", "cells": {"HEAD": {"MANAGE_PERMISSIONS": "OWN"}}},
    )
    assert turned_on.status_code == 200, turned_on.json()
    # Now: their own member, a permission they hold (IMPORT ALL) - yes.
    body = page(world, tn_head, tn_staff).json()
    resources = next(item for item in body["modules"] if item["module"] == "RESOURCES")
    assert resources["can_manage"] is True and resources["grantable"]["IMPORT"] == ["ALL"]
    assert "MANAGE_PERMISSIONS" not in resources["grantable"]
    order = next(item for item in body["modules"] if item["module"] == "ORDER")
    assert order["can_manage"] is False
    made = give(world, tn_head, tn_staff, module="RESOURCES", permission="IMPORT", scope="ALL")
    assert made.status_code == 201, made.json()
    world.act_as(tn_staff)
    assert world.client.get("/api/units/me").json()["resources"]["import_excel"] is True
    # Not somebody outside their department, not themselves, not the OWNER, not ORD.
    for target in (ads.writer, tn_head, world.owner):
        refused = give(world, tn_head, target, module="RESOURCES", permission="EDIT", scope="ALL")
        assert refused.status_code == 403, target.full_name
    refused = give(world, tn_head, tn_staff, module="ORDER", permission="ORDER_CREATE", scope="ALL")
    assert refused.status_code == 403
    # A head who holds VIEW OWN cannot hand out VIEW ALL.
    limited = await person(world, "Trưởng phòng ADS 2")
    assert seat(world, "ADS", limited, position="HEAD").status_code == 200
    ads_department = department(world, "ADS")
    assert (
        owner(world)
        .put(
            f"/api/departments/{ads_department['id']}/grants",
            json={"module": "RESOURCES", "cells": {"HEAD": {"MANAGE_PERMISSIONS": "OWN"}}},
        )
        .status_code
        == 200
    )
    too_wide = give(world, limited, ads.orderer, module="RESOURCES", permission="VIEW", scope="ALL")
    assert (
        too_wide.status_code == 422 and error_reason(too_wide.json()) == "permission_not_grantable"
    )
    narrow = give(world, limited, ads.orderer, module="RESOURCES", permission="VIEW", scope="OWN")
    assert narrow.status_code == 201
    # The head may revoke what they gave; the staff may not.
    grant_id = made.json()["id"]
    world.act_as(tn_staff)
    assert (
        world.client.delete(f"/api/permissions/people/{tn_staff.id}/grants/{grant_id}").status_code
        == 403
    )
    world.act_as(tn_head)
    assert (
        world.client.delete(f"/api/permissions/people/{tn_staff.id}/grants/{grant_id}").status_code
        == 204
    )


async def test_05_the_pr_head_shapes_their_members_review_rights(world: World) -> None:
    # The world's lead heads PR Thương Hiệu (as 0061 seeded a TEAM_LEAD).
    world.act_as(world.lead)
    made = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.member.id),
            "capability": "PR_TEAM_LEAD_REVIEW",
            "scope": {
                "content_type_scope": "ALL",
                "content_types": [],
                "include_unclassified_content": True,
                "channel_scope": "ALL",
                "channel_ids": [],
                "include_unassigned_channel": True,
            },
        },
    )
    assert made.status_code in (200, 201), made.json()
    body = page(world, world.lead, world.member).json()
    gate = rows_of(body, "PR")["PR_TEAM_LEAD_REVIEW"]
    assert gate["scope"] == "SCOPED" and len(gate["gate_grants"]) == 1
    # Not to themselves.
    world.act_as(world.lead)
    own = world.client.post(
        "/api/pr/capabilities/grant",
        json={
            "user_id": str(world.lead.id),
            "capability": "PR_HEAD_REVIEW",
            "scope": {
                "content_type_scope": "ALL",
                "content_types": [],
                "include_unclassified_content": True,
                "channel_scope": "ALL",
                "channel_ids": [],
                "include_unassigned_channel": True,
            },
        },
    )
    assert own.status_code == 403


async def test_06_an_admin_holds_every_ord_permission_whatever_the_old_column_said(
    world: World,
) -> None:
    ads: Ads = await ads_world(world)
    ads.unit.settings = {
        **(ads.unit.settings or {}),
        "permissions": {"ADMIN": {"NODE_ASSIGN": "NONE"}},
    }
    await world.session.flush()
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    me = world.client.get("/api/units/me").json()
    assert me["can_view_all"] is True
    body = page(world, admin, admin).json()
    assert rows_of(body, "ORDER")["NODE_ASSIGN"]["scope"] == "ALL"


async def test_07_the_checklist_saves_one_module_whole(world: World) -> None:
    """Tick = a personal grant, untick = revoked; what the seat gives stays."""
    ads = await ads_world(world)
    world.act_as(world.owner)
    saved = world.client.put(
        f"/api/permissions/people/{ads.orderer.id}/grants",
        json={"module": "RESOURCES", "permissions": {"VIEW": "ALL", "EDIT": "ALL", "REPORT": None}},
    )
    assert saved.status_code == 200, saved.json()
    body = saved.json()
    assert {item["permission"] for item in body["personal"]} == {"VIEW", "EDIT"}
    assert rows_of(body, "RESOURCES")["EDIT"]["scope"] == "ALL"
    # Narrow VIEW to OWN and untick EDIT: VIEW's grant is replaced, EDIT's revoked.
    saved = world.client.put(
        f"/api/permissions/people/{ads.orderer.id}/grants",
        json={"module": "RESOURCES", "permissions": {"VIEW": "OWN", "EDIT": None}},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert [(item["permission"], item["scope"]) for item in body["personal"]] == [("VIEW", "OWN")]
    # The seat's own VIEW OWN is untouched; the effective scope is still OWN.
    assert rows_of(body, "RESOURCES")["VIEW"]["scope"] == "OWN"
    assert len(rows_of(body, "RESOURCES")["VIEW"]["sources"]) == 2
    bad = world.client.put(
        f"/api/permissions/people/{ads.orderer.id}/grants",
        json={"module": "RESOURCES", "permissions": {"VIEW": "SOME"}},
    )
    assert bad.status_code == 422
    # A head saves only within what they may grant.
    world.act_as(ads.head)
    refused = world.client.put(
        f"/api/permissions/people/{ads.orderer.id}/grants",
        json={"module": "RESOURCES", "permissions": {"IMPORT": "ALL"}},
    )
    assert refused.status_code == 403


async def test_08_the_pr_head_keeps_the_roster(world: World) -> None:
    """0067: sees the PR roster and the tabs, adds people who land in PR
    Thương Hiệu, brings an existing account in; 2026-10-19: also manages the
    PR members' accounts (role between Nhân viên / Trưởng phòng, status,
    revoke) - never a system account (no Admin added or touched)."""
    world.act_as(world.lead)
    roster = world.client.get("/api/pr/members")
    assert roster.status_code == 200, roster.json()
    body = roster.json()
    assert body["may_add"] is True and body["may_tag_pr"] is True
    assert body["may_change_role"] is True and body["may_change_status"] is True
    assert [item["role"] for item in body["assignable_roles"]] == ["EMPLOYEE", "TEAM_LEAD"]
    assert world.client.get("/api/pr/roles").status_code == 200
    assert (
        world.client.get(f"/api/pr/members/{world.member.id}/effective-permissions").status_code
        == 200
    )
    # Adds an Employee, who is seated in PR at once.
    made = world.client.post(
        "/api/pr/members",
        json={"telegram_user_id": 900100200, "role": "EMPLOYEE", "full_name": "Người mới PR"},
    )
    assert made.status_code == 201, made.json()
    new_id = made.json()["user_id"]
    world.act_as(world.owner)
    seats = world.client.get("/api/units/PR/members").json()["members"]
    assert new_id in {item["user_id"] for item in seats}
    # Not an Admin; another Trưởng phòng is fine (2026-10-19).
    world.act_as(world.lead)
    refused = world.client.post(
        "/api/pr/members", json={"telegram_user_id": 900100300, "role": "ADMIN"}
    )
    assert refused.status_code == 403
    second_head = world.client.post(
        "/api/pr/members", json={"telegram_user_id": 900100301, "role": "TEAM_LEAD"}
    )
    assert second_head.status_code == 201, second_head.json()
    # Brings an existing account into PR.
    outsider = await person(world, "Người ngoài PR")
    tagged = world.client.post(
        "/api/units/PR/members", json={"user_id": str(outsider.id), "role": "MEMBER"}
    )
    assert tagged.status_code in (200, 201), tagged.json()
    # A PR member's role is theirs to change (within the department's two);
    # a system account's is not.
    changed = world.client.post(
        f"/api/pr/members/{world.member.id}/role", json={"role": "TEAM_LEAD"}
    )
    assert changed.status_code == 200, changed.json()
    refused = world.client.post(f"/api/pr/members/{world.owner.id}/role", json={"role": "EMPLOYEE"})
    assert refused.status_code == 403
    paused = world.client.post(f"/api/pr/members/{world.member.id}/deactivate", json={})
    assert paused.status_code == 200 and paused.json()["status"] == "suspended"
    # A plain member sees none of it.
    world.act_as(world.member)
    assert world.client.get("/api/pr/members").status_code == 403


async def test_09_the_pr_head_gets_the_whole_bot(world: World) -> None:
    """0067: the bot serves PR, so its head gets the full bot (AI chat, tools)
    and the tool gates open for what their PR capabilities are built on;
    the system permissions never come this way; a member keeps the basic bot."""
    lead = await with_unified_access(world.session, world.actor(world.lead))
    assert lead.full_bot is True
    assert Permission.SCRIPT_APPROVE.value in lead.extra_permissions
    assert Permission.USER_MANAGE.value not in lead.extra_permissions
    assert has_full_bot(lead)
    member = await with_unified_access(world.session, world.actor(world.member))
    assert member.full_bot is False and not has_full_bot(member)
    assert Permission.SCRIPT_SUBMIT.value in member.extra_permissions
    # The ADMIN keeps the basic bot, as before; the OWNER has it all.
    admin = await person(world, "Quản trị", Role.ADMIN)
    assert (await with_unified_access(world.session, world.actor(admin))).full_bot is False
    assert has_full_bot(await with_unified_access(world.session, world.actor(world.owner)))
    # A personal grant widens the bot too: PR_CHANNEL_MANAGE -> settings.write.
    world.act_as(world.owner)
    assert (
        world.client.put(
            f"/api/permissions/people/{world.lead.id}/grants",
            json={"module": "PR", "permissions": {"PR_CHANNEL_MANAGE": "ALL"}},
        ).status_code
        == 200
    )
    widened = await with_unified_access(world.session, world.actor(world.lead))
    assert Permission.SETTINGS_WRITE.value in widened.extra_permissions
