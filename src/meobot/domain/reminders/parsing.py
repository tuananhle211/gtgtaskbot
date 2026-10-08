"""Reading a Vietnamese reminder out of a sentence, without a model.

Deliberately deterministic. A model asked for "the date in this sentence" is
right most of the time, and the times it is wrong the user finds out by the
reminder not arriving - which is precisely the failure this release exists to
end. It also costs a chat slot and a network round trip to compute something
that is arithmetic.

The parser consumes *spans*. Each schedule pattern that matches is blanked out
of the original text, and whatever survives is the thing to be reminded about.
That is why the content keeps its accents and its capitalisation: nothing is
reconstructed from the folded form, it is simply what was left over.

**One question, at most.** ``"4 giờ"`` genuinely does not say whether it means
morning or afternoon, and guessing puts a reminder twelve hours out. Hours 1-4
are treated as ambiguous and asked about; 5-11 read as morning, which is how
``"8 giờ"`` and ``"9 giờ"`` are meant in every example anybody writes. Anything
with an explicit ``sáng``/``chiều``/``tối`` or a 24-hour number is never
ambiguous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from meobot.domain.member.normalization import strip_accents
from meobot.domain.reminders.models import ScheduleKind
from meobot.domain.reminders.schedule import ReminderSchedule

#: Hours that could plausibly mean either half of the day when written bare.
#: Anything outside this reads as written.
AMBIGUOUS_HOURS: frozenset[int] = frozenset({1, 2, 3, 4})

MAX_CONTENT_LENGTH = 300


def _w(*spellings: str) -> str:
    """A regex alternation matching each spelling with or without accents.

    People type ``thứ`` and ``thu``, ``giờ`` and ``gio``, often in the same
    message. Longest-first so ``chiều`` wins over a prefix of itself.
    """
    variants = {form for spelling in spellings for form in (spelling, strip_accents(spelling))}
    return "(?:" + "|".join(sorted(variants, key=len, reverse=True)) + ")"


_EVERY = _w("mỗi", "hàng", "hằng")
_MERIDIEM = _w("sáng", "chiều", "tối", "trưa", "đêm")

#: "mỗi tháng" - recognised only so MeoBot can decline it honestly.
_MONTHLY = re.compile(rf"\b{_EVERY}\s+{_w('tháng')}\b", re.IGNORECASE)

#: "30 phút nữa", "2 tiếng nữa", "sau 5 phút", "sau 2 giờ".
#:
#: Vietnamese brackets a duration on either side: "sau X" before it, "X nữa"
#: after it, and often both ("sau 5 phút nữa"). Requiring the trailing "nữa"
#: meant "sau 5 phút" fell through to the clock parser, which read "2 giờ" in
#: "sau 2 giờ" as an ambiguous *time of day* and asked "sáng hay chiều?" about
#: a duration. So at least one marker is required, and either will do.
_RELATIVE = re.compile(
    rf"\b(?:({_w('sau')})\s+)?(\d{{1,4}})\s*"
    rf"({_w('phút')}|p|{_w('giờ')}|h(?![\w])|{_w('tiếng')})"
    rf"(?:\s+({_w('nữa')}))?\b",
    re.IGNORECASE,
)

#: "nửa tiếng nữa", "sau nửa tiếng" - a duration with no digit in it at all.
_HALF_HOUR = re.compile(
    rf"\b(?:{_w('sau')}\s+)?{_w('nửa')}\s+{_w('tiếng', 'giờ')}(?:\s+{_w('nữa')})?\b",
    re.IGNORECASE,
)

#: "hàng tuần", "mỗi tuần" - a recurrence marker that is not next to the weekday.
_WEEKLY_MARKER = re.compile(rf"\b{_EVERY}\s+{_w('tuần')}\b", re.IGNORECASE)

#: "mỗi ngày", "hằng ngày".
_DAILY = re.compile(rf"\b{_EVERY}\s+{_w('ngày')}\b", re.IGNORECASE)

_WEEKDAY_NAMES: dict[str, int] = {
    "hai": 0,
    "2": 0,
    "ba": 1,
    "3": 1,
    "tu": 2,
    "bon": 2,
    "4": 2,
    "nam": 3,
    "5": 3,
    "sau": 4,
    "6": 4,
    "bay": 5,
    "7": 5,
}

#: "mỗi thứ 5", "vào thứ Hai", "thứ Năm". The optional leading word is captured
#: so an adjacent "mỗi"/"hàng" counts as recurrence but "vào" does not.
_WEEKDAY = re.compile(
    rf"\b(?:({_EVERY}|{_w('vào')})\s+)?{_w('thứ')}\s*"
    rf"({_w('hai', 'ba', 'tư', 'bốn', 'năm', 'sáu', 'bảy')}|[2-7])\b",
    re.IGNORECASE,
)

_SUNDAY = re.compile(rf"\b(?:({_EVERY}|{_w('vào')})\s+)?{_w('chủ nhật')}\b", re.IGNORECASE)

#: "ngày 2 tháng 8", "ngày 02/08", "2/8/2026".
_EXPLICIT_DATE = re.compile(
    rf"\b(?:{_w('ngày')}\s*)?(\d{{1,2}})\s*(?:{_w('tháng')}|/|-)\s*(\d{{1,2}})"
    r"(?:\s*(?:/|-)\s*(\d{4}))?\b",
    re.IGNORECASE,
)

#: "mai", "ngày mai", "hôm nay", "ngày kia", "mốt".
_RELATIVE_DAY = re.compile(
    rf"\b(?:{_w('ngày')}\s+)?({_w('mai', 'mốt', 'kia')}|{_w('hôm nay')}|{_w('nay')})\b",
    re.IGNORECASE,
)

#: "lúc 16 giờ", "16h30", "22h40", "22:40", "22 giờ 40", "9 giờ sáng", "10h40 tối".
#:
#: The separator alternation is ordered longest-first and the ``h`` form allows
#: digits after it. The previous ``h(?![\w])`` refused "22h40" outright,
#: because ``4`` is a word character - which is why the most natural way a
#: Vietnamese speaker writes a time was the one format MeoBot could not read.
_TIME = re.compile(
    rf"\b(?:{_w('lúc', 'vào lúc')}\s+)?(\d{{1,2}})\s*(?:{_w('giờ')}|[hH:])"
    rf"\s*(\d{{1,2}})?\s*(?:{_w('phút')})?"
    rf"(?:\s*({_MERIDIEM}))?",
    re.IGNORECASE,
)

#: "sáng mai", "chiều mai", "tối mai" - a part of the day, with no clock.
#: Resolved to a conventional hour so the reminder has something to fire at,
#: and the preview shows that hour so the person can correct it.
_DAY_PART = re.compile(
    rf"\b({_MERIDIEM})\s+({_w('mai', 'mốt', 'kia')}|{_w('nay')})\b", re.IGNORECASE
)

#: Conventional hour for each part of the day.
DAY_PART_HOURS: dict[str, int] = {"sang": 8, "trua": 12, "chieu": 15, "toi": 20, "dem": 21}

#: The pronoun-preference clause people put in front of a request:
#: "Gọi chị xưng em nhé, Nhắc chị đi ngủ...". It is a real instruction about
#: address, not part of what the reminder says, so it is lifted out before the
#: content is worked out - otherwise it ends up *inside* the reminder text.
ADDRESS_PREFERENCE = re.compile(
    rf"{_w('gọi')}\s+\S+\s+{_w('xưng')}\s+(\S+?)\s*(?:{_w('nhé', 'nha', 'nhá')})?\s*[,.;]",
    re.IGNORECASE,
)

#: "nhắc tôi", "nhắc chị", "nhắc cho mình". Removed from the leftover text.
_REMIND_VERB = re.compile(
    rf"\b{_w('nhắc', 'nhắc nhở', 'nhớ')}\s*"
    rf"(?:{_w('cho', 'giúp', 'giùm')}\s+)?"
    rf"(?:{_w('tôi', 'chị', 'anh', 'em', 'mình', 'bạn', 'tớ')})?\b",
    re.IGNORECASE,
)

#: Connectives that can be left dangling once a schedule span is removed.
_DANGLING = re.compile(
    rf"^(?:{_w('là', 'rằng', 'lúc', 'vào', 'về việc', 'việc')}\b\s*)+", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class ReminderDraft:
    """A parsed but **uncommitted** reminder.

    A draft is not a reminder. Nothing in the system says "đã tạo" while one of
    these is the only thing that exists.
    """

    content: str
    schedule: ReminderSchedule
    #: True when the sentence named a schedule but nothing to be reminded of.
    needs_content: bool = False


@dataclass(frozen=True, slots=True)
class ParseResult:
    """What could be made of one sentence.

    Exactly one of ``draft``, ``ambiguous_hour`` and ``problem`` is meaningful.
    """

    draft: ReminderDraft | None = None
    #: The bare hour that needs one clarifying question.
    ambiguous_hour: int | None = None
    #: A Vietnamese sentence explaining why this cannot become a reminder.
    problem: str | None = None
    #: Minutes from now, when the person expressed a duration rather than a
    #: clock. Kept so the preview can say "Sau 5 phút" - the words they used.
    relative_minutes: int | None = None
    #: What the reminder is about, when the sentence named it but gave no time.
    #: "Nhắc tôi đi ngủ" is a real request with one thing missing, not a
    #: failure - returning the content lets the caller open a draft and ask.
    partial_content: str | None = None
    #: The form of address asked for in the same message, if any.
    address_preference: str | None = None

    @property
    def needs_question(self) -> bool:
        return self.ambiguous_hour is not None


class _Consumer:
    """Blanks matched spans out of the original, keeping what is left."""

    def __init__(self, text: str) -> None:
        self._original = text
        self._chars = list(text)

    def restore(self, start: int, end: int) -> None:
        """Undo a consumption, when a later rule turns out to own the span."""
        for index in range(start, end):
            self._chars[index] = self._original[index]

    def take(self, pattern: re.Pattern[str]) -> re.Match[str] | None:
        """Consume the first match, if any."""
        match = pattern.search(self.remaining)
        if match is None:
            return None
        self._blank(match.start(), match.end())
        return match

    @property
    def remaining(self) -> str:
        return "".join(self._chars)

    def _blank(self, start: int, end: int) -> None:
        for index in range(start, end):
            self._chars[index] = " "


def parse_reminder(text: str, *, now: datetime, tz: ZoneInfo) -> ParseResult:
    """Read a reminder out of ``text``.

    Args:
        text: Exactly what the person wrote.
        now: The moment to resolve "mai" and "30 phút nữa" against.
        tz: The timezone the wall clock in the sentence refers to.
    """
    from meobot.domain.reminders.models import UNSUPPORTED_MONTHLY

    if _MONTHLY.search(text):
        return ParseResult(problem=UNSUPPORTED_MONTHLY)

    consumer = _Consumer(text)
    local_now = now.astimezone(tz)

    # 0. A form-of-address clause is an instruction about *how to speak*, not
    #    part of what the reminder says. Lifting it out first is what keeps
    #    "Gọi chị xưng em nhé" from becoming the reminder's content.
    address = _take_address_preference(consumer)

    # 1. A duration is self-contained: it names an instant, and nothing else in
    #    the sentence can be a schedule.
    minutes = _take_duration(consumer, local_now)
    if minutes is not None:
        if minutes <= 0 or minutes > 60 * 24 * 365:
            return ParseResult(problem="Khoảng thời gian đó dài quá, TasksBot chưa đặt lịch được.")
        moment = (local_now + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
        schedule = ReminderSchedule(
            kind=ScheduleKind.ONE_TIME,
            local_time=moment.time(),
            run_date=moment.date(),
        )
        return _finish(consumer, schedule, relative_minutes=minutes, address=address)

    # 2. Recurrence, most specific first.
    weekly_marker = consumer.take(_WEEKLY_MARKER)
    daily_marker = consumer.take(_DAILY)
    weekday, weekday_is_recurring = _take_weekday(consumer)

    # 3. A part of the day ("sáng mai") is taken *before* the bare day word,
    #    because it owns both halves of the phrase: letting "mai" be consumed
    #    first leaves a stranded "sáng" and no hour at all.
    #
    #    Only when the sentence has no clock, though. In "3 giờ chiều mai" the
    #    "chiều" belongs to the clock it follows - taking it here would leave
    #    "3 giờ" looking ambiguous and ask a question with an obvious answer.
    day_part = None if _TIME.search(consumer.remaining) else consumer.take(_DAY_PART)

    # 4. An explicit or relative calendar day, for one-time reminders.
    explicit_date = _take_explicit_date(consumer, local_now)
    relative_day = (
        day_part
        if day_part is not None
        else (consumer.take(_RELATIVE_DAY) if explicit_date is None else None)
    )

    # 5. The wall clock.
    clock = consumer.take(_TIME)
    if clock is None and relative_day is None:
        return ParseResult(
            problem="TasksBot chưa rõ bạn muốn được nhắc lúc mấy giờ. Bạn nói giúp mình giờ nhé.",
            address_preference=address,
            partial_content=_content_of(consumer.remaining) or None,
        )

    if clock is None:
        # "sáng mai" names a part of the day; "ngày mai" names none at all.
        # Both get a conventional hour, which the preview shows so the person
        # can correct it - better than refusing a request they clearly made.
        part = strip_accents(day_part.group(1)) if day_part is not None else "sang"
        local_time = time(hour=DAY_PART_HOURS.get(part, 8))
    else:
        resolved = _resolve_clock(clock)
        if resolved is None:
            return ParseResult(
                problem="Giờ bạn nói không hợp lệ. Bạn kiểm tra lại giúp mình nhé.",
                address_preference=address,
            )
        local_time, ambiguous = resolved
        if ambiguous:
            return ParseResult(ambiguous_hour=local_time.hour, address_preference=address)

    # 5. Assemble. Recurrence wins over a calendar day: "mỗi thứ Năm" is a rule,
    #    not a date, even if the sentence also says "mai".
    if weekday is not None and (weekday_is_recurring or weekly_marker is not None):
        return _finish(
            consumer,
            ReminderSchedule(kind=ScheduleKind.WEEKLY, local_time=local_time, weekday=weekday),
            address=address,
        )
    if daily_marker is not None:
        return _finish(
            consumer,
            ReminderSchedule(kind=ScheduleKind.DAILY, local_time=local_time),
            address=address,
        )
    if weekday is not None:
        # A weekday with no recurrence marker: the next one of those.
        ahead = (weekday - local_now.weekday()) % 7
        day = local_now.date() + timedelta(days=ahead)
        if ahead == 0 and local_time <= local_now.time():
            day += timedelta(days=7)
        return _finish(
            consumer,
            ReminderSchedule(kind=ScheduleKind.ONE_TIME, local_time=local_time, run_date=day),
            address=address,
        )

    named_day = explicit_date or _relative_day_to_date(relative_day, local_now)
    if named_day is None:
        # Only a time of day: today if it is still ahead, tomorrow otherwise.
        named_day = local_now.date()
        if local_time <= local_now.time():
            named_day += timedelta(days=1)
    return _finish(
        consumer,
        ReminderSchedule(kind=ScheduleKind.ONE_TIME, local_time=local_time, run_date=named_day),
        address=address,
    )


def _finish(
    consumer: _Consumer,
    schedule: ReminderSchedule,
    *,
    relative_minutes: int | None = None,
    address: str | None = None,
) -> ParseResult:
    """Turn whatever survived consumption into the reminder's content."""
    content = _content_of(consumer.remaining)
    return ParseResult(
        draft=ReminderDraft(content=content, schedule=schedule, needs_content=not content),
        relative_minutes=relative_minutes,
        address_preference=address,
    )


def _content_of(leftover: str) -> str:
    """What is left after every schedule span and the verb are removed."""
    without_verb = _REMIND_VERB.sub(" ", leftover)
    collapsed = " ".join(without_verb.split())
    trimmed = _DANGLING.sub("", collapsed).strip(" ,.;:!?-")
    return trimmed[:MAX_CONTENT_LENGTH].strip()


def _take_duration(consumer: _Consumer, local_now: datetime) -> int | None:
    """How many minutes from now the person meant, or ``None`` for no duration.

    Requires a bracketing marker - a leading "sau" or a trailing "nữa". Without
    one, "lúc 2 giờ" would read as "in two hours" rather than "at two o\'clock",
    which is a twelve-hour error in the commonest case there is.
    """
    if consumer.take(_HALF_HOUR) is not None:
        return 30

    match = consumer.take(_RELATIVE)
    if match is None:
        return None
    leading, amount_text, unit_text, trailing = match.groups()
    if not leading and not trailing:
        # Neither "sau" nor "nữa": this is a clock, not a duration. Put the
        # span back so the time parser can have it.
        consumer.restore(match.start(), match.end())
        return None

    amount = int(amount_text)
    unit = strip_accents(unit_text)
    return amount if unit.startswith(("phut", "p")) else amount * 60


def _take_address_preference(consumer: _Consumer) -> str | None:
    """The form of address requested in the same message, if any."""
    match = consumer.take(ADDRESS_PREFERENCE)
    return match.group(1).strip() if match else None


def _take_weekday(consumer: _Consumer) -> tuple[int | None, bool]:
    """The weekday named in the sentence, and whether it was marked recurring."""
    sunday = consumer.take(_SUNDAY)
    if sunday is not None:
        return 6, _is_recurrence_marker(sunday.group(1))
    match = consumer.take(_WEEKDAY)
    if match is None:
        return None, False
    key = strip_accents(match.group(2))
    return _WEEKDAY_NAMES.get(key), _is_recurrence_marker(match.group(1))


def _is_recurrence_marker(word: str | None) -> bool:
    """ "mỗi"/"hàng" make a weekday recurring; "vào" does not."""
    if not word:
        return False
    return strip_accents(word) in {"moi", "hang"}


def _take_explicit_date(consumer: _Consumer, local_now: datetime) -> date | None:
    """ "ngày 2 tháng 8" / "02/08/2026" as a local date."""
    match = consumer.take(_EXPLICIT_DATE)
    if match is None:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    year = int(match.group(3)) if match.group(3) else local_now.year
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    if match.group(3) is None and parsed < local_now.date():
        # A day that has already passed this year means next year.
        try:
            parsed = date(year + 1, month, day)
        except ValueError:  # pragma: no cover - 29 February
            return None
    return parsed


def _relative_day_to_date(match: re.Match[str] | None, local_now: datetime) -> date | None:
    """ "mai" / "hôm nay" / "ngày kia" as a local date.

    Handles both shapes: the bare day word, and the day half of a part-of-day
    phrase like "sáng mai", whose day sits in the second group.
    """
    if match is None:
        return None
    groups = match.groups()
    word = strip_accents(groups[1] if len(groups) > 1 and groups[1] else groups[0])
    if word in {"mai"}:
        return local_now.date() + timedelta(days=1)
    if word in {"mot", "kia"}:
        return local_now.date() + timedelta(days=2)
    return local_now.date()


def _resolve_clock(match: re.Match[str]) -> tuple[time, bool] | None:
    """The wall clock a time phrase names, and whether it needs a question."""
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    if hour > 23 or minute > 59:
        return None

    meridiem = strip_accents(match.group(3)) if match.group(3) else ""
    if meridiem in {"chieu", "toi"}:
        hour = hour + 12 if hour < 12 else hour
    elif meridiem == "trua":
        hour = hour + 12 if hour < 11 else hour
    elif meridiem == "dem":
        # "1 giờ đêm" is 01:00; "10 giờ đêm" is 22:00.
        hour = hour if hour <= 4 else (hour + 12 if hour < 12 else hour)
    elif meridiem == "sang":
        hour = 0 if hour == 12 else hour
    elif hour in AMBIGUOUS_HOURS:
        return time(hour=hour, minute=minute), True

    if hour > 23:
        return None
    return time(hour=hour, minute=minute), False
