"""KPI plans: writing them, approving them, and revising them. M2.

One service for the whole lifecycle, because the lifecycle is one argument:
who may write a target, when a target starts deciding anything, and what
happens to the old one when a new one replaces it. Splitting approval from
editing across two services would put the "an approved plan is immutable" rule
in a seam.

The rule the whole file is built around
----------------------------------------

**An approved plan is never edited.** A change is a new version::

    APPROVED v1  ──revise──►  DRAFT v2  ──edit──►  approve v2
                                                       │
                                            ┌──────────┴──────────┐
                                            v1 → SUPERSEDED   v2 → APPROVED
                                            (one transaction, or neither)

A quota decides how much of somebody's work counts towards their KPI. Whoever
raised a cap in the middle of a month has to stay visible afterwards, and an
approved plan that could be edited in place would erase them - which is the
same reasoning Step 1B's weekly manual reporting input already documents for
itself: *a correction is a new version, never an edit*.

Who may do it
--------------

``PR_WORK_CONFIGURE`` - ``ADMIN`` and ``OWNER`` - for every write here, and
**deliberately not** ``PR_WORK_MANAGE``.

That is the M1 patch scope model kept rather than regressed. A ``TEAM_LEAD``
holds ``PR_WORK_MANAGE`` and may assign work, accept proposals and validate
what somebody finished; none of that implies deciding what an arbitrary
colleague's KPI targets are. MeoBot models **no team, department or manager
relationship**, so there is no honest subject relationship that would let a
lead configure "their" people - and deriving one from ``PrChannelAssignment``
was explicitly ruled out in M1 and is not reintroduced here. Until a real
organisational model exists, quota configuration stays at ``ADMIN`` and above.

An employee may **read** their own approved plan and may configure nothing,
including their own. That is the whole point.

KPI self-service (migration 0038) adds one carefully bounded exception, and it
is a *proposal* right rather than a configuration right: an employee may write
their **own** draft - create the first version, or clone the plan in force into
the next one - fill it from the work-type catalogue, and **submit** it. From
that moment the draft is the manager's: the employee is locked out, a manager
may correct it, approve it or send it back with a note, and only an approval by
somebody **other than the subject** puts it into force. Nothing here lets an
employee touch a work type, a standard minute, a scoring rule or anybody
else's plan, and nothing a draft says is read by the evaluator before
approval. See ``docs/pr/WORK_KPI_SELF_SERVICE.md``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_performance_config_service import (
    PrPerformancePolicyService,
    PrWorkScoringRuleService,
)
from meobot.application.pr_performance_target_service import PrPerformanceTargetService
from meobot.application.pr_plan_workload import PlanWorkload, PrPlanWorkloadCalculator
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_notifications import PrWorkNotifier
from meobot.application.pr_work_period_service import (
    PrWorkPeriodService,
    assert_plan_period_type,
)
from meobot.application.pr_work_quota_service import (
    PrWorkQuotaEligibilityService,
    assert_period_open,
)
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkPlanStateError,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import PrWorkUnit
from meobot.domain.pr.work_quota import (
    TERMINAL_PLAN_STATUSES,
    PrPlanReadinessBlocker,
    PrPlanReviewState,
    PrWorkPlanStatus,
    PrWorkQuotaBasis,
    assert_quota_bounds,
    assert_quota_unit,
    plan_review_state,
)

logger = get_logger(__name__)

#: The longest note a plan or a quota may carry. Matches the column's use.
MAX_PLAN_NOTE = 2000

#: How many quotas one plan version may hold.
#:
#: Bounded because approving a plan writes every one of them in one
#: transaction, and because a plan naming fifty kinds of work is not a plan
#: anybody is going to read. The taxonomy is a couple of dozen types at most.
MAX_QUOTAS_PER_PLAN = 40


@dataclass(frozen=True, slots=True)
class PlanDetail:
    """One plan version, its quotas, and what this actor may do with it.

    The ``can_*`` flags are the **same** checks the writes make, rendered as
    booleans so a client draws the right controls. Hiding a control is a
    courtesy; calling the route anyway gets the identical refusal, which is the
    only version of an authorisation rule that is worth anything.
    """

    plan: PrWorkPlan
    period: PrReportingPeriod
    quotas: tuple[PrWorkQuota, ...] = ()
    work_types: tuple[PrWorkType, ...] = ()
    user_name: str | None = None
    created_by_name: str | None = None
    approved_by_name: str | None = None
    #: True when this actor may add, edit and remove quotas on this plan.
    #: False for **every** approved plan, whoever is asking - an approved plan
    #: is immutable, and the client must offer "Tạo bản điều chỉnh" instead.
    can_edit: bool = False
    can_approve: bool = False
    can_revise: bool = False
    can_discard: bool = False
    #: KPI self-service. Whether **this actor** is the plan's subject.
    is_subject: bool = False
    #: The employee may hand this draft to their manager: their own, a draft,
    #: not already submitted, on an open period, and ready.
    can_submit: bool = False
    #: A manager may send this submitted draft back.
    can_return: bool = False
    #: Why the draft is not ready, as stable codes - empty when it is. The
    #: **same** list for submission and approval; see
    #: :class:`~meobot.domain.pr.work_quota.PrPlanReadinessBlocker`.
    readiness_blockers: tuple[str, ...] = ()
    submitted_by_name: str | None = None
    returned_by_name: str | None = None
    #: The projected workload, when the scoring configuration can price it.
    workload: PlanWorkload | None = None


@dataclass(frozen=True, slots=True)
class PlanPage:
    """A page of plans, with everything a list needs, fetched once."""

    plans: tuple[PrWorkPlan, ...] = ()
    total: int = 0
    limit: int = 50
    offset: int = 0
    periods: tuple[PrReportingPeriod, ...] = ()
    #: Resolved server-side, in one query, so no list response prints a UUID.
    user_names: dict[uuid.UUID, str] = field(default_factory=dict)
    #: How many quotas each plan holds. Fetched in one grouped query rather than
    #: one per row - the N+1 M1's work list already went to some trouble to
    #: avoid.
    quota_counts: dict[uuid.UUID, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmployeePlanSummary:
    """One employee's KPI standing for one reporting month. **The main screen's row.**

    Post-M4, and the object the KPI screen was missing. It used to render the
    plan *list* - one card per ``PrWorkPlan`` row - so an employee on their third
    revision appeared three times, as three management entities, of which two
    were history. A manager reading that screen could not answer the question
    they had actually come to ask: *"who has a KPI plan this month, and who does
    not?"*

    The top-level object is therefore **employee + period**, and plan versions
    are history inside it.

    ``current_plan`` is decided here rather than in a browser, and it can be
    decided unambiguously because M2's schema already guarantees it: partial
    unique indexes allow at most one ``APPROVED`` and at most one ``DRAFT`` per
    ``(user, period)``. So:

    * the ``APPROVED`` plan is the current one - it is in force, and eligibility
      reads it;
    * failing that, the ``DRAFT`` is - somebody is writing this month's plan and
      it is the thing to open;
    * failing both, there is no current plan. ``SUPERSEDED`` and ``DISCARDED``
      rows are history and never current, however recent they are.

    An employee with nothing at all still gets a row, with ``has_plan`` false.
    That is the other half of the question, and a list built from plan rows
    structurally cannot answer it.
    """

    user_id: uuid.UUID
    user_name: str
    period_id: uuid.UUID
    #: The plan a manager acts on: approved, else draft, else nothing.
    current_plan: PrWorkPlan | None = None
    #: How many quotas the current plan holds. Zero when there is none.
    quota_count: int = 0
    #: The draft in flight, when there is one. Equal to ``current_plan`` when no
    #: approved version exists yet, and **distinct** from it when a manager is
    #: revising a plan that is already in force - which is exactly the case a
    #: screen needs to offer two different controls for.
    latest_draft: PrWorkPlan | None = None
    #: How many quotas the **draft** holds, when there is one. Zero is a real
    #: answer and the interesting one: an empty revision is the state somebody
    #: gets stuck in, and a screen that cannot say "0 hạn mức" cannot explain
    #: why *Duyệt* is unavailable.
    draft_quota_count: int = 0
    #: **Every** version for this pair - the current plan and any active draft
    #: included. It answers *"how many versions has this month had"*, which is
    #: what the row prints as "N phiên bản". It is deliberately **not** the
    #: number the detail screen's *"Lịch sử thay đổi"* shows: history is the
    #: terminal versions, and neither the plan in force nor the revision being
    #: written is one. See :attr:`terminal_count`.
    history_count: int = 0
    #: The versions that are **over**: superseded and discarded. What
    #: *"Lịch sử thay đổi (N)"* counts, and the reason it is a separate figure -
    #: counting the draft as history is how the draft became unreachable.
    terminal_count: int = 0
    #: KPI self-service. The draft's projected workload, when priced.
    draft_workload: PlanWorkload | None = None
    #: And the plan in force's, for the same row.
    current_workload: PlanWorkload | None = None

    @property
    def draft_review_state(self) -> PrPlanReviewState | None:
        """Where the draft stands between author and approver. ``None`` without one."""
        draft = self.latest_draft
        if draft is None:
            return None
        return plan_review_state(
            draft.status, submitted_at=draft.submitted_at, returned_at=draft.returned_at
        )

    @property
    def has_plan(self) -> bool:
        """Whether this employee has a plan to open at all."""
        return self.current_plan is not None


class PrWorkPlanService:
    """Writes KPI plans and quotas, and decides when one comes into force.

    Args:
        session: Unit of work. The caller owns the transaction boundary, which
            is what makes :meth:`approve` atomic without this service knowing
            about transactions.
        audit: Event writer sharing that session.
        capabilities: Resolves ``PR_WORK_CONFIGURE`` and ``PR_WORK_VIEW_ALL``.
        periods: The one place a date becomes a reporting period.
        eligibility: The one place eligibility is decided. Held so that
            approving a plan recomputes the period it applies to, in the same
            transaction - otherwise an administrator would approve a plan and
            see nothing change until somebody remembered to reconcile.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        periods: PrWorkPeriodService,
        eligibility: PrWorkQuotaEligibilityService,
        *,
        scoring_rules: PrWorkScoringRuleService | None = None,
        policies: PrPerformancePolicyService | None = None,
        targets: PrPerformanceTargetService | None = None,
        notifier: PrWorkNotifier | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._periods = periods
        self._eligibility = eligibility
        # The three M6 readers the workload preview prices a plan with. All
        # optional so a caller that only needs the lifecycle can build this
        # service alone; ``build_pr_services`` supplies them, and without them
        # the preview is simply absent rather than wrong.
        self._scoring_rules = scoring_rules
        self._policies = policies
        self._targets = targets
        # **The one formula.** Every workload figure this service returns -
        # detail, employee card, manager list - is this calculator's, and the
        # same object prices M6's planned minutes. See ``pr_plan_workload``.
        self._workloads = (
            PrPlanWorkloadCalculator(
                session, scoring_rules=scoring_rules, policies=policies, targets=targets
            )
            if scoring_rules is not None and policies is not None and targets is not None
            else None
        )
        # In-app only, and optional: a submission, approval or return is news
        # to the other party, and nothing else here is.
        self._notifier = notifier

    # =====================================================================
    # Creating and editing a draft
    # =====================================================================
    async def create_plan(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        period_id: uuid.UUID,
        note: str | None = None,
    ) -> PlanDetail:
        """Start a ``DRAFT`` plan for one employee and one month.

        ``PR_WORK_CONFIGURE``. Version 1 when there is nothing else for that
        pair, and the next number after the highest existing version otherwise -
        numbers are never reused, so "v2" means one thing for ever.

        Refuses a period that is not ``OPEN``: writing a plan for a month whose
        numbers were already agreed would be preparing to rewrite them.

        Refuses a second draft for the same pair. One revision in flight is a
        product decision - two people each writing a different next version of
        one employee's plan is a conflict better surfaced while it is cheap -
        and it is enforced by ``uq_pr_work_plans_draft`` as well as here, which
        is what settles the case where both requests arrive at once.

        **Refuses an employee who already has an approved plan.** This method
        creates the *first* plan for a pair; changing one that is in force is
        :meth:`revise`, and there is deliberately no second way to do it.

        The two are not interchangeable, which is the whole reason for the
        guard: ``revise`` locks the plan it comes from, records
        ``supersedes_plan_id``, and **copies the quotas across**, so a revision
        starts as the plan it revises and a manager edits the differences. This
        method starts from nothing. Allowing it beside an approved plan produced
        a second, semantically different revision path whose output was an empty
        draft that claimed to supersede nothing - the *"v4 · 0 hạn mức"* sitting
        under an approved v3 in production, which is how a manager ends up
        rewriting five quotas by hand or approving a plan that silently drops
        them all.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        return await self._create_first_draft(
            actor=actor, request_id=request_id, user_id=user_id, period_id=period_id, note=note
        )

    async def self_create_plan(
        self, *, actor: Actor, request_id: uuid.UUID, period_id: uuid.UUID, note: str | None = None
    ) -> PlanDetail:
        """**An employee starts their own first draft.** KPI self-service.

        The subject is the actor - derived from the session, never taken from
        the request - so this cannot create a plan for anybody else whatever a
        client sends. ``PR_WORK_EXECUTE`` is the gate: the capability every
        member of the Work module already holds, and nothing wider.

        Every other rule is :meth:`create_plan`'s, unchanged: an open month,
        one draft at a time, and an approved plan is changed through
        :meth:`revise`, never by starting a second one beside it.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
        subject = _require_user_id(actor)
        return await self._create_first_draft(
            actor=actor,
            request_id=request_id,
            user_id=subject,
            period_id=period_id,
            note=note,
            self_service=True,
        )

    async def _create_first_draft(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        period_id: uuid.UUID,
        note: str | None,
        self_service: bool = False,
    ) -> PlanDetail:
        """The one creation path, behind both the manager's and the employee's gate."""
        period = await self._periods.require_period(period_id)
        assert_plan_period_type(period)
        assert_period_open(period)
        await self._require_active_user(user_id)

        existing_draft = await self._plan_in_status(
            user_id=user_id, period_id=period.id, status=PrWorkPlanStatus.DRAFT
        )
        if existing_draft is not None:
            raise PrWorkPlanStateError(
                "A draft plan for this person and period already exists",
                details={
                    "reason": "draft_already_exists",
                    "plan_id": str(existing_draft.id),
                    "version_no": existing_draft.version_no,
                },
            )

        # **One creation path per state.** A plan already in force is changed by
        # revising it, and a distinct reason code because the two absences lead
        # to two different recoveries: `draft_already_exists` means *continue the
        # revision somebody started*, and this one means *start one*. Collapsing
        # them would send a manager to the wrong control.
        #
        # Checked without a lock, and that is sufficient: this is the courteous
        # half. If two requests race, both would go on to insert a ``DRAFT`` and
        # ``uq_pr_work_plans_draft`` refuses the second - the index is the rule
        # here exactly as it is for the check above.
        approved = await self._plan_in_status(
            user_id=user_id, period_id=period.id, status=PrWorkPlanStatus.APPROVED
        )
        if approved is not None:
            raise PrWorkPlanStateError(
                "This person already has an approved plan for this period; revise it instead",
                details={
                    "reason": "approved_plan_requires_revision",
                    "plan_id": str(approved.id),
                    "version_no": approved.version_no,
                },
            )

        plan = PrWorkPlan(
            user_id=user_id,
            period_id=period.id,
            version_no=await self._next_version(user_id=user_id, period_id=period.id),
            status=PrWorkPlanStatus.DRAFT,
            note=_optional_text(note, "note", MAX_PLAN_NOTE),
            created_by_user_id=_require_user_id(actor),
        )
        self._session.add(plan)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_CREATED,
            entity_type="pr_work_plan",
            entity_id=plan.id,
            after={
                "user_id": str(plan.user_id),
                "period_id": str(plan.period_id),
                "period_code": period.code,
                "version_no": plan.version_no,
                "status": plan.status.value,
                "self_service": self_service,
            },
        )
        return await self.detail(actor=actor, plan_id=plan.id)

    async def add_quota(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        plan_id: uuid.UUID,
        work_type_id: uuid.UUID,
        target_value: Decimal,
        eligibility_cap: Decimal,
        basis: PrWorkQuotaBasis | None = None,
        unit: PrWorkUnit | None = None,
        note: str | None = None,
    ) -> PlanDetail:
        """Put one work type's target and cap into a **draft** plan.

        ``PR_WORK_CONFIGURE``, and the plan must be a ``DRAFT`` - see
        :meth:`_require_draft`.

        ``basis`` and ``unit`` default to the work type's own, which is the
        normal case and the one that cannot be got wrong: how a kind of work is
        measured is a property of the work. Passing them explicitly is allowed
        and is **validated against the type** rather than trusted - see
        :func:`~meobot.domain.pr.work_quota.assert_quota_unit`, which is what
        refuses a ``QUANTITY`` cap in ``VIDEO`` on a type that counts comments.
        """
        plan, period = await self._require_draft(actor, plan_id)
        work_type = await self._require_work_type(work_type_id)
        if not work_type.is_active:
            raise PrValidationError(
                "That kind of work is no longer offered, so it cannot be given a new quota",
                details={
                    "field": "work_type_id",
                    "reason": "work_type_inactive",
                    "work_type_code": work_type.code,
                },
            )

        chosen_basis = basis or work_type.default_quota_basis
        chosen_unit = unit
        if chosen_unit is None and chosen_basis is PrWorkQuotaBasis.QUANTITY:
            chosen_unit = work_type.default_unit
        assert_quota_unit(
            chosen_basis,
            chosen_unit,
            type_basis=work_type.default_quota_basis,
            type_unit=work_type.default_unit,
            work_type_code=work_type.code,
        )
        assert_quota_bounds(target_value, eligibility_cap)

        if await self._quota_for(plan_id=plan.id, work_type_id=work_type.id) is not None:
            raise PrValidationError(
                "This plan already has a quota for that kind of work",
                details={
                    "field": "work_type_id",
                    "reason": "duplicate_work_type_quota",
                    "work_type_code": work_type.code,
                },
            )
        if await self._quota_count(plan.id) >= MAX_QUOTAS_PER_PLAN:
            raise PrValidationError(
                "That is more quotas than one plan may hold",
                details={
                    "field": "quotas",
                    "reason": "too_many_quotas",
                    "maximum": MAX_QUOTAS_PER_PLAN,
                },
            )

        quota = PrWorkQuota(
            plan_id=plan.id,
            work_type_id=work_type.id,
            basis=chosen_basis,
            target_value=target_value,
            eligibility_cap=eligibility_cap,
            unit=chosen_unit,
            note=_optional_text(note, "note", MAX_PLAN_NOTE),
        )
        self._session.add(quota)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_QUOTA_ADDED,
            entity_type="pr_work_quota",
            entity_id=quota.id,
            after=_quota_payload(quota, plan=plan, period=period, work_type=work_type),
        )
        return await self.detail(actor=actor, plan_id=plan.id)

    async def update_quota(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        plan_id: uuid.UUID,
        quota_id: uuid.UUID,
        target_value: Decimal | None = None,
        eligibility_cap: Decimal | None = None,
        note: str | None = None,
        work_type_id: uuid.UUID | None = None,
    ) -> PlanDetail:
        """Change a **draft** quota's numbers, or the kind of work it is about.

        Whoever may edit the draft - see :meth:`_require_draft`: a manager with
        ``PR_WORK_CONFIGURE``, or the plan's own subject while it is theirs.

        **Moving a quota to another work type** (work-taxonomy cleanup): the
        quota is re-derived from the new type - its basis and, for a quantity
        type, its unit come from the type exactly as :meth:`add_quota` derives
        them - and refused when the plan already has a quota for that type,
        because two quotas on one type in one plan would be one target written
        twice, and adding them up is a decision nobody took. The numbers are
        kept; the workload the plan implies is recomputed when the plan is read,
        through the same projection every plan uses. An approved plan never
        reaches this method.

        The pair is validated **together**, after the merge, because
        ``eligibility_cap >= target_value`` is a rule about both and checking a
        new target against an old cap would accept a plan that is invalid the
        moment the second field lands.
        """
        plan, period = await self._require_draft(actor, plan_id)
        quota = await self._require_quota(plan_id=plan.id, quota_id=quota_id)
        work_type = await self._require_work_type(quota.work_type_id)
        before = _quota_payload(quota, plan=plan, period=period, work_type=work_type)
        moved = False

        if work_type_id is not None and work_type_id != quota.work_type_id:
            target_type = await self._require_work_type(work_type_id)
            if not target_type.is_active:
                raise PrValidationError(
                    "Loại công việc này đã ngừng sử dụng nên không đặt chỉ tiêu được.",
                    details={
                        "field": "work_type_id",
                        "reason": "work_type_inactive",
                        "work_type_code": target_type.code,
                    },
                )
            if await self._quota_for(plan_id=plan.id, work_type_id=target_type.id) is not None:
                raise PrValidationError(
                    "Kế hoạch đã có chỉ tiêu cho loại công việc này.",
                    details={
                        "field": "work_type_id",
                        "reason": "quota_work_type_already_exists",
                        "work_type_code": target_type.code,
                    },
                )
            quota.work_type_id = target_type.id
            quota.basis = target_type.default_quota_basis
            quota.unit = (
                target_type.default_unit
                if target_type.default_quota_basis is PrWorkQuotaBasis.QUANTITY
                else None
            )
            assert_quota_unit(
                quota.basis,
                quota.unit,
                type_basis=target_type.default_quota_basis,
                type_unit=target_type.default_unit,
                work_type_code=target_type.code,
            )
            work_type = target_type
            moved = True

        merged_target = quota.target_value if target_value is None else target_value
        merged_cap = quota.eligibility_cap if eligibility_cap is None else eligibility_cap
        assert_quota_bounds(merged_target, merged_cap)
        quota.target_value = merged_target
        quota.eligibility_cap = merged_cap
        if note is not None:
            quota.note = _optional_text(note, "note", MAX_PLAN_NOTE)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=(
                AuditAction.PR_WORK_PLAN_QUOTA_WORK_TYPE_CHANGED
                if moved
                else AuditAction.PR_WORK_PLAN_QUOTA_UPDATED
            ),
            entity_type="pr_work_quota",
            entity_id=quota.id,
            before=before,
            after=_quota_payload(quota, plan=plan, period=period, work_type=work_type),
        )
        return await self.detail(actor=actor, plan_id=plan.id)

    async def remove_quota(
        self, *, actor: Actor, request_id: uuid.UUID, plan_id: uuid.UUID, quota_id: uuid.UUID
    ) -> PlanDetail:
        """Take one work type out of a **draft** plan.

        ``PR_WORK_CONFIGURE``, draft only. A real delete rather than a flag,
        because a draft quota has decided nothing: no allocation references it -
        the evaluator only ever reads approved plans - so there is no history to
        preserve beyond the audit row this writes.
        """
        plan, period = await self._require_draft(actor, plan_id)
        quota = await self._require_quota(plan_id=plan.id, quota_id=quota_id)
        work_type = await self._require_work_type(quota.work_type_id)
        before = _quota_payload(quota, plan=plan, period=period, work_type=work_type)
        await self._session.delete(quota)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_QUOTA_REMOVED,
            entity_type="pr_work_quota",
            entity_id=quota_id,
            before=before,
        )
        return await self.detail(actor=actor, plan_id=plan.id)

    # =====================================================================
    # The lifecycle
    # =====================================================================
    async def approve(
        self, *, actor: Actor, request_id: uuid.UUID, plan_id: uuid.UUID, note: str | None = None
    ) -> PlanDetail:
        """Put a draft plan into force. **One transaction, or none of it.**

        ``PR_WORK_CONFIGURE``. In this order, and the order matters:

        1. lock the plan, so a second approver waits rather than races;
        2. re-read its status under the lock and refuse anything but ``DRAFT``;
        3. validate the whole plan - period still ``OPEN``, employee still
           active, at least one quota, every work type active, every target and
           cap sane, every unit compatible. **An invalid plan is refused here**
           rather than approved and left for the evaluator to trip over;
        4. supersede the plan currently in force, if there is one;
        5. mark this one ``APPROVED``;
        6. recompute the period's eligibility, so the numbers on screen change
           when the decision does.

        Two administrators pressing at once: the second waits on the lock,
        re-reads ``APPROVED``, and is refused by step 2 - so a retried request
        is safe and a duplicate approval is impossible. The partial unique index
        ``uq_pr_work_plans_approved`` says the same thing independently, which
        is what covers the case where the two transactions started before either
        plan existed.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        plan = await self._lock_plan(plan_id)
        if plan.status is not PrWorkPlanStatus.DRAFT:
            raise PrWorkPlanStateError(
                "Only a draft plan can be approved",
                details={
                    "reason": "plan_not_draft",
                    "plan_id": str(plan.id),
                    "status": plan.status.value,
                },
            )
        # **Independent approval.** KPI self-service lets the subject write the
        # proposal, so the one thing that must never follow is the subject
        # deciding it - whatever else they hold. An ``OWNER`` approving their own
        # KPI is refused here exactly like an employee, and there is no
        # override: a plan for the approver is somebody else's to approve.
        _require_not_subject(actor, plan, act="approve")
        period = await self._periods.require_period(plan.period_id)
        assert_plan_period_type(period)
        assert_period_open(period)
        await self._require_active_user(plan.user_id)
        quotas = await self._validate_for_approval(plan)

        now = utcnow()
        superseded = await self._plan_in_status(
            user_id=plan.user_id, period_id=plan.period_id, status=PrWorkPlanStatus.APPROVED
        )
        if superseded is not None:
            # Locked as well, so the two writes cannot interleave with another
            # approval that read the same "currently in force" row.
            locked_previous = await self._lock_plan(superseded.id)
            locked_previous.status = PrWorkPlanStatus.SUPERSEDED
            locked_previous.superseded_at = now
            # Flushed before the new plan is marked APPROVED: the partial unique
            # index allows one approved plan per (user, period), and writing the
            # new one first would violate it inside this very transaction.
            await self._session.flush()
            plan.supersedes_plan_id = locked_previous.id

        plan.status = PrWorkPlanStatus.APPROVED
        plan.approved_at = now
        plan.approved_by_user_id = _require_user_id(actor)
        if note is not None:
            plan.note = _optional_text(note, "note", MAX_PLAN_NOTE)
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_APPROVED,
            entity_type="pr_work_plan",
            entity_id=plan.id,
            before={"status": PrWorkPlanStatus.DRAFT.value},
            after={
                "status": plan.status.value,
                "user_id": str(plan.user_id),
                "period_code": period.code,
                "version_no": plan.version_no,
                "approved_at": now.isoformat(),
                "supersedes_plan_id": str(plan.supersedes_plan_id)
                if plan.supersedes_plan_id
                else None,
                "quotas": [
                    {
                        "work_type_id": str(quota.work_type_id),
                        "basis": quota.basis.value,
                        "target_value": str(quota.target_value),
                        "eligibility_cap": str(quota.eligibility_cap),
                        "unit": quota.unit.value if quota.unit else None,
                    }
                    for quota in quotas
                ],
            },
        )
        if superseded is not None:
            await record_pr_event(
                self._audit,
                request_id=request_id,
                actor=actor,
                action=AuditAction.PR_WORK_PLAN_SUPERSEDED,
                entity_type="pr_work_plan",
                entity_id=superseded.id,
                before={"status": PrWorkPlanStatus.APPROVED.value},
                after={
                    "status": PrWorkPlanStatus.SUPERSEDED.value,
                    "superseded_by_plan_id": str(plan.id),
                    "superseded_at": now.isoformat(),
                },
            )

        # The period is ``OPEN`` - step 3 refused anything else - so this is the
        # permitted recomputation, and it is what makes an approval visible
        # immediately rather than at the next reconcile.
        await self._eligibility.evaluate(user_id=plan.user_id, period=period, now=now)
        if self._notifier is not None:
            await self._notifier.plan_decided(
                plan, approved=True, note=None, actor=actor, period_code=period.code
            )
        logger.info(
            "pr_work_plan_approved",
            extra={
                "pr_work_plan_id": str(plan.id),
                "pr_work_plan_version": plan.version_no,
                "pr_reporting_period": period.code,
            },
        )
        return await self.detail(actor=actor, plan_id=plan.id)

    async def revise(
        self, *, actor: Actor, request_id: uuid.UUID, plan_id: uuid.UUID, note: str | None = None
    ) -> PlanDetail:
        """Clone an approved plan into the next ``DRAFT`` version.

        ``PR_WORK_CONFIGURE``. The **only** way to change an approved plan, and
        the reason there is no ``PATCH`` that could do it directly.

        The old version stays ``APPROVED`` and keeps deciding eligibility while
        the draft is written - which is the behaviour somebody revising a plan
        actually wants: nothing changes for the employee until the revision is
        approved, and if it never is, nothing changed at all.

        Quotas are copied by value, so editing the draft cannot reach back into
        the version that is still in force.
        """
        source = await self._lock_plan(plan_id)
        # KPI self-service: the plan's own subject may propose the revision of
        # the plan in force, under the same rules a manager revises it by. The
        # subject relationship is read under the lock from the row, never from
        # the request.
        self_service = await self._require_configure_or_subject(actor, source)
        if source.status is not PrWorkPlanStatus.APPROVED:
            raise PrWorkPlanStateError(
                "Only the plan currently in force can be revised",
                details={
                    "reason": "plan_not_approved",
                    "plan_id": str(source.id),
                    "status": source.status.value,
                },
            )
        period = await self._periods.require_period(source.period_id)
        assert_period_open(period)

        existing_draft = await self._plan_in_status(
            user_id=source.user_id, period_id=source.period_id, status=PrWorkPlanStatus.DRAFT
        )
        if existing_draft is not None:
            raise PrWorkPlanStateError(
                "A draft revision of this plan already exists",
                details={
                    "reason": "draft_already_exists",
                    "plan_id": str(existing_draft.id),
                    "version_no": existing_draft.version_no,
                },
            )

        draft = PrWorkPlan(
            user_id=source.user_id,
            period_id=source.period_id,
            version_no=await self._next_version(user_id=source.user_id, period_id=source.period_id),
            status=PrWorkPlanStatus.DRAFT,
            supersedes_plan_id=source.id,
            note=_optional_text(note, "note", MAX_PLAN_NOTE),
            created_by_user_id=_require_user_id(actor),
        )
        self._session.add(draft)
        await self._session.flush()
        for quota in await self._quotas_of(source.id):
            self._session.add(
                PrWorkQuota(
                    plan_id=draft.id,
                    work_type_id=quota.work_type_id,
                    basis=quota.basis,
                    target_value=quota.target_value,
                    eligibility_cap=quota.eligibility_cap,
                    unit=quota.unit,
                    note=quota.note,
                )
            )
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_REVISED,
            entity_type="pr_work_plan",
            entity_id=draft.id,
            after={
                "user_id": str(draft.user_id),
                "period_code": period.code,
                "version_no": draft.version_no,
                "revises_plan_id": str(source.id),
                "revises_version_no": source.version_no,
                "self_service": self_service,
            },
        )
        return await self.detail(actor=actor, plan_id=draft.id)

    async def discard(
        self, *, actor: Actor, request_id: uuid.UUID, plan_id: uuid.UUID, note: str | None = None
    ) -> PlanDetail:
        """Abandon a draft. ``PR_WORK_CONFIGURE``, draft only.

        Not a deletion: the row and its audit trail stay, so *"who proposed this
        and who dropped it"* is still answerable. A discarded draft frees the
        one-draft slot and never becomes approved.
        """
        plan = await self._lock_plan(plan_id)
        # KPI self-service: the subject may drop their own draft, but not one
        # a manager is reviewing - a submitted proposal that vanished would be
        # an approval request nobody can find. The manager returns it first,
        # or discards it with the configuration right.
        if await self._require_configure_or_subject(actor, plan) and plan.is_submitted:
            raise PrWorkPlanStateError(
                "This draft has been submitted for review and cannot be discarded by its author",
                details={
                    "reason": "draft_submitted_locked",
                    "plan_id": str(plan.id),
                    "version_no": plan.version_no,
                },
            )
        if plan.status is not PrWorkPlanStatus.DRAFT:
            raise PrWorkPlanStateError(
                "Only a draft plan can be discarded",
                details={
                    "reason": "plan_not_draft",
                    "plan_id": str(plan.id),
                    "status": plan.status.value,
                },
            )
        now = utcnow()
        plan.status = PrWorkPlanStatus.DISCARDED
        plan.discarded_at = now
        plan.discarded_by_user_id = _require_user_id(actor)
        if note is not None:
            plan.note = _optional_text(note, "note", MAX_PLAN_NOTE)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_DISCARDED,
            entity_type="pr_work_plan",
            entity_id=plan.id,
            before={"status": PrWorkPlanStatus.DRAFT.value},
            after={"status": plan.status.value, "discarded_at": now.isoformat()},
        )
        return await self.detail(actor=actor, plan_id=plan.id)

    async def submit(
        self, *, actor: Actor, request_id: uuid.UUID, plan_id: uuid.UUID
    ) -> PlanDetail:
        """**The employee hands their draft to the manager.** KPI self-service.

        Under the row lock, so a save racing this call sees the submission and
        is refused rather than landing on a plan somebody is already
        reviewing. The subject only - a manager approves rather than submits -
        and the draft must be **ready**: the same readiness the approval
        re-checks, so nobody is sent a plan nobody could approve.

        What it does **not** do is the whole point: the status stays ``DRAFT``,
        the plan in force stays in force, the evaluator reads nothing, and no
        allocation moves. It records who asked and when, clears any earlier
        return, and locks the author out.

        Submitting twice is a structured conflict, ``draft_already_submitted``,
        rather than a second event: the first submission is the fact.
        """
        plan = await self._lock_plan(plan_id)
        if not self._is_subject(actor, plan):
            raise PrPermissionDeniedError(
                "Only the plan's own subject may submit it for review",
                details={"reason": "not_plan_subject", "plan_id": str(plan.id)},
            )
        if plan.status is not PrWorkPlanStatus.DRAFT:
            raise PrWorkPlanStateError(
                "Only a draft plan can be submitted",
                details={
                    "reason": "plan_not_draft",
                    "plan_id": str(plan.id),
                    "status": plan.status.value,
                },
            )
        if plan.is_submitted:
            raise PrWorkPlanStateError(
                "This draft has already been submitted for review",
                details={
                    "reason": "draft_already_submitted",
                    "plan_id": str(plan.id),
                    "submitted_at": plan.submitted_at.isoformat() if plan.submitted_at else None,
                },
            )
        period = await self._periods.require_period(plan.period_id)
        blockers = await self._readiness_blockers(plan, period)
        if blockers:
            raise PrValidationError(
                "This plan is not ready to be submitted",
                details={
                    "reason": "plan_not_ready",
                    "plan_id": str(plan.id),
                    "blockers": [one.value for one in blockers],
                },
            )
        now = utcnow()
        plan.submitted_at = now
        plan.submitted_by_user_id = _require_user_id(actor)
        plan.returned_at = None
        plan.returned_by_user_id = None
        plan.return_note = None
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_SUBMITTED,
            entity_type="pr_work_plan",
            entity_id=plan.id,
            after={
                "user_id": str(plan.user_id),
                "period_code": period.code,
                "version_no": plan.version_no,
                "submitted_at": now.isoformat(),
                "quota_count": await self._quota_count(plan.id),
            },
        )
        if self._notifier is not None:
            await self._notifier.plan_submitted(
                plan, await self._reviewer_ids(), actor=actor, period_code=period.code
            )
        return await self.detail(actor=actor, plan_id=plan.id)

    async def return_for_revision(
        self, *, actor: Actor, request_id: uuid.UUID, plan_id: uuid.UUID, note: str | None = None
    ) -> PlanDetail:
        """**The manager sends a submitted draft back.** ``PR_WORK_CONFIGURE``.

        The same version, reopened: the status stays ``DRAFT``, the submission
        is cleared, the employee may edit again, and the plan in force is
        untouched. ``note`` is the reason, kept on the row for the employee to
        read on the returned card and repeated in the audit trail.

        Refuses a draft nobody submitted - ``draft_not_submitted`` - because
        there is nothing to return it *from*; a manager who wants a draft they
        wrote themselves to be the employee's again simply lets them edit it.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        plan = await self._lock_plan(plan_id)
        if plan.status is not PrWorkPlanStatus.DRAFT:
            raise PrWorkPlanStateError(
                "Only a draft plan can be returned for revision",
                details={
                    "reason": "plan_not_draft",
                    "plan_id": str(plan.id),
                    "status": plan.status.value,
                },
            )
        if not plan.is_submitted:
            raise PrWorkPlanStateError(
                "This draft has not been submitted, so there is nothing to return",
                details={"reason": "draft_not_submitted", "plan_id": str(plan.id)},
            )
        period = await self._periods.require_period(plan.period_id)
        now = utcnow()
        plan.submitted_at = None
        plan.submitted_by_user_id = None
        plan.returned_at = now
        plan.returned_by_user_id = _require_user_id(actor)
        plan.return_note = _optional_text(note, "note", MAX_PLAN_NOTE)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PLAN_RETURNED,
            entity_type="pr_work_plan",
            entity_id=plan.id,
            after={
                "user_id": str(plan.user_id),
                "period_code": period.code,
                "version_no": plan.version_no,
                "returned_at": now.isoformat(),
                "return_note": plan.return_note,
            },
        )
        if self._notifier is not None:
            await self._notifier.plan_decided(
                plan, approved=False, note=plan.return_note, actor=actor, period_code=period.code
            )
        return await self.detail(actor=actor, plan_id=plan.id)

    # =====================================================================
    # Reading
    # =====================================================================
    async def detail(self, *, actor: Actor, plan_id: uuid.UUID) -> PlanDetail:
        """One plan version in full, with the controls this actor may use.

        Readable by the employee it is about and by anybody with
        ``PR_WORK_VIEW_ALL``. ``PR_WORK_MANAGE`` on its own is **not** one of
        the relationships, for the same reason it is not one on M1's work
        detail: it would be a way to walk past the list scope one id at a time.
        """
        plan = await self._require_plan(plan_id)
        await self._require_readable(actor, plan)
        # **Re-read the row before it is rendered.** Every lifecycle write above
        # returns through here after flushing an UPDATE, and an UPDATE expires
        # the database-maintained ``updated_at`` (``onupdate=func.now()``) on the
        # identity-mapped object. The API layer then serialises the row in
        # ordinary synchronous code, and touching an expired attribute there
        # asks SQLAlchemy for I/O outside its async context - which is the
        # ``MissingGreenlet`` that turned *Bỏ bản nháp* and *Gửi duyệt* into
        # HTTP 500s while the transaction they had flushed rolled back. Approval
        # escaped only because the eligibility recompute happened to SELECT the
        # plan again. One explicit refresh here, on the single path every
        # response takes, is the whole fix; no attribute a serializer reads can
        # be stale or expired after it.
        await self._session.refresh(plan)
        period = await self._periods.require_period(plan.period_id)
        quotas = await self._quotas_of(plan.id)
        types = await self._types_for([quota.work_type_id for quota in quotas])
        names = await self._names(
            [
                one
                for one in (
                    plan.user_id,
                    plan.created_by_user_id,
                    plan.approved_by_user_id,
                    plan.submitted_by_user_id,
                    plan.returned_by_user_id,
                )
                if one is not None
            ]
        )
        may_configure = await self._capabilities.allows(actor, PrCapability.PR_WORK_CONFIGURE)
        is_subject = self._is_subject(actor, plan)
        is_draft = plan.status is PrWorkPlanStatus.DRAFT
        is_approved = plan.status is PrWorkPlanStatus.APPROVED
        period_open = period.status.value == "OPEN"
        submitted = plan.is_submitted
        # Asked only where it can decide something: an approved plan may or may
        # not already have a revision in flight, and a draft *is* the one.
        existing_draft = (
            plan
            if is_draft
            else await self._plan_in_status(
                user_id=plan.user_id, period_id=plan.period_id, status=PrWorkPlanStatus.DRAFT
            )
            if is_approved
            else None
        )
        blockers = tuple(one.value for one in await self._readiness_blockers(plan, period))
        # The employee's own draft is theirs to edit until they submit it; from
        # then on it is the manager's. A manager may edit any draft, submitted or
        # not - correcting a proposal before approving it is the review.
        employee_may_edit = is_subject and not submitted
        return PlanDetail(
            plan=plan,
            period=period,
            quotas=tuple(quotas),
            work_types=tuple(types),
            user_name=names.get(plan.user_id),
            created_by_name=names.get(plan.created_by_user_id),
            approved_by_name=names.get(plan.approved_by_user_id)
            if plan.approved_by_user_id
            else None,
            submitted_by_name=names.get(plan.submitted_by_user_id)
            if plan.submitted_by_user_id
            else None,
            returned_by_name=names.get(plan.returned_by_user_id)
            if plan.returned_by_user_id
            else None,
            # False for every approved plan, whoever is asking. The client must
            # offer a revision, and the route refuses an edit either way.
            can_edit=is_draft and period_open and (may_configure or employee_may_edit),
            # Never the subject, whatever else they hold - see :meth:`approve`.
            can_approve=(
                may_configure and not is_subject and is_draft and period_open and not blockers
            ),
            # **False while a revision is already in flight.** M2 allows one
            # ``DRAFT`` per employee and period - ``uq_pr_work_plans_draft`` - so
            # offering "Tạo bản điều chỉnh" over an approved plan that already
            # has a draft is offering an act the service will refuse. That is
            # the trapped-draft bug: the only visible control led to an error,
            # and the draft it collided with had nowhere on the screen to be
            # continued from. The index is still the rule; this is the courtesy.
            can_revise=(
                (may_configure or is_subject)
                and is_approved
                and period_open
                and existing_draft is None
            ),
            can_discard=is_draft and (may_configure or employee_may_edit),
            is_subject=is_subject,
            can_submit=is_subject and is_draft and not submitted and period_open and not blockers,
            can_return=may_configure and is_draft and submitted,
            readiness_blockers=blockers,
            workload=await self._workload_for(plan, quotas, period),
        )

    async def list_plans(
        self,
        *,
        actor: Actor,
        user_id: uuid.UUID | None = None,
        period_id: uuid.UUID | None = None,
        status: PrWorkPlanStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PlanPage:
        """Plans, newest first.

        Asking about somebody else needs ``PR_WORK_VIEW_ALL``, and the request
        is **refused** rather than narrowed to the caller's own plans: a screen
        headed with a colleague's name showing your figures is worse than an
        error.
        """
        subject = await self._require_subject(actor, user_id)
        conditions = []
        if subject is not None:
            conditions.append(PrWorkPlan.user_id == subject)
        if period_id is not None:
            conditions.append(PrWorkPlan.period_id == period_id)
        if status is not None:
            conditions.append(PrWorkPlan.status == status)

        bounded = max(1, min(limit, 200))
        base: Select[tuple[PrWorkPlan]] = select(PrWorkPlan)
        if conditions:
            base = base.where(*conditions)
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        rows = (
            (
                await self._session.execute(
                    base.order_by(PrWorkPlan.created_at.desc(), PrWorkPlan.version_no.desc())
                    .limit(bounded)
                    .offset(max(0, offset))
                )
            )
            .scalars()
            .all()
        )
        periods = await self._periods_for({row.period_id for row in rows})
        names = await self._names([row.user_id for row in rows])
        counts = await self._quota_counts([row.id for row in rows])
        return PlanPage(
            plans=tuple(rows),
            total=int(total or 0),
            limit=bounded,
            offset=max(0, offset),
            periods=tuple(periods),
            user_names=names,
            quota_counts=counts,
        )

    async def period_summary(
        self, *, actor: Actor, period_id: uuid.UUID
    ) -> tuple[EmployeePlanSummary, ...]:
        """**One row per employee** for one reporting month. ``PR_WORK_VIEW_ALL``.

        The KPI screen's main query, post-M4, and the thing
        :meth:`list_plans` structurally could not be: that one lists *plans*, so
        an employee with three revisions is three rows and an employee with none
        is no row at all. Neither answers *"who has a KPI plan this month"*.

        Built from the **employee directory**, not from the plans - every active
        user appears, in the same order the assignee pickers use, and the plans
        are attached to them. That inversion is the whole change.

        Ordered by name rather than by plan activity, because the list is a
        roster: a manager looking for one person's row should find it where
        their eye expects it, and a person with no plan must not sink to the
        bottom, since they are precisely who the screen is meant to surface.

        ``PR_WORK_VIEW_ALL`` rather than ``PR_WORK_CONFIGURE``: this is a read
        of every colleague's KPI standing, which is the same act
        :class:`~meobot.application.pr_work_query_service.PrWorkScope`'s ``ALL``
        is gated on. Writing plans stays ``PR_WORK_CONFIGURE``, and a holder of
        one capability without the other meets the right refusal either way.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_VIEW_ALL)
        period = await self._periods.require_period(period_id)

        people = (
            (
                await self._session.execute(
                    select(User).where(User.active.is_(True)).order_by(User.full_name)
                )
            )
            .scalars()
            .all()
        )
        rows = (
            (
                await self._session.execute(
                    select(PrWorkPlan)
                    .where(PrWorkPlan.period_id == period.id)
                    .order_by(PrWorkPlan.version_no.desc())
                )
            )
            .scalars()
            .all()
        )
        by_person: dict[uuid.UUID, list[PrWorkPlan]] = {}
        for row in rows:
            by_person.setdefault(row.user_id, []).append(row)

        # The current plan and the active draft are different rows whenever a
        # manager is revising a plan already in force, and the screen prints a
        # quota count for each - so both are counted here rather than the draft's
        # being left for a browser to fetch one row at a time.
        counted_ids: list[uuid.UUID] = []
        for plans in by_person.values():
            if (plan := current_plan_of(plans)) is not None:
                counted_ids.append(plan.id)
            if (draft := active_draft_of(plans)) is not None:
                counted_ids.append(draft.id)
        counts = await self._quota_counts(counted_ids)
        # **Batched, not per row.** Twenty employees' current plans and drafts
        # are priced in one pass - one quota query, one rule query, one policy,
        # one calendar - rather than each row resolving its own month. The
        # figures are the same calculator's as the detail screen's.
        workloads = await self._workloads_for(
            [plan for plans in by_person.values() for plan in _counted_plans(plans)], period
        )

        summaries: list[EmployeePlanSummary] = []
        for person in people:
            plans = by_person.get(person.id, [])
            summaries.append(
                self._summarise(
                    user_id=person.id,
                    user_name=person.full_name,
                    period=period,
                    plans=plans,
                    counts=counts,
                    workloads=workloads,
                )
            )
        return tuple(summaries)

    async def employee_summary(
        self, *, actor: Actor, period_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> EmployeePlanSummary:
        """**One employee's KPI standing for one month** - the self-service screen.

        The same row :meth:`period_summary` builds for a manager, for one person,
        readable by that person about themselves and by ``PR_WORK_VIEW_ALL``
        about anybody. ``user_id`` defaults to the caller, and asking about
        somebody else without the capability is refused rather than narrowed -
        the rule :meth:`list_plans` already follows.

        Built here rather than assembled by a browser from the plan list, so the
        four states the employee's screen draws - no plan, editing, submitted,
        approved with or without a revision - are the server's reading of the
        same rows the manager sees.
        """
        subject = await self._require_subject(actor, user_id, default_to_self=True)
        assert subject is not None
        period = await self._periods.require_period(period_id)
        person = await self._session.get(User, subject)
        if person is None:
            raise PrNotFoundError("No such user", details={"entity": "user", "id": str(subject)})
        plans = (
            (
                await self._session.execute(
                    select(PrWorkPlan)
                    .where(PrWorkPlan.user_id == subject, PrWorkPlan.period_id == period.id)
                    .order_by(PrWorkPlan.version_no.desc())
                )
            )
            .scalars()
            .all()
        )
        counted = _counted_plans(plans)
        counts = await self._quota_counts([one.id for one in counted])
        return self._summarise(
            user_id=person.id,
            user_name=person.full_name,
            period=period,
            plans=plans,
            counts=counts,
            workloads=await self._workloads_for(counted, period),
        )

    async def _workloads_for(
        self, plans: Sequence[PrWorkPlan], period: PrReportingPeriod
    ) -> dict[uuid.UUID, PlanWorkload]:
        """Every plan priced in one pass; empty when the readers are absent."""
        if self._workloads is None or not plans:
            return {}
        return await self._workloads.for_plans(plans, period)

    @staticmethod
    def _summarise(
        *,
        user_id: uuid.UUID,
        user_name: str,
        period: PrReportingPeriod,
        plans: Sequence[PrWorkPlan],
        counts: dict[uuid.UUID, int],
        workloads: dict[uuid.UUID, PlanWorkload],
    ) -> EmployeePlanSummary:
        """One person's row, from their plans for the month. No queries."""
        current = current_plan_of(plans)
        draft = active_draft_of(plans)
        return EmployeePlanSummary(
            user_id=user_id,
            user_name=user_name,
            period_id=period.id,
            current_plan=current,
            quota_count=counts.get(current.id, 0) if current is not None else 0,
            latest_draft=draft,
            draft_quota_count=counts.get(draft.id, 0) if draft is not None else 0,
            history_count=len(plans),
            terminal_count=sum(1 for one in plans if one.status in TERMINAL_PLAN_STATUSES),
            draft_workload=workloads.get(draft.id) if draft is not None else None,
            # The plan in force keeps its own figure; a draft never replaces it
            # before approval and the two are never added.
            current_workload=(
                workloads.get(current.id) if current is not None and current is not draft else None
            ),
        )

    async def history(
        self, *, actor: Actor, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> tuple[tuple[PrWorkPlan, int], ...]:
        """Every version for one employee and month, newest first, with quota counts.

        Deterministic by ``version_no`` descending, which is the only ordering
        that cannot tie: numbers are allocated per ``(user, period)`` and never
        reused, so v3 is unambiguously after v2 whatever the wall clock did.
        Ordering by ``created_at`` would tie two versions written in the same
        second and reorder the history between two page loads.

        Asking about somebody else needs ``PR_WORK_VIEW_ALL``, and it is refused
        rather than narrowed - the same rule :meth:`list_plans` follows, for the
        same reason.
        """
        subject = await self._require_subject(actor, user_id)
        assert subject is not None
        plans = (
            (
                await self._session.execute(
                    select(PrWorkPlan)
                    .where(PrWorkPlan.user_id == subject, PrWorkPlan.period_id == period_id)
                    .order_by(PrWorkPlan.version_no.desc())
                )
            )
            .scalars()
            .all()
        )
        counts = await self._quota_counts([one.id for one in plans])
        return tuple((one, counts.get(one.id, 0)) for one in plans)

    async def plan_in_force(
        self, *, actor: Actor, user_id: uuid.UUID | None, period_id: uuid.UUID
    ) -> PlanDetail | None:
        """The approved plan for one person and period, or ``None``.

        ``None`` is the honest answer for an employee with no approved plan, and
        the screen says so in words - *"chưa có hạn mức KPI"* - rather than
        showing an empty table that reads as "no work".
        """
        subject = await self._require_subject(actor, user_id, default_to_self=True)
        assert subject is not None
        plan = await self._plan_in_status(
            user_id=subject, period_id=period_id, status=PrWorkPlanStatus.APPROVED
        )
        if plan is None:
            return None
        return await self.detail(actor=actor, plan_id=plan.id)

    # =====================================================================
    # Internals
    # =====================================================================
    async def _validate_for_approval(self, plan: PrWorkPlan) -> Sequence[PrWorkQuota]:
        """Every check an approval has to pass, before anything is written.

        Refusing here rather than letting the evaluator trip over a bad plan is
        the difference between *"this quota is in a unit the work is never
        recorded in"* and a KPI figure that silently stays at zero all month.

        The checks are :meth:`_readiness_blockers`, the list the employee's
        submission is held to and the detail screen shows - one rule, asked
        twice. Raised here in the words and with the codes the M2 routes have
        always used, so a client mapping ``plan_has_no_quotas`` keeps working.
        """
        quotas = await self._quotas_of(plan.id)
        if not quotas:
            raise PrValidationError(
                "A plan needs at least one quota before it can be approved",
                details={"field": "quotas", "reason": "plan_has_no_quotas"},
            )
        seen: set[uuid.UUID] = set()
        for quota in quotas:
            if quota.work_type_id in seen:  # pragma: no cover - the unique index refuses it first
                raise PrValidationError(
                    "This plan has two quotas for one kind of work",
                    details={
                        "field": "work_type_id",
                        "reason": "duplicate_work_type_quota",
                        "work_type_id": str(quota.work_type_id),
                    },
                )
            seen.add(quota.work_type_id)
            work_type = await self._require_work_type(quota.work_type_id)
            if not work_type.is_active:
                raise PrValidationError(
                    "A quota names a kind of work that is no longer offered",
                    details={
                        "field": "work_type_id",
                        "reason": "work_type_inactive",
                        "work_type_code": work_type.code,
                    },
                )
            # Re-validated at approval as well as at entry, because a work type's
            # basis may have been reconfigured since the draft was written and
            # approving a quota that no longer matches its type would be
            # approving something nobody could satisfy.
            assert_quota_unit(
                quota.basis,
                quota.unit,
                type_basis=work_type.default_quota_basis,
                type_unit=work_type.default_unit,
                work_type_code=work_type.code,
            )
            assert_quota_bounds(quota.target_value, quota.eligibility_cap)
        return quotas

    async def _readiness_blockers(
        self, plan: PrWorkPlan, period: PrReportingPeriod
    ) -> tuple[PrPlanReadinessBlocker, ...]:
        """**Why this draft could not be approved right now**, as codes.

        The single readiness rule, read by three callers: :meth:`submit`
        refuses on it, :meth:`detail` shows it, and :meth:`approve`
        re-derives the same conditions under its lock through
        :meth:`_validate_for_approval`. Every condition that method raises on
        is one code here, in the same order, so the two cannot disagree about
        what a ready plan is.

        Empty for a plan that is not a draft: readiness is a question about a
        proposal, and an approved plan already answered it.
        """
        if plan.status is not PrWorkPlanStatus.DRAFT:
            return ()
        found: list[PrPlanReadinessBlocker] = []
        if period.status.value != "OPEN" or period.period_type.value != "MONTH":
            found.append(PrPlanReadinessBlocker.PERIOD_NOT_OPEN)
        subject = await self._session.get(User, plan.user_id)
        if subject is None or not subject.active:
            found.append(PrPlanReadinessBlocker.SUBJECT_INACTIVE)
        quotas = await self._quotas_of(plan.id)
        if not quotas:
            found.append(PrPlanReadinessBlocker.PLAN_HAS_NO_QUOTAS)
        for quota in quotas:
            work_type = await self._session.get(PrWorkType, quota.work_type_id)
            if work_type is None or not work_type.is_active:
                _append_once(found, PrPlanReadinessBlocker.WORK_TYPE_INACTIVE)
                continue
            try:
                assert_quota_unit(
                    quota.basis,
                    quota.unit,
                    type_basis=work_type.default_quota_basis,
                    type_unit=work_type.default_unit,
                    work_type_code=work_type.code,
                )
            except PrValidationError:
                _append_once(found, PrPlanReadinessBlocker.QUOTA_UNIT_MISMATCH)
            try:
                assert_quota_bounds(quota.target_value, quota.eligibility_cap)
            except PrValidationError:
                _append_once(found, PrPlanReadinessBlocker.QUOTA_BOUNDS_INVALID)
        return tuple(found)

    async def _workload_for(
        self, plan: PrWorkPlan, quotas: Sequence[PrWorkQuota], period: PrReportingPeriod
    ) -> PlanWorkload | None:
        """Price the plan in standard minutes, through the one calculator.

        ``None`` when this service was built without M6's readers. Rates are
        the approved scoring rules in force on the period's last day and the
        target is the person's resolved month - see
        :mod:`meobot.application.pr_plan_workload` for the arithmetic, which is
        stated there once and nowhere else.
        """
        if self._workloads is None:
            return None
        return await self._workloads.for_plan(plan, period, quotas=quotas)

    async def _require_draft(
        self, actor: Actor, plan_id: uuid.UUID
    ) -> tuple[PrWorkPlan, PrReportingPeriod]:
        """A ``DRAFT`` this actor may edit, on an ``OPEN`` period - **under the lock**.

        The single gate every quota edit passes through, so *"an approved plan
        is never edited in place"* is one check in one place rather than four
        copies that could disagree. The refusal names the status and says what
        to do instead, because the answer is always the same: revise it.

        KPI self-service widens who may pass, and narrows when: a manager with
        ``PR_WORK_CONFIGURE`` edits any draft, submitted or not; the plan's own
        subject edits their own draft only while it is theirs - a submitted
        draft refuses them with ``draft_submitted_locked``. The row is locked
        first so a save racing a submission reads the submission rather than
        landing beside it.
        """
        plan = await self._lock_plan(plan_id)
        self_service = await self._require_configure_or_subject(actor, plan)
        if plan.status is not PrWorkPlanStatus.DRAFT:
            raise PrWorkPlanStateError(
                "This plan is not a draft, so its quotas cannot be edited. Create a revision.",
                details={
                    "reason": "plan_not_draft",
                    "plan_id": str(plan.id),
                    "status": plan.status.value,
                    "next": "revise",
                },
            )
        if self_service and plan.is_submitted:
            raise PrWorkPlanStateError(
                "This draft has been submitted for review and is locked for its author",
                details={
                    "reason": "draft_submitted_locked",
                    "plan_id": str(plan.id),
                    "version_no": plan.version_no,
                    "submitted_at": plan.submitted_at.isoformat() if plan.submitted_at else None,
                },
            )
        period = await self._periods.require_period(plan.period_id)
        assert_period_open(period)
        return plan, period

    async def _require_configure_or_subject(self, actor: Actor, plan: PrWorkPlan) -> bool:
        """``PR_WORK_CONFIGURE``, or the plan's own subject. Returns *whether the
        subject path was taken*, so a caller can apply the self-service rules.

        A manager holding the capability is never on the subject path even for
        their own plan: the configuration right is the wider one and carries
        the manager's rules - and the one act it does not carry, approving
        oneself, is refused separately by name.

        Anybody else is told the plan does not exist, matching
        :meth:`_require_readable`: whether a colleague has a draft is itself
        information.
        """
        if await self._capabilities.allows(actor, PrCapability.PR_WORK_CONFIGURE):
            return False
        if self._is_subject(actor, plan):
            await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
            return True
        raise PrNotFoundError(
            "No such KPI plan", details={"entity": "pr_work_plan", "id": str(plan.id)}
        )

    @staticmethod
    def _is_subject(actor: Actor, plan: PrWorkPlan) -> bool:
        return actor.user_id is not None and plan.user_id == actor.user_id

    async def _require_readable(self, actor: Actor, plan: PrWorkPlan) -> None:
        """Own plan, or ``PR_WORK_VIEW_ALL``. Nothing else."""
        if actor.user_id is not None and plan.user_id == actor.user_id:
            return
        if await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL):
            return
        # A not-found rather than a forbidden, matching M1's work detail: whether
        # a colleague has a KPI plan is itself information.
        raise PrNotFoundError(
            "No such KPI plan", details={"entity": "pr_work_plan", "id": str(plan.id)}
        )

    async def _require_subject(
        self, actor: Actor, user_id: uuid.UUID | None, *, default_to_self: bool = False
    ) -> uuid.UUID | None:
        """Whose plans this caller may ask about.

        ``None`` means "everybody", and only ``PR_WORK_VIEW_ALL`` gets it.
        """
        if actor.user_id is None:
            raise PrPermissionDeniedError(
                "This actor has no user row, so there are no plans to read",
                details={"reason": "actor_has_no_user_row"},
            )
        if user_id is not None and user_id == actor.user_id:
            return actor.user_id
        may_view_all = await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL)
        if user_id is None:
            if default_to_self or not may_view_all:
                return actor.user_id
            return None
        if not may_view_all:
            raise PrPermissionDeniedError(
                "You may not read another person's KPI plan",
                details={"reason": "user_filter_not_permitted"},
            )
        return user_id

    async def _next_version(self, *, user_id: uuid.UUID, period_id: uuid.UUID) -> int:
        highest = await self._session.scalar(
            select(func.max(PrWorkPlan.version_no)).where(
                PrWorkPlan.user_id == user_id, PrWorkPlan.period_id == period_id
            )
        )
        return int(highest or 0) + 1

    async def _plan_in_status(
        self, *, user_id: uuid.UUID, period_id: uuid.UUID, status: PrWorkPlanStatus
    ) -> PrWorkPlan | None:
        statement = select(PrWorkPlan).where(
            PrWorkPlan.user_id == user_id,
            PrWorkPlan.period_id == period_id,
            PrWorkPlan.status == status,
        )
        return (await self._session.execute(statement)).scalars().one_or_none()

    async def _lock_plan(self, plan_id: uuid.UUID) -> PrWorkPlan:
        """Load one plan holding it against concurrent writers.

        The reason approving twice is safe: the second transaction blocks here
        and re-reads ``APPROVED`` rather than writing a second approval over
        the first.
        """
        row = await lock_row(self._session, PrWorkPlan, plan_id)
        if row is None:
            raise PrNotFoundError(
                "No such KPI plan", details={"entity": "pr_work_plan", "id": str(plan_id)}
            )
        return row

    async def _require_plan(self, plan_id: uuid.UUID) -> PrWorkPlan:
        row = await self._session.get(PrWorkPlan, plan_id)
        if row is None:
            raise PrNotFoundError(
                "No such KPI plan", details={"entity": "pr_work_plan", "id": str(plan_id)}
            )
        return row

    async def _quotas_of(self, plan_id: uuid.UUID) -> Sequence[PrWorkQuota]:
        statement = (
            select(PrWorkQuota)
            .where(PrWorkQuota.plan_id == plan_id)
            .order_by(PrWorkQuota.created_at.asc())
        )
        return (await self._session.execute(statement)).scalars().all()

    async def _quota_for(
        self, *, plan_id: uuid.UUID, work_type_id: uuid.UUID
    ) -> PrWorkQuota | None:
        statement = select(PrWorkQuota).where(
            PrWorkQuota.plan_id == plan_id, PrWorkQuota.work_type_id == work_type_id
        )
        return (await self._session.execute(statement)).scalars().one_or_none()

    async def _require_quota(self, *, plan_id: uuid.UUID, quota_id: uuid.UUID) -> PrWorkQuota:
        statement = select(PrWorkQuota).where(
            PrWorkQuota.id == quota_id, PrWorkQuota.plan_id == plan_id
        )
        row = (await self._session.execute(statement)).scalars().one_or_none()
        if row is None:
            raise PrNotFoundError(
                "No such quota on this plan",
                details={"entity": "pr_work_quota", "id": str(quota_id)},
            )
        return row

    async def _quota_count(self, plan_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count()).select_from(PrWorkQuota).where(PrWorkQuota.plan_id == plan_id)
            )
            or 0
        )

    async def _quota_counts(self, plan_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """How many quotas each plan holds, in one query rather than per row."""
        if not plan_ids:
            return {}
        statement = (
            select(PrWorkQuota.plan_id, func.count())
            .where(PrWorkQuota.plan_id.in_(plan_ids))
            .group_by(PrWorkQuota.plan_id)
        )
        return {row[0]: int(row[1]) for row in (await self._session.execute(statement)).all()}

    async def _require_work_type(self, work_type_id: uuid.UUID) -> PrWorkType:
        row = await self._session.get(PrWorkType, work_type_id)
        if row is None:
            raise PrNotFoundError(
                "No such kind of work",
                details={"entity": "pr_work_type", "id": str(work_type_id)},
            )
        return row

    async def _types_for(self, type_ids: Sequence[uuid.UUID]) -> Sequence[PrWorkType]:
        if not type_ids:
            return []
        statement = select(PrWorkType).where(PrWorkType.id.in_(set(type_ids)))
        return (await self._session.execute(statement)).scalars().all()

    async def _periods_for(self, period_ids: set[uuid.UUID]) -> Sequence[PrReportingPeriod]:
        if not period_ids:
            return []
        statement = select(PrReportingPeriod).where(PrReportingPeriod.id.in_(period_ids))
        return (await self._session.execute(statement)).scalars().all()

    async def _require_active_user(self, user_id: uuid.UUID) -> None:
        """A plan is written for somebody who still works here.

        The same rule M1's ``_require_active_users`` applies to contributors,
        applied to the person a plan is about: a KPI target for a deactivated
        account is a target nobody will meet and a row somebody will have to
        explain.
        """
        row = await self._session.get(User, user_id)
        if row is None:
            raise PrNotFoundError("No such person", details={"entity": "user", "id": str(user_id)})
        if not row.active:
            raise PrValidationError(
                "That person is not active",
                details={"field": "user_id", "reason": "user_inactive", "user_id": str(user_id)},
            )

    async def _reviewer_ids(self) -> list[uuid.UUID]:
        """Who may approve a KPI plan: every active holder of the configuration right.

        ``PR_WORK_CONFIGURE`` is role-backed - ``ADMIN`` and ``OWNER`` - so the
        answer is a role query rather than a grant lookup. Read here, once, at
        submission; it is the only thing in this service that asks about a role
        by name, and it asks only to know whom to tell.
        """
        rows = (
            (
                await self._session.execute(
                    select(User.id).where(
                        User.active.is_(True), User.role.in_((Role.ADMIN, Role.OWNER))
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def _names(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Resolve people's names once, so no response prints a bare UUID."""
        wanted = {one for one in user_ids if one is not None}
        if not wanted:
            return {}
        statement = select(User.id, User.full_name).where(User.id.in_(wanted))
        return {row[0]: row[1] for row in (await self._session.execute(statement)).all()}


def active_draft_of(plans: Sequence[PrWorkPlan]) -> PrWorkPlan | None:
    """The revision being written, out of every version for one employee-month.

    At most one, guaranteed by ``uq_pr_work_plans_draft`` - so this is a lookup
    and not a "latest" heuristic. Named and used in one place because *"which
    row is the draft"* is a lifecycle question, and the moment a browser answers
    it the answer starts drifting from the index that enforces it.
    """
    return next((one for one in plans if one.status is PrWorkPlanStatus.DRAFT), None)


def _counted_plans(plans: Sequence[PrWorkPlan]) -> list[PrWorkPlan]:
    """The rows a summary prints figures for: the plan in force and the draft."""
    return [one for one in (current_plan_of(plans), active_draft_of(plans)) if one is not None]


def current_plan_of(plans: Sequence[PrWorkPlan]) -> PrWorkPlan | None:
    """The plan a manager acts on, out of every version for one employee-month.

    **Approved first, then draft, then nothing.** The rule is short because M2's
    schema already did the hard part: partial unique indexes allow at most one
    ``APPROVED`` and at most one ``DRAFT`` per ``(user, period)``, so neither
    branch has to break a tie and no "latest" heuristic is involved.

    ``SUPERSEDED`` and ``DISCARDED`` are never current however recent they are.
    A superseded version decided things that still have to stay explainable, and
    a discarded one decided nothing; presenting either as the plan in force
    would be showing a manager numbers that no longer apply.

    A module-level function rather than a method because the summary and the
    detail screen must not be able to disagree about which version is current.
    """
    for status in (PrWorkPlanStatus.APPROVED, PrWorkPlanStatus.DRAFT):
        found = next((one for one in plans if one.status is status), None)
        if found is not None:
            return found
    return None


def _quota_payload(
    quota: PrWorkQuota,
    *,
    plan: PrWorkPlan,
    period: PrReportingPeriod,
    work_type: PrWorkType,
) -> dict[str, object]:
    """One quota, as an audit payload.

    Carries the **codes** as well as the ids, because somebody reading an audit
    row a year later has the code in front of them and not the UUID.
    """
    return {
        "plan_id": str(plan.id),
        "plan_version_no": plan.version_no,
        "period_code": period.code,
        "user_id": str(plan.user_id),
        "work_type_id": str(quota.work_type_id),
        "work_type_code": work_type.code,
        "basis": quota.basis.value,
        "target_value": str(quota.target_value),
        "eligibility_cap": str(quota.eligibility_cap),
        "unit": quota.unit.value if quota.unit else None,
    }


def _require_not_subject(actor: Actor, plan: PrWorkPlan, *, act: str) -> None:
    """Refuse an act the plan's own subject may never perform on it."""
    if actor.user_id is not None and plan.user_id == actor.user_id:
        raise PrPermissionDeniedError(
            f"You may not {act} your own KPI plan; it needs an independent approver",
            details={
                "reason": "self_approval_forbidden",
                "plan_id": str(plan.id),
                "version_no": plan.version_no,
            },
        )


def _append_once[T](found: list[T], item: T) -> None:
    if item not in found:
        found.append(item)


def _require_user_id(actor: Actor) -> uuid.UUID:
    if actor.user_id is None:
        raise PrPermissionDeniedError(
            "This actor has no user row, so it cannot configure a KPI plan",
            details={"reason": "actor_has_no_user_row"},
        )
    return actor.user_id


def _optional_text(value: str | None, field: str, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) > limit:
        raise PrValidationError(
            f"{field} is longer than {limit} characters",
            details={"field": field, "reason": "too_long", "maximum": limit},
        )
    return trimmed


__all__: list[str] = [
    "MAX_PLAN_NOTE",
    "MAX_QUOTAS_PER_PLAN",
    "EmployeePlanSummary",
    "PlanDetail",
    "PlanPage",
    "PrWorkPlanService",
    "active_draft_of",
    "current_plan_of",
]
