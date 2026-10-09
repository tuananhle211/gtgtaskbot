"""Invites carry where the invitee lands (0051).

A stream lead's code tags its redeemer at once: a Leader of Biên kịch /
Design / Dựng brings staff of that ban reporting to them, a Trưởng phòng ORD
an orderer reporting to them, a PR team lead a PR member. The OWNER's and an
ADMIN's codes tag nobody. A ban's Leader may invite even with the system role
"Nhân viên" - but only employees.
"""

from __future__ import annotations

import uuid

import pytest

from meobot.application.audit_service import AuditService
from meobot.application.invite_service import InviteService
from meobot.application.units.directory import UnitDirectoryService
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from tests.unit.pr_world import World
from tests.unit.test_order_commands import ads_world, person
from tests.unit.test_streams import _code
from tests.unit.test_units_gating import seed_units

pytestmark = pytest.mark.asyncio


async def redeem(world: World, code: str, telegram_user_id: int) -> User:
    service = InviteService(world.session, AuditService(world.session))
    return await service.redeem(
        request_id=uuid.uuid4(), code=code, telegram_user_id=telegram_user_id, full_name="Mới"
    )


async def placed(world: World, user: User) -> tuple[str, str, uuid.UUID | None] | None:
    directory = UnitDirectoryService(world.session)
    membership = await directory.membership_for(world.actor(user))
    if not membership.entries:
        return None
    (entry,) = membership.entries
    row = await directory.member(entry.unit_id, user.id)
    assert row is not None
    return entry.unit_code.value, entry.role.value, row.manager_user_id


async def test_01_a_bans_leader_brings_staff_of_that_ban(world: World) -> None:
    ads = await ads_world(world)
    assert ads.lead_dung.role is Role.EMPLOYEE  # a Leader by tag, not by system role
    world.act_as(ads.lead_dung)
    created = world.client.post("/api/invites", json={})
    assert created.status_code == 201, created.json()
    assert created.json()["joins_label"] == (
        f"Luồng Order (ORD) · Dựng · Trưởng quản lý: {ads.lead_dung.full_name}"
    )
    # Only employees: a Leader does not mint team leads.
    assert world.client.post("/api/invites", json={"role": "TEAM_LEAD"}).status_code == 403
    listed = world.client.get("/api/invites").json()["items"]
    assert listed[0]["joins_label"] == created.json()["joins_label"]

    user = await redeem(world, created.json()["code"], 77001)
    assert user.role is Role.EMPLOYEE
    assert await placed(world, user) == ("ADS", "DUNG", ads.lead_dung.id)


async def test_02_the_ord_head_brings_an_orderer(world: World) -> None:
    ads = await ads_world(world)
    service = InviteService(world.session, AuditService(world.session))
    _, code = await service.create(actor=world.actor(ads.head), request_id=uuid.uuid4())
    user = await redeem(world, code, 77002)
    assert await placed(world, user) == ("ADS", "ORDERER", ads.head.id)


async def test_03_a_pr_team_lead_brings_a_pr_member(world: World) -> None:
    await seed_units(world)
    world.act_as(world.lead)
    created = world.client.post("/api/invites", json={})
    assert created.status_code == 201
    assert created.json()["joins_label"] == "Luồng PR · Thành viên"
    user = await redeem(world, created.json()["code"], 77003)
    assert await placed(world, user) == ("PR", "MEMBER", None)


async def test_04_owner_and_admin_invites_tag_nobody(world: World) -> None:
    await ads_world(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    for inviter, telegram_id in ((world.owner, 77004), (admin, 77005)):
        world.act_as(inviter)
        created = world.client.post("/api/invites", json={})
        assert created.status_code == 201
        assert created.json()["joins_label"] is None
        user = await redeem(world, created.json()["code"], telegram_id)
        assert await placed(world, user) is None


async def test_05_staff_may_not_invite(world: World) -> None:
    ads = await ads_world(world)
    world.act_as(ads.editor)
    refused = world.client.post("/api/invites", json={})
    assert refused.status_code == 403 and _code(refused.json()) == "invite_forbidden"


async def test_06_a_leader_who_stopped_leading_is_not_attached(world: World) -> None:
    ads = await ads_world(world)
    service = InviteService(world.session, AuditService(world.session))
    _, code = await service.create(actor=world.actor(ads.lead_dung), request_id=uuid.uuid4())
    world.act_as(world.owner)
    demoted = world.client.patch(
        f"/api/units/ADS/members/{ads.lead_dung.id}", json={"is_lead": False}
    )
    assert demoted.status_code == 200
    user = await redeem(world, code, 77006)
    assert await placed(world, user) == ("ADS", "DUNG", None)
