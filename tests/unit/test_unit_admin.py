"""Administering a unit: tags, roles, member codes, settings, health."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from meobot.application.audit_service import AuditService
from meobot.application.units.admin import UnitAdminService
from meobot.application.units.directory import UnitDirectoryService
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.units.errors import UnitValidationError
from meobot.domain.units.models import UnitCode, UnitMemberRole
from tests.unit.pr_world import World
from tests.unit.test_units_gating import error_reason, seed_units, tag

pytestmark = pytest.mark.asyncio


def service(world: World) -> UnitAdminService:
    return UnitAdminService(world.session, AuditService(world.session))


async def test_01_the_owner_tags_a_marketer_and_the_code_is_normalised(world: World) -> None:
    await seed_units(world)
    world.act_as(world.owner)
    response = world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(world.member.id), "role": "orderer", "member_code": " tuan "},
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["role"] == "ORDERER"
    assert body["role_label"] == "Marketing (người order)"
    assert body["member_code"] == "TUAN"
    assert body["active"] is True

    listed = world.client.get("/api/units/ADS/members").json()
    assert [row["user_id"] for row in listed["members"]] == [str(world.member.id)]
    assert {option["role"] for option in listed["assignable_roles"]} == {
        "ORDERER",
        "HEAD",
        "BIEN_TAP",
        "THIET_KE",
        "DUNG",
    }

    # The person now sees Ads as well, and keeps the PR tag the world gave them.
    world.act_as(world.member)
    me = world.client.get("/api/units/me").json()
    assert [unit["code"] for unit in me["units"]] == ["PR", "ADS"]


async def test_02_a_role_that_does_not_belong_to_the_unit_is_refused(world: World) -> None:
    await seed_units(world)
    world.act_as(world.owner)
    response = world.client.post(
        "/api/units/ADS/members", json={"user_id": str(world.member.id), "role": "MEMBER"}
    )
    assert response.status_code == 422
    assert error_reason(response.json()) == "role_not_for_unit"
    response = world.client.post(
        "/api/units/PR/members", json={"user_id": str(world.member.id), "role": "HEAD"}
    )
    assert response.status_code == 422
    response = world.client.post(
        "/api/units/PR/members", json={"user_id": str(world.member.id), "role": "boss"}
    )
    assert response.status_code == 422
    assert error_reason(response.json()) == "invalid_role"


async def test_03_a_member_code_is_unique_while_the_tag_is_open(world: World) -> None:
    await seed_units(world)
    world.act_as(world.owner)
    first = world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(world.member.id), "role": "ORDERER", "member_code": "TUAN"},
    )
    assert first.status_code == 201
    second = world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(world.lead.id), "role": "ORDERER", "member_code": "tuan"},
    )
    assert second.status_code == 422
    assert error_reason(second.json()) == "member_code_taken"
    bad = world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(world.lead.id), "role": "ORDERER", "member_code": "t"},
    )
    assert bad.status_code == 422
    assert error_reason(bad.json()) == "invalid_member_code"


async def test_04_untagging_closes_the_row_and_retagging_reopens_it(world: World) -> None:
    units = await seed_units(world)
    world.act_as(world.owner)
    assert (
        world.client.post(
            "/api/units/ADS/members",
            json={"user_id": str(world.member.id), "role": "THIET_KE", "is_lead": True},
        ).status_code
        == 201
    )
    removed = world.client.delete(f"/api/units/ADS/members/{world.member.id}")
    assert removed.status_code == 200
    assert removed.json()["active"] is False
    assert removed.json()["left_at"] is not None

    directory = UnitDirectoryService(world.session)
    membership = await directory.membership_for(world.actor(world.member))
    assert [entry.unit_code for entry in membership.entries] == [UnitCode.PR]
    assert world.client.get("/api/units/ADS/members").json()["members"] == []

    again = world.client.post(
        "/api/units/ADS/members", json={"user_id": str(world.member.id), "role": "DUNG"}
    )
    assert again.status_code == 201
    row = await directory.member(units[UnitCode.ADS].id, world.member.id)
    assert row is not None and row.left_at is None and row.role is UnitMemberRole.DUNG
    assert row.is_lead is False
    # One row per (unit, person), reopened rather than duplicated.
    assert len(await directory.members(units[UnitCode.ADS].id, active_only=False)) == 1


async def test_05_a_tag_can_be_edited_in_place(world: World) -> None:
    await seed_units(world)
    world.act_as(world.owner)
    world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(world.member.id), "role": "DUNG", "member_code": "ED1"},
    )
    response = world.client.patch(
        f"/api/units/ADS/members/{world.member.id}",
        json={"is_lead": True, "member_code": None, "personal_nas_url": "nas://ed"},
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["is_lead"] is True
    assert body["member_code"] is None
    assert body["personal_nas_url"] == "nas://ed"
    assert body["role"] == "DUNG"


async def test_06_an_admin_administers_every_unit_and_an_employee_none(
    world: World,
) -> None:
    units = await seed_units(world)
    admin = User(full_name="Phó phòng PR", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    await tag(world, units[UnitCode.PR], admin, UnitMemberRole.MEMBER)
    await tag(world, units[UnitCode.ADS], world.member, UnitMemberRole.ORDERER)

    world.act_as(admin)
    # Tagged in PR only, and still the administrator of Ads: an ADMIN sees all.
    assert world.client.patch("/api/units/ADS/settings", json={"urgent_days": 3}).status_code == 200
    assert world.client.get("/api/units/directory").status_code == 200

    # A plain member of Ads is inside, so the refusal is a 403 that says so.
    world.act_as(world.member)
    response = world.client.patch("/api/units/ADS/settings", json={"urgent_days": 5})
    assert response.status_code == 403
    assert error_reason(response.json()) == "unit_admin_forbidden"
    assert world.client.get("/api/units/directory").status_code == 403


async def test_07_settings_are_validated_and_defaulted(world: World) -> None:
    await seed_units(world)
    world.act_as(world.owner)
    response = world.client.patch(
        "/api/units/ADS/settings",
        json={
            "urgent_days": 10,
            "media_nas_url": "smb://nas/media",
            "btd_link_attacher": "BIEN_TAP",
        },
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert set(body["permissions"]) == {"HEAD", "ADMIN", "LEAD", "STAFF", "ORDERER"}
    for key in ("permissions", "permission_catalog", "permission_roles", "scope_labels"):
        body.pop(key)
    assert body == {
        "urgent_days": 10,
        "media_nas_url": "smb://nas/media",
        "design_nas_url": None,
        "btd_link_attacher": "BIEN_TAP",
        "telegram_enabled": False,
        "review_bien_tap": False,
        "review_thiet_ke": False,
        "review_dung": True,
        "review_video_by_script_lead": False,
    }
    assert world.client.patch("/api/units/ADS/settings", json={"urgent_days": 0}).status_code == 422
    assert (
        world.client.patch(
            "/api/units/ADS/settings", json={"btd_link_attacher": "HEAD"}
        ).status_code
        == 422
    )
    me = world.client.get("/api/units/me").json()
    ads = next(unit for unit in me["units"] if unit["code"] == "ADS")
    assert ads["settings"]["urgent_days"] == 10


async def test_08_health_names_what_is_missing(world: World) -> None:
    units = await seed_units(world)
    world.act_as(world.owner)
    warnings = world.client.get("/api/units/ADS/health").json()["warnings"]
    assert [warning["code"] for warning in warnings] == ["no_head"]

    await tag(world, units[UnitCode.ADS], world.lead, UnitMemberRole.HEAD)
    await tag(world, units[UnitCode.ADS], world.member, UnitMemberRole.ORDERER)
    designer = User(full_name="Thùy Anh", role=Role.EMPLOYEE)
    world.session.add(designer)
    await world.session.flush()
    await tag(world, units[UnitCode.ADS], designer, UnitMemberRole.THIET_KE)
    warnings = world.client.get("/api/units/ADS/health").json()["warnings"]
    assert [warning["code"] for warning in warnings] == [
        "no_lead_thiet_ke",
        "orderer_without_code",
    ]
    assert "Hảo" in warnings[1]["message"]
    # PR has no such machinery to warn about.
    assert world.client.get("/api/units/PR/health").json()["warnings"] == []


async def test_09_every_write_is_audited(world: World) -> None:
    from sqlalchemy import select

    from meobot.db.models.audit_log import AuditLog

    await seed_units(world)
    admin = service(world)
    actor = world.actor(world.owner)
    await admin.tag_member(
        actor=actor,
        request_id=uuid.uuid4(),
        code=UnitCode.ADS,
        user_id=world.member.id,
        role=UnitMemberRole.ORDERER,
        member_code="TUAN",
    )
    await admin.update_settings(
        actor=actor, request_id=uuid.uuid4(), code=UnitCode.ADS, patch={"urgent_days": 9}
    )
    # Nobody may untag themselves, and a refusal writes no audit row.
    await admin.tag_member(
        actor=actor,
        request_id=uuid.uuid4(),
        code=UnitCode.ADS,
        user_id=world.owner.id,
        role=UnitMemberRole.HEAD,
    )
    with pytest.raises(UnitValidationError) as caught:
        await admin.untag_member(
            actor=actor, request_id=uuid.uuid4(), code=UnitCode.ADS, user_id=world.owner.id
        )
    assert caught.value.details["reason"] == "self_untag"
    actions = (
        await world.session.scalars(
            select(AuditLog.action).where(AuditLog.action.like("unit.%")).order_by(AuditLog.id)
        )
    ).all()
    assert sorted(actions) == [
        "unit.member.tagged",
        "unit.member.tagged",
        "unit.settings.updated",
    ]
    rows: list[Any] = (
        (
            await world.session.execute(
                select(AuditLog.after_data).where(AuditLog.action == "unit.member.tagged")
            )
        )
        .scalars()
        .all()
    )
    assert rows[0]["member_code"] == "TUAN"


async def test_09_the_ads_positions_include_a_head_for_each_function(world: World) -> None:
    await seed_units(world)
    world.act_as(world.owner)
    listed = world.client.get("/api/units/ADS/members").json()
    positions = [
        (row["role"], row.get("is_lead", False), row["label"]) for row in listed["assignable_roles"]
    ]
    assert positions[:4] == [
        ("HEAD", False, "Trưởng phòng ORD"),
        ("BIEN_TAP", True, "Trưởng phòng Biên kịch"),
        ("THIET_KE", True, "Trưởng phòng Design"),
        ("DUNG", True, "Trưởng phòng Dựng"),
    ]
    assert ("ORDERER", False, "Marketing (người order)") in positions

    # Tagged as Trưởng phòng Dựng: DUNG + lead, labelled as such.
    response = world.client.post(
        "/api/units/ADS/members",
        json={"user_id": str(world.member.id), "role": "DUNG", "is_lead": True},
    )
    assert response.status_code == 201, response.json()
    assert response.json()["role_label"] == "Trưởng phòng Dựng"
    assert response.json()["is_lead"] is True

    # Moved to Marketing: no function, so no head flag left behind.
    moved = world.client.patch(
        f"/api/units/ADS/members/{world.member.id}", json={"role": "ORDERER"}
    )
    assert moved.status_code == 200, moved.json()
    assert moved.json()["is_lead"] is False
    assert moved.json()["role_label"] == "Marketing (người order)"


# --- video kinds ("Loại video") -------------------------------------------------------


async def test_10_the_video_kind_catalogue_is_administered_by_the_unit_admin(
    world: World,
) -> None:
    from meobot.db.models.audit_log import AuditLog

    units = await seed_units(world)
    await tag(world, units[UnitCode.ADS], world.member, UnitMemberRole.ORDERER)
    world.act_as(world.owner)
    created = world.client.post(
        "/api/units/ADS/video-kinds", json={"name": "  Short   video ", "points": 1.5}
    )
    assert created.status_code == 201, created.json()
    short = created.json()
    assert short["name"] == "Short video" and short["points"] == 1.5
    assert short["active"] is True and short["sort_order"] == 0
    second = world.client.post("/api/units/ADS/video-kinds", json={"name": "Quay cả ngày"}).json()
    assert second["points"] == 1.0 and second["sort_order"] == 1
    # One name per unit, whatever the case; points within numeric(6,2).
    taken = world.client.post("/api/units/ADS/video-kinds", json={"name": "SHORT VIDEO"})
    assert taken.status_code == 422 and error_reason(taken.json()) == "video_kind_name_taken"
    blank = world.client.post("/api/units/ADS/video-kinds", json={"name": "   "})
    assert blank.status_code == 422 and error_reason(blank.json()) == "video_kind_name_missing"
    for points in (-1, 10000):
        bad = world.client.post("/api/units/ADS/video-kinds", json={"name": "X", "points": points})
        assert bad.status_code == 422
        assert error_reason(bad.json()) == "video_kind_points_invalid"
    # The same name in another unit is fine.
    other_unit = world.client.post("/api/units/PR/video-kinds", json={"name": "Short video"})
    assert other_unit.status_code == 201

    # Rename, re-price, reorder, retire. No delete.
    renamed = world.client.patch(
        f"/api/units/ADS/video-kinds/{second['id']}",
        json={"name": "Quay cả ngày (8h)", "points": "2.25", "sort_order": 0},
    )
    assert renamed.status_code == 200, renamed.json()
    assert renamed.json()["points"] == 2.25 and renamed.json()["sort_order"] == 0
    clash = world.client.patch(
        f"/api/units/ADS/video-kinds/{second['id']}", json={"name": "short video"}
    )
    assert clash.status_code == 422 and error_reason(clash.json()) == "video_kind_name_taken"
    # Renaming a kind to its own name (another case) is not a clash.
    assert (
        world.client.patch(
            f"/api/units/ADS/video-kinds/{short['id']}", json={"name": "Short Video"}
        ).status_code
        == 200
    )
    retired = world.client.patch(
        f"/api/units/ADS/video-kinds/{short['id']}", json={"active": False}
    )
    assert retired.json()["active"] is False
    assert world.client.delete(f"/api/units/ADS/video-kinds/{short['id']}").status_code == 405
    missing = world.client.patch(f"/api/units/ADS/video-kinds/{uuid.uuid4()}", json={"points": 1})
    assert missing.status_code == 404 and error_reason(missing.json()) == "video_kind_not_found"
    # A kind of PR is not reachable through ADS.
    pr_kind = other_unit.json()["id"]
    assert (
        world.client.patch(f"/api/units/ADS/video-kinds/{pr_kind}", json={"points": 1}).status_code
        == 404
    )

    # Reading: the admin sees the retired kind on request, in display order.
    everything = world.client.get(
        "/api/units/ADS/video-kinds", params={"include_inactive": "true"}
    ).json()
    # Both at sort order 0 now: ties read alphabetically.
    assert [kind["name"] for kind in everything["kinds"]] == ["Quay cả ngày (8h)", "Short Video"]
    assert [
        kind["name"] for kind in world.client.get("/api/units/ADS/video-kinds").json()["kinds"]
    ] == ["Quay cả ngày (8h)"]
    # A member reads the active kinds only; writing is refused.
    world.act_as(world.member)
    listed = world.client.get("/api/units/ADS/video-kinds")
    assert listed.status_code == 200
    assert [kind["name"] for kind in listed.json()["kinds"]] == ["Quay cả ngày (8h)"]
    assert set(listed.json()["kinds"][0]) == {"id", "name", "points", "active", "sort_order"}
    inactive = world.client.get("/api/units/ADS/video-kinds", params={"include_inactive": "true"})
    assert inactive.status_code == 403
    assert error_reason(inactive.json()) == "unit_admin_forbidden"
    refused = world.client.post("/api/units/ADS/video-kinds", json={"name": "Của tôi"})
    assert refused.status_code == 403 and error_reason(refused.json()) == "unit_admin_forbidden"
    assert (
        world.client.patch(
            f"/api/units/ADS/video-kinds/{second['id']}", json={"active": False}
        ).status_code
        == 403
    )
    # Somebody outside Ads learns nothing.
    world.act_as(world.lead)
    assert world.client.get("/api/units/ADS/video-kinds").status_code == 404
    assert world.client.post("/api/units/ADS/video-kinds", json={"name": "Lạ"}).status_code == 404
    # An ADMIN tagged into Ads administers it.
    admin = User(full_name="Admin Ads", role=Role.ADMIN)
    world.session.add(admin)
    await world.session.flush()
    await tag(world, units[UnitCode.ADS], admin, UnitMemberRole.HEAD)
    world.act_as(admin)
    assert (
        world.client.post("/api/units/ADS/video-kinds", json={"name": "Kịch bản khác"}).status_code
        == 201
    )

    actions = (
        await world.session.scalars(
            select(AuditLog.action).where(AuditLog.action.like("unit.video_kind.%"))
        )
    ).all()
    assert sorted(set(actions)) == ["unit.video_kind.created", "unit.video_kind.updated"]
    assert actions.count("unit.video_kind.created") == 4
