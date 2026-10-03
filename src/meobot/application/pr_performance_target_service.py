"""How many standard minutes one person's month was supposed to be. M6.

The first number in the chain, and the one most easily got wrong by assuming.
**A month is not 7500 minutes.** It is the KPI workdays that person actually had,
times the policy's daily target - and if the calendar cannot answer, the honest
result is a refusal rather than a plausible default.

Why refusing beats defaulting
------------------------------

A silently-defaulted target is a wrong denominator, and a wrong denominator is a
wrong performance index that looks exactly like a right one. The repository
already takes this position elsewhere: ``WorkSchedule``'s own docstring says
MeoBot *"refuses to compute lateness rather than assuming an office opens at
08:00"*. This module inherits that stance -
:class:`~meobot.domain.pr.performance.PrPerformanceCalculationStatus.TARGET_UNRESOLVED`
is a first-class outcome and it blocks finalisation.

What the calendar is made of
-----------------------------

Three existing sources, none of them invented here:

* ``work_schedules.working_days`` - which weekdays the organisation works. One
  active row, or the target is unresolved;
* ``organization_holidays`` - company non-working days. A holiday **does not
  increase** anybody's target and does not reduce it either: it was never a
  workday, so it simply is not counted;
* ``hr_requests`` - approved leave, which **does** reduce the target, because a
  person who was legitimately away had fewer days to produce in.

The asymmetry that matters
---------------------------

**Only approved leave reduces the target.** An unauthorised absence leaves no
approved row, so it reduces nothing - the person is measured against the full
month they were expected to work, which is the correct consequence and is
achieved by reading the data rather than by a rule about it. Nothing here invents
an HR policy the existing tables cannot support: there is no unpaid-leave rate,
no accrual, and no partial-day arithmetic beyond what the request types already
distinguish.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.hr import HrRequest, OrganizationHoliday, WorkSchedule
from meobot.db.models.pr_performance import PrPerformanceTargetOverride
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.domain.hr.models import HrRequestStatus, HrRequestType
from meobot.domain.pr.performance import MINUTES_QUANTUM, quantize

#: How much of a workday each kind of approved leave removes.
#:
#: Read from the request *type* rather than from its hours, because that is what
#: the type already means and the hours are optional. ``HOURLY_LEAVE`` and
#: ``LATE_ARRIVAL`` deliberately remove **nothing**: an hour out of a day is not
#: a day off, and treating it as a fraction would make a person who took two
#: hours over a month measurably easier to score than one who did not.
LEAVE_DAY_FRACTIONS: dict[HrRequestType, Decimal] = {
    HrRequestType.FULL_DAY_LEAVE: Decimal("1"),
    HrRequestType.MULTI_DAY_LEAVE: Decimal("1"),
    HrRequestType.MORNING_LEAVE: Decimal("0.5"),
    HrRequestType.AFTERNOON_LEAVE: Decimal("0.5"),
    HrRequestType.HOURLY_LEAVE: Decimal("0"),
    HrRequestType.LATE_ARRIVAL: Decimal("0"),
}


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """One person's month, explained.

    Every field is here so a screen can show the arithmetic rather than the
    answer: *"22 working days, minus 2 days' approved leave, times 300"* is a
    sentence somebody can check, and ``7500`` on its own is not.
    """

    #: ``None`` when the calendar could not answer. The caller reports
    #: ``TARGET_UNRESOLVED`` and refuses to finalise.
    target_standard_minutes: Decimal | None
    #: Weekdays the organisation works, minus company holidays.
    calendar_workdays: Decimal = Decimal("0")
    #: Days removed by **approved** leave. Never by an unauthorised absence.
    approved_leave_days: Decimal = Decimal("0")
    eligible_workdays: Decimal = Decimal("0")
    daily_target_minutes: int = 0
    #: Set when an owner replaced the computed number, with their reason.
    override_reason: str | None = None
    #: Why the calendar could not answer, when it could not.
    unresolved_reason: str | None = None
    holidays: tuple[date, ...] = field(default_factory=tuple)

    @property
    def is_overridden(self) -> bool:
        return self.override_reason is not None

    @property
    def resolved(self) -> bool:
        return self.target_standard_minutes is not None


class PrPerformanceTargetService:
    """Resolves the monthly workload target for one person.

    Reads the HR calendar and the owner's overrides; writes nothing. Kept apart
    from the calculation service because *"how long was this person's month"* is
    a question about the organisation's calendar, not about performance, and it
    is the piece most likely to need changing when HR policy does.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        user_id: uuid.UUID,
        period: PrReportingPeriod,
        daily_target_minutes: int,
    ) -> TargetResolution:
        """The month's target in standard minutes, or an explained refusal.

        An **override wins outright** and short-circuits the calendar: an owner
        who has said "this person's September was 4000 minutes, here is why" has
        made a decision, and recomputing around it would be second-guessing a
        decision the system asked them to justify in writing.

        One person is the batched form for one person - see
        :meth:`resolve_many` - so a manager's list and a single detail cannot
        resolve the same month two ways.
        """
        found = await self.resolve_many(
            user_ids=(user_id,), period=period, daily_target_minutes=daily_target_minutes
        )
        return found[user_id]

    async def resolve_many(
        self,
        *,
        user_ids: Iterable[uuid.UUID],
        period: PrReportingPeriod,
        daily_target_minutes: int,
    ) -> dict[uuid.UUID, TargetResolution]:
        """Every person's month at once: four queries, however many people.

        The calendar - the schedule and the holidays - is the organisation's
        and is read once. Overrides and approved leave are per person and are
        read in one query each, then folded per person with exactly the
        arithmetic :meth:`resolve` documents. Written for the KPI manager list,
        which shows twenty people's workload on one screen and must not resolve
        twenty calendars one at a time.
        """
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        overrides = await self._overrides(user_ids=ids, period_id=period.id)
        results: dict[uuid.UUID, TargetResolution] = {}
        for user_id in ids:
            if (override := overrides.get(user_id)) is not None:
                target, reason = override
                results[user_id] = TargetResolution(
                    target_standard_minutes=quantize(target, MINUTES_QUANTUM),
                    daily_target_minutes=daily_target_minutes,
                    override_reason=reason,
                )
        pending = [user_id for user_id in ids if user_id not in results]
        if not pending:
            return results

        schedule = await self._active_schedule(period.date_start, period.date_end)
        if schedule is None:
            # The same refusal ``resolve_late_minutes`` makes, for the same
            # reason: without a schedule there is no such thing as a workday,
            # and inventing Monday-to-Friday would be inventing the company's
            # working week.
            for user_id in pending:
                results[user_id] = TargetResolution(
                    target_standard_minutes=None,
                    daily_target_minutes=daily_target_minutes,
                    unresolved_reason="no_active_work_schedule",
                )
            return results
        if not schedule.working_days:
            for user_id in pending:
                results[user_id] = TargetResolution(
                    target_standard_minutes=None,
                    daily_target_minutes=daily_target_minutes,
                    unresolved_reason="work_schedule_has_no_working_days",
                )
            return results

        holidays = await self._holidays(period.date_start, period.date_end)
        working = [
            day
            for day in _days(period.date_start, period.date_end)
            if day.weekday() in set(schedule.working_days) and day not in holidays
        ]
        calendar_workdays = Decimal(len(working))

        leave_by_user = await self._approved_leave_days_many(
            user_ids=pending, days=set(working), period=period
        )
        for user_id in pending:
            leave = leave_by_user.get(user_id, Decimal("0"))
            eligible = max(calendar_workdays - leave, Decimal("0"))
            results[user_id] = TargetResolution(
                target_standard_minutes=quantize(
                    eligible * Decimal(daily_target_minutes), MINUTES_QUANTUM
                ),
                calendar_workdays=calendar_workdays,
                approved_leave_days=leave,
                eligible_workdays=eligible,
                daily_target_minutes=daily_target_minutes,
                holidays=tuple(sorted(holidays)),
            )
        return results

    # -----------------------------------------------------------------
    async def _overrides(
        self, *, user_ids: Sequence[uuid.UUID], period_id: uuid.UUID
    ) -> dict[uuid.UUID, tuple[Decimal, str]]:
        rows = (
            (
                await self._session.execute(
                    select(PrPerformanceTargetOverride).where(
                        PrPerformanceTargetOverride.user_id.in_(user_ids),
                        PrPerformanceTargetOverride.reporting_period_id == period_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Both columns are NOT NULL - a row here *is* an override - so this is a
        # read rather than a validation, and the reason is carried to the screen
        # because it is the justification the number rests on.
        return {row.user_id: (row.monthly_target_override, row.override_reason) for row in rows}

    async def _active_schedule(self, start: date, end: date) -> WorkSchedule | None:
        """The schedule in force over this month, newest first.

        Bounded by the period rather than by "today": recomputing an open
        September in October must use September's working week, not whatever was
        configured afterwards.
        """
        statement = (
            select(WorkSchedule)
            .where(
                WorkSchedule.is_active.is_(True),
                (WorkSchedule.active_from.is_(None)) | (WorkSchedule.active_from <= end),
                (WorkSchedule.active_until.is_(None)) | (WorkSchedule.active_until >= start),
            )
            .order_by(WorkSchedule.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalars().one_or_none()

    async def _holidays(self, start: date, end: date) -> set[date]:
        rows = (
            (
                await self._session.execute(
                    select(OrganizationHoliday.holiday_date).where(
                        OrganizationHoliday.holiday_date >= start,
                        OrganizationHoliday.holiday_date <= end,
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(rows)

    async def _approved_leave_days_many(
        self, *, user_ids: Sequence[uuid.UUID], days: set[date], period: PrReportingPeriod
    ) -> dict[uuid.UUID, Decimal]:
        """Working days removed by **approved** leave.

        Counted against ``days`` - the days that were working days in the first
        place - so leave taken over a weekend or a public holiday removes
        nothing, which is the same statement as "a holiday does not increase the
        target" read from the other side.

        A multi-day request spans ``work_date``..``end_date`` inclusive; every
        other type is the single ``work_date``. Days are collected in a set
        before they are counted, so two overlapping approved requests for one day
        cannot remove that day twice.
        """
        rows = (
            (
                await self._session.execute(
                    select(HrRequest).where(
                        HrRequest.requester_user_id.in_(user_ids),
                        HrRequest.status == HrRequestStatus.APPROVED,
                        HrRequest.work_date <= period.date_end,
                    )
                )
            )
            .scalars()
            .all()
        )

        per_user_day: dict[uuid.UUID, dict[date, Decimal]] = {}
        for row in rows:
            fraction = LEAVE_DAY_FRACTIONS.get(row.request_type, Decimal("0"))
            if fraction == 0:
                continue
            per_day = per_user_day.setdefault(row.requester_user_id, {})
            last = row.end_date or row.work_date
            for day in _days(row.work_date, last):
                if day in days:
                    # The largest claim on a day wins rather than the sum: a
                    # morning and an afternoon request are one day off, not one
                    # and a half.
                    per_day[day] = max(per_day.get(day, Decimal("0")), fraction)
        return {
            user_id: sum(per_day.values(), Decimal("0"))
            for user_id, per_day in per_user_day.items()
        }


def _days(start: date, end: date) -> list[date]:
    """Every calendar day from ``start`` to ``end`` inclusive."""
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


__all__: list[str] = ["LEAVE_DAY_FRACTIONS", "PrPerformanceTargetService", "TargetResolution"]
