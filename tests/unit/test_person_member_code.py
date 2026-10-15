"""One member code per person (0063), edited by the OWNER and the ADMIN only.

* ``PUT /api/account/members/{id}/member-code`` gives any account a code - in
  a stream or not - folded like every code, blank clears it;
* the code is the person's: written on the account and every open tag, and a
  tag opened later takes it;
* nobody else changes it, on that route or on the stream roster;
* an order opens with it.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from meobot.db.models.audit_log import AuditLog
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitCode, unit_seed_id
from tests.unit.pr_world import World
from tests.unit.test_departments import order, seat
from tests.unit.test_order_commands import ads_world, person
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio


def put_code(world: World, by: User, target: User, value: str | None) -> Any:
    world.act_as(by)
    return world.client.put(
        f"/api/account/members/{target.id}/member-code", json={"member_code": value}
    )


async def tag_code(world: World, user: User, unit: UnitCode) -> str | None:
    row = await world.session.scalar(
        select(OrgUnitMember).where(
            OrgUnitMember.unit_id == unit_seed_id(unit),
            OrgUnitMember.user_id == user.id,
            OrgUnitMember.left_at.is_(None),
        )
    )
    assert row is not None
    await world.session.refresh(row)
    return row.member_code


async def account_code(world: World, user: User) -> str | None:
    await world.session.refresh(user)
    return user.member_code


async def test_01_an_admin_gives_anybody_a_code_folded_and_clears_it(world: World) -> None:
    await ads_world(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    untagged = await person(world, "Chưa Có Luồng")
    done = put_code(world, admin, untagged, "  lê-đức 9 ")
    assert done.status_code == 200, done.json()
    assert done.json() == {"member_code": "LEDUC9"}
    assert await account_code(world, untagged) == "LEDUC9"
    # The member list carries it, stream or not.
    listed = world.client.get("/api/account/members").json()["members"]
    assert next(row for row in listed if row["user_id"] == str(untagged.id))["member_code"] == (
        "LEDUC9"
    )
    audit = (
        await world.session.scalars(
            select(AuditLog).where(AuditLog.action == "user.member_code.updated")
        )
    ).all()
    assert [(row.before_data, row.after_data) for row in audit] == [
        ({"member_code": None}, {"member_code": "LEDUC9"})
    ]
    # Blank clears it.
    cleared = put_code(world, admin, untagged, "  ")
    assert cleared.status_code == 200 and cleared.json() == {"member_code": None}
    assert await account_code(world, untagged) is None


async def test_02_nobody_else_may_change_it(world: World) -> None:
    ads = await ads_world(world)
    for refused_for in (ads.head, ads.lead_tk, ads.orderer):
        refused = put_code(world, refused_for, ads.designer, "XX")
        assert refused.status_code == 403, refused.json()
        assert error_reason(refused.json()) == "member_code_forbidden"
    assert await account_code(world, ads.designer) is None
    # The OWNER may, on anybody, themselves included.
    assert put_code(world, world.owner, world.owner, "chu").json() == {"member_code": "CHU"}
    missing = put_code(world, world.owner, ads.designer, "X")
    assert missing.status_code == 200


async def test_03_the_code_is_the_persons_on_every_tag_and_later_ones(world: World) -> None:
    ads = await ads_world(world)
    # In ORD; then seated in PR as well: the PR tag takes the code.
    assert put_code(world, world.owner, ads.writer, "Viet").status_code == 200
    assert await tag_code(world, ads.writer, UnitCode.ADS) == "VIET"
    assert seat(world, "PR", ads.writer).status_code == 200
    assert await tag_code(world, ads.writer, UnitCode.PR) == "VIET"
    # A change reaches both.
    assert put_code(world, world.owner, ads.writer, "VIET2").status_code == 200
    assert await tag_code(world, ads.writer, UnitCode.ADS) == "VIET2"
    assert await tag_code(world, ads.writer, UnitCode.PR) == "VIET2"
    # Somebody coded before any stream keeps it when they are seated.
    newcomer = await person(world, "Người Mới")
    assert put_code(world, world.owner, newcomer, "MOI").status_code == 200
    assert seat(world, "D", newcomer).status_code == 200
    assert await tag_code(world, newcomer, UnitCode.ADS) == "MOI"


async def test_04_the_stream_roster_leaves_the_code_to_the_admins(world: World) -> None:
    ads = await ads_world(world)
    assert put_code(world, world.owner, ads.designer, "MY").status_code == 200
    world.act_as(ads.head)
    url = f"/api/units/ADS/members/{ads.designer.id}"
    # The rest of the row, with the code unchanged, still saves.
    same = world.client.patch(url, json={"member_code": "my", "personal_nas_url": "https://nas/my"})
    assert same.status_code == 200, same.json()
    changed = world.client.patch(url, json={"member_code": "KHAC"})
    assert changed.status_code == 403, changed.json()
    assert error_reason(changed.json()) == "member_code_forbidden"
    cleared = world.client.patch(url, json={"member_code": None})
    assert cleared.status_code == 403
    assert await account_code(world, ads.designer) == "MY"
    # An ADMIN changes it there too - for the person, not just this tag.
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    assert world.client.patch(url, json={"member_code": "Diem"}).status_code == 200
    assert await account_code(world, ads.designer) == "DIEM"
    assert await tag_code(world, ads.designer, UnitCode.ADS) == "DIEM"


async def test_05_an_order_opens_with_the_persons_code(world: World) -> None:
    ads = await ads_world(world)
    assert put_code(world, world.owner, ads.orderer, "Order1").status_code == 200
    made = order(world, ads.orderer)
    assert made.status_code == 201, made.json()
    assert made.json()["order"]["code"].startswith("ORDER1-D-")


async def test_06_a_code_made_from_the_name_is_kept_on_the_account(world: World) -> None:
    await ads_world(world)
    newcomer = await person(world, "Ngọc Hà")
    assert seat(world, "ADS", newcomer).status_code == 200
    assert await account_code(world, newcomer) is None
    made = order(world, newcomer)
    assert made.status_code == 201, made.json()
    prefix = made.json()["order"]["code"].split("-")[0]
    assert prefix == "NGOC"
    assert await account_code(world, newcomer) == "NGOC"
    assert await tag_code(world, newcomer, UnitCode.ADS) == "NGOC"
