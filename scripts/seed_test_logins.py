"""Login accounts for testing every role on a LOCAL database. Never run against production.

Username = the number in the first column (it is stored as the Telegram id, which
is what /login takes). Password for all: 12345. Re-running resets them.

    docker compose exec -T api python - < scripts/seed_test_logins.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select

from meobot.application.account.passwords import hash_password
from meobot.core.config import get_settings
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.db.session import Database
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitMemberRole

PASSWORD = "12345"  # noqa: S105 - local test accounts only

# (username/telegram id, name, base role, [(unit, unit role, is_lead, member code)])
ACCOUNTS = [
    (1000, "Test Quản trị viên", Role.ADMIN, []),
    (1100, "Test Trưởng nhóm PR", Role.TEAM_LEAD, [("PR", UnitMemberRole.MEMBER, False, None)]),
    (1101, "Test Nhân viên PR", Role.EMPLOYEE, [("PR", UnitMemberRole.MEMBER, False, None)]),
    (1200, "Test Trưởng phòng ORD", Role.TEAM_LEAD, [("ADS", UnitMemberRole.HEAD, False, "TPORD")]),
    (1201, "Test Marketing order", Role.EMPLOYEE, [("ADS", UnitMemberRole.ORDERER, False, "MKT")]),
    (1210, "Test Trưởng Biên kịch", Role.EMPLOYEE, [("ADS", UnitMemberRole.BIEN_TAP, True, None)]),
    (
        1211,
        "Test Nhân viên Biên kịch",
        Role.EMPLOYEE,
        [("ADS", UnitMemberRole.BIEN_TAP, False, None)],
    ),
    (1220, "Test Trưởng Design", Role.EMPLOYEE, [("ADS", UnitMemberRole.THIET_KE, True, None)]),
    (1221, "Test Nhân viên Design", Role.EMPLOYEE, [("ADS", UnitMemberRole.THIET_KE, False, None)]),
    (1230, "Test Trưởng Dựng", Role.EMPLOYEE, [("ADS", UnitMemberRole.DUNG, True, None)]),
    (1231, "Test Nhân viên Dựng", Role.EMPLOYEE, [("ADS", UnitMemberRole.DUNG, False, None)]),
    (
        1300,
        "Test Hai luồng",
        Role.EMPLOYEE,
        [
            ("PR", UnitMemberRole.MEMBER, False, None),
            ("ADS", UnitMemberRole.ORDERER, False, "HAILUONG"),
        ],
    ),
    (1400, "Test Chưa có luồng", Role.EMPLOYEE, []),
]


async def main() -> None:
    db = Database(get_settings())
    hashed = hash_password(PASSWORD)
    async with db.session_factory() as session:
        units = {u.code: u for u in (await session.scalars(select(OrgUnit))).all()}
        for tg_id, name, role, tags in ACCOUNTS:
            user = await session.scalar(select(User).where(User.telegram_user_id == tg_id))
            if user is None:
                user = User(telegram_user_id=tg_id, full_name=name, role=role, active=True)
                session.add(user)
            user.full_name, user.role, user.active = name, role, True
            user.password_hash = hashed
            user.password_temporary = False
            user.failed_login_count = 0
            user.locked_until = None
            await session.flush()
            await session.execute(delete(OrgUnitMember).where(OrgUnitMember.user_id == user.id))
            for unit, unit_role, is_lead, code in tags:
                session.add(
                    OrgUnitMember(
                        unit_id=units[unit].id,
                        user_id=user.id,
                        role=unit_role,
                        is_lead=is_lead,
                        member_code=code,
                    )
                )
            print(f"{tg_id:<6} {name}")
        await session.commit()
    await db.engine.dispose()


asyncio.run(main())
