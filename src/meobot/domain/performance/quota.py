"""The points quota ("định mức", 2026-10-29), with no I/O.

From the department's sheet: 1 point = 1 minute of standard work, so a work
day is ``points_per_hour x hours_per_day`` points (60 x 8 = 480). A person is
expected to score ``quota_pct`` of that (85% by default: the rest is meetings,
briefs, admin) on each standard work day. The standard work days of a month
are its days minus every Sunday and half of every Saturday.

    Oct 2026: 31 - 4 Sundays - 5 x 0.5 Saturdays = 24.5 days
    quota = 24.5 x 480 x 85% = 9,996 points
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

SUNDAY = 6
SATURDAY = 5
HALF = Decimal("0.5")

DEFAULT_POINTS_PER_HOUR = 60
DEFAULT_HOURS_PER_DAY = 8
DEFAULT_QUOTA_PCT = 85

#: The week buckets of the reports (days 1-7, 8-14, 15-21, 22-28, 29-end).
WEEK_BUCKETS = ((1, 7), (8, 14), (15, 21), (22, 28), (29, 31))


@dataclass(frozen=True, slots=True)
class QuotaSettings:
    points_per_hour: int = DEFAULT_POINTS_PER_HOUR
    hours_per_day: int = DEFAULT_HOURS_PER_DAY
    #: The default share of a work day a person is expected to score, in %.
    quota_pct: int = DEFAULT_QUOTA_PCT

    @property
    def points_per_day(self) -> int:
        return self.points_per_hour * self.hours_per_day

    @property
    def daily_quota(self) -> Decimal:
        return Decimal(self.points_per_day) * Decimal(self.quota_pct) / 100


def day_weight(day: date) -> Decimal:
    """How much of a standard work day a calendar day is."""
    if day.weekday() == SUNDAY:
        return Decimal(0)
    if day.weekday() == SATURDAY:
        return HALF
    return Decimal(1)


def work_days(first: date, last: date) -> Decimal:
    """Standard work days from ``first`` to ``last``, both included."""
    total = Decimal(0)
    day = first
    while day <= last:
        total += day_weight(day)
        day += timedelta(days=1)
    return total


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def points(days: Decimal, settings: QuotaSettings) -> int:
    """The quota of ``days`` standard work days, rounded half up."""
    return int((days * settings.daily_quota).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class MonthQuota:
    """One month's quota for one person."""

    month: str
    settings: QuotaSettings
    work_days: Decimal
    monthly: int
    #: What should be scored by today (the whole month once it is over,
    #: nothing before it starts).
    to_date: int
    #: The quota of each week bucket of the reports.
    by_week: tuple[int, ...]


def month_quota(year: int, month: int, settings: QuotaSettings, *, today: date) -> MonthQuota:
    first, last = month_bounds(year, month)
    days = work_days(first, last)
    if today < first:
        elapsed = Decimal(0)
    elif today >= last:
        elapsed = days
    else:
        elapsed = work_days(first, today)
    weeks = tuple(
        points(work_days(date(year, month, start), date(year, month, min(end, last.day))), settings)
        if start <= last.day
        else 0
        for start, end in WEEK_BUCKETS
    )
    return MonthQuota(
        month=f"{year:04d}-{month:02d}",
        settings=settings,
        work_days=days,
        monthly=points(days, settings),
        to_date=points(elapsed, settings),
        by_week=weeks,
    )


__all__ = [
    "DEFAULT_HOURS_PER_DAY",
    "DEFAULT_POINTS_PER_HOUR",
    "DEFAULT_QUOTA_PCT",
    "MonthQuota",
    "QuotaSettings",
    "day_weight",
    "month_bounds",
    "month_quota",
    "points",
    "work_days",
]
