"""HR totals, computed from rows and never from a model.

Every number a person reads about absence comes from a SQL query and Python
arithmetic. A language model may be asked to *narrate* a set of validated
totals afterwards; it is never asked what the totals are. Two reasons:

* a wrong figure in "18 yêu cầu đã duyệt, tổng 12,5 ngày công" is not a wrong
  sentence, it is a wrong fact about people's pay and attendance;
* these summaries are read by the person who decides them, so they have to be
  reproducible - somebody must be able to check the number by hand.

Scope is enforced in the queries. There is a per-person method and a
department-wide method, and the department one takes an ``Actor`` and refuses
anybody without the management permission - so "show me another Member's
lateness" has no code path, not merely no button.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.errors import AuthorizationError
from meobot.db.models.hr import HrRequest
from meobot.db.models.user import User
from meobot.domain.hr.models import (
    HrRequestStatus,
    HrRequestType,
    LateTotals,
    LeaveTotals,
    duration_days,
)
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.permissions.matrix import Permission, has_permission

ONLY_OWNER_REPORTS = f"Chỉ {role_label(Role.OWNER)} được xem báo cáo nhân sự của cả phòng."


@dataclass(frozen=True, slots=True)
class PersonalHrSummary:
    """One person's own figures for a period."""

    leave: LeaveTotals
    late: LateTotals
    latest: HrRequest | None


@dataclass(frozen=True, slots=True)
class AbsenceToday:
    """Who is away right now, for the daily card."""

    full_day: list[tuple[str, HrRequest]]
    morning: list[tuple[str, HrRequest]]
    afternoon: list[tuple[str, HrRequest]]
    late: list[tuple[str, HrRequest]]
    pending_count: int


@dataclass(frozen=True, slots=True)
class DepartmentHrSummary:
    """Department figures for a period, plus the things needing attention."""

    leave: LeaveTotals
    late: LateTotals
    pending_over_a_day: int
    days_over_threshold: list[tuple[date, int]]


def month_bounds(day: date) -> tuple[date, date]:
    """First and last day of the month ``day`` falls in."""
    first = day.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first, next_month - timedelta(days=1)


def week_bounds(day: date) -> tuple[date, date]:
    """Monday and Sunday of the week ``day`` falls in."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


class HrStatisticsService:
    """Aggregates HR rows. No provider call happens anywhere in this class.

    Args:
        session: Read-only unit of work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _rows_between(
        self, *, start: date, end: date, user_id: uuid.UUID | None = None
    ) -> Sequence[HrRequest]:
        statement = select(HrRequest).where(
            HrRequest.work_date >= start, HrRequest.work_date <= end
        )
        if user_id is not None:
            statement = statement.where(HrRequest.requester_user_id == user_id)
        result = await self._session.execute(statement)
        return result.scalars().all()

    @staticmethod
    def _tally(rows: Sequence[HrRequest]) -> tuple[LeaveTotals, LateTotals]:
        """Split rows into leave and lateness, counting each status separately.

        Approved, pending and rejected are kept apart on purpose: a report that
        folds a rejected request into the total would overstate how much time
        the department actually lost.
        """
        leave_approved = leave_pending = leave_rejected = 0
        leave_days = 0.0
        late_approved = late_pending = late_rejected = 0
        late_minutes = 0

        for row in rows:
            if row.request_type is HrRequestType.LATE_ARRIVAL:
                if row.status is HrRequestStatus.APPROVED:
                    late_approved += 1
                    late_minutes += row.late_minutes or 0
                elif row.status is HrRequestStatus.PENDING:
                    late_pending += 1
                elif row.status is HrRequestStatus.REJECTED:
                    late_rejected += 1
                continue

            if row.status is HrRequestStatus.APPROVED:
                leave_approved += 1
                if row.start_at is not None and row.end_at is not None:
                    leave_days += duration_days(row.request_type, row.start_at, row.end_at)
            elif row.status is HrRequestStatus.PENDING:
                leave_pending += 1
            elif row.status is HrRequestStatus.REJECTED:
                leave_rejected += 1

        return (
            LeaveTotals(
                approved_count=leave_approved,
                pending_count=leave_pending,
                rejected_count=leave_rejected,
                approved_days=round(leave_days, 2),
            ),
            LateTotals(
                approved_count=late_approved,
                pending_count=late_pending,
                rejected_count=late_rejected,
                total_minutes=late_minutes,
            ),
        )

    async def personal(self, *, user_id: uuid.UUID, start: date, end: date) -> PersonalHrSummary:
        """One person's own figures. Always scoped to ``user_id``."""
        rows = await self._rows_between(start=start, end=end, user_id=user_id)
        leave, late = self._tally(rows)
        latest = await self._session.execute(
            select(HrRequest)
            .where(HrRequest.requester_user_id == user_id)
            .order_by(HrRequest.created_at.desc())
            .limit(1)
        )
        return PersonalHrSummary(leave=leave, late=late, latest=latest.scalar_one_or_none())

    async def department(
        self, *, actor: Actor, start: date, end: date, absence_threshold: float = 0.2
    ) -> DepartmentHrSummary:
        """Figures for everybody. Refuses anybody without the permission.

        Raises:
            AuthorizationError: The actor may not read department-wide HR data.
        """
        self._require_manager(actor)
        rows = await self._rows_between(start=start, end=end)
        leave, late = self._tally(rows)

        from meobot.core.time import ensure_utc, utcnow

        cutoff = utcnow() - timedelta(hours=24)
        stale = sum(
            1
            for row in rows
            if row.status is HrRequestStatus.PENDING
            and row.submitted_at is not None
            and ensure_utc(row.submitted_at) < cutoff
        )

        headcount = await self._headcount()
        per_day: dict[date, int] = {}
        for row in rows:
            if row.status is HrRequestStatus.APPROVED and row.request_type.is_leave:
                per_day[row.work_date] = per_day.get(row.work_date, 0) + 1
        heavy = sorted(
            (day, count)
            for day, count in per_day.items()
            if headcount > 0 and count / headcount >= absence_threshold
        )

        return DepartmentHrSummary(
            leave=leave, late=late, pending_over_a_day=stale, days_over_threshold=heavy
        )

    async def absence_on(self, *, actor: Actor, day: date) -> AbsenceToday:
        """Who is away on one day, with names. Management-only."""
        self._require_manager(actor)
        result = await self._session.execute(
            select(HrRequest, User)
            .join(User, User.id == HrRequest.requester_user_id)
            .where(HrRequest.work_date == day)
        )
        full: list[tuple[str, HrRequest]] = []
        morning: list[tuple[str, HrRequest]] = []
        afternoon: list[tuple[str, HrRequest]] = []
        late: list[tuple[str, HrRequest]] = []
        pending = 0

        for row, user in result.all():
            if row.status is HrRequestStatus.PENDING:
                pending += 1
                continue
            if row.status is not HrRequestStatus.APPROVED:
                continue
            entry = (user.full_name, row)
            if row.request_type is HrRequestType.LATE_ARRIVAL:
                late.append(entry)
            elif row.request_type is HrRequestType.MORNING_LEAVE:
                morning.append(entry)
            elif row.request_type is HrRequestType.AFTERNOON_LEAVE:
                afternoon.append(entry)
            else:
                full.append(entry)

        return AbsenceToday(
            full_day=full, morning=morning, afternoon=afternoon, late=late, pending_count=pending
        )

    async def _headcount(self) -> int:
        """Active accounts, used only as the denominator of the absence rate."""
        from meobot.domain.access.models import UserStatus

        result = await self._session.execute(select(User).where(User.status == UserStatus.ACTIVE))
        return len(result.scalars().all())

    @staticmethod
    def _require_manager(actor: Actor) -> None:
        if not has_permission(actor.role, Permission.HR_DEPARTMENT_REPORT):
            raise AuthorizationError(ONLY_OWNER_REPORTS)
