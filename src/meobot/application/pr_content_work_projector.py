"""Content → Work. **The only place content becomes work.** M3.

One service, because the mapping is one argument: which content milestones are
real jobs, whose they are, and whether the source supplied somebody independent
to confirm them. Spreading any of it across a router, a Celery task and two UIs
would put the anti-gaming boundary in a seam - the same reasoning
:mod:`meobot.application.pr_work_service` is one file for.

State-convergent, not event-driven
-----------------------------------

The projector does **not** insert on an event. It reads the current canonical
source facts for one content item and makes the Work Ledger match them::

    read the source  ─►  what should exist?  ─►  make it so

That is what lets it be run once, ten times, after a worker retry, after an
application restart, after an undo and after a redo, with the same final result.
The alternative - *"a HEAD_REVIEW approval happened, insert a work item"* - is
correct exactly once and wrong every other time: a retry duplicates, an undo
leaves counted work for a deliverable nobody accepts, and a redo duplicates
again.

Convergence is also what makes the reconcile path cheap to offer. "Catch up on
this content" and "handle this event" are the same operation, so there is no
second implementation to keep in step with the first.

What it must never do
----------------------

**Decide eligibility.** Not one line here reads a quota, a plan, an allocation
or a ``quota_status``, and it must not start: a missing allocation row means
``NO_QUOTA``, ``PENDING_EVALUATION`` or *a projection failed*, and only M2 can
tell which - see ``docs/pr/WORK_QUOTA_M2.md`` §6b. M3 produces trustworthy
``COUNTED`` work and hands over; whatever M2 then says, M3 behaves identically.

**Write work columns directly.** Every state change goes through
:class:`~meobot.application.pr_work_service.PrWorkService`, which owns the
ladder, ``counted_at``, the history, the audit row and the M2 handoff as one
ordered transaction. A projector reproducing that order in its own SQL would be
a second implementation of the rule the module is built to have one of.

**Guess.** A milestone whose contributor cannot be reconstructed produces
:attr:`~meobot.domain.pr.content_work.PrContentWorkOutcome.UNRESOLVED_CONTRIBUTOR`
and nothing else. Crediting the wrong person is worse than crediting nobody, and
afterwards it looks exactly like a correct row.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_content_work_service import (
    ContentWorkResolver,
    PrContentWorkRuleService,
)
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.application.pr_work_result_service import PrWorkResultService
from meobot.application.pr_work_service import PrWorkService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_content_work import PrContentWorkProjection
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.content_work import (
    AUTO_WORK_TYPE_CATEGORIES,
    AUTO_WORK_TYPE_UNIT,
    AUTOMATIC_KINDS,
    KIND_STAGE_MILESTONES,
    SELF_RECORDED_KINDS,
    PrContentWorkKind,
    PrContentWorkOutcome,
    PrContentWorkProjectionStatus,
    auto_work_type_code,
    auto_work_type_name,
    content_work_source_key,
)
from meobot.domain.pr.errors import PrNotFoundError
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import (
    PrWorkCountStatus,
    PrWorkSourceType,
    PrWorkStatus,
)
from meobot.domain.pr.work_results import (
    PrWorkResultSource,
    source_may_restore,
    source_may_reverse_count,
)
from meobot.domain.pr.workflow import PrTransitionTrigger

logger = get_logger(__name__)

#: How many content items one reconcile request may sweep.
#:
#: Bounded because a reconcile is a write per content item inside one
#: transaction, and because *"reconcile everything"* is the request that turns
#: into an unreviewed backfill of the department's whole history. A hundred is
#: several days of the department's output.
MAX_RECONCILE_CONTENT = 200

#: How many pending rows one sweeper pass claims. Small and frequent, matching
#: ``pr.sweep_ai_review_runs``: a claim that commits and then dispatches is
#: cheap to repeat and expensive to make large.
SWEEP_BATCH = 25


async def request_content_work_projection(
    session: AsyncSession, content_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    """Note that this content may need projecting. **The trigger.** M3.

    A module-level function rather than a method, and that is what lets the
    content workflow call it. The workflow service is built before the work
    services in the bundle and has no business holding a projector; what it
    needs is one upsert, and this is one upsert.

    **Called from inside the content transaction that changed something**, which
    is the whole of the delivery guarantee. If the transition committed, so did
    the request - a worker that was down for an hour finds the row waiting rather
    than having missed a window it has no record of, and there is no after-commit
    hook for anything to be lost in.

    Deliberately **total**: no validation, no capability check, no content read,
    nothing that can raise a domain error. Part J's rule is that a successful
    content operation must never fail because work projection has an opinion, and
    the way to keep that true is for the thing inside the content transaction to
    have no opinions.

    A row already ``RUNNING`` is moved back to ``PENDING`` rather than left
    alone: the worker holding it is looking at facts that have since changed, and
    its settlement must not be the last word.
    """
    moment = now or utcnow()
    row = (
        (
            await session.execute(
                select(PrContentWorkProjection).where(
                    PrContentWorkProjection.content_id == content_id
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if row is None:
        session.add(
            PrContentWorkProjection(
                content_id=content_id,
                status=PrContentWorkProjectionStatus.PENDING,
                requested_at=moment,
            )
        )
    else:
        row.status = PrContentWorkProjectionStatus.PENDING
        row.requested_at = moment
    await session.flush()


@dataclass(frozen=True, slots=True)
class SourceMilestone:
    """One qualifying content milestone, reduced to the facts work needs.

    Every field is a **historical** fact read from a row that does not change
    under it - not from the content item's current columns. That is Part Q's
    requirement made structural: ``contributor_user_id`` comes from the version
    that was approved or the submission that was accepted, never from today's
    ``owner_user_id`` or ``producer_user_id``, so a reassignment next week
    rewrites nobody's credit.
    """

    kind: PrContentWorkKind
    #: What the source key names: the content item, for both kinds V1 projects.
    #: (The retired ``PUBLICATION`` kind named the publication row instead, and
    #: :func:`content_work_source_key` still resolves it so historical work keeps
    #: its identity.)
    entity_id: uuid.UUID
    #: Whose work it is. ``None`` when the source genuinely cannot say.
    contributor_user_id: uuid.UUID | None
    #: Who confirmed it at source. ``None`` for a self-recorded kind, which is
    #: not the same as "nobody" - it means *the source has no second person to
    #: offer*, and the work waits for one through the Work module.
    validated_by_user_id: uuid.UUID | None
    #: **When the business event happened.** Never the projector's clock - see
    #: :meth:`PrContentWorkProjector._settle`. For ``PRODUCTION`` this is the
    #: moment the cut was handed in, which is when the *workload* happened.
    occurred_at: datetime
    title: str
    #: **When the independent validation happened**, or ``None`` when it has not.
    #:
    #: M3.1 split this away from :attr:`occurred_at`, and the split is the
    #: milestone: an editor submits at one instant and somebody accepts at
    #: another, so the workload belongs to the first and the ``COUNTED``
    #: contribution belongs to the second. For ``CONTENT_CREATION`` the two are
    #: the same instant, because the head's approval is both facts at once.
    validated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """What one run concluded about one semantic piece of work."""

    kind: PrContentWorkKind
    outcome: PrContentWorkOutcome
    source_key: str | None = None
    work_item_id: uuid.UUID | None = None
    detail: str | None = None
    #: The work type the mapping resolved to for this milestone, when it
    #: resolved one. ``None`` on a refusal, on a reversal, and on a dry run that
    #: would provision the type. Read by the maintenance preview, which compares
    #: it with the type an existing result is filed under.
    work_type_id: uuid.UUID | None = None
    #: Whose work the source says it is. ``None`` when the source cannot say.
    contributor_user_id: uuid.UUID | None = None
    #: Whether this run provisioned - or, on a dry run, would provision - the
    #: work type and its mapping.
    provisioned: bool = False


@dataclass(frozen=True, slots=True)
class WorkTypeResolution:
    """What the projector found, or made, to file one milestone under.

    Three shapes. A ``work_type_id`` is the answer - configured, or provisioned
    this run when ``provisioned`` is set. ``would_provision`` is a dry run's
    honest version of the second: nothing was written, and a real run would
    create the type and the binding. A ``refusal`` is the reason there is no
    answer, and the caller reports it as ``NO_MAPPING`` verbatim.
    """

    work_type_id: uuid.UUID | None = None
    provisioned: bool = False
    would_provision: bool = False
    refusal: str | None = None


@dataclass(frozen=True, slots=True)
class ContentProjectionReport:
    """Everything one content item's projection concluded, per kind."""

    content_id: uuid.UUID
    content_code: str
    results: tuple[ProjectionResult, ...] = ()

    @property
    def worst(self) -> PrContentWorkOutcome:
        """The outcome a queue row should record for the item as a whole.

        The **least settled** of them, by the order in
        :data:`_OUTCOME_SEVERITY`, so a piece whose script projected cleanly and
        whose production has no mapping is reported as needing attention rather
        than as done. A report that showed only the good half would be silence
        dressed as success.
        """
        if not self.results:
            return PrContentWorkOutcome.NOT_QUALIFIED
        return max(
            (one.outcome for one in self.results), key=lambda outcome: _OUTCOME_SEVERITY[outcome]
        )


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """What a bounded catch-up did, counted by outcome.

    Every field is a **count of semantic work results**, not of content items: a
    piece whose script counted and whose production is unmapped contributes to
    two of them, which is the honest shape - the operator's next action is about
    the unmapped production, not about the piece.
    """

    requested: int = 0
    content_items: int = 0
    dry_run: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    reports: tuple[ContentProjectionReport, ...] = ()


#: How far from "done" each outcome is. Used only by :attr:`ContentProjectionReport.worst`.
_OUTCOME_SEVERITY: dict[PrContentWorkOutcome, int] = {
    PrContentWorkOutcome.UNCHANGED: 0,
    PrContentWorkOutcome.NOT_QUALIFIED: 1,
    PrContentWorkOutcome.PROJECTED: 2,
    PrContentWorkOutcome.REVERSED: 3,
    PrContentWorkOutcome.HELD_BY_VALIDATOR: 3,
    PrContentWorkOutcome.PENDING_VALIDATION: 4,
    PrContentWorkOutcome.NO_MAPPING: 5,
    PrContentWorkOutcome.UNRESOLVED_CONTRIBUTOR: 6,
    PrContentWorkOutcome.BLOCKED_BY_PERIOD: 7,
}


class PrContentWorkProjector:
    """Makes the Work Ledger match what the content workflow says happened.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        rules: The Content → Work mapping. Asked once per run for a resolver
            rather than once per candidate.
        work: **The only writer of work state.** Every ladder move, every
            ``counted_at`` and the M2 handoff go through it.
        periods: Resolves a milestone instant to a reporting period, so the
            projector can refuse to rewrite a month that has been agreed.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        rules: PrContentWorkRuleService,
        work: PrWorkService,
        periods: PrWorkPeriodService,
        results: PrWorkResultService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._rules = rules
        self._work = work
        self._periods = periods
        # Period-container patch. Content milestones now contribute **results**
        # to the writer's or editor's monthly stream rather than one work item
        # each. Optional so a projector built without it behaves as M3.1 did.
        self._results = results

    # =====================================================================
    # Requesting
    # =====================================================================
    async def request(self, content_id: uuid.UUID, *, now: datetime | None = None) -> None:
        """Note that this content may need projecting. **Idempotent.**

        The same operation the content workflow calls, reached through the
        service for callers that already hold one - see
        :func:`request_content_work_projection`, which is the single
        implementation.
        """
        await request_content_work_projection(self._session, content_id, now=now)

    async def resolver(self) -> ContentWorkResolver:
        """The active mapping, loaded once, for a caller running several projections.

        What :meth:`reconcile` builds for itself; exposed so the maintenance
        service can run one preview or one rebuild against one picture of the
        configuration, which is what makes its counts mean something.
        """
        return await self._rules.resolver()

    async def claim_batch(
        self, *, limit: int = SWEEP_BATCH, now: datetime | None = None
    ) -> Sequence[uuid.UUID]:
        """Take a bounded batch of pending content ids for a worker.

        Claim then dispatch, in that order and in different transactions - the
        shape ``pr.sweep_ai_review_runs`` already uses. A dispatch lost after the
        claim leaves the row ``RUNNING``, which :meth:`recover_stale` collects: a
        bounded, visible failure rather than a projection that silently never
        happens.
        """
        moment = now or utcnow()
        statement = (
            select(PrContentWorkProjection)
            .where(PrContentWorkProjection.status == PrContentWorkProjectionStatus.PENDING)
            .order_by(PrContentWorkProjection.requested_at.asc())
            .limit(max(1, min(limit, MAX_RECONCILE_CONTENT)))
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        for row in rows:
            row.status = PrContentWorkProjectionStatus.RUNNING
            row.claimed_at = moment
            row.attempts += 1
        await self._session.flush()
        return [row.content_id for row in rows]

    async def recover_stale(self, *, older_than: datetime) -> int:
        """Return claims stranded ``RUNNING`` by a worker that died.

        Back to ``PENDING`` rather than ``FAILED``: nothing about the content
        changed, and the projector is convergent, so the honest state is *"still
        needs looking at"*.
        """
        statement = select(PrContentWorkProjection).where(
            PrContentWorkProjection.status == PrContentWorkProjectionStatus.RUNNING,
            PrContentWorkProjection.claimed_at < older_than,
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        for row in rows:
            row.status = PrContentWorkProjectionStatus.PENDING
        await self._session.flush()
        return len(rows)

    # =====================================================================
    # Projecting
    # =====================================================================
    async def project_content(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        resolver: ContentWorkResolver | None = None,
        dry_run: bool = False,
    ) -> ContentProjectionReport:
        """Make one content item's work match its source. **Idempotent.**

        The whole projector, and the only method that writes work. Everything
        else in this class is queueing, batching or reporting.

        For each semantic kind it asks two questions and acts on the pair:

        1. **does a qualifying milestone exist right now?** Read from the live
           transition history, so an undone approval simply is not one any more.
           There is no separate "reversal event" to notice;
        2. **what does the ledger say?** Then it converges - creating the work
           item, counting it if the source supplied somebody independent, or
           taking it back out of the count if the milestone has gone.

        ``dry_run`` runs the reads and the decisions and writes nothing, so an
        operator can see what a catch-up would do before doing it.
        """
        content = await self._session.get(PrContentItem, content_id)
        if content is None:
            raise PrNotFoundError(
                "Không tìm thấy nội dung.",
                details={"entity": "pr_content_item", "id": str(content_id)},
            )
        mapping = resolver if resolver is not None else await self._rules.resolver()

        results: list[ProjectionResult] = []
        for milestone in await self._milestones(content):
            results.append(
                await self._converge(
                    actor=actor,
                    request_id=request_id,
                    content=content,
                    milestone=milestone,
                    resolver=mapping,
                    dry_run=dry_run,
                )
            )
        # Work whose milestone has since vanished entirely - a head approval
        # that was undone produces no milestone at all, so nothing above looks
        # at it. Found by walking what the ledger already has rather than what
        # the source now says, which is the only way to notice an absence.
        live = {one.entity_id for one in await self._milestones(content)}
        results.extend(
            await self._converge_orphans(
                actor=actor,
                request_id=request_id,
                content=content,
                live=live,
                dry_run=dry_run,
            )
        )
        results.extend(
            await self._converge_orphan_results(
                actor=actor, request_id=request_id, content=content, live=live, dry_run=dry_run
            )
        )
        return ContentProjectionReport(
            content_id=content.id, content_code=content.code, results=tuple(results)
        )

    async def settle(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        resolver: ContentWorkResolver | None = None,
    ) -> ContentProjectionReport:
        """Project one content item and record the outcome on its queue row.

        What the worker calls. The queue row's ``requested_at`` is **not**
        reset, so a content item that changed while the worker held it stays
        ``PENDING`` and is looked at again - see :meth:`request`.
        """
        report = await self.project_content(
            actor=actor, request_id=request_id, content_id=content_id, resolver=resolver
        )
        row = (
            (
                await self._session.execute(
                    select(PrContentWorkProjection).where(
                        PrContentWorkProjection.content_id == content_id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if row is not None and row.status is PrContentWorkProjectionStatus.RUNNING:
            row.status = PrContentWorkProjectionStatus.SETTLED
            row.settled_at = utcnow()
            row.last_outcome = report.worst
            row.last_error_code = None
            await self._session.flush()
        return report

    async def reconcile(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_ids: Sequence[uuid.UUID] | None = None,
        limit: int = 50,
        dry_run: bool = False,
    ) -> ReconcileReport:
        """A **bounded, explicit** catch-up over content that already happened.

        Not a backfill. The migration wrote nothing and this is what replaces
        it: somebody names the content, or accepts the bounded default, and
        watches what happens. That is the difference between a decision and a
        migration that quietly credited two years of work to whoever owns the
        rows today.

        Without ``content_ids`` the default is *"content with a qualifying
        milestone inside a currently-open reporting period"* - the only window in
        which anything can legitimately change. Closed months are untouched by
        construction rather than by a filter somebody could forget.
        """
        await self._require_configure(actor)
        mapping = await self._rules.resolver()
        targets = (
            list(dict.fromkeys(content_ids))[:MAX_RECONCILE_CONTENT]
            if content_ids
            else await self._open_period_candidates(limit=min(limit, MAX_RECONCILE_CONTENT))
        )

        reports: list[ContentProjectionReport] = []
        counts: dict[str, int] = {outcome.value: 0 for outcome in PrContentWorkOutcome}
        for target in targets:
            report = await self.project_content(
                actor=actor,
                request_id=request_id,
                content_id=target,
                resolver=mapping,
                dry_run=dry_run,
            )
            reports.append(report)
            for result in report.results:
                counts[result.outcome.value] += 1

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_WORK_RECONCILED,
            entity_type="pr_content_work_projection",
            entity_id=targets[0] if targets else uuid.UUID(int=0),
            after={
                "content_items": len(targets),
                "content_ids": [str(one) for one in targets],
                "dry_run": dry_run,
                **counts,
            },
        )
        logger.info(
            "pr_content_work_reconciled",
            extra={"pr_content_items": len(targets), "dry_run": dry_run, **counts},
        )
        return ReconcileReport(
            requested=len(targets),
            content_items=len(targets),
            dry_run=dry_run,
            counts=counts,
            reports=tuple(reports),
        )

    # =====================================================================
    # Reading the source
    # =====================================================================
    async def _milestones(self, content: PrContentItem) -> list[SourceMilestone]:
        """Every qualifying milestone this content has **right now**.

        The convergence point. Nothing here looks at what happened; it looks at
        what is still true, so an undone approval produces no milestone and the
        work that came from it is reconciled out by the caller without any
        reversal event needing to be noticed.
        """
        found: list[SourceMilestone] = []
        creation = await self._stage_milestone(content, PrContentWorkKind.CONTENT_CREATION)
        if creation is not None:
            found.append(creation)
        production = await self._production_milestone(content)
        if production is not None:
            found.append(production)
        # **No publication milestones. M3.1.** See
        # :data:`~meobot.domain.pr.content_work.AUTOMATIC_KINDS`. Work already
        # projected under that kind is left alone rather than reconciled away -
        # see :meth:`_converge_orphans`.
        return found

    async def _stage_milestone(
        self, content: PrContentItem, kind: PrContentWorkKind
    ) -> SourceMilestone | None:
        """The live approval milestone for a stage-driven kind, or ``None``.

        *Live* is the whole of it: the newest transition on the qualifying edge
        whose ``reversed_by_event_id`` is null. An undo sets that column on the
        original in the same flush as the backward stage change, so *"has this
        been taken back"* is one predicate rather than a scan for a matching
        reversal.

        The **newest** rather than the first, because a milestone genuinely
        reversed and re-made is a new business event with a new instant - which
        is the timestamp a redo must count under. See
        ``docs/pr/CONTENT_WORK_PROJECTION_M3.md`` on why that is not
        double-counting.
        """
        from_stage, to_stage, _gate = KIND_STAGE_MILESTONES[kind]
        statement = (
            select(PrContentTransitionEvent)
            .where(
                PrContentTransitionEvent.content_id == content.id,
                PrContentTransitionEvent.from_stage == from_stage,
                PrContentTransitionEvent.to_stage == to_stage,
                PrContentTransitionEvent.trigger == PrTransitionTrigger.HUMAN_APPROVAL,
                PrContentTransitionEvent.reversed_by_event_id.is_(None),
            )
            .order_by(PrContentTransitionEvent.created_at.desc())
            .limit(1)
        )
        event = (await self._session.execute(statement)).scalars().one_or_none()
        if event is None:
            return None

        approval = (
            await self._session.get(PrApprovalEvent, event.approval_event_id)
            if event.approval_event_id is not None
            else None
        )
        if approval is None:
            # A qualifying edge with no approval behind it should be
            # unreachable - the matrix only lets ``HUMAN_APPROVAL`` drive it -
            # but reading the reviewer out of thin air is not the fallback.
            return None

        contributor = await self._contributor_for(kind, content, event, approval)
        return SourceMilestone(
            kind=kind,
            entity_id=content.id,
            contributor_user_id=contributor,
            validated_by_user_id=approval.reviewer_user_id,
            # **The reviewer's own instant**, not the row's birthday: a decision
            # taken in a meeting is recorded afterwards, and the work belongs to
            # the month it was decided in.
            occurred_at=approval.decided_at,
            title=content.title,
            # The two facts coincide for a stage-driven kind: the approval is
            # both "the deliverable is accepted" and "somebody independent said
            # so". ``PRODUCTION`` is where they come apart - see
            # :meth:`_production_milestone`.
            validated_at=approval.decided_at,
        )

    async def _contributor_for(
        self,
        kind: PrContentWorkKind,
        content: PrContentItem,
        event: PrContentTransitionEvent,
        approval: PrApprovalEvent,
    ) -> uuid.UUID | None:
        """Whose work the milestone was, from an **immutable historical** row.

        Never from the content item's current columns, and that is Part Q rather
        than fastidiousness: ``owner_user_id`` and ``producer_user_id`` both
        change, and reading them during a catch-up would credit today's owner
        for a script somebody else wrote two months ago - a wrong row that looks
        exactly like a right one.

        * ``CONTENT_CREATION`` -> the **author of the version that was
          approved**. The transition event pins the version current at the
          moment the stage changed, and ``pr_content_versions`` is append-only
          with an immutable ``created_by_user_id``;
        * ``PRODUCTION`` -> the **producer of the submission that was
          accepted**, copied onto the submission at hand-over precisely *"so a
          later reassignment does not rewrite who made this file"*.

        ``None`` where the chain breaks - a pre-versioning content item, an
        internal review with no submission on it. The caller reports
        ``UNRESOLVED_CONTRIBUTOR`` and writes nothing.
        """
        if kind is PrContentWorkKind.CONTENT_CREATION:
            if event.content_version_id is None:
                return None
            version = await self._session.get(PrContentVersion, event.content_version_id)
            return version.created_by_user_id if version is not None else None

        submission_id = approval.production_submission_id or event.production_submission_id
        if submission_id is None:
            return None
        submission = await self._session.get(PrProductionSubmission, submission_id)
        return submission.producer_user_id if submission is not None else None

    async def _production_milestone(self, content: PrContentItem) -> SourceMilestone | None:
        """The editing job, from *Gửi bản dựng* rather than from its acceptance.

        **The M3.1 milestone.** M3 keyed this on the internal approval, which
        made a finished cut invisible until a reviewer got to it: an editor who
        handed in on Friday had no workload recorded until Monday, and a queue of
        unreviewed cuts looked like a queue of people who had done nothing.

        So the qualifying fact is the **submission** - one
        ``pr_production_submissions`` row exists, which is exactly what
        :meth:`PrProductionService.submit_production` writes and what "Gửi bản
        dựng" means. Not a producer assignment, not the start of production, not
        an artifact pasted somewhere: the hand-in.

        Two instants, deliberately:

        * :attr:`SourceMilestone.occurred_at` - the cut was handed in. The work
          item is created ``COMPLETED`` on it;
        * :attr:`SourceMilestone.validated_at` - somebody accepted it. ``None``
          until then, which is what leaves the contribution uncounted and the
          board saying *"chờ xác nhận"*.

        **Submissions are never deleted.** ``PrUndoService`` refuses to undo the
        handoff while one exists, so this milestone does not un-qualify once it
        has qualified - which is correct, because the editor did the work
        whatever happens to the cut afterwards. What *can* be withdrawn is the
        acceptance, and :meth:`_settle` takes the count back out when it is.

        The **latest** submission supplies the contributor and the instant: V2 is
        the same job as V1 rather than a second one, and the current cut is the
        job as it stands. The source key does not mention the submission at all
        - see :func:`content_work_source_key` - so four versions converge on one
        work item.
        """
        submission = (
            (
                await self._session.execute(
                    select(PrProductionSubmission)
                    .where(PrProductionSubmission.content_id == content.id)
                    .order_by(PrProductionSubmission.submission_no.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one_or_none()
        )
        if submission is None:
            return None

        # The acceptance, if it is still in force. Same "live transition" rule
        # the stage-driven kinds use: an undone approval sets
        # ``reversed_by_event_id`` and stops being a validator, which is what
        # sends the contribution back out of the count.
        from_stage, to_stage, _gate = KIND_STAGE_MILESTONES[PrContentWorkKind.PRODUCTION]
        event = (
            (
                await self._session.execute(
                    select(PrContentTransitionEvent)
                    .where(
                        PrContentTransitionEvent.content_id == content.id,
                        PrContentTransitionEvent.from_stage == from_stage,
                        PrContentTransitionEvent.to_stage == to_stage,
                        PrContentTransitionEvent.trigger == PrTransitionTrigger.HUMAN_APPROVAL,
                        PrContentTransitionEvent.reversed_by_event_id.is_(None),
                    )
                    .order_by(PrContentTransitionEvent.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one_or_none()
        )
        approval = (
            await self._session.get(PrApprovalEvent, event.approval_event_id)
            if event is not None and event.approval_event_id is not None
            else None
        )

        # Credit follows the accepted cut when there is one, and the current cut
        # otherwise. Both come from ``producer_user_id``, copied onto the
        # submission at hand-over precisely so a later reassignment does not
        # rewrite who made the file.
        accepted_id = approval.production_submission_id if approval is not None else None
        accepted = (
            await self._session.get(PrProductionSubmission, accepted_id)
            if accepted_id is not None
            else None
        )
        producer = (accepted or submission).producer_user_id

        return SourceMilestone(
            kind=PrContentWorkKind.PRODUCTION,
            entity_id=content.id,
            contributor_user_id=producer,
            validated_by_user_id=approval.reviewer_user_id if approval is not None else None,
            occurred_at=submission.created_at,
            title=content.title,
            validated_at=approval.decided_at if approval is not None else None,
        )

    # =====================================================================
    # Converging
    # =====================================================================
    async def _converge(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        milestone: SourceMilestone,
        resolver: ContentWorkResolver,
        dry_run: bool,
    ) -> ProjectionResult:
        """Make one semantic piece of work match one live milestone."""
        source_key = content_work_source_key(milestone.kind, milestone.entity_id)
        existing = await self._work_item(source_key)

        # **Legacy work items converge as they always did; everything new is a
        # result.** A milestone that already has its own work item - created
        # before the period-container patch - keeps it, so history stays
        # readable and countable. A milestone with none contributes one result
        # to the contributor's stream for the month, which is the rule this
        # patch implements: *one container, many results, never one work item
        # per content item*.
        if existing is None and self._results is not None:
            return await self._converge_result(
                actor=actor,
                request_id=request_id,
                content=content,
                milestone=milestone,
                resolver=resolver,
                source_key=source_key,
                dry_run=dry_run,
            )

        if milestone.contributor_user_id is None:
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.UNRESOLVED_CONTRIBUTOR,
                source_key=source_key,
                work_item_id=existing.id if existing else None,
                detail="the source does not record who did this work",
            )

        if existing is None:
            resolution = await self._resolve_work_type(
                actor=actor,
                request_id=request_id,
                content=content,
                kind=milestone.kind,
                resolver=resolver,
                dry_run=dry_run,
            )
            if resolution.refusal is not None:
                return ProjectionResult(
                    kind=milestone.kind,
                    outcome=PrContentWorkOutcome.NO_MAPPING,
                    source_key=source_key,
                    detail=resolution.refusal,
                )
            work_type_id = resolution.work_type_id
        else:
            work_type_id = resolver.resolve(milestone.kind, content.content_type)

        if existing is None:
            if dry_run:
                return ProjectionResult(
                    kind=milestone.kind,
                    outcome=PrContentWorkOutcome.PROJECTED,
                    source_key=source_key,
                    detail="would create source-derived work",
                )
            assert work_type_id is not None
            existing = await self._create_work(
                actor=actor,
                request_id=request_id,
                content=content,
                milestone=milestone,
                source_key=source_key,
                work_type_id=work_type_id,
            )
        elif work_type_id is not None and existing.work_type_id != work_type_id and not dry_run:
            # **M3.1: the correction path.** Two ways a work type turns out
            # wrong without anybody doing anything wrong - somebody fixes the
            # content's type, or the owner changes the mapping - and in both the
            # honest answer while nothing is counted yet is to re-file the work
            # rather than leave it under a heading that is now wrong.
            #
            # The service refuses once anything on the item is ``COUNTED``, so
            # a reported month is never re-typed by a mapping edit. That refusal
            # lives there rather than here because it is a fact about the
            # ledger, not about projection.
            await self._work.retype_source_work(
                actor=actor,
                request_id=request_id,
                work_item_id=existing.id,
                work_type_id=work_type_id,
                reason=f"{content.code}: ánh xạ nội dung đã thay đổi",
            )

        return await self._settle(
            actor=actor,
            request_id=request_id,
            content=content,
            milestone=milestone,
            item=existing,
            source_key=source_key,
            dry_run=dry_run,
        )

    async def _converge_result(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        milestone: SourceMilestone,
        resolver: ContentWorkResolver,
        source_key: str,
        dry_run: bool,
    ) -> ProjectionResult:
        """One milestone as one **result** in the contributor's monthly stream.

        The same three decisions :meth:`_settle` makes for a legacy work item -
        count it on the source's instant when the source's validator is not
        the contributor, leave it pending otherwise, take it back when the
        acceptance was withdrawn - applied to a row in ``pr_work_results``
        keyed on ``(CONTENT, source_key)``. A replay finds that row and changes
        nothing; that is the idempotency rule.
        """
        assert self._results is not None
        existing = await self._results.result_for_source(PrWorkResultSource.CONTENT, source_key)
        container_id = existing.work_item_id if existing is not None else None

        if milestone.contributor_user_id is None:
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.UNRESOLVED_CONTRIBUTOR,
                source_key=source_key,
                work_item_id=container_id,
                detail="the source does not record who did this work",
            )
        if (
            existing is not None
            and existing.status is PrWorkCountStatus.EXCLUDED
            and not source_may_restore(existing.exclusion_kind)
        ):
            # **The convergence rule with a person in it.** The milestone is
            # live and the source would count it - and a validator has said
            # no. That decision outranks the source; the row is neither
            # refiled, restored nor counted, in a dry run or a real one, by
            # the worker or by any button. ``record_source_result`` enforces
            # the same rule underneath, so no caller can reach past this.
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.HELD_BY_VALIDATOR,
                source_key=source_key,
                work_item_id=existing.work_item_id,
                contributor_user_id=milestone.contributor_user_id,
                detail=(
                    "a validator rejected this result; only 'Xem xét lại' releases it"
                    if existing.exclusion_kind is not None
                    else "excluded before 0041 by nobody on record; only 'Xem xét lại' releases it"
                ),
            )
        provisioned = False
        if existing is None:
            # **The missing-binding path.** A configured mapping answers from
            # the resolver; an unconfigured content type is bound here - work
            # type, rule and, below, the first result, in this one transaction.
            resolution = await self._resolve_work_type(
                actor=actor,
                request_id=request_id,
                content=content,
                kind=milestone.kind,
                resolver=resolver,
                dry_run=dry_run,
            )
            if resolution.refusal is not None:
                return ProjectionResult(
                    kind=milestone.kind,
                    outcome=PrContentWorkOutcome.NO_MAPPING,
                    source_key=source_key,
                    detail=resolution.refusal,
                )
            work_type_id = resolution.work_type_id
            provisioned = resolution.provisioned or resolution.would_provision
        else:
            work_type_id = resolver.resolve(milestone.kind, content.content_type)
            if work_type_id is None:
                container = await self._session.get(PrWorkItem, existing.work_item_id)
                assert container is not None
                work_type_id = container.work_type_id

        independent = (
            milestone.kind not in SELF_RECORDED_KINDS
            and milestone.validated_by_user_id is not None
            and milestone.validated_by_user_id != milestone.contributor_user_id
        )
        validation_instant = milestone.validated_at or milestone.occurred_at
        already_counted = existing is not None and existing.status is PrWorkCountStatus.COUNTED

        if already_counted:
            assert existing is not None
            # **The one convergence rule.** The milestone is live (this loop
            # only sees live ones), so the question is whether the source
            # still backs the count. Its own independent validation does;
            # so does a Work validator's confirmation, which the source never
            # made and cannot take back. Only a count the *source* made, whose
            # validation the source has since withdrawn, is reversed.
            origin = await self._results.count_origin(existing)
            if not source_may_reverse_count(
                source_eligible=True, source_independent=independent, origin=origin
            ):
                return ProjectionResult(
                    kind=milestone.kind,
                    outcome=PrContentWorkOutcome.UNCHANGED,
                    source_key=source_key,
                    work_item_id=existing.work_item_id,
                    work_type_id=work_type_id,
                    contributor_user_id=milestone.contributor_user_id,
                    detail=(
                        None
                        if independent
                        else "counted by a Work validator; the source still qualifies"
                    ),
                )
            blocked = await self._period_block(
                validation_instant, user_id=milestone.contributor_user_id
            )
            if blocked is not None:
                if not dry_run:
                    await self._record_blocked_result(
                        request_id=request_id,
                        actor=actor,
                        content=content,
                        milestone=milestone,
                        result=existing,
                        period_status=blocked,
                    )
                return ProjectionResult(
                    kind=milestone.kind,
                    outcome=PrContentWorkOutcome.BLOCKED_BY_PERIOD,
                    source_key=source_key,
                    work_item_id=existing.work_item_id,
                    detail=f"acceptance withdrawn, but the period is {blocked}",
                )
            if not dry_run:
                await self._results.reverse_source_result(
                    actor=actor,
                    request_id=request_id,
                    source_type=PrWorkResultSource.CONTENT,
                    source_key=source_key,
                    reason=f"{content.code}: nguồn không còn xác nhận công việc này",
                )
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.REVERSED,
                source_key=source_key,
                work_item_id=existing.work_item_id,
                detail="the source withdrew its independent validation",
            )

        blocked = (
            await self._period_block(validation_instant, user_id=milestone.contributor_user_id)
            if independent
            else None
        )
        if dry_run:
            outcome = (
                PrContentWorkOutcome.BLOCKED_BY_PERIOD
                if blocked is not None
                else PrContentWorkOutcome.PROJECTED
                if independent
                else PrContentWorkOutcome.PENDING_VALIDATION
            )
            return ProjectionResult(
                kind=milestone.kind,
                outcome=outcome,
                source_key=source_key,
                work_item_id=container_id,
                detail=(
                    "would provision a work type and mapping for this content type, "
                    "then record a result in the contributor's stream"
                    if provisioned
                    else "would record a result in the contributor's stream"
                ),
                work_type_id=work_type_id,
                contributor_user_id=milestone.contributor_user_id,
                provisioned=provisioned,
            )
        assert work_type_id is not None
        result = await self._results.record_source_result(
            actor=actor,
            request_id=request_id,
            source_type=PrWorkResultSource.CONTENT,
            source_key=source_key,
            work_type_id=work_type_id,
            subject_user_id=milestone.contributor_user_id,
            occurred_at=milestone.occurred_at,
            validated_by_user_id=(
                milestone.validated_by_user_id if independent and blocked is None else None
            ),
            validated_at=validation_instant,
            # The piece's code first, so a result row reads "CNT-2026-000042 ·
            # Bí quyết ngủ ngon" - the thing the writer recognises.
            label=f"{content.code} · {content.title}"[:200],
        )
        if blocked is not None:
            await self._record_blocked_result(
                request_id=request_id,
                actor=actor,
                content=content,
                milestone=milestone,
                result=result,
                period_status=blocked,
            )
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.BLOCKED_BY_PERIOD,
                source_key=source_key,
                work_item_id=result.work_item_id,
                detail=f"the milestone falls in a {blocked} reporting period",
            )
        if not independent:
            if not await self._already_audited(
                result.id, AuditAction.PR_CONTENT_WORK_PENDING_VALIDATION
            ):
                await record_pr_event(
                    self._audit,
                    request_id=request_id,
                    actor=actor,
                    action=AuditAction.PR_CONTENT_WORK_PENDING_VALIDATION,
                    entity_type="pr_work_result",
                    entity_id=result.id,
                    after={
                        "work_item_id": str(result.work_item_id),
                        "source_key": source_key,
                        "contribution_kind": milestone.kind.value,
                        "content_code": content.code,
                        "contributor_user_id": str(milestone.contributor_user_id),
                        "source_validated_by_user_id": (
                            str(milestone.validated_by_user_id)
                            if milestone.validated_by_user_id
                            else None
                        ),
                        "reason": "no_independent_source_validator",
                    },
                )
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.PENDING_VALIDATION,
                source_key=source_key,
                work_item_id=result.work_item_id,
                detail="the source milestone has no validator other than the contributor",
                work_type_id=work_type_id,
                contributor_user_id=milestone.contributor_user_id,
                provisioned=provisioned,
            )
        if existing is None and not await self._already_audited(
            result.id, AuditAction.PR_CONTENT_WORK_PROJECTED
        ):
            await record_pr_event(
                self._audit,
                request_id=request_id,
                actor=actor,
                action=AuditAction.PR_CONTENT_WORK_PROJECTED,
                entity_type="pr_work_result",
                entity_id=result.id,
                after={
                    "work_item_id": str(result.work_item_id),
                    "source_key": source_key,
                    "contribution_kind": milestone.kind.value,
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "contributor_user_id": str(milestone.contributor_user_id),
                    "occurred_at": milestone.occurred_at.isoformat(),
                    "work_type_id": str(work_type_id),
                    "auto_provisioned": provisioned,
                },
            )
        return ProjectionResult(
            kind=milestone.kind,
            outcome=PrContentWorkOutcome.PROJECTED,
            source_key=source_key,
            work_item_id=result.work_item_id,
            detail=(
                "work type and mapping provisioned for this content type" if provisioned else None
            ),
            work_type_id=work_type_id,
            contributor_user_id=milestone.contributor_user_id,
            provisioned=provisioned,
        )

    async def _resolve_work_type(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        kind: PrContentWorkKind,
        resolver: ContentWorkResolver,
        dry_run: bool,
    ) -> WorkTypeResolution:
        """Which work type a milestone with **no ledger row yet** is filed under.

        **No preconfiguration is required for content work.** The precedence,
        in order, and the first answer wins:

        1. an **exact** rule for ``(kind, content_type)`` - the resolver's;
        2. the kind's **default** rule - the resolver's, and deliberately ahead
           of provisioning: a department that said *"anything approved is a
           script"* meant it, and a new content type falls under that sentence
           rather than growing a type of its own beside it;
        3. **provision**: create the work type in the reserved namespace, bind
           the content type to it, and hand the id back so the milestone that
           triggered it becomes the first result in the same transaction.

        Three things are refused rather than guessed, and each comes back as a
        ``refusal`` the caller reports as ``NO_MAPPING``: content with **no
        ``content_type``**, because there is no stable identity to bind; a kind
        the projector does not provision; and a rule an administrator has
        **deactivated** for this exact case, which is a decision and not a gap.

        The resolver holds the active rules loaded once per run, so a miss costs
        exactly one extra query - the rule row, active or not - and only on the
        missing-binding path. A binding provisioned here is taught to the
        resolver so the next piece of the same type in the same batch pays
        nothing.
        """
        found = resolver.resolve(kind, content.content_type)
        if found is not None:
            return WorkTypeResolution(work_type_id=found)
        content_type = content.content_type
        if content_type is None:
            return WorkTypeResolution(
                refusal=(
                    "no active rule maps this content kind, and content with no content "
                    "type has nothing to bind a work type to"
                )
            )
        if kind not in AUTO_WORK_TYPE_CATEGORIES:
            return WorkTypeResolution(
                refusal="no active rule maps this content kind to a work type"
            )
        rule = await self._rules.rule_for(kind, content_type)
        if rule is not None:
            if not rule.is_active:
                return WorkTypeResolution(
                    refusal=(
                        "the mapping for this content type was deactivated by an "
                        "administrator; nothing is provisioned over a decision"
                    )
                )
            # Written since this run's resolver was loaded - by another piece
            # of the same batch, another worker, or an administrator. It is
            # the binding either way.
            resolver.bind(kind, content_type, rule.work_type_id)
            return WorkTypeResolution(work_type_id=rule.work_type_id)
        if dry_run:
            return WorkTypeResolution(would_provision=True)

        code = auto_work_type_code(kind, content_type)
        work_type = await self._work.ensure_source_work_type(
            actor=actor,
            request_id=request_id,
            code=code,
            name=auto_work_type_name(kind, content_type),
            category=AUTO_WORK_TYPE_CATEGORIES[kind],
            default_unit=AUTO_WORK_TYPE_UNIT,
            provenance={
                "reason": "content_auto_provision",
                "contribution_kind": kind.value,
                "content_type": content_type.value,
                "content_id": str(content.id),
                "content_code": content.code,
            },
        )
        if not work_type.is_active:
            # Only reachable if the binding row was removed by hand after the
            # type was retired: the service refuses retiring a type an active
            # rule still names. Reported, never worked around with a second type.
            return WorkTypeResolution(
                refusal=(
                    f"the work type {work_type.code} provisioned for this content type "
                    "is inactive; reactivate it or map the content type explicitly"
                )
            )
        rule = await self._rules.ensure_auto_rule(
            actor=actor,
            request_id=request_id,
            contribution_kind=kind,
            content_type=content_type,
            work_type=work_type,
        )
        if not rule.is_active:  # pragma: no cover - a deactivation raced this run
            return WorkTypeResolution(
                refusal=(
                    "the mapping for this content type was deactivated by an "
                    "administrator; nothing is provisioned over a decision"
                )
            )
        resolver.bind(kind, content_type, rule.work_type_id)
        logger.info(
            "pr_content_work_auto_provisioned",
            extra={
                "pr_content_id": str(content.id),
                "pr_contribution_kind": kind.value,
                "pr_content_type": content_type.value,
                "pr_work_type_code": work_type.code,
            },
        )
        return WorkTypeResolution(
            work_type_id=rule.work_type_id, provisioned=rule.work_type_id == work_type.id
        )

    async def _record_blocked_result(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        content: PrContentItem,
        milestone: SourceMilestone | None,
        result: PrWorkResult,
        period_status: str,
    ) -> None:
        """The result-grain twin of :meth:`_record_blocked`."""
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_WORK_BLOCKED,
            entity_type="pr_work_result",
            entity_id=result.id,
            after={
                "work_item_id": str(result.work_item_id),
                "source_key": result.source_key,
                "content_code": content.code,
                "contribution_kind": milestone.kind.value if milestone else None,
                "period_status": period_status,
                "reason": "reporting_period_not_open",
            },
        )

    async def _converge_orphan_results(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        live: set[uuid.UUID],
        dry_run: bool,
    ) -> list[ProjectionResult]:
        """Counted **and pending** results whose milestone is no longer there.

        The result-grain orphan sweep. Pending rows are swept too: a result
        the source no longer supports must not sit ``PENDING`` where a
        validator could count it - source truth wins, and the row goes
        ``SOURCE_REVERSED`` exactly as a counted one does. Rows already out
        (a rejection, a removal, an earlier reversal) are left as they are;
        ``reverse_source_result`` refuses to overwrite why.
        """
        if self._results is None:
            return []
        keys = {content_work_source_key(kind, content.id): kind for kind in AUTOMATIC_KINDS}
        rows = (
            (
                await self._session.execute(
                    select(PrWorkResult).where(
                        PrWorkResult.source_type == PrWorkResultSource.CONTENT,
                        PrWorkResult.source_key.in_(list(keys)),
                        PrWorkResult.status.in_(
                            [PrWorkCountStatus.COUNTED, PrWorkCountStatus.PENDING]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        results: list[ProjectionResult] = []
        for row in rows:
            assert row.source_key is not None
            kind = keys[row.source_key]
            if content.id in live:
                continue
            at = row.counted_at or row.reported_at
            blocked = await self._period_block(at, user_id=row.user_id)
            if blocked is not None:
                if not dry_run:
                    await self._record_blocked_result(
                        request_id=request_id,
                        actor=actor,
                        content=content,
                        milestone=None,
                        result=row,
                        period_status=blocked,
                    )
                results.append(
                    ProjectionResult(
                        kind=kind,
                        outcome=PrContentWorkOutcome.BLOCKED_BY_PERIOD,
                        source_key=row.source_key,
                        work_item_id=row.work_item_id,
                        detail=f"source withdrawn, but the period is {blocked}",
                    )
                )
                continue
            if not dry_run:
                await self._results.reverse_source_result(
                    actor=actor,
                    request_id=request_id,
                    source_type=PrWorkResultSource.CONTENT,
                    source_key=row.source_key,
                    reason=f"{content.code}: nguồn không còn xác nhận công việc này",
                )
            results.append(
                ProjectionResult(
                    kind=kind,
                    outcome=PrContentWorkOutcome.REVERSED,
                    source_key=row.source_key,
                    work_item_id=row.work_item_id,
                )
            )
        return results

    async def _settle(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        milestone: SourceMilestone,
        item: PrWorkItem,
        source_key: str,
        dry_run: bool,
    ) -> ProjectionResult:
        """Decide whether this work should be counted, and make it so.

        Three cases, and the middle one is the milestone's whole point:

        * the source supplied a validator who is **not** the contributor ->
          count it, on the source's instant;
        * the source's only validator **is** the contributor, or the kind has no
          validator at all -> leave it completed and uncounted. Independent
          validation is still required, and a person who did not do the work can
          supply it through the ordinary Work route;
        * it is already counted -> nothing to do.
        """
        contributions = await self._contributions(item.id)
        already_counted = any(
            row.count_status is PrWorkCountStatus.COUNTED for row in contributions
        )
        independent = (
            milestone.kind not in SELF_RECORDED_KINDS
            and milestone.validated_by_user_id is not None
            and milestone.validated_by_user_id != milestone.contributor_user_id
        )
        # **The instant the count belongs to.** For ``PRODUCTION`` this is the
        # video approval rather than the hand-in: the workload happened when the
        # cut was submitted, and the *KPI* belongs to the month somebody accepted
        # it. ``occurred_at`` is the fallback for kinds where the two coincide.
        validation_instant = milestone.validated_at or milestone.occurred_at

        if already_counted:
            # The same convergence rule the result path applies, read off the
            # legacy item's own timeline: a count a Work validator made through
            # ``approve`` stands while the milestone qualifies; a count the
            # source made goes when the source withdraws its validation.
            origin = await self._work.count_origin(item)
            if not source_may_reverse_count(
                source_eligible=True, source_independent=independent, origin=origin
            ):
                return ProjectionResult(
                    kind=milestone.kind,
                    outcome=PrContentWorkOutcome.UNCHANGED,
                    source_key=source_key,
                    work_item_id=item.id,
                    detail=(
                        None
                        if independent
                        else "counted by a Work validator; the source still qualifies"
                    ),
                )
            # The milestone still stands but its **acceptance was withdrawn** -
            # an undone video approval, with the submission still on file. M3
            # could not reach this state, because the acceptance *was* the
            # milestone and losing it made the work an orphan. M3.1 keeps the
            # work item, because the editing job still happened, and takes the
            # count back out, because nobody accepts the result any more.
            blocked = await self._period_block(
                validation_instant, user_id=milestone.contributor_user_id
            )
            if blocked is not None:
                if not dry_run:
                    await self._record_blocked(
                        request_id=request_id,
                        actor=actor,
                        content=content,
                        milestone=milestone,
                        item=item,
                        period_status=blocked,
                    )
                return ProjectionResult(
                    kind=milestone.kind,
                    outcome=PrContentWorkOutcome.BLOCKED_BY_PERIOD,
                    source_key=source_key,
                    work_item_id=item.id,
                    detail=f"acceptance withdrawn, but the period is {blocked}",
                )
            if not dry_run:
                await self._work.reverse_source_work(
                    actor=actor,
                    request_id=request_id,
                    work_item_id=item.id,
                    reason=f"{content.code}: nguồn không còn xác nhận công việc này",
                )
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.REVERSED,
                source_key=source_key,
                work_item_id=item.id,
                detail="the source withdrew its independent validation",
            )

        if not independent:
            if not dry_run:
                await self._record_pending(
                    request_id=request_id,
                    actor=actor,
                    content=content,
                    milestone=milestone,
                    item=item,
                )
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.PENDING_VALIDATION,
                source_key=source_key,
                work_item_id=item.id,
                detail="the source milestone has no validator other than the contributor",
            )

        blocked = await self._period_block(
            validation_instant, user_id=milestone.contributor_user_id
        )
        if blocked is not None:
            if not dry_run:
                await self._record_blocked(
                    request_id=request_id,
                    actor=actor,
                    content=content,
                    milestone=milestone,
                    item=item,
                    period_status=blocked,
                )
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.BLOCKED_BY_PERIOD,
                source_key=source_key,
                work_item_id=item.id,
                detail=f"the milestone falls in a {blocked} reporting period",
            )

        if dry_run:
            return ProjectionResult(
                kind=milestone.kind,
                outcome=PrContentWorkOutcome.PROJECTED,
                source_key=source_key,
                work_item_id=item.id,
                detail="would count against the source milestone",
            )
        assert milestone.validated_by_user_id is not None
        await self._work.count_source_work(
            actor=actor,
            request_id=request_id,
            work_item_id=item.id,
            validated_by_user_id=milestone.validated_by_user_id,
            # **The business instant.** A worker retry tomorrow must not move
            # this contribution into tomorrow's reporting period - see
            # ``PrWorkService.count_source_work``. M3.1: the *validation*
            # instant, which for a production job is the video approval and not
            # the hand-in.
            effective_validation_at=validation_instant,
            note=f"{content.code} · {milestone.kind.value}",
        )
        return ProjectionResult(
            kind=milestone.kind,
            outcome=PrContentWorkOutcome.PROJECTED,
            source_key=source_key,
            work_item_id=item.id,
        )

    async def _converge_orphans(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        live: set[uuid.UUID],
        dry_run: bool,
    ) -> list[ProjectionResult]:
        """Reconcile work whose milestone is **no longer** there.

        The other half of convergence, and the half an event-driven projector
        cannot do at all: an approval that was undone raises no event saying
        *"un-count that"*, it simply stops being a live milestone. So the ledger
        is walked and anything that no longer has a source is taken back out.

        A ``CLOSED`` or ``LOCKED`` period stops it, and the discrepancy is
        reported rather than resolved. Historical performance stays as it was
        agreed - see ``docs/pr/WORK_QUOTA_M2.md`` §11 for the same rule one layer
        down.
        """
        statement = select(PrWorkItem).where(
            PrWorkItem.content_id == content.id,
            PrWorkItem.source_type == PrWorkSourceType.CONTENT,
            PrWorkItem.status == PrWorkStatus.APPROVED,
        )
        results: list[ProjectionResult] = []
        for item in (await self._session.execute(statement)).scalars().all():
            kind = _kind_of(item.source_key)
            if kind is None:
                continue
            # **Work of a retired kind is grandfathered. M3.1.**
            #
            # A kind outside :data:`AUTOMATIC_KINDS` produces no milestone any
            # more, so without this line every work item M3 created under one
            # would look orphaned and be reversed on the next sweep - this
            # milestone silently deleting counted workload from months people
            # have already been assessed on, which is precisely what the period
            # rules exist to prevent. Turning the tap off is not draining the
            # tank: no new work of that kind is created, and what exists is left
            # alone.
            #
            # Written against the set rather than against ``PUBLICATION`` by
            # name, so retiring a second kind needs one edit rather than two
            # that can disagree.
            if kind not in AUTOMATIC_KINDS:
                continue
            entity = _entity_of(item.source_key)
            if entity is not None and entity in live:
                continue

            counted = [
                row
                for row in await self._contributions(item.id)
                if row.count_status is PrWorkCountStatus.COUNTED
            ]
            if not counted:
                continue
            at = counted[0].counted_at or utcnow()
            blocked = await self._period_block(at, user_id=counted[0].user_id)
            if blocked is not None:
                if not dry_run:
                    await self._record_blocked(
                        request_id=request_id,
                        actor=actor,
                        content=content,
                        milestone=None,
                        item=item,
                        period_status=blocked,
                    )
                results.append(
                    ProjectionResult(
                        kind=kind,
                        outcome=PrContentWorkOutcome.BLOCKED_BY_PERIOD,
                        source_key=item.source_key,
                        work_item_id=item.id,
                        detail=f"source withdrawn, but the period is {blocked}",
                    )
                )
                continue
            if not dry_run:
                await self._work.reverse_source_work(
                    actor=actor,
                    request_id=request_id,
                    work_item_id=item.id,
                    reason=f"{content.code}: nguồn không còn xác nhận công việc này",
                )
            results.append(
                ProjectionResult(
                    kind=kind,
                    outcome=PrContentWorkOutcome.REVERSED,
                    source_key=item.source_key,
                    work_item_id=item.id,
                )
            )
        return results

    # =====================================================================
    # Writing
    # =====================================================================
    async def _create_work(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        milestone: SourceMilestone,
        source_key: str,
        work_type_id: uuid.UUID,
    ) -> PrWorkItem:
        """Write the source-derived work item and its contribution.

        Lands at ``COMPLETED``: the deliverable exists and the source says so,
        and what it is waiting for is the counting decision. That is exactly what
        M1's ``COMPLETED`` means - *"a contributor says it is finished, and
        nobody has confirmed it"* - so source-derived work joins the ladder at
        the same rung manual work does rather than at one of its own.

        ``uq_pr_work_items_source`` is what makes two concurrent workers safe:
        the second insert fails on the partial unique index rather than
        producing a second work item, and the caller's next run finds the first.
        """
        item = await self._work.create_source_work(
            actor=actor,
            request_id=request_id,
            source_key=source_key,
            work_type_id=work_type_id,
            title=_work_title(content, milestone),
            contributor_user_id=milestone.contributor_user_id,  # type: ignore[arg-type]
            content_id=content.id,
            occurred_at=milestone.occurred_at,
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_WORK_PROJECTED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "source_key": source_key,
                "contribution_kind": milestone.kind.value,
                "content_id": str(content.id),
                "content_code": content.code,
                "contributor_user_id": str(milestone.contributor_user_id),
                "occurred_at": milestone.occurred_at.isoformat(),
            },
        )
        return item

    async def _record_pending(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        content: PrContentItem,
        milestone: SourceMilestone,
        item: PrWorkItem,
    ) -> None:
        """Record that work was projected and deliberately left uncounted.

        Written once per state rather than once per run, so a piece that sits
        waiting for a validator for a week does not produce a week of identical
        audit rows: the history table already carries the ``COMPLETED`` line, and
        this is only appended when the audit trail does not already say it.
        """
        if await self._already_audited(item.id, AuditAction.PR_CONTENT_WORK_PENDING_VALIDATION):
            return
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_WORK_PENDING_VALIDATION,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "source_key": item.source_key,
                "contribution_kind": milestone.kind.value,
                "content_code": content.code,
                "contributor_user_id": str(milestone.contributor_user_id),
                "source_validated_by_user_id": (
                    str(milestone.validated_by_user_id) if milestone.validated_by_user_id else None
                ),
                "reason": "no_independent_source_validator",
            },
        )

    async def _record_blocked(
        self,
        *,
        request_id: uuid.UUID,
        actor: Actor,
        content: PrContentItem,
        milestone: SourceMilestone | None,
        item: PrWorkItem,
        period_status: str,
    ) -> None:
        """Record that the source and the work disagree and the month is shut.

        The discrepancy is **recorded, not resolved**. Somebody reading the
        period afterwards can see that the source changed and that the numbers
        were deliberately left as they were agreed, which is a different and much
        better state than a silent mismatch nobody knows about.
        """
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_WORK_BLOCKED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "source_key": item.source_key,
                "content_code": content.code,
                "contribution_kind": milestone.kind.value if milestone else None,
                "period_status": period_status,
                "reason": "reporting_period_not_open",
            },
        )

    # =====================================================================
    # Internals
    # =====================================================================
    async def source_is_eligible(
        self, source_type: PrWorkResultSource, source_key: str
    ) -> bool | None:
        """Does the source still support this result **right now**?

        The validator's guard, asked by the result service before a
        source-derived ``PENDING`` row is counted or a rejected one released.
        ``True`` when the milestone the key names exists in the live
        transition history; ``False`` when the content is gone or the
        milestone is not there any more - an undone approval simply is not
        one; ``None`` when this projector is not the authority (another
        source, a malformed key, or a kind M3.1 leaves alone), which the
        caller treats as *no opinion*, never as *ineligible*.
        """
        if source_type is not PrWorkResultSource.CONTENT:
            return None
        kind = _kind_of(source_key)
        content_id = _entity_of(source_key)
        if kind is None or content_id is None or kind not in AUTOMATIC_KINDS:
            return None
        content = await self._session.get(PrContentItem, content_id)
        if content is None:
            return False
        return any(one.kind is kind for one in await self._milestones(content))

    async def _period_block(self, at: datetime, *, user_id: uuid.UUID | None = None) -> str | None:
        """The period status blocking a write at this instant, or ``None``.

        ``None`` covers two different cases on purpose: the period is ``OPEN``,
        or **no period exists yet**. The second is not a block - M2 already
        handles a month nobody has opened by leaving the contribution
        unallocated and logging it, and refusing to count the work as well would
        punish an employee for an administrator not having created a row.

        ``FINALIZED`` when the contributor's month has an agreed performance
        figure: the period may still be ``OPEN``, and the count still may not
        move under a number somebody signed.
        """
        period = await self._periods.period_for(at)
        if period is None:
            return None
        if period.status is not PrPeriodStatus.OPEN:
            return period.status.value
        if user_id is not None and await self._periods.performance_finalized(
            user_id=user_id, period_id=period.id
        ):
            return "FINALIZED"
        return None

    async def _work_item(self, source_key: str) -> PrWorkItem | None:
        statement = select(PrWorkItem).where(
            PrWorkItem.source_type == PrWorkSourceType.CONTENT,
            PrWorkItem.source_key == source_key,
        )
        return (await self._session.execute(statement)).scalars().one_or_none()

    async def _contributions(self, work_item_id: uuid.UUID) -> Sequence[PrWorkContribution]:
        statement = (
            select(PrWorkContribution)
            .where(PrWorkContribution.work_item_id == work_item_id)
            .order_by(PrWorkContribution.assigned_at.asc())
        )
        return (await self._session.execute(statement)).scalars().all()

    async def _already_audited(self, entity_id: uuid.UUID, action: AuditAction) -> bool:
        from meobot.db.models.audit_log import AuditLog

        statement = (
            select(AuditLog.id)
            .where(AuditLog.entity_id == str(entity_id), AuditLog.action == action.value)
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def _open_period_candidates(self, *, limit: int) -> list[uuid.UUID]:
        """Content with recent work-relevant activity, bounded.

        The queue is the real work list; this is the catch-up default for
        content that predates M3 or whose request row was lost. It reads the
        transition history rather than the content table, because *"something
        happened that could be a milestone"* is what makes a piece a candidate -
        a content item nobody has touched has nothing to project.
        """
        statement = (
            select(PrContentTransitionEvent.content_id)
            .where(
                PrContentTransitionEvent.trigger == PrTransitionTrigger.HUMAN_APPROVAL,
                PrContentTransitionEvent.reversed_by_event_id.is_(None),
            )
            .order_by(PrContentTransitionEvent.created_at.desc())
            .limit(max(1, limit) * 4)
        )
        seen: list[uuid.UUID] = []
        for content_id in (await self._session.execute(statement)).scalars():
            if content_id not in seen:
                seen.append(content_id)
            if len(seen) >= limit:
                break
        return seen

    async def _require_configure(self, actor: Actor) -> None:
        from meobot.domain.pr.policy import PrCapability

        await self._work.capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)


def _work_title(content: PrContentItem, milestone: SourceMilestone) -> str:
    """What the work item is called on a work board.

    Names the **content**, because that is what somebody recognises: *"Kịch bản:
    Bí quyết ngủ ngon"* is a job a person remembers doing, and a title that said
    only "CONTENT_CREATION" would make them look it up.
    """
    prefix = {
        PrContentWorkKind.CONTENT_CREATION: "Nội dung",
        PrContentWorkKind.PRODUCTION: "Sản xuất",
        PrContentWorkKind.PUBLICATION: "Đăng bài",
    }[milestone.kind]
    return f"{prefix}: {content.title}"[:300]


def _kind_of(source_key: str | None) -> PrContentWorkKind | None:
    """Which semantic kind a source key names, or ``None`` if it is not ours."""
    if not source_key:
        return None
    milestone = source_key.rsplit(":", 1)[-1]
    try:
        return PrContentWorkKind(milestone)
    except ValueError:
        return None


def _entity_of(source_key: str | None) -> uuid.UUID | None:
    """The entity the key names. ``None`` when the key is not one of ours."""
    if not source_key:
        return None
    parts = source_key.split(":")
    if len(parts) != 3:
        return None
    try:
        return uuid.UUID(parts[1])
    except ValueError:
        return None


__all__: list[str] = [
    "MAX_RECONCILE_CONTENT",
    "SWEEP_BATCH",
    "ContentProjectionReport",
    "PrContentWorkProjector",
    "ProjectionResult",
    "ReconcileReport",
    "SourceMilestone",
    "WorkTypeResolution",
    "request_content_work_projection",
]
