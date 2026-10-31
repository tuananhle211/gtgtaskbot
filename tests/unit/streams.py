"""Stream ("Luồng") tags for test worlds built with ``create_all``.

Untagged no longer means PR: a user with no open ``org_unit_members`` row sees
no stream. A test world whose people work in PR therefore tags them, exactly
as production does (migration ``0048`` for the accounts that existed, a team
lead or an admin for everybody since). The unit rows are the ones ``0042``
seeds, with the same ids.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.departments import (
    ensure_default_departments,
    function_department,
    open_seat,
)
from meobot.core.time import utcnow
from meobot.db.models.department import DepartmentMember
from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.identity.models import Role
from meobot.domain.org.models import DepartmentFunction, Position
from meobot.domain.units.models import UnitCode, UnitMemberRole, unit_seed_id


async def ensure_units(session: AsyncSession) -> dict[UnitCode, OrgUnit]:
    """The two unit rows ``0042`` seeds, and the departments ``0061`` seeds,
    created when missing."""
    units: dict[UnitCode, OrgUnit] = {}
    for code in UnitCode:
        row = await session.get(OrgUnit, unit_seed_id(code))
        if row is None:
            # The PR suites drive pieces through every stage without task
            # codes: the 2026-10-31 gate is switched on by the tests about it.
            settings = {"pr_task_codes_required": False} if code is UnitCode.PR else {}
            row = OrgUnit(
                id=unit_seed_id(code), code=code, name=f"Luồng {code.value}", settings=settings
            )
            session.add(row)
        units[code] = row
    await session.flush()
    await ensure_default_departments(session)
    return units


async def tag_pr(session: AsyncSession, users: Iterable[User]) -> None:
    """Give each user an open PR ``MEMBER`` tag (a no-op for one who has it)."""
    units = await ensure_units(session)
    pr = units[UnitCode.PR]
    team = await function_department(session, DepartmentFunction.PR)
    assert team is not None
    for user in users:
        # The seat behind the tag (0061), written without re-deriving the
        # person's other tags.
        if await open_seat(session, team.id, user.id) is None:
            # As 0061 seeded production: a TEAM_LEAD heads PR Thương Hiệu.
            session.add(
                DepartmentMember(
                    department_id=team.id,
                    user_id=user.id,
                    position=Position.HEAD if user.role is Role.TEAM_LEAD else Position.MEMBER,
                    joined_at=utcnow(),
                )
            )
        existing = await session.scalar(
            select(OrgUnitMember).where(
                OrgUnitMember.unit_id == pr.id, OrgUnitMember.user_id == user.id
            )
        )
        if existing is None:
            session.add(
                OrgUnitMember(
                    unit_id=pr.id,
                    user_id=user.id,
                    role=UnitMemberRole.MEMBER,
                    joined_at=utcnow(),
                )
            )
        elif existing.left_at is not None:
            existing.left_at = None
    await session.flush()


__all__ = ["ensure_units", "tag_pr"]
