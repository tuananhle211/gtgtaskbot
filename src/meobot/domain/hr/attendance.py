"""Nhân sự đợt 3-4 (0100): đơn từ, phép năm, ca làm việc, bảng công - the maths.

Pure - no database. Decisions (10/11/2026, ``docs/hr/HR_MODULE_PLAN.md`` §6-7):

* **Đơn từ** = nghỉ (cả ngày / sáng / chiều / nhiều ngày, theo loại nghỉ),
  đi muộn, về sớm. A leave goes up the chain **Trưởng phòng → Giám đốc khối →
  Giám đốc → HCNS**; a late / early one only to the first of the three that
  has somebody, then HCNS ("2 cấp: Trưởng phòng hoặc … và HCNS").
  A level is skipped when nobody is on it but the requester, or when somebody
  who already decided the request sits on it (:func:`level_skipped`).
* **Phép năm theo luật** (Q9): 12 days a year + 1 per full 5 years worked,
  earned month by month from the official date (probation earns nothing),
  nothing carried over to the next year.
* **Ca** (Q12): per weekday a morning and/or an afternoon session; a person's
  shift = the first shift naming one of their departments, else their branch,
  else the default one (08:00-12:00, 13:30-17:30, Saturday morning, 5 minutes'
  grace).
* **Bảng công**: one day = its sessions (½ công each); a session is worked
  when the first punch-in / last punch-out cover it, on leave when an
  approved leave covers it; late / early minutes beyond the grace count
  unless an approved request covers them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from types import MappingProxyType

# --- requests ------------------------------------------------------------------------------


class AbsenceKind(StrEnum):
    LEAVE = "LEAVE"
    LATE = "LATE"
    EARLY = "EARLY"
    #: 0101 "Giải trình chấm công": forgot to punch in / out - the real times,
    #: with pictures as proof (screenshots of a web history, a chat…).
    EXPLAIN = "EXPLAIN"


KIND_LABELS: Mapping[AbsenceKind, str] = MappingProxyType(
    {
        AbsenceKind.LEAVE: "Nghỉ",
        AbsenceKind.LATE: "Đi muộn",
        AbsenceKind.EARLY: "Về sớm",
        AbsenceKind.EXPLAIN: "Giải trình chấm công",
    }
)

#: 0101: a "Giải trình chấm công" carries 1 to 5 pictures, each at most 5 MB.
MAX_EVIDENCE_FILES = 5
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024


class DayPart(StrEnum):
    FULL = "FULL"
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"


PART_LABELS: Mapping[DayPart, str] = MappingProxyType(
    {DayPart.FULL: "Cả ngày", DayPart.MORNING: "Buổi sáng", DayPart.AFTERNOON: "Buổi chiều"}
)


class AbsenceStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


STATUS_LABELS: Mapping[AbsenceStatus, str] = MappingProxyType(
    {
        AbsenceStatus.PENDING: "Đang duyệt",
        AbsenceStatus.APPROVED: "Đã duyệt",
        AbsenceStatus.REJECTED: "Không duyệt",
        AbsenceStatus.CANCELLED: "Đã huỷ",
    }
)


class StepLevel(StrEnum):
    MANAGER = "MANAGER"
    BLOCK = "BLOCK"
    DIRECTOR = "DIRECTOR"
    HR = "HR"


LEVEL_LABELS: Mapping[StepLevel, str] = MappingProxyType(
    {
        StepLevel.MANAGER: "Trưởng phòng",
        StepLevel.BLOCK: "Giám đốc khối",
        StepLevel.DIRECTOR: "Giám đốc",
        StepLevel.HR: "HCNS xác nhận",
    }
)


class StepStatus(StrEnum):
    WAITING = "WAITING"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"
    #: The request was cancelled / rejected before this step's turn.
    CLOSED = "CLOSED"


STEP_STATUS_LABELS: Mapping[StepStatus, str] = MappingProxyType(
    {
        StepStatus.WAITING: "Chưa tới lượt",
        StepStatus.PENDING: "Đang chờ",
        StepStatus.APPROVED: "Đã duyệt",
        StepStatus.REJECTED: "Không duyệt",
        StepStatus.SKIPPED: "Bỏ qua",
        StepStatus.CLOSED: "Không cần",
    }
)

MANAGEMENT_LEVELS: tuple[StepLevel, ...] = (StepLevel.MANAGER, StepLevel.BLOCK, StepLevel.DIRECTOR)


def plan_levels(
    kind: AbsenceKind,
    candidates: Mapping[StepLevel, Iterable[object]],
    requester: object,
) -> list[StepLevel]:
    """The levels a request goes through, in order.

    A leave: all four. A late / early one: the first management level with
    somebody on it but the requester, then HCNS (none = straight to HCNS)."""
    if kind is AbsenceKind.LEAVE:
        return [*MANAGEMENT_LEVELS, StepLevel.HR]
    for level in MANAGEMENT_LEVELS:
        if set(candidates.get(level, ())) - {requester}:
            return [level, StepLevel.HR]
    return [StepLevel.HR]


def level_skipped(
    approvers: Iterable[object], requester: object, deciders: Iterable[object]
) -> bool:
    """A level nobody but the requester is on, or one somebody who already
    decided the request sits on (their decision stands for it)."""
    people = set(approvers) - {requester}
    return not people or bool(people & set(deciders))


# --- money-free numbers --------------------------------------------------------------------


def half_up(value: Decimal | float, places: str = "0.1") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


# --- shifts --------------------------------------------------------------------------------

SESSIONS: tuple[str, ...] = ("morning", "afternoon")
SESSION_LABELS: Mapping[str, str] = MappingProxyType({"morning": "Sáng", "afternoon": "Chiều"})
WEEKDAY_LABELS: tuple[str, ...] = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN")
WEEKDAY_SHORT: tuple[str, ...] = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")


@dataclass(frozen=True, slots=True)
class Session:
    start: time
    end: time

    @property
    def minutes(self) -> int:
        return _minutes(self.end) - _minutes(self.start)


@dataclass(frozen=True, slots=True)
class Shift:
    """One working pattern: ``week[weekday] = {"morning": Session, ...}``."""

    name: str
    week: Mapping[int, Mapping[str, Session]]
    grace_minutes: int = 5

    def sessions(self, day: date) -> dict[str, Session]:
        return dict(self.week.get(day.weekday(), {}))

    def value(self, day: date) -> Decimal:
        """The day's công: ½ per session."""
        return Decimal("0.5") * len(self.sessions(day))


def _parse_clock(text: object) -> time:
    if isinstance(text, time):
        return text
    hours, minutes = str(text).strip().split(":")[:2]
    return time(int(hours), int(minutes))


def shift_from_json(name: str, week: object, grace_minutes: int) -> Shift:
    """``{"0": {"morning": ["08:00", "12:00"], ...}, ...}`` → :class:`Shift`
    (bad cells are dropped)."""
    parsed: dict[int, dict[str, Session]] = {}
    if isinstance(week, Mapping):
        for raw_day, sessions in week.items():
            try:
                weekday = int(raw_day)
            except (TypeError, ValueError):
                continue
            if not 0 <= weekday <= 6 or not isinstance(sessions, Mapping):
                continue
            day: dict[str, Session] = {}
            for key in SESSIONS:
                pair = sessions.get(key)
                if not isinstance(pair, Sequence) or len(pair) != 2:
                    continue
                try:
                    start, end = _parse_clock(pair[0]), _parse_clock(pair[1])
                except (TypeError, ValueError):
                    continue
                if start < end:
                    day[key] = Session(start, end)
            if day:
                parsed[weekday] = day
    return Shift(name=name, week=parsed, grace_minutes=max(0, int(grace_minutes)))


def week_errors(week: object) -> list[str]:
    """What is wrong with a shift's week as typed (empty = fine)."""
    problems: list[str] = []
    if not isinstance(week, Mapping):
        return ["Lịch tuần không hợp lệ."]
    for raw_day, sessions in week.items():
        try:
            weekday = int(raw_day)
        except (TypeError, ValueError):
            problems.append(f"Ngày {raw_day} không hợp lệ.")
            continue
        if not 0 <= weekday <= 6 or not isinstance(sessions, Mapping):
            problems.append(f"Ngày {raw_day} không hợp lệ.")
            continue
        times: list[Session] = []
        for key, pair in sessions.items():
            if key not in SESSIONS:
                problems.append(f"{WEEKDAY_LABELS[weekday]}: buổi {key} không hợp lệ.")
                continue
            if pair is None:
                continue
            try:
                start, end = _parse_clock(pair[0]), _parse_clock(pair[1])
            except (TypeError, ValueError, IndexError, KeyError):
                problems.append(f"{WEEKDAY_LABELS[weekday]}: giờ buổi {SESSION_LABELS[key]} sai.")
                continue
            if start >= end:
                problems.append(
                    f"{WEEKDAY_LABELS[weekday]}: buổi {SESSION_LABELS[key]} "
                    "phải bắt đầu trước khi kết thúc."
                )
            times.append(Session(start, end))
        if len(times) == 2 and times[0].end > times[1].start:
            problems.append(f"{WEEKDAY_LABELS[weekday]}: buổi sáng phải kết thúc trước buổi chiều.")
    return problems


def week_to_json(shift: Shift) -> dict[str, dict[str, list[str]]]:
    return {
        str(weekday): {
            key: [session.start.strftime("%H:%M"), session.end.strftime("%H:%M")]
            for key, session in sessions.items()
        }
        for weekday, sessions in sorted(shift.week.items())
    }


_FULL = {"morning": ["08:00", "12:00"], "afternoon": ["13:30", "17:30"]}
#: The default "Hành chính" shift (user 10/11): Mon-Fri 08:00-12:00 + 13:30-17:30,
#: Saturday morning, 5 minutes' grace.
DEFAULT_SHIFT_WEEK: Mapping[str, Mapping[str, list[str]]] = MappingProxyType(
    {**{str(day): _FULL for day in range(5)}, "5": {"morning": ["08:00", "12:00"]}}
)
DEFAULT_SHIFT_NAME = "Hành chính"
DEFAULT_GRACE_MINUTES = 5


def default_shift() -> Shift:
    return shift_from_json(DEFAULT_SHIFT_NAME, DEFAULT_SHIFT_WEEK, DEFAULT_GRACE_MINUTES)


def parts_of(part: DayPart) -> tuple[str, ...]:
    if part is DayPart.MORNING:
        return ("morning",)
    if part is DayPart.AFTERNOON:
        return ("afternoon",)
    return SESSIONS


def leave_days(
    start: date,
    end: date,
    part: DayPart,
    shift_of: object,
    holidays: Iterable[date] = (),
) -> Decimal:
    """Working days a leave covers: ½ per scheduled session it takes, never a
    holiday. ``shift_of(day) -> Shift``."""
    off = set(holidays)
    total = Decimal(0)
    day = start
    while day <= end:
        if day not in off:
            sessions = shift_of(day).sessions(day)  # type: ignore[operator]
            wanted = parts_of(part) if start == end else SESSIONS
            total += Decimal("0.5") * sum(1 for key in sessions if key in wanted)
        day += timedelta(days=1)
    return total


# --- leave types + annual leave ------------------------------------------------------------

ANNUAL_TYPE = "ANNUAL"


@dataclass(frozen=True, slots=True)
class LeaveTypeSeed:
    code: str
    name: str
    paid: bool
    annual: bool
    max_days: int | None
    symbol: str


#: Q9 / Bộ luật Lao động 2019 Điều 113, 115; HCNS edits them on "Danh mục".
LEAVE_TYPE_SEED: tuple[LeaveTypeSeed, ...] = (
    LeaveTypeSeed("ANNUAL", "Nghỉ phép năm", True, True, None, "P"),
    LeaveTypeSeed("SICK", "Nghỉ ốm (hưởng BHXH)", False, False, None, "Ô"),
    LeaveTypeSeed("UNPAID", "Nghỉ không lương", False, False, None, "KL"),
    LeaveTypeSeed("MARRIAGE", "Nghỉ kết hôn", True, False, 3, "R"),
    LeaveTypeSeed("CHILD_MARRIAGE", "Con kết hôn", True, False, 1, "R"),
    LeaveTypeSeed("BEREAVEMENT", "Nghỉ tang (bố mẹ, vợ/chồng, con)", True, False, 3, "R"),
    LeaveTypeSeed("COMPENSATORY", "Nghỉ bù", True, False, None, "NB"),
)

ANNUAL_BASE_DAYS = 12
SENIORITY_YEARS = 5


def full_years(since: date | None, on: date) -> int:
    if since is None or since > on:
        return 0
    years = on.year - since.year
    if (on.month, on.day) < (since.month, since.day):
        years -= 1
    return max(0, years)


def annual_entitlement(joined_on: date | None, year: int) -> int:
    """Days of annual leave for ``year``: 12 + 1 per full 5 years worked by its end."""
    return ANNUAL_BASE_DAYS + full_years(joined_on, date(year, 12, 31)) // SENIORITY_YEARS


def earning_months(official_on: date | None, left_on: date | None, year: int, until: date) -> int:
    """Months of ``year`` up to ``until`` that earn annual leave: official in
    them (a month counts when official by its 15th), not left before its 1st."""
    if official_on is None:
        return 0
    count = 0
    for month in range(1, 13):
        first = date(year, month, 1)
        if first > until:
            break
        if official_on > date(year, month, 15):
            continue
        if left_on is not None and left_on < first:
            continue
        count += 1
    return count


@dataclass(frozen=True, slots=True)
class AnnualBalance:
    year: int
    entitlement: int
    seniority_days: int
    months: int
    earned: Decimal
    adjustment: Decimal
    used: Decimal
    pending: Decimal

    @property
    def remaining(self) -> Decimal:
        """Earned so far + adjustments - taken (pending not counted)."""
        return self.earned + self.adjustment - self.used

    @property
    def year_remaining(self) -> Decimal:
        """What is left of the whole year's entitlement."""
        return Decimal(self.entitlement) + self.adjustment - self.used

    def as_dict(self) -> dict[str, object]:
        return {
            "year": self.year,
            "entitlement": self.entitlement,
            "seniority_days": self.seniority_days,
            "months": self.months,
            "earned": float(self.earned),
            "adjustment": float(self.adjustment),
            "used": float(self.used),
            "pending": float(self.pending),
            "remaining": float(self.remaining),
            "year_remaining": float(self.year_remaining),
        }


def annual_balance(
    *,
    year: int,
    today: date,
    joined_on: date | None,
    official_on: date | None,
    left_on: date | None,
    used: Decimal,
    pending: Decimal = Decimal(0),
    adjustment: Decimal = Decimal(0),
) -> AnnualBalance:
    entitlement = annual_entitlement(joined_on, year)
    until = (
        min(today, date(year, 12, 31)) if year <= today.year else date(year, 1, 1) - timedelta(1)
    )
    months = earning_months(official_on, left_on, year, until)
    return AnnualBalance(
        year=year,
        entitlement=entitlement,
        seniority_days=entitlement - ANNUAL_BASE_DAYS,
        months=months,
        earned=half_up(Decimal(entitlement) * months / 12),
        adjustment=adjustment,
        used=used,
        pending=pending,
    )


# --- one day of the timesheet --------------------------------------------------------------


class DaySymbol(StrEnum):
    WORK = "X"
    HALF = "X/2"
    HOLIDAY = "L"
    ABSENT = "V"
    MISSING = "!"
    OFF = "-"
    FUTURE = ""


SYMBOL_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "X": "Đi làm đủ",
        "X/2": "Đi làm nửa ngày",
        "L": "Nghỉ lễ",
        "V": "Vắng không phép",
        "!": "Thiếu giờ vào / ra",
        "-": "Không có ca",
        "P": "Nghỉ phép năm",
        "Ô": "Nghỉ ốm",
        "KL": "Nghỉ không lương",
        "R": "Nghỉ việc riêng có lương",
        "NB": "Nghỉ bù",
    }
)


@dataclass(frozen=True, slots=True)
class LeaveCover:
    """An approved leave's hold on one day."""

    sessions: tuple[str, ...]
    paid: bool
    symbol: str
    type_name: str


@dataclass(frozen=True, slots=True)
class DayResult:
    day: date
    expected: Decimal
    check_in: time | None
    check_out: time | None
    worked: Decimal
    paid_leave: Decimal
    unpaid_leave: Decimal
    holiday: Decimal
    absent: Decimal
    late: int
    early: int
    late_excused: int
    early_excused: int
    missing: bool
    symbol: str
    leave_names: tuple[str, ...] = ()
    edited: bool = False
    note: str | None = None
    #: 0101: an approved "Giải trình chấm công" supplied the times.
    explained: bool = False

    @property
    def late_counted(self) -> int:
        return max(0, self.late - self.late_excused)

    @property
    def early_counted(self) -> int:
        return max(0, self.early - self.early_excused)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def evaluate_day(
    *,
    day: date,
    shift: Shift,
    holiday: bool,
    check_in: time | None,
    check_out: time | None,
    leaves: Sequence[LeaveCover] = (),
    late_allowance: int = 0,
    early_allowance: int = 0,
    future: bool = False,
    edited: bool = False,
    note: str | None = None,
    explained: bool = False,
) -> DayResult:
    """One person's one day. ``late_allowance`` / ``early_allowance`` = the
    minutes approved "đi muộn" / "về sớm" requests cover."""
    sessions = shift.sessions(day)
    expected = Decimal("0.5") * len(sessions)
    half = Decimal("0.5")
    base = {
        "day": day,
        "expected": expected,
        "check_in": check_in,
        "check_out": check_out,
        "edited": edited,
        "note": note,
        "explained": explained,
    }
    if not sessions:
        # No shift that day (punches, if any, stay on the sheet, not counted).
        return DayResult(
            **base,  # type: ignore[arg-type]
            worked=Decimal(0),
            paid_leave=Decimal(0),
            unpaid_leave=Decimal(0),
            holiday=Decimal(0),
            absent=Decimal(0),
            late=0,
            early=0,
            late_excused=0,
            early_excused=0,
            missing=False,
            symbol=DaySymbol.OFF.value if not holiday else DaySymbol.HOLIDAY.value,
        )
    if holiday:
        return DayResult(
            **base,  # type: ignore[arg-type]
            worked=Decimal(0),
            paid_leave=Decimal(0),
            unpaid_leave=Decimal(0),
            holiday=expected,
            absent=Decimal(0),
            late=0,
            early=0,
            late_excused=0,
            early_excused=0,
            missing=False,
            symbol=DaySymbol.HOLIDAY.value,
        )
    on_leave: dict[str, LeaveCover] = {}
    for cover in leaves:
        for key in cover.sessions:
            if key in sessions and key not in on_leave:
                on_leave[key] = cover
    paid = half * sum(1 for cover in on_leave.values() if cover.paid)
    unpaid = half * sum(1 for cover in on_leave.values() if not cover.paid)
    to_work = [key for key in SESSIONS if key in sessions and key not in on_leave]
    leave_names = tuple(dict.fromkeys(cover.type_name for cover in on_leave.values()))
    leave_symbol = next((cover.symbol for cover in on_leave.values()), "")
    if not to_work:
        return DayResult(
            **base,  # type: ignore[arg-type]
            worked=Decimal(0),
            paid_leave=paid,
            unpaid_leave=unpaid,
            holiday=Decimal(0),
            absent=Decimal(0),
            late=0,
            early=0,
            late_excused=0,
            early_excused=0,
            missing=False,
            symbol=leave_symbol,
            leave_names=leave_names,
        )
    if check_in is None and check_out is None:
        absent = Decimal(0) if future else half * len(to_work)
        symbol = (
            DaySymbol.FUTURE.value
            if future
            else (
                leave_symbol + "/" + DaySymbol.ABSENT.value if on_leave else DaySymbol.ABSENT.value
            )
        )
        return DayResult(
            **base,  # type: ignore[arg-type]
            worked=Decimal(0),
            paid_leave=paid,
            unpaid_leave=unpaid,
            holiday=Decimal(0),
            absent=absent,
            late=0,
            early=0,
            late_excused=0,
            early_excused=0,
            missing=False,
            symbol=symbol,
            leave_names=leave_names,
        )
    if check_in is None or check_out is None or check_out <= check_in:
        return DayResult(
            **base,  # type: ignore[arg-type]
            worked=Decimal(0),
            paid_leave=paid,
            unpaid_leave=unpaid,
            holiday=Decimal(0),
            absent=Decimal(0),
            late=0,
            early=0,
            late_excused=0,
            early_excused=0,
            missing=True,
            symbol=DaySymbol.MISSING.value,
            leave_names=leave_names,
        )
    start, end = _minutes(check_in), _minutes(check_out)
    present = [
        key
        for key in to_work
        if start < _minutes(sessions[key].end) and end > _minutes(sessions[key].start)
    ]
    absent = half * (len(to_work) - len(present))
    late = early = 0
    if present:
        first = sessions[present[0]].start
        last = sessions[present[-1]].end
        gap = start - _minutes(first)
        late = gap if gap > shift.grace_minutes else 0
        gap = _minutes(last) - end
        early = gap if gap > shift.grace_minutes else 0
    worked = half * len(present)
    if worked == expected:
        symbol = DaySymbol.WORK.value
    elif worked:
        symbol = DaySymbol.HALF.value if not on_leave else f"{leave_symbol}/X"
    else:
        symbol = DaySymbol.ABSENT.value
    return DayResult(
        **base,  # type: ignore[arg-type]
        worked=worked,
        paid_leave=paid,
        unpaid_leave=unpaid,
        holiday=Decimal(0),
        absent=absent,
        late=late,
        early=early,
        late_excused=min(late, late_allowance),
        early_excused=min(early, early_allowance),
        missing=False,
        symbol=symbol,
        leave_names=leave_names,
    )


@dataclass(slots=True)
class MonthTotals:
    standard: Decimal = Decimal(0)
    worked: Decimal = Decimal(0)
    paid_leave: Decimal = Decimal(0)
    unpaid_leave: Decimal = Decimal(0)
    holiday: Decimal = Decimal(0)
    absent: Decimal = Decimal(0)
    late_count: int = 0
    late_minutes: int = 0
    early_count: int = 0
    early_minutes: int = 0
    missing: int = 0
    leave_by_type: dict[str, Decimal] = field(default_factory=dict)

    @property
    def payable(self) -> Decimal:
        """Công tính lương = đi làm + nghỉ có lương + lễ."""
        return self.worked + self.paid_leave + self.holiday

    def add(self, result: DayResult) -> None:
        self.standard += result.expected
        self.worked += result.worked
        self.paid_leave += result.paid_leave
        self.unpaid_leave += result.unpaid_leave
        self.holiday += result.holiday
        self.absent += result.absent
        if result.late_counted:
            self.late_count += 1
            self.late_minutes += result.late_counted
        if result.early_counted:
            self.early_count += 1
            self.early_minutes += result.early_counted
        if result.missing:
            self.missing += 1

    def as_dict(self) -> dict[str, object]:
        return {
            "standard": float(self.standard),
            "worked": float(self.worked),
            "paid_leave": float(self.paid_leave),
            "unpaid_leave": float(self.unpaid_leave),
            "holiday": float(self.holiday),
            "absent": float(self.absent),
            "payable": float(self.payable),
            "late_count": self.late_count,
            "late_minutes": self.late_minutes,
            "early_count": self.early_count,
            "early_minutes": self.early_minutes,
            "missing": self.missing,
        }


def month_days(month: date) -> list[date]:
    first = month.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return [first + timedelta(days=offset) for offset in range((following - first).days)]


def minutes_between(earlier: time, later: time) -> int:
    return _minutes(later) - _minutes(earlier)


def clock_text(value: time | None) -> str | None:
    return None if value is None else value.strftime("%H:%M")


def as_time(value: datetime | time) -> time:
    return value.time() if isinstance(value, datetime) else value


__all__ = [
    "ANNUAL_TYPE",
    "DEFAULT_SHIFT_NAME",
    "DEFAULT_SHIFT_WEEK",
    "KIND_LABELS",
    "LEAVE_TYPE_SEED",
    "LEVEL_LABELS",
    "MANAGEMENT_LEVELS",
    "MAX_EVIDENCE_BYTES",
    "MAX_EVIDENCE_FILES",
    "PART_LABELS",
    "SESSIONS",
    "STATUS_LABELS",
    "STEP_STATUS_LABELS",
    "SYMBOL_LABELS",
    "WEEKDAY_LABELS",
    "AbsenceKind",
    "AbsenceStatus",
    "AnnualBalance",
    "DayPart",
    "DayResult",
    "LeaveCover",
    "MonthTotals",
    "Shift",
    "StepLevel",
    "StepStatus",
    "annual_balance",
    "annual_entitlement",
    "default_shift",
    "evaluate_day",
    "leave_days",
    "level_skipped",
    "month_days",
    "plan_levels",
    "shift_from_json",
    "week_errors",
    "week_to_json",
]
