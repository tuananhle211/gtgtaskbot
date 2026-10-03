"""Turning active recurring templates into work, safely, once.

Milestone M4B. This is the half of recurring work that runs at four in the
morning with nobody watching, so every decision in it is about what happens when
something goes wrong rather than about what happens when everything is fine.

The traversal, and why it walks rather than enumerates
-------------------------------------------------------

MeoBot's recurrence engine answers exactly one question - *what fires next* -
and this module was written around that primitive rather than around a
missing-occurrence API invented to suit it::

    cursor = template.last_evaluated_occurrence_at
        |
        v
    occurrence = schedule.next_after(cursor)
        |
        +--> past now, past end_date, or batch full?  -> stop
        |
        v
    reserve the occurrence          (its own committed transaction)
        |
        v
    generate the work               (one transaction, with the settle)
        |
        v
    cursor = occurrence             ... and round again

Every step is idempotent, every step is bounded, and the cursor only ever moves
onto ground that has a durable row behind it.

The three transactions, and why they are three
------------------------------------------------

**Reserve, then generate, then settle** - with the reservation committed on its
own and the generation and settlement sharing one:

1. *reserve*: insert the occurrence as ``PENDING``. Committed alone, because a
   worker that dies immediately afterwards must leave evidence that the
   occurrence was owed. This is also where concurrency is decided: the unique
   constraint over ``(template_id, occurrence_key)`` means two workers reserving
   the same firing produce one row and one loser;
2. *generate + settle*: create the work items and move the occurrence to
   ``GENERATED`` **in the same transaction**. The ledger therefore never claims
   work that does not exist. If this transaction fails the occurrence is left
   ``PENDING`` or marked ``FAILED_RETRYABLE``, and the next sweep finds it in
   the retry set;
3. and the cursor advances in a third small write, once the occurrence has *any*
   durable outcome.

Why the cursor may advance past a failure
------------------------------------------

Because advancing past it loses nothing. The occurrence row is the record of the
obligation, and it stays ``FAILED_RETRYABLE`` until it succeeds; the sweep
processes the retry set *before* it walks forward, so a failed Tuesday is
retried on every subsequent sweep whether or not Wednesday has been reached.
Leaving the cursor parked on the failure would have been the other design, and
it has a head-of-line problem: one permanently unfixable occurrence would stop
the routine for ever.

What is refused, permanently
-----------------------------

An occurrence whose scheduled instant falls in a ``CLOSED`` or ``LOCKED``
reporting period is marked ``SKIPPED_CLOSED_PERIOD`` and never attempted again.
Generating into a locked month would move a number underneath a report that
already quotes it, and retrying tomorrow would fail for the identical reason for
ever. The row records which period and why, because *"the routine produced
nothing in August"* is a question somebody asks in September.

An ``OPEN`` period, and a month with no period row at all, both generate.
``period_for`` returning ``None`` is M2's deliberate answer that nobody has set
the month up - an operational fact, not a lock - and refusing to file work
because of it would make a missing configuration row silently delete the
department's work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.application.pr_work_recurring_service import schedule_of_template
from meobot.application.pr_work_result_service import PrWorkResultService
from meobot.application.pr_work_service import CreateWorkCommand, PrWorkService
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkItem
from meobot.db.models.pr_work_recurring import (
    PrWorkRecurringOccurrence,
    PrWorkRecurringTemplate,
)
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.recurring import (
    MAX_CATCH_UP_DAYS,
    MAX_OCCURRENCES_PER_SWEEP,
    UNRESOLVED_OCCURRENCE_STATES,
    PrRecurringOccurrenceState,
    PrRecurringTemplateStatus,
    day_end,
    occurrence_key,
    recurring_source_key,
)
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkAssignmentMode, PrWorkSourceType

logger = get_logger(__name__)

#: How many templates one sweep looks at. Bounded like every other beat task in
#: MeoBot: a sweep that could grow without limit is a sweep that eventually
#: holds a connection open past its timeout.
MAX_TEMPLATES_PER_SWEEP = 200


@dataclass(slots=True)
class SweepOutcome:
    """What one template's sweep did. Returned rather than logged, so a task can report it."""

    template_id: uuid.UUID
    generated: int = 0
    skipped_closed_period: int = 0
    failed: int = 0
    #: Occurrences examined, whatever became of them. The number that says
    #: whether a catch-up is still working through a backlog.
    evaluated: int = 0
    errors: list[str] = field(default_factory=list)


class PrWorkRecurringGenerator:
    """Walks active templates forward and files the work they owe.

    Args:
        session: Unit of work. **This service commits**, unlike every other PR
            service, and that is deliberate: the reserve/generate ordering that
            makes a crash recoverable is an ordering *of commits*, and a caller
            that owned the transaction boundary could not express it. The Celery
            task hands it a session and nothing else shares it.
        audit: Event writer.
        work: M1's own service. **The only way work is created here** - the
            generator has no ``PrWorkItem`` constructor of its own, so a
            generated job goes through the same capability check, the same
            quantity rule, the same active-user check, the same history row and
            the same audit event as one a manager typed in.
        periods: M2's period service, asked one question: is the month this
            occurrence falls in still open?
        timezone: The department's wall clock.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        work: PrWorkService,
        periods: PrWorkPeriodService,
        results: PrWorkResultService | None = None,
        *,
        timezone: ZoneInfo,
    ) -> None:
        self._session = session
        self._audit = audit
        self._work = work
        self._periods = periods
        # Period-container patch. A firing of an accumulating routine ensures
        # the month's containers rather than filing a job; the one service that
        # creates containers is the one used. Optional for the scheduler tests
        # that build this generator without it.
        self._results = results
        self._timezone = timezone

    # =====================================================================
    # The sweep
    # =====================================================================
    async def due_templates(self, *, at: datetime | None = None) -> tuple[uuid.UUID, ...]:
        """Every template a sweep should look at, oldest cursor first.

        Only ``ACTIVE`` ones, which is the whole of "pause generates nothing"
        and "a draft generates nothing" - neither needs a second check anywhere,
        because neither is ever returned here.

        Ordered by cursor so a template that has fallen furthest behind is swept
        first. With the per-sweep bound above, that is what stops one busy
        routine from starving a neglected one.
        """
        moment = at or utcnow()
        statement = (
            select(PrWorkRecurringTemplate.id)
            .where(PrWorkRecurringTemplate.status == PrRecurringTemplateStatus.ACTIVE)
            .where(
                (PrWorkRecurringTemplate.end_date.is_(None))
                | (PrWorkRecurringTemplate.end_date >= moment.astimezone(self._timezone).date())
            )
            .order_by(PrWorkRecurringTemplate.last_evaluated_occurrence_at.asc().nulls_first())
            .limit(MAX_TEMPLATES_PER_SWEEP)
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    async def sweep_template(
        self,
        *,
        template_id: uuid.UUID,
        request_id: uuid.UUID,
        at: datetime | None = None,
    ) -> SweepOutcome:
        """Settle one template's backlog, then walk it forward to now.

        Two phases, and the order matters:

        1. **the retry set first.** Every ``PENDING`` or ``FAILED_RETRYABLE``
           occurrence already on the ledger, oldest first. These are obligations
           the system has already recorded, and a sweep that walked forward
           before settling them could report progress while quietly leaving work
           undone;
        2. **then forward**, one ``next_after`` at a time from the cursor, until
           the next firing is in the future, past the end date, older than
           :data:`~meobot.domain.pr.recurring.MAX_CATCH_UP_DAYS`, or the batch is
           full.

        Returns:
            What happened, so the task can log one line per sweep instead of one
            per occurrence.
        """
        now = at or utcnow()
        outcome = SweepOutcome(template_id=template_id)

        for occurrence_id in await self._unresolved(template_id):
            await self._attempt(occurrence_id, request_id=request_id, outcome=outcome)
            if outcome.evaluated >= MAX_OCCURRENCES_PER_SWEEP:
                return outcome

        while outcome.evaluated < MAX_OCCURRENCES_PER_SWEEP:
            reserved = await self._reserve_next(template_id, now=now)
            if reserved is None:
                break
            await self._attempt(reserved, request_id=request_id, outcome=outcome)
        return outcome

    # =====================================================================
    # Phase one: reserving the next occurrence
    # =====================================================================
    async def _reserve_next(self, template_id: uuid.UUID, *, now: datetime) -> uuid.UUID | None:
        """Walk one step forward and write the occurrence down. Commits.

        ``None`` means there is nothing due: the next firing has not happened
        yet, the routine has ended, or it is so far in the past that catching up
        would file work nobody wants. Each of those advances the cursor to the
        point examined, so the same fruitless walk is not repeated every thirty
        seconds.

        The template row is **locked** for the duration. That is what makes an
        occurrence coherent with respect to a concurrent edit: the revision
        number written onto the occurrence is the one that was true while the
        schedule was read, and an edit committed a microsecond later belongs to
        the next occurrence rather than to half of this one.
        """
        template = await lock_row(self._session, PrWorkRecurringTemplate, template_id)
        if template is None or template.status is not PrRecurringTemplateStatus.ACTIVE:
            # Ended or paused between the sweep's dispatch and this moment, which
            # is ordinary rather than an error. Committed rather than rolled back
            # even though nothing was written: the lock ``lock_row`` took has to
            # be released, and a rollback here would discard whatever the caller
            # had in flight - which is not this method's to decide.
            await self._session.commit()
            return None

        schedule = schedule_of_template(template)
        # ``ensure_utc`` on every stored instant this method compares. The
        # repository convention, and it earns its place here rather than being
        # defensive noise: PostgreSQL returns these aware, and the offline test
        # fixture is SQLite, whose driver drops the offset - so a cursor read
        # back from a committed row is naive there and cannot be compared with a
        # computed firing at all. The rule this module has to be right about is
        # *which instant*, and that is unaffected either way.
        cursor = (
            None
            if template.last_evaluated_occurrence_at is None
            else ensure_utc(template.last_evaluated_occurrence_at)
        )
        if cursor is None:
            # A template can only be ACTIVE by way of ``activate``, which always
            # sets the cursor. A null here is a row written by something else -
            # a fixture, a manual repair - and the safe reading is "start now",
            # never "start at the beginning of time".
            cursor = now
        floor = now - timedelta(days=MAX_CATCH_UP_DAYS)
        if cursor < floor:
            cursor = floor

        firing = schedule.next_after(cursor, tz=self._timezone)
        ends_at = (
            day_end(template.end_date, tz=self._timezone) if template.end_date is not None else None
        )
        if firing > now or (ends_at is not None and firing >= ends_at):
            # Nothing due. Move the cursor up to the ground actually examined so
            # the next sweep does not re-walk it, but never past the firing
            # itself - that would skip it.
            settled = min(now, firing - timedelta(microseconds=1))
            # Compared against the normalised ``cursor`` rather than the raw
            # column, for the reason above and because ``cursor`` may have been
            # floored - moving it back below the catch-up floor would re-open
            # history the floor exists to close.
            if template.last_evaluated_occurrence_at is None or settled > cursor:
                template.last_evaluated_occurrence_at = settled
            await self._session.commit()
            return None

        key = occurrence_key(firing, tz=self._timezone)
        occurrence = PrWorkRecurringOccurrence(
            template_id=template.id,
            occurrence_key=key,
            scheduled_for=firing,
            template_revision_no=template.revision_no,
            state=PrRecurringOccurrenceState.PENDING,
        )
        self._session.add(occurrence)
        template.last_evaluated_occurrence_at = firing
        try:
            await self._session.commit()
        except IntegrityError:
            # Another worker reserved this firing first. Losing the race is the
            # unique constraint working, and the winner's row is the one both
            # workers will now attempt - harmlessly, because generation is
            # idempotent on the work item's own unique index.
            await self._session.rollback()
            existing = await self._session.scalar(
                select(PrWorkRecurringOccurrence.id).where(
                    PrWorkRecurringOccurrence.template_id == template_id,
                    PrWorkRecurringOccurrence.occurrence_key == key,
                )
            )
            if existing is None:
                # Not the race, then. Something else refused this insert, and a
                # constraint violation nobody hears about is the one that gets
                # diagnosed six weeks later.
                raise
            # The rollback took the cursor advance with it, so this walk would
            # otherwise recompute the same firing, collide again, and spin until
            # the batch bound stopped it. The winner has already moved the cursor
            # here; saying so again is idempotent and lets this worker move on.
            await self._advance_cursor(template_id, to=firing)
            return existing
        return occurrence.id

    async def _advance_cursor(self, template_id: uuid.UUID, *, to: datetime) -> None:
        """Move one template's cursor forward, never backwards. Commits.

        Its own tiny transaction because the caller's has just been rolled back
        by a lost race, and ``max`` because two workers arriving here in either
        order must leave the same answer.
        """
        template = await lock_row(self._session, PrWorkRecurringTemplate, template_id)
        if template is not None:
            current = (
                None
                if template.last_evaluated_occurrence_at is None
                else ensure_utc(template.last_evaluated_occurrence_at)
            )
            if current is None or to > current:
                template.last_evaluated_occurrence_at = to
        await self._session.commit()

    async def _unresolved(self, template_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
        """The retry set: occurrences with an outcome still owed, oldest first."""
        statement = (
            select(PrWorkRecurringOccurrence.id)
            .where(PrWorkRecurringOccurrence.template_id == template_id)
            .where(PrWorkRecurringOccurrence.state.in_(tuple(UNRESOLVED_OCCURRENCE_STATES)))
            .order_by(PrWorkRecurringOccurrence.scheduled_for.asc())
            .limit(MAX_OCCURRENCES_PER_SWEEP)
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    # =====================================================================
    # Phase two: turning one occurrence into work
    # =====================================================================
    async def _attempt(
        self, occurrence_id: uuid.UUID, *, request_id: uuid.UUID, outcome: SweepOutcome
    ) -> None:
        """One occurrence, once. Commits on success and on a recorded refusal.

        Every exception is caught, and that is the correct shape for a scheduler
        rather than laziness: the caller is a sweep with other templates to get
        through, and a routine that failed is a fact to record on its own row
        rather than a reason to abandon the department's other twelve routines.
        The failure is recorded as ``FAILED_RETRYABLE`` with its message, which
        is what makes the retry set self-populating.
        """
        outcome.evaluated += 1
        try:
            generated = await self._generate(occurrence_id, request_id=request_id)
        except Exception as error:
            await self._session.rollback()
            await self._record_failure(occurrence_id, error=error)
            outcome.failed += 1
            outcome.errors.append(str(error))
            logger.warning(
                "pr_recurring_occurrence_failed",
                extra={
                    "pr_recurring_occurrence_id": str(occurrence_id),
                    "error": type(error).__name__,
                },
            )
            return
        if generated is None:
            outcome.skipped_closed_period += 1
        else:
            outcome.generated += generated

    async def _generate(self, occurrence_id: uuid.UUID, *, request_id: uuid.UUID) -> int | None:
        """Create the work one occurrence owes, and settle the occurrence with it.

        One transaction for both, so ``GENERATED`` and the work items it claims
        commit together or not at all.

        Returns:
            How many items were created, or ``None`` when the occurrence was
            permanently declined because its reporting period is closed.
        """
        occurrence = await lock_row(self._session, PrWorkRecurringOccurrence, occurrence_id)
        if occurrence is None:
            return 0
        if occurrence.state in {
            PrRecurringOccurrenceState.GENERATED,
            PrRecurringOccurrenceState.SKIPPED_CLOSED_PERIOD,
        }:
            # Settled by whoever won the race. Nothing to do, and saying so is
            # not an error - so the transaction ends by committing nothing
            # rather than by rolling back work this method did not do.
            await self._session.commit()
            return 0

        template = await lock_row(self._session, PrWorkRecurringTemplate, occurrence.template_id)
        if template is None:
            raise RuntimeError("Mẫu định kỳ đã biến mất giữa hai lần quét.")
        # Counted for the paths that commit - a successful generation and a
        # permanent closed-period refusal. A *failed* attempt is counted by
        # ``_record_failure``, whose whole reason for existing is that this
        # transaction will be gone.
        occurrence.attempts += 1
        # See ``_reserve_next`` for why every stored instant is read through
        # ``ensure_utc``. This one is arithmetic on a deadline and a lookup of a
        # reporting month, and both would be an hour out on a naive value.
        scheduled_for = ensure_utc(occurrence.scheduled_for)

        period = await self._periods.period_for(scheduled_for)
        if period is not None:
            occurrence.reporting_period_id = period.id
            if period.status in {PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED}:
                await self._decline_closed_period(
                    occurrence, template, period=period, request_id=request_id
                )
                # ``None`` rather than ``0``: the caller counts a permanent
                # refusal separately from "there was nothing to do", because one
                # of them is an answer somebody will ask about.
                return None

        actor = await self._authorizing_actor(template)
        contributors = await self._contributor_ids(template.id)
        if not contributors:
            raise RuntimeError("Mẫu định kỳ không còn người thực hiện nào.")

        due_at = (
            scheduled_for + timedelta(hours=template.due_after_hours)
            if template.due_after_hours is not None
            else None
        )
        # ``SHARED_WORK`` is one job everybody is on; ``SEPARATE_PER_ASSIGNEE``
        # is one job each. M4A's distinction, read off the template where it is
        # genuinely a stored fact rather than an instruction in a request body.
        batches: tuple[tuple[uuid.UUID | None, tuple[uuid.UUID, ...]], ...] = (
            ((None, contributors),)
            if template.assignment_mode is PrWorkAssignmentMode.SHARED_WORK
            else tuple((one, (one,)) for one in contributors)
        )

        created: list[str] = []
        if template.accumulate_by_period:
            # **A stream, not a job.** The firing's month gets one container per
            # assignee, keyed on this occurrence when it is this firing that
            # opens it; a container that already exists - opened by activation,
            # by an earlier firing, or by somebody reporting into it - is left
            # exactly as it is. The occurrence still settles as ``GENERATED``
            # with the number it actually opened, which is what makes "why is
            # there no work for Tuesday" answerable for a routine that only
            # ever opens one thing a month.
            created.extend(
                await self._ensure_containers(
                    template, occurrence, actor=actor, request_id=request_id, at=scheduled_for
                )
            )
            batches = ()
        for subject, people in batches:
            source_key = recurring_source_key(occurrence.id, user_id=subject)
            if await self._already_generated(source_key):
                # A previous attempt got this far and died before settling. The
                # work exists; do not make a second copy of it.
                continue
            item = await self._work.generate_recurring_work(
                actor=actor,
                request_id=request_id,
                command=CreateWorkCommand(
                    work_type_id=template.work_type_id,
                    title=template.name,
                    description=template.description,
                    priority=template.priority,
                    quantity=template.quantity,
                    due_at=due_at,
                    contributor_user_ids=people,
                ),
                source_key=source_key,
                occurred_at=scheduled_for,
                occurrence_id=occurrence.id,
            )
            created.append(item.code)

        occurrence.state = PrRecurringOccurrenceState.GENERATED
        occurrence.generated_at = utcnow()
        occurrence.work_item_count = len(created) if template.accumulate_by_period else len(batches)
        occurrence.last_error = None
        template.last_generated_at = occurrence.generated_at

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RECURRING_GENERATED,
            entity_type="pr_work_recurring_occurrence",
            entity_id=occurrence.id,
            after={
                "template_id": str(template.id),
                "template_revision_no": occurrence.template_revision_no,
                "occurrence_key": occurrence.occurrence_key,
                "scheduled_for": scheduled_for.isoformat(),
                "assignment_mode": template.assignment_mode.value,
                "work_item_codes": created,
                "work_item_count": occurrence.work_item_count,
            },
        )
        await self._session.commit()
        logger.info(
            "pr_recurring_work_generated",
            extra={
                "pr_recurring_template_id": str(template.id),
                "pr_recurring_occurrence_key": occurrence.occurrence_key,
                "pr_work_items": len(created),
            },
        )
        return len(created)

    async def _ensure_containers(
        self,
        template: PrWorkRecurringTemplate,
        occurrence: PrWorkRecurringOccurrence,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        at: datetime,
    ) -> list[str]:
        """Open the containers one firing of an accumulating routine owes."""
        if self._results is None:
            raise RuntimeError("Mẫu tích lũy theo kỳ cần dịch vụ kết quả để tạo công việc.")
        period = await self._results.period_for_moment(at, actor=actor, request_id=request_id)
        codes: list[str] = []
        for user_id in await self._contributor_ids(template.id):
            if (
                await self._results.container_for(
                    work_type_id=template.work_type_id,
                    subject_user_id=user_id,
                    period_id=period.id,
                )
                is not None
            ):
                continue
            item = await self._results.ensure_container(
                actor=actor,
                request_id=request_id,
                work_type_id=template.work_type_id,
                subject_user_id=user_id,
                period=period,
                source_type=PrWorkSourceType.RECURRING,
                source_key=recurring_source_key(occurrence.id, user_id=user_id),
                recurring_occurrence_id=occurrence.id,
                title=template.name,
                description=template.description,
                assigned_by_user_id=actor.user_id,
            )
            codes.append(item.code)
        return codes

    async def _decline_closed_period(
        self,
        occurrence: PrWorkRecurringOccurrence,
        template: PrWorkRecurringTemplate,
        *,
        period: PrReportingPeriod,
        request_id: uuid.UUID,
    ) -> None:
        """Refuse an occurrence whose month is closed. **Terminal, and once.**

        Not a failure and not a retry: the reason will be true for ever, so the
        row is settled rather than left in the retry set. The message is written
        for a person, because the question this answers - *"why did the daily
        seeding routine produce nothing for the last week of August"* - is asked
        by whoever noticed, not by whoever reads logs.
        """
        code = period.code
        occurrence.state = PrRecurringOccurrenceState.SKIPPED_CLOSED_PERIOD
        occurrence.generated_at = None
        occurrence.work_item_count = 0
        occurrence.last_error = f"Kỳ báo cáo {code} đã chốt nên không tạo việc lùi ngày vào kỳ này."
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=await self._authorizing_actor(template),
            action=AuditAction.PR_WORK_RECURRING_SKIPPED,
            entity_type="pr_work_recurring_occurrence",
            entity_id=occurrence.id,
            after={
                "template_id": str(template.id),
                "occurrence_key": occurrence.occurrence_key,
                "scheduled_for": ensure_utc(occurrence.scheduled_for).isoformat(),
                "reason": "SKIPPED_CLOSED_PERIOD",
                "reporting_period": code,
                "reporting_period_status": period.status.value,
            },
        )
        await self._session.commit()
        logger.info(
            "pr_recurring_occurrence_skipped_closed_period",
            extra={
                "pr_recurring_template_id": str(template.id),
                "pr_recurring_occurrence_key": occurrence.occurrence_key,
            },
        )

    async def _record_failure(self, occurrence_id: uuid.UUID, *, error: Exception) -> None:
        """Mark one occurrence retryable, in its own transaction.

        Its own transaction because the one that failed has been rolled back,
        and a failure nobody could record would be a failure nobody could see.
        """
        occurrence = await self._session.get(PrWorkRecurringOccurrence, occurrence_id)
        if occurrence is None:
            return
        if occurrence.state not in UNRESOLVED_OCCURRENCE_STATES:
            # Somebody settled it while this attempt was failing. Their outcome
            # stands; overwriting it with a failure would be the loser of a race
            # deciding the result.
            await self._session.commit()
            return
        occurrence.state = PrRecurringOccurrenceState.FAILED_RETRYABLE
        # Counted **here** rather than in ``_generate``, because the transaction
        # that tried and failed has been rolled back and took its increment with
        # it. A retry counter that resets on every failure would say nothing.
        occurrence.attempts += 1
        occurrence.last_error = str(error)[:2000]
        await self._session.commit()

    # =====================================================================
    # Internals
    # =====================================================================
    async def _already_generated(self, source_key: str) -> bool:
        """Does the work this key names already exist?

        A read before the write, and **not** the guarantee - the guarantee is
        ``uq_pr_work_items_source``. This exists so the common retry path
        returns quietly instead of raising an ``IntegrityError`` and rolling
        back a transaction that had other assignees still to file.
        """
        return bool(
            await self._session.scalar(
                select(PrWorkItem.id).where(PrWorkItem.source_key == source_key).limit(1)
            )
        )

    async def _authorizing_actor(self, template: PrWorkRecurringTemplate) -> Actor:
        """The manager whose standing authorization this template is.

        Resolved from ``activated_by_user_id`` rather than from the worker
        process, so a routine job's audit trail and its ``assigned_by_user_id``
        both name a person who decided something. If that account is gone or
        deactivated the generation fails and says so - which is the right
        outcome, because work cannot be assigned on an authority that no longer
        exists, and the occurrence stays in the retry set where somebody can see
        it.
        """
        if template.activated_by_user_id is None:
            raise RuntimeError("Mẫu định kỳ chưa có người phê duyệt nên không thể sinh việc.")
        user = await self._session.get(User, template.activated_by_user_id)
        if user is None or not user.active:
            raise RuntimeError(
                "Người kích hoạt mẫu định kỳ không còn hoạt động nên không thể giao việc."
            )
        return Actor(user_id=user.id, full_name=user.full_name, role=user.role, active=True)

    async def _contributor_ids(self, template_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
        from meobot.db.models.pr_work_recurring import PrWorkRecurringTemplateContributor

        statement = (
            select(PrWorkRecurringTemplateContributor.user_id)
            .where(PrWorkRecurringTemplateContributor.template_id == template_id)
            .order_by(PrWorkRecurringTemplateContributor.display_order)
        )
        return tuple((await self._session.execute(statement)).scalars().all())


__all__: list[str] = ["MAX_TEMPLATES_PER_SWEEP", "PrWorkRecurringGenerator", "SweepOutcome"]
