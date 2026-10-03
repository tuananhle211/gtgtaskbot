"""Turning a schedule into the next instant it fires.

Two rules, and they are the reason this is a pure module with no database and
no model in sight:

**Recurrence is computed in the user's local timezone, and stored in UTC.**
"16:00 mỗi thứ Năm" is a statement about a wall clock in Ho Chi Minh City. It
has to be evaluated there and then converted, because computing it in UTC and
converting afterwards puts the reminder an hour out the moment anybody deploys
this anywhere with daylight saving.

**Nothing here asks a model anything.** A date is arithmetic. Asking an LLM for
one costs a network call, costs a chat slot, and is wrong often enough to
matter - and a reminder that fires on the wrong day is worse than no reminder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from meobot.domain.reminders.models import ScheduleKind, weekday_label

__all__ = ["ReminderSchedule", "ScheduleKind", "describe", "weekday_label"]


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    """When a reminder fires, expressed in local wall-clock terms.

    Args:
        kind: One-time, daily or weekly.
        local_time: The wall clock, in the reminder's own timezone.
        weekday: Monday=0. Required for :attr:`ScheduleKind.WEEKLY`.
        run_date: The local date. Required for :attr:`ScheduleKind.ONE_TIME`.
    """

    kind: ScheduleKind
    local_time: time
    weekday: int | None = None
    run_date: date | None = None

    def __post_init__(self) -> None:
        if self.kind is ScheduleKind.WEEKLY and self.weekday is None:
            raise ValueError("A weekly schedule needs a weekday")
        if self.kind is ScheduleKind.ONE_TIME and self.run_date is None:
            raise ValueError("A one-time schedule needs a date")

    def next_after(self, moment: datetime, *, tz: ZoneInfo) -> datetime:
        """The first firing strictly after ``moment``, as a UTC instant.

        A one-time schedule returns its single instant whether or not that
        instant has passed - deciding what to do about a reminder that is
        already late is the caller's job, not the calendar's.
        """
        local_now = moment.astimezone(tz)

        if self.kind is ScheduleKind.ONE_TIME:
            assert self.run_date is not None
            return self._at(self.run_date, tz)

        if self.kind is ScheduleKind.DAILY:
            candidate = self._at(local_now.date(), tz)
            if candidate <= moment:
                candidate = self._at(local_now.date() + timedelta(days=1), tz)
            return candidate

        assert self.weekday is not None
        ahead = (self.weekday - local_now.weekday()) % 7
        candidate = self._at(local_now.date() + timedelta(days=ahead), tz)
        if candidate <= moment:
            # Today is the right weekday but the hour has passed: a week out.
            candidate = self._at(local_now.date() + timedelta(days=ahead + 7), tz)
        return candidate

    def _at(self, day: date, tz: ZoneInfo) -> datetime:
        """One local date plus the wall clock, as a UTC instant."""
        local = datetime.combine(day, self.local_time).replace(tzinfo=tz)
        return local.astimezone(ZoneInfo("UTC"))

    @property
    def recurrence_rule(self) -> str:
        """A compact, stable description stored on the row.

        Deliberately not iCalendar RRULE. This supports three shapes; a full
        RRULE parser would be a promise to support the rest of the standard,
        and the stored string would then be a schedule MeoBot could not
        actually run.
        """
        clock = self.local_time.strftime("%H:%M")
        if self.kind is ScheduleKind.DAILY:
            return f"DAILY;{clock}"
        if self.kind is ScheduleKind.WEEKLY:
            return f"WEEKLY;{self.weekday};{clock}"
        return f"ONCE;{self.run_date.isoformat() if self.run_date else ''};{clock}"


def parse_recurrence_rule(rule: str, *, local_time: time) -> ReminderSchedule:
    """Rebuild a schedule from its stored rule.

    Raises:
        ValueError: The stored rule is not one this version can run - which is
            better than silently firing something else.
    """
    head, _, rest = rule.partition(";")
    if head == "DAILY":
        return ReminderSchedule(kind=ScheduleKind.DAILY, local_time=local_time)
    if head == "WEEKLY":
        weekday_text = rest.partition(";")[0]
        return ReminderSchedule(
            kind=ScheduleKind.WEEKLY, local_time=local_time, weekday=int(weekday_text)
        )
    if head == "ONCE":
        day_text = rest.partition(";")[0]
        return ReminderSchedule(
            kind=ScheduleKind.ONE_TIME,
            local_time=local_time,
            run_date=date.fromisoformat(day_text),
        )
    raise ValueError(f"Unsupported recurrence rule: {rule!r}")


def describe(schedule: ReminderSchedule) -> str:
    """The schedule in Vietnamese, as shown in a preview and in a list."""
    clock = schedule.local_time.strftime("%H:%M")
    if schedule.kind is ScheduleKind.DAILY:
        return f"{clock} mỗi ngày"
    if schedule.kind is ScheduleKind.WEEKLY:
        assert schedule.weekday is not None
        return f"{clock} mỗi {weekday_label(schedule.weekday)}"
    assert schedule.run_date is not None
    return f"{clock} ngày {schedule.run_date.strftime('%d/%m/%Y')}"


def describe_instant(moment: datetime, *, tz: ZoneInfo) -> str:
    """One instant as a Vietnamese wall-clock phrase."""
    local = moment.astimezone(tz)
    clock = local.strftime("%H:%M")
    return f"{clock} {weekday_label(local.weekday())}, ngày {local.strftime('%d/%m/%Y')}"
