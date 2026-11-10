"""Reading seats and grants: who sits where, and what that gives them.

Read-only and cheap, shared by everything that needs a person's standing:
:class:`~meobot.application.units.directory.UnitDirectoryService` builds each
request's membership from it, and the order flow's approver lists read every
seat at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.domain.orders.models import OrderNodeType
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


async def function_staff(
    session: AsyncSession, function: DepartmentFunction
) -> frozenset[uuid.UUID]:
    """Everybody with an open seat in an active department of ``function``.

    For PR (2026-10-30) these are the people PR work is handed to: the PR
    stream tag also follows a Ban Giám đốc seat, so the tag is not the
    department's staff."""
    found = await session.scalars(
        select(DepartmentMember.user_id)
        .join(Department, Department.id == DepartmentMember.department_id)
        .where(
            Department.function == function,
            Department.active.is_(True),
            DepartmentMember.left_at.is_(None),
        )
    )
    return frozenset(found.all())


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


def builtin_order_grants(seats: Sequence[Seat]) -> list[Grant]:
    """The order flow's built-in grants: Ban Giám đốc sees every order, every
    department head approves the orders sent to them (:data:`HEAD_ORDER_GRANT`)."""
    grants: list[Grant] = []
    for seat in seats:
        if seat.function is DepartmentFunction.BOARD:
            grants.append(Grant("VIEW", "ALL", seat.function))
        if seat.position is Position.HEAD:
            grants.append(Grant(HEAD_ORDER_GRANT[0], HEAD_ORDER_GRANT[1], seat.function))
    return grants


def overridden(grants: Sequence[Grant], personal: Mapping[str, str] | None) -> list[Grant]:
    """``grants`` under a person's personal rows (0098: ``permission -> scope``
    sets the scope whatever the seats give; ``NONE`` takes it away), plus the
    personal rows themselves."""
    if not personal:
        return list(grants)
    out: list[Grant] = []
    for grant in grants:
        cap = personal.get(grant.permission)
        if cap is None:
            out.append(grant)
        elif cap != "NONE":
            out.append(replace(grant, scope=_capped(grant.scope, cap)))
    out.extend(
        Grant(permission, scope, DepartmentFunction.NONE)
        for permission, scope in personal.items()
        if scope != "NONE"
    )
    return out


def _capped(scope: str, cap: str) -> str:
    rank = {"NONE": 0, "OWN": 1, "ALL": 2}
    return scope if rank.get(scope, 0) <= rank.get(cap, 0) else cap


def grants_of(
    seats: Sequence[Seat],
    table: dict[tuple[uuid.UUID, str], tuple[tuple[str, str], ...]],
    personal: Mapping[str, str] | None = None,
) -> tuple[Grant, ...]:
    """What a person's seats grant, with the built-in ones
    (:func:`builtin_order_grants`), under their personal rows (``personal``)."""
    grants: list[Grant] = []
    for seat in seats:
        for permission, scope in table.get((seat.department_id, seat.position.value), ()):
            grants.append(Grant(permission, scope, seat.function))
    grants.extend(builtin_order_grants(seats))
    return tuple(overridden(grants, personal))


__all__ = [
    "HEAD_ORDER_GRANT",
    "builtin_order_grants",
    "grants_of",
    "load_grants",
    "load_seats",
    "overridden",
    "points_department",
]


def points_department(seats: Iterable[Seat], phase: OrderNodeType) -> uuid.UUID | None:
    """0075: the department a step's points count for - the person's
    department whose production step is ``phase`` (a Dựng step: the Dựng
    department), else the first one they sit in, else none."""
    ordered = list(seats)
    for seat in ordered:
        if seat.node is phase:
            return seat.department_id
    return ordered[0].department_id if ordered else None
