"""The Ads permission matrix: defaults, editing it, and the engine obeying it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import update

from meobot.db.models.org_unit import OrgUnitMember
from meobot.domain.identity.models import Role
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.orders.permissions import (
    DEFAULT_MATRIX,
    AdsPermission,
    AdsPermissionMatrixError,
    AdsPermissions,
    AdsRoleKey,
    AdsScope,
    normalise_matrix,
    resolve_matrix,
)
from meobot.domain.units.models import UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_order_commands import act, ads_world, create, node, person
from tests.unit.test_units_gating import error_reason, tag

pytestmark = pytest.mark.asyncio


def kinds(world: World, user: Any, detail: dict[str, Any]) -> set[str]:
    world.act_as(user)
    response = world.client.get(f"/api/orders/{detail['order']['id']}")
    if response.status_code == 404:
        return set()  # not even visible to them
    return {action["kind"] for action in response.json()["available_actions"]}


def set_matrix(world: World, cells: dict[str, dict[str, str]]) -> Any:
    world.act_as(world.owner)
    return world.client.patch("/api/units/ADS/settings", json={"permissions": cells})


# --- the matrix itself --------------------------------------------------------------


async def test_01_defaults_fill_in_and_bad_cells_are_refused() -> None:
    matrix = resolve_matrix({"LEAD": {"NODE_ASSIGN": "NONE"}})
    assert matrix[AdsRoleKey.LEAD][AdsPermission.NODE_ASSIGN] is AdsScope.NONE
    assert matrix[AdsRoleKey.LEAD][AdsPermission.NODE_REVIEW] is AdsScope.OWN
    assert matrix[AdsRoleKey.HEAD][AdsPermission.FINAL_REVIEW] is AdsScope.ALL
    assert normalise_matrix(None)["STAFF"]["VIEW"] == "OWN"
    for bad in (
        {"NOBODY": {"VIEW": "ALL"}},
        {"LEAD": {"FLY": "ALL"}},
        {"LEAD": {"VIEW": "SOMETIMES"}},
        # Approving an order has no "own function": yes or no only.
        {"LEAD": {"ORDER_APPROVE": "OWN"}},
    ):
        with pytest.raises(AdsPermissionMatrixError):
            resolve_matrix(bad)


async def test_02_effective_permissions_are_the_union_of_a_persons_roles() -> None:
    lead_dung = AdsPermissions.compute(
        resolve_matrix(None),
        roles=[AdsRoleKey.STAFF, AdsRoleKey.LEAD],
        function_nodes=frozenset({OrderNodeType.DUNG, OrderNodeType.GAN_LINK}),
        lead_nodes=frozenset({OrderNodeType.DUNG, OrderNodeType.GAN_LINK}),
    )
    assert lead_dung.nodes(AdsPermission.NODE_ASSIGN) == {
        OrderNodeType.DUNG,
        OrderNodeType.GAN_LINK,
    }
    assert not lead_dung.allows(AdsPermission.ORDER_APPROVE)
    # An ADMIN tagged as a Leader holds the Admin column too: everything.
    admin_lead = AdsPermissions.compute(
        resolve_matrix(None),
        roles=[AdsRoleKey.STAFF, AdsRoleKey.LEAD, AdsRoleKey.ADMIN],
        function_nodes=frozenset({OrderNodeType.DUNG}),
        lead_nodes=frozenset({OrderNodeType.DUNG}),
    )
    assert admin_lead.nodes(AdsPermission.NODE_REVIEW) == frozenset(OrderNodeType)
    assert DEFAULT_MATRIX[AdsRoleKey.ORDERER][AdsPermission.ORDER_CREATE] is AdsScope.ALL


# --- the engine reads it ------------------------------------------------------------


async def test_03_by_default_leaders_run_their_own_node_and_the_head_none(
    world: World,
) -> None:
    """The production steps are the Leaders' (and the Admin's, the OWNER's);
    a Trưởng phòng ORD decides the order and the final product only (0057)."""
    ads = await ads_world(world)
    admin = await person(world, "Quản trị Ads", Role.ADMIN)
    await tag(world, ads.unit, admin, UnitMemberRole.ORDERER)
    detail = create(world, ads, video_type="TD")
    detail = act(world, ads.head, "/approve", detail)
    for manager in (world.owner, admin, ads.lead_tk):
        assert "ASSIGN" in kinds(world, manager, detail), manager.full_name
    for other in (ads.head, ads.lead_dung, ads.designer):
        assert "ASSIGN" not in kinds(world, other, detail), other.full_name
    assert "SET_NODE_PLAN" not in kinds(world, ads.head, detail)

    tk = node(detail, "THIET_KE")["id"]
    world.act_as(ads.head)
    refused = world.client.post(
        f"/api/orders/{detail['order']['id']}/nodes/{tk}/assign",
        json={
            "version": detail["order"]["version"],
            "assignee_user_id": str(ads.designer.id),
            "tokens": 1,
            "deadline_at": "2030-01-01T00:00:00+00:00",
        },
    )
    assert refused.status_code == 403, refused.json()
    detail = act(
        world, ads.lead_tk, f"/nodes/{tk}/assign", detail, assignee_user_id=str(ads.designer.id)
    )
    assert node(detail, "THIET_KE")["assignee_user_id"] == str(ads.designer.id)


async def test_04_editing_the_matrix_changes_what_people_may_do(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, video_type="TD")
    detail = act(world, ads.head, "/approve", detail)
    assert "ASSIGN" in kinds(world, ads.lead_tk, detail)

    # Leaders no longer assign; the head does once the matrix says so.
    response = set_matrix(world, {"LEAD": {"NODE_ASSIGN": "NONE"}, "HEAD": {"NODE_ASSIGN": "ALL"}})
    assert response.status_code == 200, response.json()
    assert response.json()["permissions"]["LEAD"]["NODE_ASSIGN"] == "NONE"
    assert "ASSIGN" not in kinds(world, ads.lead_tk, detail)
    assert "ASSIGN" in kinds(world, ads.head, detail)

    # Marketing may no longer order.
    assert set_matrix(world, {"ORDERER": {"ORDER_CREATE": "NONE"}}).status_code == 200
    world.act_as(ads.orderer)
    refused = world.client.post(
        "/api/orders",
        json={"title": "X", "video_type": "TD", "order_content": "y"},
    )
    assert refused.status_code == 422 and error_reason(refused.json()) == "not_an_orderer"

    # Staff may see every order once VIEW is ALL for them.
    world.act_as(ads.editor)
    hidden = world.client.get(f"/api/orders/{detail['order']['id']}")
    assert hidden.status_code == 404
    assert set_matrix(world, {"STAFF": {"VIEW": "ALL"}}).status_code == 200
    world.act_as(ads.editor)
    assert world.client.get(f"/api/orders/{detail['order']['id']}").status_code == 200


async def test_05_a_bad_matrix_is_refused_and_only_admins_edit_it(world: World) -> None:
    ads = await ads_world(world)
    response = set_matrix(world, {"LEAD": {"ORDER_APPROVE": "OWN"}})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "permissions"
    world.act_as(ads.lead_tk)
    forbidden = world.client.patch(
        "/api/units/ADS/settings", json={"permissions": {"LEAD": {"VIEW": "ALL"}}}
    )
    assert forbidden.status_code in (403, 404)


async def test_06_who_a_review_waits_for_follows_the_matrix(world: World) -> None:
    ads = await ads_world(world)
    detail = create(world, ads, video_type="D", design_link="https://example.com/design")
    detail = act(world, ads.head, "/approve", detail)
    dung = node(detail, "DUNG")["id"]
    detail = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", detail, assignee_user_id=str(ads.editor.id)
    )
    detail = act(world, ads.editor, f"/nodes/{dung}/accept", detail)
    detail = act(world, ads.editor, f"/nodes/{dung}/submit", detail, link="https://example.com/c")

    def holder() -> str:
        world.act_as(world.owner)
        rows = world.client.get("/api/board/tasks", params={"unit": "ADS"}).json()["items"]
        row = next(item for item in rows if item["code"] == detail["order"]["code"])
        return str(row["current_person_name"])

    assert holder() == ads.lead_dung.full_name
    # Leaders stop reviewing and the matrix lets the head: the head is waited for.
    assert (
        set_matrix(
            world, {"LEAD": {"NODE_REVIEW": "NONE"}, "HEAD": {"NODE_REVIEW": "ALL"}}
        ).status_code
        == 200
    )
    assert holder() == ads.head.full_name
    # Nobody may review: "Chờ giao".
    assert (
        set_matrix(
            world,
            {
                "LEAD": {"NODE_REVIEW": "NONE"},
                "HEAD": {"NODE_REVIEW": "NONE"},
                "ADMIN": {"NODE_REVIEW": "NONE"},
            },
        ).status_code
        == 200
    )
    assert holder() == "Chờ giao"


async def test_07_a_node_nobody_was_chosen_for_goes_to_its_leader(
    world: World,
) -> None:
    """...and to nobody else by default: a Trưởng phòng ORD does not hand out
    production steps (0057) unless the matrix gives them that."""
    ads = await ads_world(world)
    first = create(world, ads, video_type="D", design_link="https://example.com/d1")
    first = act(world, ads.head, "/approve", first)
    assert node(first, "DUNG")["assignee_user_id"] == str(ads.lead_dung.id)
    # The Leader hands it to a member.
    dung = node(first, "DUNG")["id"]
    first = act(
        world, ads.lead_dung, f"/nodes/{dung}/assign", first, assignee_user_id=str(ads.editor.id)
    )
    assert node(first, "DUNG")["assignee_user_id"] == str(ads.editor.id)

    # No Dựng Leader left: it waits unassigned - not the head's to hand out.
    await world.session.execute(
        update(OrgUnitMember)
        .where(OrgUnitMember.user_id == ads.lead_dung.id)
        .values(left_at=datetime.now(UTC))
    )
    await world.session.flush()
    waiting = create(world, ads, video_type="D", design_link="https://example.com/d0")
    waiting = act(world, ads.head, "/approve", waiting)
    assert node(waiting, "DUNG")["status"] == "CHUA_GIAO"
    # The matrix may give the head that job: then the head gets it.
    assert set_matrix(world, {"HEAD": {"NODE_ASSIGN": "ALL"}}).status_code == 200
    second = create(world, ads, video_type="D", design_link="https://example.com/d2")
    second = act(world, ads.head, "/approve", second)
    assert node(second, "DUNG")["assignee_user_id"] == str(ads.head.id)

    # Nobody may assign Dựng at all: only then does it wait unassigned.
    assert (
        set_matrix(
            world,
            {
                "HEAD": {"NODE_ASSIGN": "NONE"},
                "ADMIN": {"NODE_ASSIGN": "NONE"},
                "LEAD": {"NODE_ASSIGN": "NONE"},
            },
        ).status_code
        == 200
    )
    third = create(world, ads, video_type="D", design_link="https://example.com/d3")
    third = act(world, ads.head, "/approve", third)
    assert node(third, "DUNG")["status"] == "CHUA_GIAO"
    assert node(third, "DUNG")["assignee_user_id"] is None
