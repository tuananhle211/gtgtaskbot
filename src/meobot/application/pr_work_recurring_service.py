"""Recurring templates: writing them, moving them through their life, previewing them.

Milestone M4B. **This service generates nothing.** It owns the configuration -
what a routine is, who it is for, when it fires and whether it is running - and
:mod:`meobot.application.pr_work_recurring_generator` owns the sweep that turns
an active one into work. The split is the same one M2 made between the plan and
the evaluator, for the same reason: the rules about what a manager may *say* and
the rules about what a worker may *do at 4am* fail differently and are argued
about separately.

The cursor is written here and read there
------------------------------------------

Three of this service's methods move ``last_evaluated_occurrence_at``, and each
one is a product decision rather than bookkeeping:

* :meth:`~PrWorkRecurringService.activate` sets it to the **activation
  boundary** - the later of the template's start date and this moment - so a
  routine written last month and activated today does not open with a month of
  backdated work;
* :meth:`~PrWorkRecurringService.resume` moves it to the resume instant, so the
  paused fortnight is never walked and never backfilled;
* nothing else touches it. In particular **editing does not**, because an edit
  is not a statement about which occurrences have been dealt with.

Worker downtime looks nothing like either: nobody calls anything, the cursor
stays where it was, and the generator walks the missed occurrences on the next
sweep. That is the whole of the pause-versus-downtime distinction, and it is a
distinction in *who moved the cursor* rather than a flag anybody has to set.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_query import day_bounds
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_period_service import PrWorkPeriodService, month_bounds
from meobot.application.pr_work_result_service import PrWorkResultService
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.db.models.pr_work_recurring import (
    PrWorkRecurringOccurrence,
    PrWorkRecurringTemplate,
    PrWorkRecurringTemplateContributor,
)
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrConflictError, PrNotFoundError, PrValidationError
from meobot.domain.pr.models import PrPriority
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.recurring import (
    EDITABLE_TEMPLATE_STATUSES,
    MAX_TEMPLATE_CONTRIBUTORS,
    MAX_TEMPLATE_NAME,
    PrRecurringFrequency,
    PrRecurringTemplateStatus,
    RecurringSchedule,
    assert_template_transition,
    day_end,
    day_start,
    normalise_weekdays,
    recurring_source_key,
)
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkAssignmentMode, PrWorkSourceType

logger = get_logger(__name__)

#: The reason code a mode switch carries when the current month already holds
#: work this routine produced. Structured so a client can name the field.
RECURRING_ACCOUNTING_MODE_LOCKED = "recurring_accounting_mode_locked_for_period"

#: How many future firings a preview shows. Enough to make a weekly or monthly
#: schedule obviously right or obviously wrong, and short enough to read.
PREVIEW_OCCURRENCES = 5

MAX_DESCRIPTION = 4000


@dataclass(frozen=True, slots=True)
class RecurringTemplateCommand:
    """One routine somebody is describing.

    Carries **no status and no cursor**. Which method was called decides the
    first, and only this service's lifecycle methods may touch the second, so
    there is no request body anywhere that can file a template as already
    running or claim that six weeks of occurrences have been dealt with.
    """

    name: str
    work_type_id: uuid.UUID
    assignment_mode: PrWorkAssignmentMode
    frequency: PrRecurringFrequency
    run_time: time
    start_date: date
    contributor_user_ids: tuple[uuid.UUID, ...]
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None
    quantity: Decimal | None = None
    priority: PrPriority = PrPriority.NORMAL
    due_after_hours: int | None = None
    end_date: date | None = None
    description: str | None = None
    #: **Tích lũy kết quả theo kỳ.** Period-container patch. ``True`` makes the
    #: routine a stream: one container per assignee per month, results reported
    #: into it. See the column.
    accumulate_by_period: bool = False


@dataclass(frozen=True, slots=True)
class RecurringTemplateDetail:
    """One template with everything a screen draws, resolved server-side."""

    template: PrWorkRecurringTemplate
    work_type: PrWorkType
    contributor_user_ids: tuple[uuid.UUID, ...]
    contributor_names: dict[uuid.UUID, str]
    #: The schedule as a Vietnamese sentence, and the next few firings. Both
    #: come from the object that will actually do the firing - a preview
    #: assembled in the browser would be a second implementation of the
    #: calendar, and the one that is wrong would be the one people trust.
    schedule_label: str
    next_occurrences: tuple[datetime, ...]
    #: How many jobs this routine has produced, and how many occurrences are
    #: still unsettled. The second is what makes a stalled template visible.
    generated_work_items: int
    unsettled_occurrences: int
    #: Every firing the scheduler has recorded, whatever became of it. What
    #: decides deletability, and deliberately not ``generated_work_items``: a
    #: firing that was declined because its month was closed produced no work
    #: and is still the stored answer to "why did this routine do nothing in
    #: August", so a template that has one is not deletable either.
    occurrence_count: int


class PrWorkRecurringService:
    """Creates, edits and runs the life of a recurring template. ``PR_WORK_MANAGE``.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Resolves ``PR_WORK_MANAGE``.
        timezone: The department's wall clock. Every schedule in this module is
            evaluated in it and stored as UTC, which is the same rule
            :class:`~meobot.application.pr_work_period_service.PrWorkPeriodService`
            follows for month boundaries - and for the same reason: a calendar
            that means two different things on two screens is worse than no
            calendar.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        *,
        timezone: ZoneInfo,
        results: PrWorkResultService | None = None,
        periods: PrWorkPeriodService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._timezone = timezone
        # The accounting-mode guard asks which month is current through the
        # one service that owns the calendar. Optional for the same reason as
        # ``results``; the application wiring always passes it.
        self._periods = periods
        # Period-container patch. Activating an accumulating routine opens the
        # current month's containers immediately, through the one service
        # that creates them. Optional so the scheduler tests that build this
        # service alone keep working.
        self._results = results

    # =====================================================================
    # Writing
    # =====================================================================
    async def create_template(
        self, *, actor: Actor, request_id: uuid.UUID, command: RecurringTemplateCommand
    ) -> PrWorkRecurringTemplate:
        """Write a new routine. Lands at ``DRAFT``. ``PR_WORK_MANAGE``.

        ``DRAFT`` rather than ``ACTIVE``, and the extra click is the point: a
        template is a standing instruction to create work every day until
        somebody stops it, and the person who wrote it should see the schedule
        preview before it starts running. Activation is a separate act with its
        own audit row naming who performed it.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        work_type = await self._require_work_type(command.work_type_id)
        schedule = self._schedule_of(command)
        contributors = await self._validated_contributors(command)
        self._validate_shape(work_type, command)
        self._validate_dates(command)

        template = PrWorkRecurringTemplate(
            name=_require_text(command.name, "name", MAX_TEMPLATE_NAME),
            description=_optional_text(command.description, "description", MAX_DESCRIPTION),
            work_type_id=work_type.id,
            assignment_mode=command.assignment_mode,
            accumulate_by_period=command.accumulate_by_period,
            quantity=None if command.accumulate_by_period else command.quantity,
            priority=command.priority,
            frequency=command.frequency,
            weekdays=list(schedule.weekdays),
            day_of_month=schedule.day_of_month
            if command.frequency is PrRecurringFrequency.MONTHLY
            else None,
            run_time=command.run_time,
            due_after_hours=_require_due_hours(command.due_after_hours),
            start_date=command.start_date,
            end_date=command.end_date,
            status=PrRecurringTemplateStatus.DRAFT,
            revision_no=1,
            created_by_user_id=_require_user_id(actor),
        )
        self._session.add(template)
        await self._session.flush()
        await self._replace_contributors(template, contributors)

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_TEMPLATE_CREATED,
            entity_type="pr_work_recurring_template",
            entity_id=template.id,
            after=self._snapshot(template, contributors),
        )
        logger.info(
            "pr_recurring_template_created",
            extra={"pr_recurring_template_id": str(template.id)},
        )
        return template

    async def update_template(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        template_id: uuid.UUID,
        command: RecurringTemplateCommand,
    ) -> PrWorkRecurringTemplate:
        """Rewrite a routine, and **bump its revision**. ``PR_WORK_MANAGE``.

        Editing a running template is deliberately allowed: a routine whose
        quantity went from eighty comments to a hundred is normal operations,
        and forcing a manager to end it and write a new one would break the
        provenance chain of everything it has already produced.

        What that costs is the coherence problem, and ``revision_no`` is how it
        is paid. Every edit bumps it; the generator locks this row while it
        builds an occurrence and stamps the number it saw onto that occurrence.
        A sweep that started before this edit and finished after it therefore
        either produced work entirely from the old revision or entirely from the
        new one, and the occurrence says which.

        **Already-generated work is never rewritten.** A job created last
        Tuesday from the eighty-comment version is a record of what was asked
        for last Tuesday.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        template = await self._locked(template_id)
        if template.status not in EDITABLE_TEMPLATE_STATUSES:
            raise PrValidationError(
                "Mẫu định kỳ đã kết thúc thì không sửa được nữa.",
                details={"field": "status", "reason": "template_not_editable"},
            )
        work_type = await self._require_work_type(command.work_type_id)
        schedule = self._schedule_of(command)
        contributors = await self._validated_contributors(command)
        self._validate_shape(work_type, command)
        self._validate_dates(command)
        if command.accumulate_by_period != template.accumulate_by_period:
            await self._require_accounting_mode_unlocked(template)
        before = self._snapshot(template, await self.contributor_ids(template.id))

        template.name = _require_text(command.name, "name", MAX_TEMPLATE_NAME)
        template.description = _optional_text(command.description, "description", MAX_DESCRIPTION)
        template.work_type_id = work_type.id
        template.assignment_mode = command.assignment_mode
        template.accumulate_by_period = command.accumulate_by_period
        template.quantity = None if command.accumulate_by_period else command.quantity
        template.priority = command.priority
        template.frequency = command.frequency
        template.weekdays = list(schedule.weekdays)
        template.day_of_month = (
            schedule.day_of_month if command.frequency is PrRecurringFrequency.MONTHLY else None
        )
        template.run_time = command.run_time
        template.due_after_hours = _require_due_hours(command.due_after_hours)
        template.start_date = command.start_date
        template.end_date = command.end_date
        template.revision_no += 1
        await self._replace_contributors(template, contributors)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_TEMPLATE_UPDATED,
            entity_type="pr_work_recurring_template",
            entity_id=template.id,
            before=before,
            after=self._snapshot(template, contributors),
        )
        return template

    async def delete_template(
        self, *, actor: Actor, request_id: uuid.UUID, template_id: uuid.UUID
    ) -> None:
        """Remove a draft that never ran. ``PR_WORK_MANAGE``.

        Refused the moment a template has any occurrence at all - not merely any
        *generated* one. A ``PENDING`` or ``FAILED_RETRYABLE`` row is an
        obligation the scheduler has not settled, and deleting the template
        under it would strand it; a ``SKIPPED_CLOSED_PERIOD`` row is the stored
        answer to "why did this routine produce nothing in August".

        A routine that has run is **ended**, not deleted. That is the same
        decision the content module made about published work: history is not
        something a delete should be able to remove, and the foreign keys say so
        independently of this check.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        template = await self._locked(template_id)
        if template.status is not PrRecurringTemplateStatus.DRAFT:
            raise PrValidationError(
                "Chỉ xoá được việc định kỳ còn ở trạng thái nháp. Việc đã chạy thì hãy kết thúc.",
                details={"field": "status", "reason": "only_draft_deletable"},
            )
        if await self._has_occurrences(template.id):
            raise PrValidationError(
                "Việc định kỳ này đã sinh việc nên không xoá được. Hãy kết thúc nó.",
                details={"field": "status", "reason": "template_has_occurrences"},
            )
        before = self._snapshot(template, await self.contributor_ids(template.id))
        await self._session.execute(
            delete(PrWorkRecurringTemplateContributor).where(
                PrWorkRecurringTemplateContributor.template_id == template.id
            )
        )
        await self._session.delete(template)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_TEMPLATE_DELETED,
            entity_type="pr_work_recurring_template",
            entity_id=template_id,
            before=before,
        )

    # =====================================================================
    # The life of a template
    # =====================================================================
    async def activate(
        self, *, actor: Actor, request_id: uuid.UUID, template_id: uuid.UUID
    ) -> PrWorkRecurringTemplate:
        """Start the routine. ``PR_WORK_MANAGE``. **The standing authorization.**

        Two things happen, and the second is the one that has bitten every
        scheduler ever written.

        **The actor becomes the authorizing manager.** Every job this template
        generates from now on is created by and assigned by this person, so a
        routine job's audit trail names a manager rather than a worker process,
        and the work legitimately enters ``ACCEPTED`` - *the assignment is the
        authorization*, exactly as it is for
        :meth:`~meobot.application.pr_work_service.PrWorkService.assign_work`.
        Activation is emphatically not validation: nothing here makes any work
        count, and M1's independent approval is still required.

        **The cursor is set to the activation boundary**, which is the later of
        local midnight on ``start_date`` and this moment. That is what stops a
        template whose ``start_date`` was six weeks ago from filing six weeks of
        backdated work in its first sweep - the flood that turns a new routine
        into an incident. A start date in the future is honoured for the same
        reason from the other direction: the boundary is the later of the two,
        so nothing fires before the routine was meant to begin.

        Reactivating an ``ENDED`` template is refused. A routine that is over is
        copied, not restarted, because restarting it would resume a cursor that
        stopped meaning anything the day it ended.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        template = await self._locked(template_id)
        assert_template_transition(template.status, PrRecurringTemplateStatus.ACTIVE)
        was_paused = template.status is PrRecurringTemplateStatus.PAUSED
        if was_paused:
            return await self.resume(actor=actor, request_id=request_id, template_id=template_id)

        work_type = await self._require_work_type(template.work_type_id)
        if not template.accumulate_by_period:
            self._validate_quantity(work_type, template.quantity)
        if not work_type.is_active:
            raise PrValidationError(
                "Loại công việc này đã ngừng sử dụng.",
                details={"field": "work_type_id", "reason": "work_type_inactive"},
            )
        contributors = await self.contributor_ids(template.id)
        if not contributors:
            raise PrValidationError(
                "Hãy chọn ít nhất một người thực hiện trước khi chạy.",
                details={"field": "contributor_user_ids", "reason": "no_contributor"},
            )
        await self._require_active_users(contributors)

        now = utcnow()
        template.status = PrRecurringTemplateStatus.ACTIVE
        template.activated_at = now
        template.activated_by_user_id = _require_user_id(actor)
        template.paused_at = None
        template.last_evaluated_occurrence_at = self.activation_boundary(template, at=now)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_TEMPLATE_ACTIVATED,
            entity_type="pr_work_recurring_template",
            entity_id=template.id,
            after={
                "status": template.status.value,
                "revision_no": template.revision_no,
                "activation_boundary": template.last_evaluated_occurrence_at.isoformat()
                if template.last_evaluated_occurrence_at
                else None,
                "contributors": [str(one) for one in contributors],
            },
        )
        logger.info(
            "pr_recurring_template_activated",
            extra={"pr_recurring_template_id": str(template.id)},
        )
        if template.accumulate_by_period:
            await self.ensure_period_containers(
                actor=actor, request_id=request_id, template=template, at=now
            )
        return template

    async def ensure_period_containers(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        template: PrWorkRecurringTemplate,
        at: datetime,
        occurrence_id: uuid.UUID | None = None,
    ) -> int:
        """Open the month's container for every assignee of an accumulating routine.

        Called on activation for the month activation falls in, and by the
        generator for the month each firing falls in. Idempotent: a stream that
        already exists is left alone, whoever created it, and the count
        returned is how many were **new**.

        The container is ``RECURRING`` work keyed on the occurrence when a
        firing produced it, so the source-key contract holds; on activation
        there is no occurrence yet and the container is filed as ``MANUAL``
        work assigned by the activating manager, which is what it is.
        """
        if self._results is None or not template.accumulate_by_period:
            return 0
        if (
            template.end_date is not None
            and at.astimezone(self._timezone).date() > template.end_date
        ):
            return 0
        period = await self._results.period_for_moment(at, actor=actor, request_id=request_id)
        if period.status is not PrPeriodStatus.OPEN:
            return 0
        created = 0
        for user_id in await self.contributor_ids(template.id):
            source_key = (
                recurring_source_key(occurrence_id, user_id=user_id)
                if occurrence_id is not None
                else None
            )
            existing = await self._results.container_for(
                work_type_id=template.work_type_id, subject_user_id=user_id, period_id=period.id
            )
            if existing is not None:
                continue
            await self._results.ensure_container(
                actor=actor,
                request_id=request_id,
                work_type_id=template.work_type_id,
                subject_user_id=user_id,
                period=period,
                source_type=(PrWorkSourceType.RECURRING if source_key else PrWorkSourceType.MANUAL),
                source_key=source_key,
                recurring_occurrence_id=occurrence_id,
                title=template.name,
                description=template.description,
                assigned_by_user_id=template.activated_by_user_id or actor.user_id,
            )
            created += 1
        return created

    def activation_boundary(self, template: PrWorkRecurringTemplate, *, at: datetime) -> datetime:
        """The earliest instant a newly activated template may fire **after**.

        ``max(local midnight of start_date, at)``. Exposed rather than inlined
        because it is the rule the activation-flood test asserts, and a rule
        worth a test is worth a name.
        """
        return max(day_start(template.start_date, tz=self._timezone), at)

    async def pause(
        self, *, actor: Actor, request_id: uuid.UUID, template_id: uuid.UUID
    ) -> PrWorkRecurringTemplate:
        """Suspend the routine. ``PR_WORK_MANAGE``.

        **The cursor is deliberately left alone.** It is
        :meth:`resume` that moves it forward, and the asymmetry is what makes
        pausing safe to do in a hurry: an occurrence that was already owed and
        has not been settled - a ``PENDING`` row, or one that failed - is still
        owed, and the generator's retry set will still come back to it when the
        template runs again. What resume suppresses is the *interval*, not the
        backlog.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        template = await self._locked(template_id)
        assert_template_transition(template.status, PrRecurringTemplateStatus.PAUSED)
        template.status = PrRecurringTemplateStatus.PAUSED
        template.paused_at = utcnow()
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_TEMPLATE_PAUSED,
            entity_type="pr_work_recurring_template",
            entity_id=template.id,
            after={"status": template.status.value},
        )
        return template

    async def resume(
        self, *, actor: Actor, request_id: uuid.UUID, template_id: uuid.UUID
    ) -> PrWorkRecurringTemplate:
        """Restart a paused routine, **without backfilling the pause**. ``PR_WORK_MANAGE``.

        The cursor jumps forward to this moment, and that one line is the entire
        pause-versus-downtime distinction:

        * a **pause** is a decision that the work should not have happened. The
          fortnight it covers is never walked by the generator, so it produces
          no occurrences, no work and no rows at all - which is also why a
          template paused for a year does not owe the database three hundred
          skip records saying nothing happened;
        * **downtime** is the opposite: nobody decided anything, nobody called
          this method, the cursor stayed where it was, and the generator walks
          every missed firing on the next sweep and generates it.

        Modelling pause as downtime would give a manager who suspended a routine
        over Tết a fortnight of backdated work the moment they turned it back
        on. Modelling downtime as pause would silently lose work the department
        genuinely owed.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        template = await self._locked(template_id)
        assert_template_transition(template.status, PrRecurringTemplateStatus.ACTIVE)
        # The same check :meth:`activate` makes, for the same reason: a routine
        # resumed onto somebody who left during the pause would otherwise fail
        # every night at 4am, and the only record would be a
        # ``FAILED_RETRYABLE`` row nobody is watching. Refusing here puts it in
        # front of the person who can edit the template.
        await self._require_active_users(await self.contributor_ids(template.id))
        now = utcnow()
        template.status = PrRecurringTemplateStatus.ACTIVE
        template.paused_at = None
        # ``ensure_utc`` because this is arithmetic on a stored instant, and the
        # offline test fixture's driver drops the offset - see the generator's
        # ``_reserve_next``. ``max`` rather than assignment: resuming must never
        # move a cursor *backwards* onto ground that has already been settled.
        template.last_evaluated_occurrence_at = max(
            ensure_utc(template.last_evaluated_occurrence_at)
            if template.last_evaluated_occurrence_at is not None
            else now,
            now,
        )
        if template.activated_by_user_id is None:
            template.activated_by_user_id = _require_user_id(actor)
            template.activated_at = now
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_TEMPLATE_RESUMED,
            entity_type="pr_work_recurring_template",
            entity_id=template.id,
            after={
                "status": template.status.value,
                "resumed_cursor": template.last_evaluated_occurrence_at.isoformat()
                if template.last_evaluated_occurrence_at
                else None,
            },
        )
        return template

    async def end(
        self, *, actor: Actor, request_id: uuid.UUID, template_id: uuid.UUID
    ) -> PrWorkRecurringTemplate:
        """Retire the routine for good. ``PR_WORK_MANAGE``. Terminal.

        Reachable from every other state, because "we are not doing this any
        more" is a sentence somebody may need to say about a draft, a running
        routine or a paused one alike.

        Work already generated is untouched and still has to be done: ending the
        instruction is not cancelling the obligations it already created, and
        cancelling those is M1's :meth:`~meobot.application.pr_work_service.PrWorkService.cancel`,
        one job at a time, by somebody who decided so.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        template = await self._locked(template_id)
        assert_template_transition(template.status, PrRecurringTemplateStatus.ENDED)
        template.status = PrRecurringTemplateStatus.ENDED
        template.ended_at = utcnow()
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_TEMPLATE_ENDED,
            entity_type="pr_work_recurring_template",
            entity_id=template.id,
            after={"status": template.status.value},
        )
        return template

    # =====================================================================
    # Reading
    # =====================================================================
    async def list_templates(
        self, *, actor: Actor, statuses: Sequence[PrRecurringTemplateStatus] | None = None
    ) -> tuple[RecurringTemplateDetail, ...]:
        """Every routine, newest first, with its preview already computed.

        ``PR_WORK_MANAGE``: a template is management configuration, and reading
        the department's standing instructions is the same act as writing them.
        An employee sees the *work* a template generates on the ordinary ledger,
        which is what they actually have to do.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        statement = select(PrWorkRecurringTemplate).order_by(
            PrWorkRecurringTemplate.created_at.desc()
        )
        if statuses:
            statement = statement.where(PrWorkRecurringTemplate.status.in_(tuple(statuses)))
        rows = (await self._session.execute(statement)).scalars().all()
        return tuple([await self._detail(row) for row in rows])

    async def template_detail(
        self, *, actor: Actor, template_id: uuid.UUID
    ) -> RecurringTemplateDetail:
        """One routine, with its schedule sentence and next firings."""
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        return await self._detail(await self._require_template(template_id))

    async def occurrences(
        self, *, actor: Actor, template_id: uuid.UUID, limit: int = 50
    ) -> tuple[PrWorkRecurringOccurrence, ...]:
        """The scheduler's own record for one template, newest first.

        This is the screen that answers *"why is there no work for Tuesday"*,
        and it is the reason the occurrence table stores a state and a
        ``last_error`` rather than emitting a log line: an operational question
        about a missing job should be answerable by the person who noticed,
        without a log aggregator.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)
        await self._require_template(template_id)
        statement = (
            select(PrWorkRecurringOccurrence)
            .where(PrWorkRecurringOccurrence.template_id == template_id)
            .order_by(PrWorkRecurringOccurrence.scheduled_for.desc())
            .limit(max(1, min(limit, 200)))
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    def preview(
        self, command: RecurringTemplateCommand, *, at: datetime | None = None
    ) -> tuple[str, tuple[datetime, ...]]:
        """What this schedule would do, before anybody commits to it.

        **Computed by the server**, from the same
        :class:`~meobot.domain.pr.recurring.RecurringSchedule` the generator
        will use. A preview assembled in the browser would be a second
        implementation of the calendar, and the day the two disagreed the wrong
        one would be the one the manager had read.

        Bounded by ``end_date`` and floored at ``start_date``, so the preview of
        a routine that ends on Friday stops on Friday rather than promising a
        Saturday nobody will get.
        """
        schedule = self._schedule_of(command)
        moment = max(at or utcnow(), day_start(command.start_date, tz=self._timezone))
        limit = (
            day_end(command.end_date, tz=self._timezone) if command.end_date is not None else None
        )
        firings: list[datetime] = []
        for _ in range(PREVIEW_OCCURRENCES):
            moment = schedule.next_after(moment, tz=self._timezone)
            if limit is not None and moment >= limit:
                break
            firings.append(moment)
        return schedule.describe(), tuple(firings)

    async def _require_accounting_mode_unlocked(self, template: PrWorkRecurringTemplate) -> None:
        """A routine's accounting mode is fixed for a month it has produced work in.

        Per-firing jobs and an accumulating container are both ``COUNTED``
        contributions for the same person, type and month, and every reader
        sums them together. Switching mode mid-month would therefore count
        the same routine twice - once as the jobs already filed, once as the
        container that opens next. The rule is the simplest safe one: once
        any firing this month generated work, or the month's container holds
        a result, the switch waits for the next reporting period. Nothing is
        migrated; the manager is told when the change can apply.
        """
        now = utcnow()
        local = now.astimezone(self._timezone)
        first, last = month_bounds(local.year, local.month)
        lower, upper = day_bounds(first, last, tz=self._timezone)
        assert lower is not None and upper is not None
        generated = await self._session.scalar(
            select(func.count())
            .select_from(PrWorkRecurringOccurrence)
            .where(
                PrWorkRecurringOccurrence.template_id == template.id,
                PrWorkRecurringOccurrence.scheduled_for >= lower,
                PrWorkRecurringOccurrence.scheduled_for < upper,
                PrWorkRecurringOccurrence.work_item_count > 0,
            )
        )
        locked = bool(generated)
        if not locked and template.accumulate_by_period and self._periods is not None:
            # An accumulating routine's stream may hold results without any
            # firing of its own - activation opens the month's containers.
            period = await self._periods.period_for(now)
            contributors = await self.contributor_ids(template.id)
            if period is not None and contributors:
                results = await self._session.scalar(
                    select(func.count())
                    .select_from(PrWorkResult)
                    .join(PrWorkItem, PrWorkItem.id == PrWorkResult.work_item_id)
                    .where(
                        PrWorkItem.reporting_period_id == period.id,
                        PrWorkItem.work_type_id == template.work_type_id,
                        PrWorkItem.subject_user_id.in_(list(contributors)),
                    )
                )
                locked = bool(results)
        if locked:
            raise PrConflictError(
                "Không thể đổi cách ghi nhận công việc định kỳ trong kỳ đã phát sinh dữ liệu. "
                "Thay đổi có thể áp dụng từ kỳ tiếp theo.",
                details={
                    "field": "accumulate_by_period",
                    "reason": RECURRING_ACCOUNTING_MODE_LOCKED,
                    "period": f"{local.year:04d}-{local.month:02d}",
                    "template_id": str(template.id),
                },
            )

    async def contributor_ids(self, template_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
        """Who a template names, in the order they were named.

        The order is load-bearing: the first becomes the ``PRIMARY``
        contributor of shared work, which is M1's rule applied to a list that is
        stored rather than posted.
        """
        statement = (
            select(PrWorkRecurringTemplateContributor.user_id)
            .where(PrWorkRecurringTemplateContributor.template_id == template_id)
            .order_by(PrWorkRecurringTemplateContributor.display_order)
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    # =====================================================================
    # Internals
    # =====================================================================
    async def _detail(self, template: PrWorkRecurringTemplate) -> RecurringTemplateDetail:
        contributors = await self.contributor_ids(template.id)
        work_type = await self._require_work_type(template.work_type_id)
        schedule = schedule_of_template(template)
        # From the cursor when there is one, so a running template's preview is
        # what it will *actually* do next rather than what a fresh one would.
        moment = (
            ensure_utc(template.last_evaluated_occurrence_at)
            if template.last_evaluated_occurrence_at is not None
            else utcnow()
        )
        limit = (
            day_end(template.end_date, tz=self._timezone) if template.end_date is not None else None
        )
        firings: list[datetime] = []
        if template.status in {
            PrRecurringTemplateStatus.DRAFT,
            PrRecurringTemplateStatus.ACTIVE,
            PrRecurringTemplateStatus.PAUSED,
        }:
            cursor = max(moment, day_start(template.start_date, tz=self._timezone))
            for _ in range(PREVIEW_OCCURRENCES):
                cursor = schedule.next_after(cursor, tz=self._timezone)
                if limit is not None and cursor >= limit:
                    break
                firings.append(cursor)

        # ``sum``, not a count of occurrences: one firing in
        # ``SEPARATE_PER_ASSIGNEE`` mode produces one job **per person**, and a
        # screen saying "3 công việc" over nine real jobs would be wrong in the
        # direction that matters.
        generated = int(
            await self._session.scalar(
                select(func.coalesce(func.sum(PrWorkRecurringOccurrence.work_item_count), 0))
                .select_from(PrWorkRecurringOccurrence)
                .where(PrWorkRecurringOccurrence.template_id == template.id)
            )
            or 0
        )
        recorded = int(
            await self._session.scalar(
                select(func.count())
                .select_from(PrWorkRecurringOccurrence)
                .where(PrWorkRecurringOccurrence.template_id == template.id)
            )
            or 0
        )
        unsettled = int(
            await self._session.scalar(
                select(func.count())
                .select_from(PrWorkRecurringOccurrence)
                .where(PrWorkRecurringOccurrence.template_id == template.id)
                .where(
                    PrWorkRecurringOccurrence.state.in_(
                        ("PENDING", "FAILED_RETRYABLE"),
                    )
                )
            )
            or 0
        )
        return RecurringTemplateDetail(
            template=template,
            work_type=work_type,
            contributor_user_ids=contributors,
            contributor_names=await self._names(contributors),
            schedule_label=schedule.describe(),
            next_occurrences=tuple(firings),
            generated_work_items=generated,
            unsettled_occurrences=unsettled,
            occurrence_count=recorded,
        )

    def _schedule_of(self, command: RecurringTemplateCommand) -> RecurringSchedule:
        """The command's calendar, validated by the domain rather than here."""
        return RecurringSchedule(
            frequency=command.frequency,
            run_time=command.run_time,
            weekdays=normalise_weekdays(command.weekdays),
            day_of_month=command.day_of_month
            if command.frequency is PrRecurringFrequency.MONTHLY
            else None,
        )

    def _validate_dates(self, command: RecurringTemplateCommand) -> None:
        if command.end_date is not None and command.end_date < command.start_date:
            raise PrValidationError(
                "Ngày kết thúc phải sau ngày bắt đầu.",
                details={"field": "end_date", "reason": "date_range_reversed"},
            )

    def _validate_shape(self, work_type: PrWorkType, command: RecurringTemplateCommand) -> None:
        """A per-firing routine needs its quantity; an accumulating one refuses a shared stream.

        The quantity rule is M4B's and unchanged for a routine that files a job
        per firing. An accumulating routine carries **no** quantity - what a
        stream should reach is a KPI target, which lives in the plan - and it
        must be ``SEPARATE_PER_ASSIGNEE``, because a container is one person's
        actual and a shared stream would be one number two people report into.
        """
        if not command.accumulate_by_period:
            self._validate_quantity(work_type, command.quantity)
            return
        if command.assignment_mode is not PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE:
            raise PrValidationError(
                "Việc tích lũy kết quả theo kỳ phải giao riêng cho từng người.",
                details={"field": "assignment_mode", "reason": "container_requires_separate"},
            )

    def _validate_quantity(self, work_type: PrWorkType, quantity: Decimal | None) -> None:
        """The template's half of M4B's quantity boundary.

        The same rule the operational commands enforce, applied one step earlier
        so a manager finds out while writing the routine rather than at 4am when
        the first sweep refuses it. The generator checks it again through
        :meth:`~meobot.application.pr_work_service.PrWorkService.generate_recurring_work`,
        because a rule enforced in exactly one place is a rule one bug away from
        being unenforced - and because a work type's basis can be changed after
        a template was written.
        """
        from meobot.application.pr_work_service import require_quantity_for_basis

        require_quantity_for_basis(work_type, quantity)
        if quantity is not None and quantity <= 0:
            raise PrValidationError(
                "Số lượng phải lớn hơn 0.",
                details={"field": "quantity", "reason": "out_of_range"},
            )

    async def _validated_contributors(
        self, command: RecurringTemplateCommand
    ) -> tuple[uuid.UUID, ...]:
        contributors = tuple(dict.fromkeys(command.contributor_user_ids))
        if not contributors:
            raise PrValidationError(
                "Hãy chọn ít nhất một người thực hiện.",
                details={"field": "contributor_user_ids", "reason": "no_contributor"},
            )
        if len(contributors) > MAX_TEMPLATE_CONTRIBUTORS:
            raise PrValidationError(
                f"Một việc định kỳ không nhận quá {MAX_TEMPLATE_CONTRIBUTORS} người thực hiện.",
                details={
                    "field": "contributor_user_ids",
                    "reason": "too_many_contributors",
                },
            )
        await self._require_active_users(contributors)
        return contributors

    async def _replace_contributors(
        self, template: PrWorkRecurringTemplate, contributors: tuple[uuid.UUID, ...]
    ) -> None:
        await self._session.execute(
            delete(PrWorkRecurringTemplateContributor).where(
                PrWorkRecurringTemplateContributor.template_id == template.id
            )
        )
        for order, user_id in enumerate(contributors):
            self._session.add(
                PrWorkRecurringTemplateContributor(
                    template_id=template.id, user_id=user_id, display_order=order
                )
            )
        await self._session.flush()

    async def _has_occurrences(self, template_id: uuid.UUID) -> bool:
        """Has the scheduler ever reached this template at all?

        One query, over occurrences alone, and that is sufficient rather than
        lazy: an occurrence row is written *before* any work exists and is never
        deleted, so no work item can descend from this template without one. The
        occurrence table is the scheduler's ledger, and asking the ledger is the
        whole reason it is a table.
        """
        return bool(
            await self._session.scalar(
                select(exists().where(PrWorkRecurringOccurrence.template_id == template_id))
            )
        )

    async def _require_template(self, template_id: uuid.UUID) -> PrWorkRecurringTemplate:
        row = await self._session.get(PrWorkRecurringTemplate, template_id)
        if row is None:
            raise PrNotFoundError(
                "Không tìm thấy việc định kỳ.", details={"template_id": str(template_id)}
            )
        return row

    async def _locked(self, template_id: uuid.UUID) -> PrWorkRecurringTemplate:
        row = await lock_row(self._session, PrWorkRecurringTemplate, template_id)
        if row is None:
            raise PrNotFoundError(
                "Không tìm thấy việc định kỳ.", details={"template_id": str(template_id)}
            )
        return row

    async def _require_work_type(self, work_type_id: uuid.UUID) -> PrWorkType:
        row = await self._session.get(PrWorkType, work_type_id)
        if row is None:
            raise PrNotFoundError(
                "Không tìm thấy loại công việc.",
                details={"work_type_id": str(work_type_id)},
            )
        return row

    async def _require_active_users(self, user_ids: Sequence[uuid.UUID]) -> None:
        """Every named person exists and is still here.

        The same check :meth:`~meobot.application.pr_work_service.PrWorkService._create`
        makes, applied at the template so a routine is never activated onto
        somebody who has left - which would otherwise fail every night at 4am
        instead of once, in front of the person who could fix it.
        """
        if not user_ids:
            return
        rows = (
            (
                await self._session.execute(
                    select(User).where(User.id.in_(tuple(dict.fromkeys(user_ids))))
                )
            )
            .scalars()
            .all()
        )
        found = {row.id: row for row in rows}
        for user_id in user_ids:
            user = found.get(user_id)
            if user is None:
                raise PrNotFoundError(
                    "Không tìm thấy người thực hiện.", details={"user_id": str(user_id)}
                )
            if not user.active:
                raise PrValidationError(
                    f"{user.full_name} đã ngừng hoạt động.",
                    details={"field": "contributor_user_ids", "reason": "inactive_user"},
                )

    async def _names(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not user_ids:
            return {}
        rows = (
            (await self._session.execute(select(User).where(User.id.in_(tuple(user_ids)))))
            .scalars()
            .all()
        )
        return {row.id: row.full_name for row in rows}

    def _snapshot(
        self, template: PrWorkRecurringTemplate, contributors: Sequence[uuid.UUID]
    ) -> dict[str, object]:
        """What an audit row records about a template. Configuration, not work."""
        return {
            "name": template.name,
            "work_type_id": str(template.work_type_id),
            "assignment_mode": template.assignment_mode.value,
            "accumulate_by_period": template.accumulate_by_period,
            "quantity": str(template.quantity) if template.quantity is not None else None,
            "frequency": template.frequency.value,
            "weekdays": list(template.weekdays or []),
            "day_of_month": template.day_of_month,
            "run_time": template.run_time.isoformat(),
            "due_after_hours": template.due_after_hours,
            "start_date": template.start_date.isoformat(),
            "end_date": template.end_date.isoformat() if template.end_date else None,
            "status": template.status.value,
            "revision_no": template.revision_no,
            "contributors": [str(one) for one in contributors],
        }


def schedule_of_template(template: PrWorkRecurringTemplate) -> RecurringSchedule:
    """One stored template as the calendar object that fires it.

    A module-level function rather than a method because the generator needs it
    too, and a schedule rebuilt slightly differently in two places is a schedule
    that will eventually fire on two different days.
    """
    return RecurringSchedule(
        frequency=template.frequency,
        run_time=template.run_time,
        weekdays=tuple(template.weekdays or ()),
        day_of_month=template.day_of_month,
    )


def _require_text(value: str, field: str, max_length: int) -> str:
    text = (value or "").strip()
    if not text or len(text) > max_length:
        raise PrValidationError(
            "Nội dung không hợp lệ.", details={"field": field, "reason": "invalid_length"}
        )
    return text


def _optional_text(value: str | None, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise PrValidationError(
            "Nội dung quá dài.", details={"field": field, "reason": "invalid_length"}
        )
    return text


def _require_due_hours(value: int | None) -> int | None:
    """A deadline measured from the occurrence, or none at all.

    ``None`` is a real answer: a routine nobody times is a routine with no
    deadline, and inventing one would make every such job overdue by tomorrow.
    """
    if value is None:
        return None
    if value <= 0 or value > 8760:
        raise PrValidationError(
            "Hạn hoàn thành phải từ 1 đến 8760 giờ.",
            details={"field": "due_after_hours", "reason": "out_of_range"},
        )
    return value


def _require_user_id(actor: Actor) -> uuid.UUID:
    if actor.user_id is None:
        raise PrValidationError(
            "Hành động này cần một tài khoản người dùng.",
            details={"field": "actor", "reason": "actor_has_no_user"},
        )
    return actor.user_id


__all__: list[str] = [
    "PREVIEW_OCCURRENCES",
    "PrWorkRecurringService",
    "RecurringTemplateCommand",
    "RecurringTemplateDetail",
    "schedule_of_template",
]
