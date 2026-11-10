"""Ca làm việc, ngày lễ, khoá bảng công (0100): what the requests and the
timesheet both read.

A person's shift = the first active shift naming one of their departments,
else their branch, else the default shift, else the built-in "Hành chính"
(:func:`meobot.domain.hr.attendance.default_shift`). Holidays are
``organization_holidays``. A locked month ("Khoá bảng công") is kept in
``system_settings`` ``hr_timesheet``: its timesheet, punches and requests no
longer change.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.hr.profiles import HrValidationError
from meobot.application.org.seats import load_seats
from meobot.db.models.hr import OrganizationHoliday
from meobot.db.models.hr_attendance import HrShift
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.user import User
from meobot.domain.hr.attendance import Shift, default_shift, shift_from_json

LOCKS_KEY = "hr_timesheet"


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


@dataclass(slots=True)
class ShiftBook:
    """The shifts and who works which, loaded once per request."""

    shifts: list[tuple[HrShift, Shift]]
    seats: dict[uuid.UUID, frozenset[str]] = field(default_factory=dict)
    branches: dict[uuid.UUID, str | None] = field(default_factory=dict)

    def row_for(self, user_id: uuid.UUID) -> HrShift | None:
        departments = self.seats.get(user_id, frozenset())
        branch = self.branches.get(user_id)
        for row, _ in self.shifts:
            if not row.is_default and departments & set(row.department_ids or ()):
                return row
        for row, _ in self.shifts:
            if not row.is_default and branch and branch in (row.branches or ()):
                return row
        for row, _ in self.shifts:
            if row.is_default:
                return row
        return None

    def for_user(self, user_id: uuid.UUID) -> Shift:
        row = self.row_for(user_id)
        if row is None:
            return default_shift()
        return next(shift for item, shift in self.shifts if item is row)


async def load_shift_book(session: AsyncSession, user_ids: Iterable[uuid.UUID]) -> ShiftBook:
    rows = (
        await session.scalars(
            select(HrShift)
            .where(HrShift.active.is_(True))
            .order_by(HrShift.sort_order, HrShift.created_at)
        )
    ).all()
    wanted = list(set(user_ids))
    seats = await load_seats(session, wanted) if wanted else {}
    branches: dict[uuid.UUID, str | None] = {}
    if wanted:
        for user_id, branch in (
            await session.execute(select(User.id, User.branch).where(User.id.in_(wanted)))
        ).all():
            branches[user_id] = branch
    return ShiftBook(
        shifts=[(row, shift_from_json(row.name, row.week, row.grace_minutes)) for row in rows],
        seats={
            user_id: frozenset(str(seat.department_id) for seat in held)
            for user_id, held in seats.items()
        },
        branches=branches,
    )


async def holidays_between(session: AsyncSession, start: date, end: date) -> dict[date, str]:
    rows = (
        await session.execute(
            select(OrganizationHoliday.holiday_date, OrganizationHoliday.name).where(
                OrganizationHoliday.holiday_date >= start,
                OrganizationHoliday.holiday_date <= end,
            )
        )
    ).all()
    return dict(rows)  # type: ignore[arg-type]


async def locked_months(session: AsyncSession) -> frozenset[str]:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == LOCKS_KEY))
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    locked = value.get("locked")
    return (
        frozenset(item for item in locked if isinstance(item, str))
        if isinstance(locked, list)
        else frozenset()
    )


class MonthLockedError(HrValidationError):
    def __init__(self, months: Iterable[str]) -> None:
        listed = ", ".join(sorted({f"{key[5:]}/{key[:4]}" for key in months}))
        super().__init__(
            "hr_month_locked",
            f"Bảng công tháng {listed} đã khoá. Nhờ HCNS mở khoá nếu cần sửa.",
        )


async def require_open(session: AsyncSession, days: Iterable[date]) -> None:
    locked = await locked_months(session)
    hit = {month_key(day) for day in days} & locked
    if hit:
        raise MonthLockedError(hit)


__all__ = [
    "LOCKS_KEY",
    "MonthLockedError",
    "ShiftBook",
    "holidays_between",
    "load_shift_book",
    "locked_months",
    "month_key",
    "require_open",
]
