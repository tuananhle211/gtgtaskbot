"""Reading seats and grants: who sits where, and what that gives them.

Read-only and cheap, shared by everything that needs a person's standing:
:class:`~meobot.application.units.directory.UnitDirectoryService` builds each
request's membership from it, and the order flow's approver lists read every
seat at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.domain.org.models import DepartmentFunction, Grant, Position, Seat


async def load_seats(
    session: AsyncSession, user_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, tuple[Seat, ...]]:
    """Every open seat in an active department, by person (all people when
    ``user_ids`` is None), in the departments' order."""
    statement = (
        select(DepartmentMember, Department)
        .join(Department, Department.id == DepartmentMember.department_id)
        .where(DepartmentMember.left_at.is_(None), Department.active.is_(True))
        .order_by(Department.sort_order, Department.code)
    )
    if user_ids is not None:
        wanted = list(set(user_ids))
        if not wanted:
            return {}
        statement = statement.where(DepartmentMember.user_id.in_(wanted))
    found: dict[uuid.UUID, list[Seat]] = {}
    for member, department in (await session.execute(statement)).all():
        found.setdefault(member.user_id, []).append(
            Seat(
                department_id=department.id,
                code=department.code,
                name=department.name,
                function=department.function,
                position=member.position,
                manager_user_id=member.manager_user_id,
                sort_order=department.sort_order,
            )
        )
    return {user_id: tuple(seats) for user_id, seats in found.items()}


async def load_grants(
    session: AsyncSession, module: str, department_ids: Iterable[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], tuple[tuple[str, str], ...]]:
    """``(department, position) -> ((permission, scope), ...)`` for one module."""
    ids = list(set(department_ids))
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(DepartmentPermission).where(
                DepartmentPermission.module == module,
                DepartmentPermission.department_id.in_(ids),
            )
        )
    ).all()
    found: dict[tuple[uuid.UUID, str], list[tuple[str, str]]] = {}
    for row in rows:
        if row.scope != "NONE":
            found.setdefault((row.department_id, row.position.value), []).append(
                (row.permission, row.scope)
            )
    return {key: tuple(value) for key, value in found.items()}


#: Every Trưởng phòng (any department) may be picked as an order's approver
#: and approves what is sent to them (OWN); their own orders need no approval.
HEAD_ORDER_GRANT: tuple[str, str] = ("ORDER_APPROVE", "OWN")


def grants_of(
    seats: Sequence[Seat], table: dict[tuple[uuid.UUID, str], tuple[tuple[str, str], ...]]
) -> tuple[Grant, ...]:
    """What a person's seats grant. Built in: Ban Giám đốc sees everything,
    and every department head approves the orders sent to them
    (:data:`HEAD_ORDER_GRANT`)."""
    grants: list[Grant] = []
    for seat in seats:
        for permission, scope in table.get((seat.department_id, seat.position.value), ()):
            grants.append(Grant(permission, scope, seat.function))
        if seat.function is DepartmentFunction.BOARD:
            grants.append(Grant("VIEW", "ALL", seat.function))
        if seat.position is Position.HEAD:
            grants.append(Grant(HEAD_ORDER_GRANT[0], HEAD_ORDER_GRANT[1], seat.function))
    return tuple(grants)


__all__ = ["HEAD_ORDER_GRANT", "grants_of", "load_grants", "load_seats"]
