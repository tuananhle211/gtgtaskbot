"""What the recurring-work API sends and accepts. M4B.

Its own module beside ``schemas/pr_work.py`` for the same reason that one is
separate from ``schemas/pr.py``: templates are configuration with their own
readers, and a screen that draws a routine has nothing in common with one that
draws a work item.

Three rules run through every model here
-----------------------------------------

**No scheduler internals arrive from a client, and none leave for a manager.**
There is no ``cron`` field, no ``rrule``, no ``source_key``, no cursor and no
``revision_no`` on any request body. A manager states a frequency, some
weekdays or a day of the month and a time; everything the scheduler needs is
derived from that on the server. The reverse direction is nearly as strict: the
*only* internals a response carries are the occurrence history's state and
message, and they exist to answer one operational question - *"why is there no
work for Tuesday"*.

**No status arrives from a client.** ``DRAFT`` is where a template starts and
the four lifecycle routes are the only way it moves, so there is no body that
can file a routine as already running.

**The preview comes from the server.** ``schedule_label`` and
``next_occurrences`` are computed by the same
:class:`~meobot.domain.pr.recurring.RecurringSchedule` the generator fires from.
A browser that computed them would be a second implementation of the calendar,
and the day the two disagreed the wrong one would be the one the manager read
before activating.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from meobot.application.pr_work_recurring_service import RecurringTemplateDetail
from meobot.db.models.pr_work_recurring import PrWorkRecurringOccurrence
from meobot.domain.pr.labels import priority_label
from meobot.domain.pr.models import PrPriority
from meobot.domain.pr.recurring import (
    MAX_TEMPLATE_CONTRIBUTORS,
    MAX_TEMPLATE_NAME,
    PrRecurringFrequency,
    PrRecurringTemplateStatus,
)
from meobot.domain.pr.work import PrWorkAssignmentMode
from meobot.domain.pr.work_labels import (
    recurring_frequency_label,
    recurring_occurrence_state_label,
    recurring_template_status_label,
    work_unit_label,
)


class _Body(BaseModel):
    """Request bodies refuse unknown fields.

    ``extra="forbid"``, and load-bearing here in the same way it is for work: a
    body carrying ``status``, ``revision_no`` or ``last_evaluated_occurrence_at``
    is **refused** rather than silently dropped, so an attempt to drive the
    scheduler from a request is an error somebody sees.
    """

    model_config = ConfigDict(extra="forbid")


class RecurringTemplateRequest(_Body):
    """Describe a routine. ``PR_WORK_MANAGE``.

    The whole form a manager fills in, and deliberately nothing more. What is
    **absent** is the design:

    * no ``cron`` and no ``rrule`` - three honest shapes the system can actually
      run, chosen from a dropdown;
    * no ``source_key`` - composed by the generator from the occurrence it
      reserved, and never assembled from anything a client sent;
    * no cursor and no occurrence state - the scheduler's own bookkeeping, and a
      field a client could set is a field a client could corrupt;
    * no workload minutes and no performance weighting - M6's, and a template
      that carried one would be inventing a rate nobody approved.
    """

    name: str = Field(min_length=1, max_length=MAX_TEMPLATE_NAME)
    work_type_id: uuid.UUID
    #: **Required, never defaulted.** One job several people share and one job
    #: each are different facts, and M4A's reasoning applies unchanged: three
    #: independent obligations recorded as one shared item means one person
    #: completes it for all three and one validation counts all three.
    assignment_mode: PrWorkAssignmentMode
    contributor_user_ids: list[uuid.UUID] = Field(
        min_length=1, max_length=MAX_TEMPLATE_CONTRIBUTORS
    )
    frequency: PrRecurringFrequency
    #: Local wall clock - "Giờ tạo". Evaluated in the department's timezone and
    #: stored as UTC, so 09:00 is 09:00 in Ho Chi Minh City all year.
    run_time: time
    start_date: date
    #: Monday=0. Required for ``WEEKLY`` and refused otherwise, by the validator
    #: below rather than by a comment.
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    #: 1-31, required for ``MONTHLY``. A day that does not exist in a shorter
    #: month is clamped to that month's last day rather than skipped, so a
    #: "ngày 31" report does not lose February.
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    #: Required when the work type is measured by ``QUANTITY``, refused by the
    #: server otherwise-unmeasurable. One hundred comments a day is **one** work
    #: item with ``quantity = 100``, never a hundred items.
    quantity: Decimal | None = Field(default=None, gt=0)
    #: **Tích lũy kết quả theo kỳ.** Period-container patch. ``True`` makes the
    #: routine a stream: each firing ensures one container per assignee for
    #: the month it falls in, and results are reported into it. Requires
    #: ``SEPARATE_PER_ASSIGNEE``; ``quantity`` is ignored, because what a
    #: stream should reach is a KPI target and that lives in the plan.
    accumulate_by_period: bool = False
    priority: PrPriority = PrPriority.NORMAL
    #: Hours after the occurrence. ``None`` is a real answer - a routine nobody
    #: times has no deadline, and inventing one would make every such job overdue
    #: by tomorrow.
    due_after_hours: int | None = Field(default=None, ge=1, le=8760)
    end_date: date | None = None
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _schedule_parameters_match_the_frequency(self) -> RecurringTemplateRequest:
        """A weekly routine needs weekdays; a monthly one needs a day.

        Checked here as well as in the domain so the form gets a field-level
        ``422`` rather than a generic refusal. The domain checks it again
        because a service-level caller never touched this model.
        """
        if self.frequency is PrRecurringFrequency.WEEKLY and not self.weekdays:
            raise ValueError("weekdays is required for a WEEKLY template")
        if self.frequency is PrRecurringFrequency.MONTHLY and self.day_of_month is None:
            raise ValueError("day_of_month is required for a MONTHLY template")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays must be 0 (Monday) to 6 (Sunday)")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class SchedulePreviewRequest(RecurringTemplateRequest):
    """The same form, asked *"what would this do"* before anything is written.

    Deliberately the same shape rather than a reduced one, so the preview a
    manager reads is computed from exactly the body that would be saved. A
    narrower preview body is how a preview comes to disagree with the thing it
    was previewing.
    """


class SchedulePreviewResponse(BaseModel):
    """A schedule as a sentence, and the next few instants it would fire."""

    #: "09:00 mỗi thứ Hai, thứ Tư" - from
    #: :meth:`~meobot.domain.pr.recurring.RecurringSchedule.describe`.
    schedule_label: str
    #: UTC instants. Empty when the routine's end date has already passed, which
    #: is the honest answer rather than a promise of firings nobody will get.
    next_occurrences: list[datetime]


class RecurringTemplateResponse(BaseModel):
    """One routine, with everything a screen draws already resolved.

    Names, labels and the preview are all composed here rather than fetched
    per-row by the browser: the same rule the whole panel follows - *the server
    owns the vocabulary, the client owns the layout*.
    """

    id: uuid.UUID
    name: str
    description: str | None = None
    work_type_id: uuid.UUID
    work_type_name: str
    #: What a quantity on this template means, when it has one.
    work_type_unit_label: str
    assignment_mode: str
    accumulate_by_period: bool = False
    quantity: Decimal | None = None
    priority: str
    priority_label: str
    frequency: str
    frequency_label: str
    weekdays: list[int]
    day_of_month: int | None = None
    run_time: time
    due_after_hours: int | None = None
    start_date: date
    end_date: date | None = None
    status: str
    status_label: str
    contributor_user_ids: list[uuid.UUID]
    contributor_names: dict[uuid.UUID, str]
    #: Server-computed, from the object that will do the firing.
    schedule_label: str
    next_occurrences: list[datetime]
    #: How many occurrences produced work, and how many are still owed. The
    #: second is what makes a stalled routine visible without opening a log.
    generated_work_items: int
    unsettled_occurrences: int
    #: Every firing the scheduler has recorded, whatever became of it.
    occurrence_count: int
    activated_at: datetime | None = None
    #: **Who authorised the routine.** Every job it generates is filed under
    #: this person, so a screen can say whose standing authorization the work
    #: rests on rather than showing a worker process.
    activated_by_user_id: uuid.UUID | None = None
    #: Server-decided, from the same transitions the writes assert. A screen
    #: drawn a minute ago is not authorization; these exist so a client offers
    #: only what the server would accept.
    can_activate: bool
    can_pause: bool
    can_resume: bool
    can_end: bool
    can_edit: bool
    can_delete: bool

    @classmethod
    def from_detail(cls, detail: RecurringTemplateDetail) -> RecurringTemplateResponse:
        row = detail.template
        status = row.status
        return cls(
            id=row.id,
            name=row.name,
            description=row.description,
            work_type_id=row.work_type_id,
            work_type_name=detail.work_type.name,
            work_type_unit_label=work_unit_label(detail.work_type.default_unit),
            assignment_mode=row.assignment_mode.value,
            accumulate_by_period=row.accumulate_by_period,
            quantity=row.quantity,
            priority=row.priority.value,
            priority_label=priority_label(row.priority),
            frequency=row.frequency.value,
            frequency_label=recurring_frequency_label(row.frequency),
            weekdays=list(row.weekdays or []),
            day_of_month=row.day_of_month,
            run_time=row.run_time,
            due_after_hours=row.due_after_hours,
            start_date=row.start_date,
            end_date=row.end_date,
            status=status.value,
            status_label=recurring_template_status_label(status),
            contributor_user_ids=list(detail.contributor_user_ids),
            contributor_names=dict(detail.contributor_names),
            schedule_label=detail.schedule_label,
            next_occurrences=list(detail.next_occurrences),
            generated_work_items=detail.generated_work_items,
            unsettled_occurrences=detail.unsettled_occurrences,
            occurrence_count=detail.occurrence_count,
            activated_at=row.activated_at,
            activated_by_user_id=row.activated_by_user_id,
            can_activate=status is PrRecurringTemplateStatus.DRAFT,
            can_pause=status is PrRecurringTemplateStatus.ACTIVE,
            can_resume=status is PrRecurringTemplateStatus.PAUSED,
            can_end=status is not PrRecurringTemplateStatus.ENDED,
            can_edit=status is not PrRecurringTemplateStatus.ENDED,
            # Only a draft that has never been reached by the scheduler. The
            # server checks the occurrence ledger as well; this flag is the
            # cheap half of the same answer and the screen may not rely on it.
            # A draft the scheduler has **never reached**. Keyed on the
            # occurrence count rather than on generated work, because a firing
            # declined for a closed month produced no work and is still the
            # stored answer to a question somebody may ask. The server refuses
            # on the same condition; this flag is the cheap half of it.
            can_delete=(status is PrRecurringTemplateStatus.DRAFT and detail.occurrence_count == 0),
        )


class RecurringTemplateListResponse(BaseModel):
    """Every routine, newest first."""

    items: list[RecurringTemplateResponse]


class RecurringOccurrenceResponse(BaseModel):
    """One scheduled firing and what became of it.

    **The one place scheduler internals reach a screen**, and the reason they do:
    *"the daily seeding routine produced nothing last Thursday"* is an
    operational question whose answer should be visible to the person who
    noticed it, not only in a log aggregator.
    """

    id: uuid.UUID
    scheduled_for: datetime
    state: str
    state_label: str
    #: How many work items this firing produced. One for shared work, one per
    #: assignee otherwise.
    work_item_count: int
    attempts: int
    #: Prose for a person: what went wrong, or why the firing was declined.
    message: str | None = None
    generated_at: datetime | None = None
    #: Which version of the template produced it. Shown because a routine edited
    #: mid-month legitimately produced two different kinds of job, and this is
    #: what explains it.
    template_revision_no: int

    @classmethod
    def from_row(cls, row: PrWorkRecurringOccurrence) -> RecurringOccurrenceResponse:
        return cls(
            id=row.id,
            scheduled_for=row.scheduled_for,
            state=row.state.value,
            state_label=recurring_occurrence_state_label(row.state),
            work_item_count=row.work_item_count,
            attempts=row.attempts,
            message=row.last_error,
            generated_at=row.generated_at,
            template_revision_no=row.template_revision_no,
        )


class RecurringOccurrenceListResponse(BaseModel):
    """One template's scheduler history, newest first."""

    items: list[RecurringOccurrenceResponse]


__all__: list[str] = [
    "RecurringOccurrenceListResponse",
    "RecurringOccurrenceResponse",
    "RecurringTemplateListResponse",
    "RecurringTemplateRequest",
    "RecurringTemplateResponse",
    "SchedulePreviewRequest",
    "SchedulePreviewResponse",
]
