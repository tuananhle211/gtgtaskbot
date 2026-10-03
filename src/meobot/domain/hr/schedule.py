"""Working hours, and the arithmetic that turns Vietnamese into timestamps.

**No model touches any of this.** "Ngày mai", "sáng thứ Sáu", "9 giờ tôi có
mặt", "đi muộn 30 phút" all become concrete UTC instants here, by calendar
arithmetic against a configured schedule. Asking an LLM what date tomorrow is
would be slower, cost a chat slot, and occasionally be wrong - and being wrong
about which day somebody is absent is not a small error.

**Local in, UTC out.** People think in ``Asia/Ho_Chi_Minh``; the database stores
UTC. Every conversion happens in this module so the two can never be mixed up
somewhere else.

**Never invent the office hours.** If no schedule is configured,
:func:`resolve_late_minutes` refuses rather than assuming 08:00. MeoBot says so
and asks - guessing when somebody's workday starts would silently produce wrong
lateness figures in a report the whole department sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from meobot.domain.hr.models import HrRequestType
from meobot.domain.member.normalization import strip_accents

#: Vietnamese weekday names, Monday first. ``thứ hai`` is Monday, and Sunday is
#: ``chủ nhật`` rather than "thứ tám".
WEEKDAY_WORDS: dict[str, int] = {
    "thu hai": 0,
    "thu ba": 1,
    "thu tu": 2,
    "thu nam": 3,
    "thu sau": 4,
    "thu bay": 5,
    "chu nhat": 6,
}

#: ``ngày 2 tháng 8`` / ``2/8`` / ``02-08``.
_DAY_MONTH_WORDS = re.compile(r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})")
_DAY_MONTH_SLASH = re.compile(r"\b(\d{1,2})\s*[/-]\s*(\d{1,2})(?:\s*[/-]\s*(\d{4}))?\b")

#: ``9 gio``, ``9 gio 30`` - already expanded from ``9h30`` by the normalizer.
_CLOCK = re.compile(r"\b(\d{1,2})\s*gio(?:\s+(\d{1,2}))?\b")
#: ``30 phut`` - already expanded from ``30p``.
_MINUTES = re.compile(r"\b(\d{1,3})\s*phut\b")


@dataclass(frozen=True, slots=True)
class WorkSchedule:
    """When this organisation works.

    Deliberately a value object rather than a row: the service loads the active
    row and hands one of these to the arithmetic, so every function here is
    pure and directly testable against any schedule.
    """

    timezone: str = "Asia/Ho_Chi_Minh"
    #: Monday=0 … Sunday=6.
    working_days: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    morning_start: time = time(8, 30)
    morning_end: time = time(12, 0)
    afternoon_start: time = time(13, 30)
    afternoon_end: time = time(17, 30)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def day_start(self) -> time:
        """When the working day begins - what lateness is measured against."""
        return self.morning_start

    def is_working_day(self, day: date) -> bool:
        return day.weekday() in self.working_days

    def local(self, day: date, moment: time) -> datetime:
        """A local wall-clock instant on ``day``."""
        return datetime.combine(day, moment, tzinfo=self.zone)

    def utc(self, day: date, moment: time) -> datetime:
        """The same instant, converted to UTC for storage."""
        from datetime import UTC

        return self.local(day, moment).astimezone(UTC)


#: What MeoBot falls back to for *display* only. It is never used to compute
#: lateness - see :func:`resolve_late_minutes`.
DEFAULT_SCHEDULE = WorkSchedule()


def today_local(now: datetime, schedule: WorkSchedule) -> date:
    """The local calendar date ``now`` falls on."""
    return now.astimezone(schedule.zone).date()


def resolve_date(text: str, *, now: datetime, schedule: WorkSchedule) -> date | None:
    """Read a date out of a Vietnamese sentence, or return ``None``.

    Handles the forms people actually use: "hôm nay", "ngày mai", "mai", "ngày
    kia", a weekday name ("thứ sáu" - the *next* one, today included), an
    explicit "ngày 2 tháng 8", and "2/8". Anything else returns ``None`` and the
    caller asks, rather than guessing.

    ``text`` is expected to be accent-folded already; it is folded again here so
    the function is safe to call directly.
    """
    folded = strip_accents(text)
    today = today_local(now, schedule)

    if "hom nay" in folded or "bua nay" in folded:
        return today
    if "ngay kia" in folded or "ngay mot" in folded:
        return today + timedelta(days=2)
    if "ngay mai" in folded or re.search(r"\bmai\b", folded):
        return today + timedelta(days=1)
    if "hom qua" in folded:
        return today - timedelta(days=1)

    explicit = _DAY_MONTH_WORDS.search(folded)
    if explicit is not None:
        return _safe_date(today.year, int(explicit.group(2)), int(explicit.group(1)), today)

    slashed = _DAY_MONTH_SLASH.search(folded)
    if slashed is not None:
        year = int(slashed.group(3)) if slashed.group(3) else today.year
        return _safe_date(year, int(slashed.group(2)), int(slashed.group(1)), today)

    for word, weekday in WEEKDAY_WORDS.items():
        if word in folded:
            return _next_weekday(today, weekday)
    return None


def _safe_date(year: int, month: int, day: int, today: date) -> date | None:
    """Build a date, rolling to next year when the day has already passed.

    "Ngày 2 tháng 8" said in December means next August, not one four months
    ago - people do not file leave for the past.
    """
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if candidate < today and candidate.year == today.year:
        try:
            return date(year + 1, month, day)
        except ValueError:  # pragma: no cover - 29 February
            return None
    return candidate


def _next_weekday(today: date, weekday: int) -> date:
    """The next occurrence of ``weekday``, today counting as itself."""
    ahead = (weekday - today.weekday()) % 7
    return today + timedelta(days=ahead)


def resolve_period(text: str) -> HrRequestType | None:
    """Read morning / afternoon / all day out of a sentence.

    Returns ``None`` when nothing was said, which is what makes MeoBot ask
    "Bạn muốn nghỉ khoảng thời gian nào?" instead of assuming a whole day.
    """
    folded = strip_accents(text)
    if "ca ngay" in folded or "nguyen ngay" in folded or "tron ngay" in folded:
        return HrRequestType.FULL_DAY_LEAVE
    if "buoi sang" in folded or re.search(r"\bsang\s+(mai|nay|thu)", folded):
        return HrRequestType.MORNING_LEAVE
    if "buoi chieu" in folded or re.search(r"\bchieu\s+(mai|nay|thu)", folded):
        return HrRequestType.AFTERNOON_LEAVE
    if "mot ngay" in folded:
        return HrRequestType.FULL_DAY_LEAVE
    return None


def resolve_clock(text: str) -> time | None:
    """Read a time of day, or ``None``.

    Expects the normalizer to have turned ``9h30`` into ``9 gio 30`` already.
    """
    match = _CLOCK.search(strip_accents(text))
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def resolve_minutes(text: str) -> int | None:
    """Read a duration in minutes, or ``None``."""
    match = _MINUTES.search(strip_accents(text))
    if match is None:
        return None
    minutes = int(match.group(1))
    return minutes if 0 < minutes <= 600 else None


def resolve_hourly_range(
    text: str, *, day: date, schedule: WorkSchedule
) -> tuple[datetime, datetime] | None:
    """Read "từ 14 giờ đến 17 giờ" into a concrete local interval."""
    folded = strip_accents(text)
    clocks = _CLOCK.findall(folded)
    if len(clocks) < 2:
        return None
    start = time(int(clocks[0][0]), int(clocks[0][1] or 0))
    end = time(int(clocks[1][0]), int(clocks[1][1] or 0))
    if start >= end:
        return None
    return schedule.local(day, start), schedule.local(day, end)


def leave_interval(
    request_type: HrRequestType,
    *,
    day: date,
    schedule: WorkSchedule,
    end_day: date | None = None,
) -> tuple[datetime, datetime]:
    """The local start and end of one leave request.

    A morning is the configured morning; an afternoon is the configured
    afternoon. That is why a schedule has four times rather than two: an office
    with a long lunch should not have "nghỉ buổi sáng" silently include it.
    """
    if request_type is HrRequestType.MORNING_LEAVE:
        return schedule.local(day, schedule.morning_start), schedule.local(
            day, schedule.morning_end
        )
    if request_type is HrRequestType.AFTERNOON_LEAVE:
        return schedule.local(day, schedule.afternoon_start), schedule.local(
            day, schedule.afternoon_end
        )
    if request_type is HrRequestType.MULTI_DAY_LEAVE and end_day is not None:
        return schedule.local(day, schedule.morning_start), schedule.local(
            end_day, schedule.afternoon_end
        )
    return schedule.local(day, schedule.morning_start), schedule.local(day, schedule.afternoon_end)


def resolve_late_minutes(
    *,
    arrival: time | None,
    minutes: int | None,
    schedule: WorkSchedule | None,
) -> tuple[time | None, int | None]:
    """Reconcile "9 giờ tôi có mặt" and "đi muộn 30 phút" into both numbers.

    Whichever the person said, the other is derived - but **only when a schedule
    exists**. With no configured work-start time there is no honest way to turn
    "30 phút" into an arrival time or the reverse, so this returns what it was
    given and the caller asks the question.

    Returns ``(arrival_time, late_minutes)``.
    """
    if schedule is None:
        return arrival, minutes

    start = schedule.day_start
    if arrival is not None and minutes is None:
        delta = _minutes_between(start, arrival)
        return arrival, max(0, delta)
    if minutes is not None and arrival is None:
        base = datetime.combine(date(2000, 1, 1), start) + timedelta(minutes=minutes)
        return base.time(), minutes
    if arrival is not None and minutes is not None:
        # Both stated. The arrival time is the one that gets checked at the
        # door, so it wins and the duration is recomputed from it.
        return arrival, max(0, _minutes_between(start, arrival))
    return None, None


def _minutes_between(start: time, end: time) -> int:
    """Whole minutes from ``start`` to ``end`` on the same day."""
    first = datetime.combine(date(2000, 1, 1), start)
    second = datetime.combine(date(2000, 1, 1), end)
    return int((second - first) / timedelta(minutes=1))


def format_local_date(value: date) -> str:
    """``31/07/2026`` - the way a date is written in Vietnamese."""
    return value.strftime("%d/%m/%Y")


def format_local_time(value: datetime, schedule: WorkSchedule) -> str:
    """``09:00`` in the organisation's timezone."""
    return value.astimezone(schedule.zone).strftime("%H:%M")


def format_local_datetime(value: datetime, schedule: WorkSchedule) -> str:
    """``15:42 ngày 30/07/2026``."""
    local = value.astimezone(schedule.zone)
    return f"{local.strftime('%H:%M')} ngày {local.strftime('%d/%m/%Y')}"


def describe_period(request_type: HrRequestType, *, day: date, end_day: date | None = None) -> str:
    """How a leave period reads on a card: "Sáng ngày 31/07/2026"."""
    when = format_local_date(day)
    if request_type is HrRequestType.MORNING_LEAVE:
        return f"Sáng ngày {when}"
    if request_type is HrRequestType.AFTERNOON_LEAVE:
        return f"Chiều ngày {when}"
    if request_type is HrRequestType.MULTI_DAY_LEAVE and end_day is not None:
        return f"Từ ngày {when} đến ngày {format_local_date(end_day)}"
    if request_type is HrRequestType.HOURLY_LEAVE:
        return f"Ngày {when}"
    return f"Cả ngày {when}"
