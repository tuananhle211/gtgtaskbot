"""Recurring work: the template's vocabulary, its calendar, and its identity.

Milestone M4B. Pure module - no session, no model, no Celery - so the two
things that are genuinely hard here can be tested as arithmetic:

* **which instant is next**, computed in the department's own wall clock; and
* **what that occurrence is called**, which is what makes generating it twice
  structurally impossible rather than merely unlikely.

Why there is no enumeration primitive here
-------------------------------------------

:meth:`RecurringSchedule.next_after` answers *"what fires next"* and nothing
else. It deliberately does **not** answer *"which occurrences did we miss
between Tuesday and Friday"*, and the sweeper does not ask: it walks forward one
``next_after`` at a time from a stored cursor, recording each occurrence
durably before it asks for the following one - see
:mod:`meobot.application.pr_work_recurring_generator`.

That is not a limitation being worked around. An enumerator would have to
decide, inside a pure function with no database, what to do about an occurrence
that was already generated, one that fell in a closed month and one that failed
half way; walking forward from a cursor puts every one of those decisions in the
place that can actually record the answer.

The three schedules, and why not RRULE
---------------------------------------

``DAILY``, ``WEEKLY`` over a chosen set of weekdays, and ``MONTHLY`` on a chosen
day. :mod:`meobot.domain.reminders.schedule` made the same call for the same
reason and its docstring says it out loud: storing a full RRULE would be a
promise to run schedules this system cannot actually run. The daily and weekly
cases here are computed *by* that module - ``RecurringSchedule`` composes
:class:`~meobot.domain.reminders.schedule.ReminderSchedule` rather than
reimplementing the wall-clock arithmetic - so there is one recurrence engine in
MeoBot and M4B is a second caller of it, not a second copy.

``ReminderSchedule`` itself is untouched. Widening its ``WEEKLY`` to carry
several weekdays would change the meaning of ``recurrence_rule`` strings already
stored against live reminders, which is a migration with a behaviour change
attached and nothing to do with recurring work.
"""

from __future__ import annotations

import calendar
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from meobot.domain.pr.errors import PrValidationError
from meobot.domain.reminders.models import ScheduleKind, weekday_label
from meobot.domain.reminders.schedule import ReminderSchedule


class PrRecurringTemplateStatus(StrEnum):
    """How far a recurring template is through its own life.

    Four states, and only one of them generates work. The pair that has to stay
    distinguishable is :attr:`PAUSED` and :attr:`ENDED`: a pause is a decision
    somebody expects to undo, and ending is a decision about a routine that is
    over. Collapsing them would make "resume" either impossible or a way to
    restart a routine that was deliberately retired.
    """

    #: Being written. Generates nothing, and may still be deleted outright
    #: because nothing has been produced from it.
    DRAFT = "DRAFT"
    #: Live. **The only state that generates work**, and the state that carries
    #: management's standing authorization for everything it produces.
    ACTIVE = "ACTIVE"
    #: Deliberately suspended. Generates nothing, and the interval spent here is
    #: never backfilled on resume - see
    #: :class:`PrRecurringOccurrenceState`.
    PAUSED = "PAUSED"
    #: Over. Terminal: a routine that is finished is not resumed, it is copied.
    ENDED = "ENDED"


#: Which template transitions are legal. Missing key means "from nowhere".
RECURRING_TEMPLATE_TRANSITIONS: dict[
    PrRecurringTemplateStatus, frozenset[PrRecurringTemplateStatus]
] = {
    PrRecurringTemplateStatus.DRAFT: frozenset(
        {PrRecurringTemplateStatus.ACTIVE, PrRecurringTemplateStatus.ENDED}
    ),
    PrRecurringTemplateStatus.ACTIVE: frozenset(
        {PrRecurringTemplateStatus.PAUSED, PrRecurringTemplateStatus.ENDED}
    ),
    PrRecurringTemplateStatus.PAUSED: frozenset(
        {PrRecurringTemplateStatus.ACTIVE, PrRecurringTemplateStatus.ENDED}
    ),
    PrRecurringTemplateStatus.ENDED: frozenset(),
}

#: Statuses a template may still be edited from. ``ACTIVE`` is deliberately in
#: the set - a routine whose quantity changed on Monday is normal operations -
#: and every edit bumps ``revision_no`` so an occurrence records which version
#: of the template produced it.
EDITABLE_TEMPLATE_STATUSES: frozenset[PrRecurringTemplateStatus] = frozenset(
    {
        PrRecurringTemplateStatus.DRAFT,
        PrRecurringTemplateStatus.ACTIVE,
        PrRecurringTemplateStatus.PAUSED,
    }
)


class PrRecurringFrequency(StrEnum):
    """How often a template fires. Three shapes, honestly supported."""

    DAILY = "DAILY"
    #: One or more chosen weekdays. Monday is 0, matching ``date.weekday()``
    #: and :class:`~meobot.domain.reminders.schedule.ReminderSchedule`.
    WEEKLY = "WEEKLY"
    #: A chosen day of the month, clamped to the last day in months that are
    #: shorter - see :meth:`RecurringSchedule.next_after`.
    MONTHLY = "MONTHLY"


class PrRecurringOccurrenceState(StrEnum):
    """What happened to one scheduled occurrence. **The scheduler's ledger.**

    Four states, and the set is deliberately this small. Each one exists to make
    a different failure impossible:

    * :attr:`PENDING` - reserved and not yet turned into work. Written in its
      own committed transaction *before* any work exists, so a worker that dies
      mid-generation leaves a row saying "this was owed" rather than nothing;
    * :attr:`GENERATED` - the work exists. Written in the **same** transaction
      as the :class:`~meobot.db.models.pr_work.PrWorkItem` rows, so there is no
      instant at which the ledger claims work that is not there;
    * :attr:`SKIPPED_CLOSED_PERIOD` - **terminal**, and the reason the
      scheduler cannot loop. The occurrence fell in a reporting period that has
      been closed or locked; generating into it would move a number underneath a
      report that already quotes it, and retrying tomorrow would fail for exactly
      the same reason for ever;
    * :attr:`FAILED_RETRYABLE` - the attempt did not finish and the reason may
      not still be true. Retried by the next sweep, from the retry set rather
      than by rewinding the cursor.

    **There is no state for "skipped because the template was paused."** A pause
    is not an occurrence that happened and was declined; it is an interval the
    scheduler never walks. Resuming moves the cursor to the resume instant, so
    the paused window is never traversed and produces no rows at all - which is
    exactly what distinguishes it from worker downtime, where the cursor stays
    put and the missed occurrences are walked and generated. Materialising a
    skip row per occurrence would also mean a template paused for a year owed
    the database three hundred and sixty five rows saying nothing happened.

    **There is no state for "not yet evaluated"** either, for the same kind of
    reason: that is the absence of a row, bounded by the cursor.
    """

    PENDING = "PENDING"
    GENERATED = "GENERATED"
    SKIPPED_CLOSED_PERIOD = "SKIPPED_CLOSED_PERIOD"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"


#: Occurrence states the sweeper must come back to. Everything else is settled.
UNRESOLVED_OCCURRENCE_STATES: frozenset[PrRecurringOccurrenceState] = frozenset(
    {PrRecurringOccurrenceState.PENDING, PrRecurringOccurrenceState.FAILED_RETRYABLE}
)

#: Occurrence states that will never be attempted again.
TERMINAL_OCCURRENCE_STATES: frozenset[PrRecurringOccurrenceState] = frozenset(
    {PrRecurringOccurrenceState.GENERATED, PrRecurringOccurrenceState.SKIPPED_CLOSED_PERIOD}
)

#: How many occurrences one template may be advanced by in a single sweep.
#:
#: Bounded because catch-up after a long outage is the case this number exists
#: for: a template that has been active and unswept for three months must not
#: try to generate ninety days of work inside one transaction batch. It catches
#: up over several sweeps instead, which is slower and cannot time out.
MAX_OCCURRENCES_PER_SWEEP = 30

#: How far back catch-up will look at all, regardless of the cursor.
#:
#: A separate bound from :data:`MAX_OCCURRENCES_PER_SWEEP` because they answer
#: different questions: that one limits a batch, this one limits *history*.
#: Work that was owed four months ago and never generated is not work anybody
#: wants filed today, and the reporting period it belonged to is closed anyway.
MAX_CATCH_UP_DAYS = 45

#: The longest a template name may be. Matches the column and, deliberately,
#: :class:`~meobot.db.models.pr_work.PrWorkItem`'s ``title``: the name *becomes*
#: the title of every item generated, so a name that fitted here and not there
#: would be a template that generated work it could not name.
MAX_TEMPLATE_NAME = 300

#: How many people one template may name. The same bound as
#: ``MAX_CONTRIBUTORS``, and the same reason.
MAX_TEMPLATE_CONTRIBUTORS = 20


@dataclass(frozen=True, slots=True)
class RecurringSchedule:
    """When a template fires, in the department's own wall clock.

    Args:
        frequency: Which of the three shapes.
        run_time: The local wall clock the occurrence is stamped at. "Giờ tạo".
        weekdays: Monday=0. Required and non-empty for ``WEEKLY``, ignored
            otherwise.
        day_of_month: 1-31. Required for ``MONTHLY``, ignored otherwise, and
            clamped to the last day of shorter months.
    """

    frequency: PrRecurringFrequency
    run_time: time
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None

    def __post_init__(self) -> None:
        if self.frequency is PrRecurringFrequency.WEEKLY:
            if not self.weekdays:
                raise PrValidationError(
                    "Hãy chọn ít nhất một ngày trong tuần.",
                    details={"field": "weekdays", "reason": "weekdays_required"},
                )
            if any(day < 0 or day > 6 for day in self.weekdays):
                raise PrValidationError(
                    "Ngày trong tuần không hợp lệ.",
                    details={"field": "weekdays", "reason": "weekday_out_of_range"},
                )
        if self.frequency is PrRecurringFrequency.MONTHLY and (
            self.day_of_month is None or not 1 <= self.day_of_month <= 31
        ):
            raise PrValidationError(
                "Hãy chọn ngày trong tháng từ 1 đến 31.",
                details={"field": "day_of_month", "reason": "day_of_month_out_of_range"},
            )

    # -----------------------------------------------------------------
    # The one forward primitive
    # -----------------------------------------------------------------
    def next_after(self, moment: datetime, *, tz: ZoneInfo) -> datetime:
        """The first firing **strictly after** ``moment``, as a UTC instant.

        Strictly after, because that is what makes it safe to feed its own
        output back in: ``next_after(next_after(t))`` is the occurrence after
        next and never the same one twice, which is the whole basis of the
        sweeper's traversal.

        ``DAILY`` and ``WEEKLY`` are computed by
        :class:`~meobot.domain.reminders.schedule.ReminderSchedule` - one
        weekday at a time for ``WEEKLY``, taking the earliest - so the local
        wall clock arithmetic that has to be right about daylight saving exists
        once in MeoBot rather than twice.

        ``MONTHLY`` is computed here because that module has no monthly kind.
        A day of the month that does not exist is **clamped to the last day**
        rather than skipped: "báo cáo ngày 31" means the end of the month, and a
        routine that silently produced nothing in February would be a monthly
        report that lost a month.
        """
        if self.frequency is PrRecurringFrequency.DAILY:
            return ReminderSchedule(kind=ScheduleKind.DAILY, local_time=self.run_time).next_after(
                moment, tz=tz
            )
        if self.frequency is PrRecurringFrequency.WEEKLY:
            return min(
                ReminderSchedule(
                    kind=ScheduleKind.WEEKLY, local_time=self.run_time, weekday=day
                ).next_after(moment, tz=tz)
                for day in self.weekdays
            )

        assert self.day_of_month is not None
        local = moment.astimezone(tz)
        year, month = local.year, local.month
        # At most two candidates are ever needed: this month's, and next
        # month's when this month's has passed.
        for _ in range(2):
            candidate = _at(_clamped(year, month, self.day_of_month), self.run_time, tz)
            if candidate > moment:
                return candidate
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        raise AssertionError("a monthly schedule always fires within two months")

    # -----------------------------------------------------------------
    # Presentation
    # -----------------------------------------------------------------
    def describe(self) -> str:
        """The schedule in Vietnamese, for the preview and the list.

        Produced by the server rather than assembled in the browser, so the
        sentence a manager reads before activating comes from the same object
        that will actually do the firing.
        """
        clock = self.run_time.strftime("%H:%M")
        if self.frequency is PrRecurringFrequency.DAILY:
            return f"{clock} mỗi ngày"
        if self.frequency is PrRecurringFrequency.WEEKLY:
            days = ", ".join(weekday_label(day) for day in sorted(self.weekdays))
            return f"{clock} mỗi {days}"
        return f"{clock} ngày {self.day_of_month} hằng tháng"


def _clamped(year: int, month: int, day: int) -> date:
    """``day`` of ``year``/``month``, or the last day when it is shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _at(day: date, clock: time, tz: ZoneInfo) -> datetime:
    """One local date plus a wall clock, as a UTC instant."""
    return datetime.combine(day, clock).replace(tzinfo=tz).astimezone(UTC)


def day_start(day: date, *, tz: ZoneInfo) -> datetime:
    """Local midnight of ``day``, as a UTC instant.

    The lower edge of a template's ``start_date``: a routine that starts on the
    first of September starts at the beginning of the Vietnamese first of
    September, not at midnight UTC seven hours earlier.
    """
    return _at(day, time(0, 0), tz)


def day_end(day: date, *, tz: ZoneInfo) -> datetime:
    """The instant local ``day`` stops, as a UTC instant.

    The exclusive upper edge of ``end_date``, so an end date of the thirtieth
    includes everything that fires *on* the thirtieth.
    """
    return _at(day + timedelta(days=1), time(0, 0), tz)


# =====================================================================
# Occurrence identity
# =====================================================================
#: How wide an occurrence key may be. Matches the column.
MAX_OCCURRENCE_KEY_LENGTH = 40


def occurrence_key(moment: datetime, *, tz: ZoneInfo) -> str:
    """The stable name of one scheduled occurrence: ``20260904T0900``.

    **Local, not UTC**, and that is the whole point. "Báo cáo 9 giờ sáng ngày 4"
    is a statement about a Vietnamese wall clock; a key written in UTC would
    name the same occurrence ``20260904T0200`` and, on a deployment that ever
    moved timezone, would name two different occurrences the same thing.

    Second-resolution is deliberately absent: a schedule fires on a minute, and
    a key carrying seconds would be a key that changed if the minute were ever
    recomputed from a slightly different instant.
    """
    return moment.astimezone(tz).strftime("%Y%m%dT%H%M")


#: The subject segment for work one occurrence produces once, for everybody.
SHARED_SUBJECT = "SHARED"


def occurrence_subject(user_id: uuid.UUID | None) -> str:
    """The third source-key segment: who this particular item is *for*.

    ``SHARED`` when the occurrence produces one job several people worked on,
    and ``A_`` followed by the assignee's uuid in upper-case hex when it
    produces one job each.

    The hex is written without dashes and upper-cased because it has to survive
    :data:`~meobot.domain.pr.work.SOURCE_KEY_PATTERN`, whose milestone segment
    is ``[A-Z][A-Z0-9_]{2,39}``. ``A_`` plus thirty-two hex characters is
    thirty-four, which fits - and it is the assignee's **whole** id rather than
    a hash of it, so two colleagues can never collide into one key. That
    mattered enough to check: a truncated id would have made "one work item per
    assignee" a probabilistic guarantee, and the unique index over it would then
    have silently swallowed somebody's work instead of duplicating it.
    """
    if user_id is None:
        return SHARED_SUBJECT
    return f"A_{user_id.hex.upper()}"


def recurring_source_key(occurrence_id: uuid.UUID, *, user_id: uuid.UUID | None) -> str:
    """``recurring:{occurrence-uuid}:{SUBJECT}``. The idempotency guarantee.

    Composed through :func:`~meobot.domain.pr.work.work_source_key`, so it is
    validated by the same assertion every other source key is and cannot drift
    into a shape the unique index treats differently.

    **The uuid segment is the occurrence's, not the template's**, and that is
    the design decision the three-segment contract forced. The key has to
    distinguish a template's Tuesday from its Wednesday and, in
    ``SEPARATE_PER_ASSIGNEE`` mode, one colleague's Tuesday from another's -
    three facts in a format with two variable segments. Naming the occurrence
    row in the uuid segment moves the date into a row that already has to exist,
    unique on ``(template_id, occurrence_key)``, and leaves the third segment
    free to carry the person. Nothing had to be widened, and no key anywhere
    else in MeoBot changed meaning.
    """
    from meobot.domain.pr.work import PrWorkSourceType, work_source_key

    return work_source_key(PrWorkSourceType.RECURRING, occurrence_id, occurrence_subject(user_id))


def assert_template_transition(
    current: PrRecurringTemplateStatus, target: PrRecurringTemplateStatus
) -> None:
    """Raise unless a template may move from ``current`` to ``target``."""
    if target not in RECURRING_TEMPLATE_TRANSITIONS.get(current, frozenset()):
        raise PrValidationError(
            "Không thể chuyển trạng thái việc định kỳ này.",
            details={
                "field": "status",
                "reason": "illegal_template_transition",
                "from": current.value,
                "to": target.value,
            },
        )


def normalise_weekdays(values: Sequence[int] | None) -> tuple[int, ...]:
    """Sorted, de-duplicated weekdays. Order in a set is not information."""
    return tuple(sorted({int(value) for value in values or ()}))


__all__: list[str] = [
    "EDITABLE_TEMPLATE_STATUSES",
    "MAX_CATCH_UP_DAYS",
    "MAX_OCCURRENCES_PER_SWEEP",
    "MAX_OCCURRENCE_KEY_LENGTH",
    "MAX_TEMPLATE_CONTRIBUTORS",
    "MAX_TEMPLATE_NAME",
    "RECURRING_TEMPLATE_TRANSITIONS",
    "SHARED_SUBJECT",
    "TERMINAL_OCCURRENCE_STATES",
    "UNRESOLVED_OCCURRENCE_STATES",
    "PrRecurringFrequency",
    "PrRecurringOccurrenceState",
    "PrRecurringTemplateStatus",
    "RecurringSchedule",
    "assert_template_transition",
    "day_end",
    "day_start",
    "normalise_weekdays",
    "occurrence_key",
    "occurrence_subject",
    "recurring_source_key",
]
