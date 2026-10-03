"""The organisation's working hours, and whether they are configured at all.

The important behaviour here is the *absence* case. With no active row, this
service returns ``None`` rather than a plausible default, and every caller that
needs to compute lateness has to deal with that. MeoBot then says:

    MeoBot chưa có cấu hình giờ làm việc.

and asks the Member what time they expect to arrive, instead of quietly
measuring their lateness against an office-opening time nobody set.

A default *is* used for rendering dates and times, because a timezone has to
come from somewhere and ``Asia/Ho_Chi_Minh`` is the deployment's. That is a
display concern and never feeds a number in a report.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger
from meobot.db.models.hr import OrganizationHoliday
from meobot.db.models.hr import WorkSchedule as WorkScheduleRow
from meobot.domain.hr.schedule import DEFAULT_SCHEDULE, WorkSchedule
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

NOT_CONFIGURED = (
    "MeoBot chưa có cấu hình giờ làm việc.\n"
    "Hãy thiết lập giờ làm trước khi tự động tính thời gian đi muộn."
)


class WorkScheduleService:
    """Loads the active work schedule and the holiday calendar.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_row(self) -> WorkScheduleRow | None:
        """The active schedule row, or ``None`` when none is configured."""
        result = await self._session.execute(
            select(WorkScheduleRow)
            .where(WorkScheduleRow.is_active.is_(True))
            .order_by(WorkScheduleRow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def active(self) -> WorkSchedule | None:
        """The active schedule as a value object, or ``None``.

        ``None`` is a real answer, not an error: it means lateness cannot be
        computed and the caller must ask instead of assuming.
        """
        row = await self.active_row()
        if row is None:
            return None
        return WorkSchedule(
            timezone=row.timezone,
            working_days=tuple(row.working_days or (0, 1, 2, 3, 4, 5)),
            morning_start=row.morning_start,
            morning_end=row.morning_end,
            afternoon_start=row.afternoon_start,
            afternoon_end=row.afternoon_end,
        )

    async def for_display(self) -> WorkSchedule:
        """A schedule good enough to *format* a date with.

        Never used to compute lateness - :meth:`active` is, and it can say no.
        """
        return await self.active() or DEFAULT_SCHEDULE

    async def configure(
        self,
        *,
        actor: Actor,
        schedule: WorkSchedule,
        name: str = "Mặc định",
    ) -> WorkScheduleRow:
        """Replace the active schedule.

        The previous row is deactivated rather than updated, so a lateness
        figure computed last month can still be explained by the schedule that
        was in force when it was computed.
        """
        current = await self.active_row()
        if current is not None:
            current.is_active = False

        row = WorkScheduleRow(
            name=name,
            timezone=schedule.timezone,
            working_days=list(schedule.working_days),
            morning_start=schedule.morning_start,
            morning_end=schedule.morning_end,
            afternoon_start=schedule.afternoon_start,
            afternoon_end=schedule.afternoon_end,
            is_active=True,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("work_schedule_configured", extra={"schedule_id": str(row.id)})
        return row

    async def holidays(self, *, start: date, end: date) -> Sequence[OrganizationHoliday]:
        """Company non-working days inside a range."""
        result = await self._session.execute(
            select(OrganizationHoliday)
            .where(
                OrganizationHoliday.holiday_date >= start,
                OrganizationHoliday.holiday_date <= end,
            )
            .order_by(OrganizationHoliday.holiday_date)
        )
        return result.scalars().all()

    async def is_holiday(self, day: date) -> bool:
        """True when the company is closed on ``day``."""
        result = await self._session.execute(
            select(OrganizationHoliday).where(OrganizationHoliday.holiday_date == day)
        )
        return result.scalar_one_or_none() is not None

    async def add_holiday(
        self, *, actor: Actor, day: date, name: str, is_paid: bool = True
    ) -> OrganizationHoliday:
        """Register a company holiday, or return the one already there."""
        result = await self._session.execute(
            select(OrganizationHoliday).where(OrganizationHoliday.holiday_date == day)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        row = OrganizationHoliday(
            holiday_date=day,
            name=name[:200],
            is_paid=is_paid,
            created_by_user_id=actor.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def schedule_id(self) -> uuid.UUID | None:
        """Primary key of the active schedule, for audit payloads."""
        row = await self.active_row()
        return row.id if row is not None else None
