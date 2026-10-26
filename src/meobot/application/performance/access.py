"""Who may see whose points (``docs/perf/PERFORMANCE_REPORTS_PLAN.md`` §0).

* everybody their own;
* a Trưởng phòng also their direct reports (seated in a department they head
  with them as "Trưởng quản lý" - ``org/departments.reports_of``, strict);
* the whole company: the OWNER, an ADMIN, and whoever holds ``PERFORMANCE
  VIEW ALL`` (a department or personal grant - nobody by default);
* the Excel export: the OWNER, an ADMIN, or ``PERFORMANCE EXPORT``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import Access, load_access
from meobot.application.org.departments import reports_of
from meobot.domain.identity.models import Role
from meobot.domain.org.models import Position
from meobot.domain.performance.models import MODULE_PERFORMANCE, PerformancePermission
from meobot.domain.units.models import UnitMembership


@dataclass(frozen=True, slots=True)
class PerformanceAccess:
    user_id: uuid.UUID | None
    view_all: bool = False
    export: bool = False
    #: Heads a department (the "Phòng của tôi" screen is theirs, even empty).
    heads: bool = False
    #: The people who report to them directly.
    reports: frozenset[uuid.UUID] = field(default_factory=frozenset)

    @property
    def view_team(self) -> bool:
        return self.view_all or self.heads

    def may_view(self, user_id: uuid.UUID) -> bool:
        return self.view_all or user_id == self.user_id or user_id in self.reports


async def performance_access(
    session: AsyncSession, membership: UnitMembership
) -> PerformanceAccess:
    """What ``membership`` may see of the points reports."""
    if membership.user_id is None:
        return PerformanceAccess(user_id=None)
    if membership.sees_all:
        heads = any(seat.position is Position.HEAD for seat in membership.seats)
        return PerformanceAccess(
            user_id=membership.user_id,
            view_all=True,
            export=True,
            heads=heads,
            reports=await reports_of(session, membership.user_id) if heads else frozenset(),
        )
    access = (
        membership.access
        if isinstance(membership.access, Access)
        else await load_access(session, user_id=membership.user_id, role=Role.EMPLOYEE)
    )
    view_all = access.scope(MODULE_PERFORMANCE, PerformancePermission.VIEW.value) == "ALL"
    export = access.scope(MODULE_PERFORMANCE, PerformancePermission.EXPORT.value) == "ALL"
    heads = any(seat.position is Position.HEAD for seat in access.seats)
    return PerformanceAccess(
        user_id=membership.user_id,
        view_all=view_all,
        export=export,
        heads=heads,
        reports=await reports_of(session, membership.user_id) if heads else frozenset(),
    )


__all__ = ["PerformanceAccess", "performance_access"]
