"""From eligible work to a performance index nobody has to take on trust. M6.

The service that assembles the chain, and the one place the whole milestone's
argument is visible in order:

``COUNTED`` (M1) → **eligible** (M2) → standard minutes (a versioned rate) →
workload score → weighted with three monthly manager judgements → quality gate →
final index → performance band.

**And it stops there.** M6 scores and reports; it allocates no money. The
department head decides performance pay separately, using this report as
evidence, and nothing here turns an index into an amount.

Every step is stored with the version of whatever decided it, because the
question this module exists to answer is not *"what was my score"* - the database
could always answer that - but *"why"*, asked in December about September, after
the rates have changed.

What it never does
-------------------

It writes nothing belonging to M1, M2 or M3. ``count_status`` is M1's word,
quota allocations are M2's rows and content mappings are M3's: this module reads
all three and owns none of them. It also computes no manager judgement: quality,
timeliness and contribution arrive from
:class:`~meobot.application.pr_performance_review_service.PrPerformanceReviewService`
or the month is not ready.

Preview and finalisation are the same arithmetic
--------------------------------------------------

:meth:`PrPerformanceService.calculate` runs on an open period as often as anybody
asks and converges; :meth:`finalize` runs the identical calculation, refuses if
anything is unresolved, and stamps the row. There is deliberately no separate
"final" code path - a preview that could differ from the finalised figure would
make every preview a guess.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_query import day_bounds
from meobot.application.pr_performance_config_service import (
    PrPerformancePolicyService,
    PrWorkScoringRuleService,
)
from meobot.application.pr_performance_review_service import PrPerformanceReviewService
from meobot.application.pr_performance_target_service import (
    PrPerformanceTargetService,
    TargetResolution,
)
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_quota_service import counted_in_period, measure_counted
from meobot.core.time import utcnow
from meobot.db.models.pr_performance import (
    PrPerformancePolicy,
    PrPerformanceResult,
    PrPerformanceReview,
    PrPerformanceTargetOverride,
    PrWorkScoreAllocation,
    PrWorkScoringRule,
)
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota, PrWorkQuotaAllocation
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrValidationError,
)
from meobot.domain.pr.performance import (
    BLOCKING_STATUSES,
    DEFAULT_DAILY_TARGET_MINUTES,
    INDEX_QUANTUM,
    MINUTES_QUANTUM,
    PrContributionScoreStatus,
    PrPerformanceCalculationStatus,
    PrWorkScoringMode,
    final_performance_index,
    performance_band,
    quality_gate_cap,
    quantize,
    raw_performance_index,
    workload_score,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkCountStatus
from meobot.domain.pr.work_quota import PrWorkPlanStatus
from meobot.domain.pr.work_results import compare_to_target


@dataclass(frozen=True, slots=True)
class WorkTypeBreakdown:
    """One work type's contribution to the month, for the evidence panel."""

    work_type_id: uuid.UUID
    work_type_code: str
    work_type_name: str
    contributions: int
    #: M2's decomposition: how much of the counted amount an approved cap
    #: called eligible. Informational since the period-container patch.
    eligible_amount: Decimal
    #: **The amount priced.** The whole counted amount, whatever the cap said.
    counted_amount: Decimal
    standard_minutes: Decimal
    status: PrContributionScoreStatus
    #: What the KPI plan asked for, when it asked. Comparison only.
    target_value: Decimal | None = None
    completion_percent: Decimal | None = None
    over_target_amount: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class TimelinessEvidence:
    """System facts about deadlines. **Evidence, never a score.**

    Shown beside the timeliness question so a manager judges against something
    rather than from memory. It is deliberately not summarised into a number:
    the moment the system offers "83% on time" the manager is agreeing or
    disagreeing with an arithmetic answer instead of making their own, and the
    reason the cut was late is exactly the thing the system cannot see.
    """

    work_items: int = 0
    with_due_at: int = 0
    on_time: int = 0
    overdue: int = 0
    overdue_standard_minutes: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    """One person's month, fully explained, without anything being written.

    What an API returns and what the result row stores are built from this same
    object, so a preview and a finalised figure cannot disagree.
    """

    user_id: uuid.UUID
    period: PrReportingPeriod
    policy: PrPerformancePolicy | None
    target: TargetResolution
    eligible_standard_minutes: Decimal
    workload: Decimal | None
    review: PrPerformanceReview | None
    quality: Decimal | None
    timeliness: Decimal | None
    business_contribution: Decimal | None
    raw_index: Decimal | None
    gate_cap: Decimal | None
    final_index: Decimal | None
    band: str | None
    status: PrPerformanceCalculationStatus
    diagnostics: dict[str, object] = field(default_factory=dict)
    breakdown: tuple[WorkTypeBreakdown, ...] = ()
    evidence: TimelinessEvidence = field(default_factory=TimelinessEvidence)
    counted_contributions: int = 0
    eligible_contributions: int = 0
    over_quota_contributions: int = 0
    #: **M6B.** Whether the stored result has been agreed. Read from the result
    #: row rather than inferred from ``status``: a finalised month keeps the
    #: components it was finalised with, and a screen that guessed from the
    #: live calculation would offer edit controls the server refuses.
    is_finalized: bool = False
    finalized_at: datetime | None = None
    finalized_by_user_id: uuid.UUID | None = None
    #: **M6B, Part AV.** The approved KPI plan's caps translated into standard
    #: minutes at the same rates the workload uses - so an owner can see that a
    #: plan is worth 69% of the month's capacity *before* the month is over.
    #: ``None`` when there is no approved plan. **Diagnostic only**: nothing here
    #: writes a quota, and M2 remains the only thing that decides one.
    planned_standard_minutes: Decimal | None = None

    @property
    def ready(self) -> bool:
        return self.status not in BLOCKING_STATUSES


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    """One month's counts, for the head's report header.

    Averages are ``None`` rather than zero when nothing qualifies: a department
    where nobody has been reviewed has *no* average, and printing 0,00 would
    report it as having performed badly.
    """

    period: PrReportingPeriod
    employees: int
    reviewed: int
    pending_review: int
    finalized: int
    average_final_index: Decimal | None
    average_workload: Decimal | None
    average_quality: Decimal | None
    average_timeliness: Decimal | None
    average_business_contribution: Decimal | None
    bands: dict[str, int]


class PrPerformanceService:
    """Projects workload, computes the index, and finalises the month."""

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        rules: PrWorkScoringRuleService,
        policies: PrPerformancePolicyService,
        reviews: PrPerformanceReviewService,
        targets: PrPerformanceTargetService,
        *,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._rules = rules
        self._policies = policies
        self._reviews = reviews
        self._targets = targets
        # The business timezone, for the same reason ``PrWorkPeriodService``
        # carries one: a reporting month is a range of Vietnamese calendar days
        # and the columns are UTC instants.
        self._timezone = timezone or ZoneInfo("UTC")

    # =================================================================
    # Reading
    # =================================================================
    async def snapshot(
        self, *, actor: Actor, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PerformanceSnapshot:
        """Compute one person's month without writing anything.

        **Your own, or anybody's if you may review or see the whole department.**
        An employee reading the judgement made about them is the point of making
        it; reading a colleague's is a different act.
        """
        if actor.user_id != user_id and not await self._capabilities.allows(
            actor, PrCapability.PR_PERFORMANCE_REVIEW
        ):
            # Either capability admits the read: a reviewer needs it to do the
            # review, and PR_WORK_VIEW_ALL is already "may see the whole
            # department's work". Neither is implied by PR_WORK_MANAGE.
            await self._capabilities.require(actor, PrCapability.PR_WORK_VIEW_ALL)
        period = await self._require_period(period_id)
        return await self._compute(user_id=user_id, period=period)

    async def _compute(
        self, *, user_id: uuid.UUID, period: PrReportingPeriod
    ) -> PerformanceSnapshot:
        policy = await self._policies.policy_for(on=period.date_end)
        daily = policy.daily_target_minutes if policy else DEFAULT_DAILY_TARGET_MINUTES
        target = await self._targets.resolve(
            user_id=user_id, period=period, daily_target_minutes=daily
        )
        scored = await self._project(user_id=user_id, period=period)
        eligible_minutes = quantize(
            sum((row.standard_minutes for row in scored.breakdown), Decimal("0")),
            MINUTES_QUANTUM,
        )

        diagnostics: dict[str, object] = {}
        unscored = [
            row
            for row in scored.breakdown
            if row.status is PrContributionScoreStatus.NO_SCORING_RULE
        ]
        if unscored:
            diagnostics["missing_scoring_rules"] = [row.work_type_code for row in unscored]

        review = await self._reviews.review_for(user_id=user_id, period_id=period.id)
        missing = [
            name
            for name in ("quality", "timeliness", "business_contribution")
            if review is None or getattr(review, f"{name}_score") is None
        ]
        if missing:
            diagnostics["missing_review_dimensions"] = missing
        if not target.resolved:
            diagnostics["target"] = target.unresolved_reason

        # **Order matters.** An unresolved target is reported before a missing
        # rule, and both before a pending review, because that is the order the
        # owner has to fix them in: no target means no workload score at all.
        if not target.resolved:
            status = PrPerformanceCalculationStatus.TARGET_UNRESOLVED
        elif unscored:
            status = PrPerformanceCalculationStatus.NO_SCORING_RULE
        elif missing:
            status = PrPerformanceCalculationStatus.PERFORMANCE_REVIEW_PENDING
        else:
            status = PrPerformanceCalculationStatus.READY

        workload = raw = gate = final = None
        band = None
        if target.resolved:
            assert target.target_standard_minutes is not None
            cap = policy.workload_score_cap if policy else Decimal("120")
            workload = workload_score(
                eligible_standard_minutes=eligible_minutes,
                target_standard_minutes=target.target_standard_minutes,
                cap=cap,
            )
        quality = review.quality_score if review else None
        timeliness = review.timeliness_score if review else None
        contribution = review.business_contribution_score if review else None

        if workload is not None and not missing and policy is not None:
            assert quality is not None and timeliness is not None and contribution is not None
            raw = raw_performance_index(
                workload=workload,
                quality=quality,
                timeliness=timeliness,
                business_contribution=contribution,
                workload_weight=policy.workload_weight,
                quality_weight=policy.quality_weight,
                timeliness_weight=policy.timeliness_weight,
                business_contribution_weight=policy.business_contribution_weight,
            )
            gate = quality_gate_cap(quality, _gate_of(policy))
            final = final_performance_index(raw, gate)
            band = performance_band(final, _bands_of(policy))
        if policy is None:
            diagnostics["policy"] = "no_approved_performance_policy"

        stored = (
            (
                await self._session.execute(
                    select(PrPerformanceResult).where(
                        PrPerformanceResult.user_id == user_id,
                        PrPerformanceResult.reporting_period_id == period.id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

        return PerformanceSnapshot(
            is_finalized=stored is not None and stored.finalized_at is not None,
            finalized_at=stored.finalized_at if stored else None,
            finalized_by_user_id=stored.finalized_by_user_id if stored else None,
            planned_standard_minutes=await self._planned_minutes(user_id=user_id, period=period),
            user_id=user_id,
            period=period,
            policy=policy,
            target=target,
            eligible_standard_minutes=eligible_minutes,
            workload=workload,
            review=review,
            quality=quality,
            timeliness=timeliness,
            business_contribution=contribution,
            raw_index=raw,
            gate_cap=gate,
            final_index=final,
            band=band,
            status=status,
            diagnostics=diagnostics,
            breakdown=scored.breakdown,
            evidence=scored.evidence,
            counted_contributions=scored.counted,
            eligible_contributions=scored.eligible,
            over_quota_contributions=scored.over_quota,
        )

    # =================================================================
    # Projection
    # =================================================================
    @dataclass(frozen=True, slots=True)
    class _Projection:
        breakdown: tuple[WorkTypeBreakdown, ...]
        evidence: TimelinessEvidence
        counted: int
        eligible: int
        over_quota: int

    async def _project(
        self, *, user_id: uuid.UUID, period: PrReportingPeriod
    ) -> PrPerformanceService._Projection:
        """Turn this month's **counted** amounts into standard minutes.

        **Driven by M1's ``COUNTED`` contributions, priced on the whole counted
        amount.** Until the period-container patch this read M2's allocations
        and priced ``eligible_amount``, which made a KPI cap a ceiling on what
        work was worth and made work with no approved plan worth nothing. The
        rule is now the one the business states: *WORK is actual work
        performed; KPI is a target only.* So:

        * every counted contribution in the month enters the sum - with an
          allocation (inside the cap, over it, or ``NO_QUOTA``) or with none at
          all (no approved plan yet);
        * the amount priced is the contribution's **counted amount**:
          ``allocation.basis_amount`` when M2 measured it, otherwise the same
          :func:`~meobot.domain.pr.work_quota.measure_contribution` M2 uses,
          applied here so there is still one measuring function;
        * ``eligible_amount`` is carried beside it as M2's decomposition, and
          the counts of eligible / over-quota contributions are still reported
          for the KPI screen. Nothing downstream is limited by them.

        "In the month" is :func:`~meobot.application.pr_work_quota_service.counted_in_period`
        - a period container belongs to the month it is, a one-off job to the
        month containing its ``counted_at``.
        """
        lower, upper = self._period_bounds(period)
        rows = (
            await self._session.execute(
                select(PrWorkContribution, PrWorkItem, PrWorkType, PrWorkQuotaAllocation)
                .join(PrWorkItem, PrWorkItem.id == PrWorkContribution.work_item_id)
                .join(PrWorkType, PrWorkType.id == PrWorkItem.work_type_id)
                .outerjoin(
                    PrWorkQuotaAllocation,
                    PrWorkQuotaAllocation.work_contribution_id == PrWorkContribution.id,
                )
                .where(
                    PrWorkContribution.user_id == user_id,
                    PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
                    counted_in_period(period, lower, upper),
                )
                .order_by(
                    PrWorkContribution.counted_at.asc(),
                    PrWorkContribution.created_at.asc(),
                    PrWorkContribution.id.asc(),
                )
            )
        ).all()
        quotas = await self._approved_quotas(user_id=user_id, period=period)

        buckets: dict[uuid.UUID, dict[str, object]] = {}
        # **One rate lookup per (work type, day), not per contribution.**
        #
        # A month is a handful of work types and one or two effective dates, so
        # the same approved rule was being re-fetched for every row - linear, but
        # linear in the wrong thing. Cached for the duration of one projection
        # rather than on the service, because a rule approved *while* a
        # recalculation runs must not be half-applied: every contribution in one
        # run resolves against one picture of the configuration, which is the
        # same guarantee M3's resolver makes.
        rules: dict[tuple[uuid.UUID, date], PrWorkScoringRule | None] = {}
        counted = eligible = over_quota = 0
        for contribution, item, work_type, allocation in rows:
            counted += 1
            eligible_amount = Decimal("0")
            if allocation is not None:
                eligible_amount = allocation.eligible_amount
                if allocation.eligible_amount > 0:
                    eligible += 1
                if allocation.over_quota_amount > 0:
                    over_quota += 1
            counted_amount = counted_amount_of(contribution, item, work_type, allocation=allocation)

            # **The rate in force when the work was counted**, never today's.
            # A counted contribution always carries its instant - M1 pairs them
            # in one CHECK - so the fallback is unreachable in practice and
            # present so that a malformed row is priced at the month's end
            # rather than taking a whole department's recalculation down.
            counted_at = contribution.counted_at
            on = counted_at.date() if counted_at is not None else period.date_end
            key = (work_type.id, on)
            if key not in rules:
                rules[key] = await self._rules.rule_for(work_type.id, on=on)
            rule = rules[key]
            minutes, status, per_unit = price_amount(counted_amount, rule)

            bucket = buckets.setdefault(
                work_type.id,
                {
                    "code": work_type.code,
                    "name": work_type.name,
                    "count": 0,
                    "amount": Decimal("0"),
                    "counted_amount": Decimal("0"),
                    "minutes": Decimal("0"),
                    "status": status,
                    "rule": rule,
                    "per_unit": per_unit,
                },
            )
            bucket["count"] = int(bucket["count"]) + 1  # type: ignore[call-overload]
            bucket["amount"] = Decimal(str(bucket["amount"])) + eligible_amount
            bucket["counted_amount"] = Decimal(str(bucket["counted_amount"])) + counted_amount
            bucket["minutes"] = Decimal(str(bucket["minutes"])) + minutes
            # A work type whose rule is missing for *any* of its contributions is
            # reported as missing: partial pricing would understate the month and
            # look like a low workload rather than a configuration gap.
            if status is PrContributionScoreStatus.NO_SCORING_RULE:
                bucket["status"] = status

            await self._record_allocation(
                contribution=contribution,
                period=period,
                work_type=work_type,
                allocation=allocation,
                rule=rule,
                eligible_amount=eligible_amount,
                counted_amount=counted_amount,
                minutes=minutes,
                status=status,
                per_unit=per_unit,
            )

        breakdown = []
        for type_id, bucket in buckets.items():
            quota = quotas.get(type_id)
            total = quantize(Decimal(str(bucket["counted_amount"])), Decimal("0.01"))
            comparison = compare_to_target(total, quota.target_value if quota is not None else None)
            breakdown.append(
                WorkTypeBreakdown(
                    work_type_id=type_id,
                    work_type_code=str(bucket["code"]),
                    work_type_name=str(bucket["name"]),
                    contributions=int(bucket["count"]),  # type: ignore[call-overload]
                    eligible_amount=quantize(Decimal(str(bucket["amount"])), Decimal("0.01")),
                    counted_amount=total,
                    standard_minutes=quantize(Decimal(str(bucket["minutes"])), MINUTES_QUANTUM),
                    status=bucket["status"],  # type: ignore[arg-type]
                    target_value=comparison.target,
                    completion_percent=comparison.completion_percent,
                    over_target_amount=comparison.over_target,
                )
            )
        return PrPerformanceService._Projection(
            breakdown=tuple(breakdown),
            evidence=await self._evidence(user_id=user_id, period=period),
            counted=counted,
            eligible=eligible,
            over_quota=over_quota,
        )

    def _period_bounds(self, period: PrReportingPeriod) -> tuple[datetime, datetime]:
        """The period's UTC instant range, through the one calendar helper."""
        lower, upper = day_bounds(period.date_start, period.date_end, tz=self._timezone)
        assert lower is not None and upper is not None
        return lower, upper

    async def _approved_quotas(
        self, *, user_id: uuid.UUID, period: PrReportingPeriod
    ) -> dict[uuid.UUID, PrWorkQuota]:
        """The approved plan's targets by work type, for the comparison column."""
        plan = (
            (
                await self._session.execute(
                    select(PrWorkPlan).where(
                        PrWorkPlan.user_id == user_id,
                        PrWorkPlan.period_id == period.id,
                        PrWorkPlan.status == PrWorkPlanStatus.APPROVED,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if plan is None:
            return {}
        rows = (
            (await self._session.execute(select(PrWorkQuota).where(PrWorkQuota.plan_id == plan.id)))
            .scalars()
            .all()
        )
        return {row.work_type_id: row for row in rows}

    async def _record_allocation(
        self,
        *,
        contribution: PrWorkContribution,
        period: PrReportingPeriod,
        work_type: PrWorkType,
        allocation: PrWorkQuotaAllocation | None,
        rule: PrWorkScoringRule | None,
        eligible_amount: Decimal,
        counted_amount: Decimal,
        minutes: Decimal,
        status: PrContributionScoreStatus,
        per_unit: Decimal | None,
    ) -> None:
        """Store the provenance row, converging rather than accumulating."""
        existing = (
            (
                await self._session.execute(
                    select(PrWorkScoreAllocation).where(
                        PrWorkScoreAllocation.work_contribution_id == contribution.id
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        row = existing or PrWorkScoreAllocation(
            work_contribution_id=contribution.id,
            user_id=contribution.user_id,
            reporting_period_id=period.id,
            work_type_id=work_type.id,
        )
        row.reporting_period_id = period.id
        row.work_type_id = work_type.id
        row.scoring_rule_id = rule.id if rule is not None else None
        row.eligible_amount = eligible_amount
        row.counted_amount = counted_amount
        row.standard_minutes_per_unit = per_unit
        row.eligible_standard_minutes = minutes
        row.status = status
        row.evaluated_at = utcnow()
        if existing is None:
            self._session.add(row)
        await self._session.flush()

    async def _evidence(
        self, *, user_id: uuid.UUID, period: PrReportingPeriod
    ) -> TimelinessEvidence:
        """Deadline facts for the reviewer. Counted, never judged."""
        lower, upper = self._period_bounds(period)
        rows = (
            (
                await self._session.execute(
                    select(PrWorkItem)
                    .join(PrWorkContribution, PrWorkContribution.work_item_id == PrWorkItem.id)
                    .where(
                        PrWorkContribution.user_id == user_id,
                        PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
                        counted_in_period(period, lower, upper),
                    )
                )
            )
            .scalars()
            .all()
        )
        with_due = [item for item in rows if item.due_at is not None]
        overdue = [
            item
            for item in with_due
            if item.completed_at is not None
            and item.due_at is not None
            and item.completed_at > item.due_at
        ]
        return TimelinessEvidence(
            work_items=len(rows),
            with_due_at=len(with_due),
            on_time=len(with_due) - len(overdue),
            overdue=len(overdue),
        )

    # =================================================================
    # Writing
    # =================================================================
    async def calculate(
        self, *, actor: Actor, request_id: uuid.UUID, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PerformanceSnapshot:
        """Recompute and store the month's figure. ``PR_PERFORMANCE_REVIEW``.

        Refused on a closed or locked period: agreed numbers do not move because
        somebody pressed refresh.
        """
        await self._capabilities.require(actor, PrCapability.PR_PERFORMANCE_REVIEW)
        period = await self._require_period(period_id)
        if period.status is not PrPeriodStatus.OPEN:
            raise PrConflictError(
                "Kỳ báo cáo đã đóng nên không tính lại được.",
                details={"reason": "period_not_open", "status": period.status.value},
            )
        snapshot = await self._compute(user_id=user_id, period=period)
        await self._store(actor=actor, request_id=request_id, snapshot=snapshot)
        return snapshot

    async def refresh_stored(
        self, *, actor: Actor, request_id: uuid.UUID, user_id: uuid.UUID, period: PrReportingPeriod
    ) -> bool:
        """Recompute a **stored, unfinalised** figure after the actual moved. Internal.

        Reachable from no route: the work maintenance service calls it after a
        rebuild or an administrative exclusion changed what was counted, and it
        holds ``PR_WORK_CONFIGURE`` rather than the review capability - the
        figure is being kept true, not reviewed. Nothing is computed for a
        person with no stored row (a snapshot is computed on read), a finalised
        row is left exactly as it was, and a period that is not open computes
        nothing either. Returns whether a row was rewritten.
        """
        if period.status is not PrPeriodStatus.OPEN:
            return False
        existing = (
            (
                await self._session.execute(
                    select(PrPerformanceResult).where(
                        PrPerformanceResult.user_id == user_id,
                        PrPerformanceResult.reporting_period_id == period.id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if existing is None or existing.finalized_at is not None:
            return False
        snapshot = await self._compute(user_id=user_id, period=period)
        await self._store(actor=actor, request_id=request_id, snapshot=snapshot)
        return True

    async def finalize(
        self, *, actor: Actor, request_id: uuid.UUID, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PerformanceSnapshot:
        """Agree the month. ``PR_PERFORMANCE_REVIEW``. **No force flag.**

        The identical calculation, refusing rather than proceeding when anything
        is unresolved - and the diagnostics travel with the refusal, so an owner
        is told which employee needs which thing rather than that "finalisation
        failed".
        """
        await self._capabilities.require(actor, PrCapability.PR_PERFORMANCE_REVIEW)
        period = await self._require_period(period_id)
        if period.status is not PrPeriodStatus.OPEN:
            raise PrConflictError(
                "Chỉ chốt được hiệu suất khi kỳ báo cáo còn mở.",
                details={"reason": "period_not_open", "status": period.status.value},
            )
        snapshot = await self._compute(user_id=user_id, period=period)
        if snapshot.status in BLOCKING_STATUSES:
            raise PrValidationError(
                "Chưa đủ điều kiện để chốt hiệu suất tháng.",
                details={
                    "reason": "performance_not_ready",
                    "status": snapshot.status.value,
                    "diagnostics": snapshot.diagnostics,
                },
            )
        if snapshot.policy is None:
            raise PrValidationError(
                "Chưa có chính sách hiệu suất được duyệt.",
                details={"reason": "no_approved_performance_policy"},
            )
        result = await self._store(actor=actor, request_id=request_id, snapshot=snapshot)
        if result.finalized_at is not None:
            raise PrConflictError(
                "Hiệu suất tháng này đã được chốt.",
                details={"reason": "already_finalized"},
            )
        result.finalized_at = utcnow()
        result.finalized_by_user_id = actor.user_id
        result.calculation_status = PrPerformanceCalculationStatus.FINALIZED
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PERFORMANCE_FINALIZED,
            entity_type="pr_performance_result",
            entity_id=result.id,
            after={
                "user_id": str(user_id),
                "period_code": period.code,
                "final_performance_index": str(snapshot.final_index),
                "performance_band": snapshot.band,
                "policy_version": snapshot.policy.version_no,
            },
        )
        return snapshot

    async def _store(
        self, *, actor: Actor, request_id: uuid.UUID, snapshot: PerformanceSnapshot
    ) -> PrPerformanceResult:
        """Materialise the figure with its provenance. Refuses to touch a finalised row."""
        existing = (
            (
                await self._session.execute(
                    select(PrPerformanceResult).where(
                        PrPerformanceResult.user_id == snapshot.user_id,
                        PrPerformanceResult.reporting_period_id == snapshot.period.id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if existing is not None and existing.finalized_at is not None:
            return existing

        row = existing or PrPerformanceResult(
            user_id=snapshot.user_id,
            reporting_period_id=snapshot.period.id,
            eligible_standard_minutes=Decimal("0"),
            calculation_status=snapshot.status,
            calculated_at=utcnow(),
        )
        if existing is not None:
            locked = await lock_row(self._session, PrPerformanceResult, existing.id)
            if locked is not None:
                row = locked
        row.policy_id = snapshot.policy.id if snapshot.policy else None
        row.performance_review_id = snapshot.review.id if snapshot.review else None
        row.target_standard_minutes = snapshot.target.target_standard_minutes
        row.eligible_standard_minutes = snapshot.eligible_standard_minutes
        row.workload_score = snapshot.workload
        row.quality_score = snapshot.quality
        row.timeliness_score = snapshot.timeliness
        row.business_contribution_score = snapshot.business_contribution
        row.raw_performance_index = snapshot.raw_index
        row.quality_gate_cap = snapshot.gate_cap
        row.final_performance_index = snapshot.final_index
        row.calculation_status = snapshot.status
        row.diagnostics = snapshot.diagnostics or None
        row.calculated_at = utcnow()
        if existing is None:
            self._session.add(row)
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PERFORMANCE_RECALCULATED,
            entity_type="pr_performance_result",
            entity_id=row.id,
            after={
                "user_id": str(snapshot.user_id),
                "period_code": snapshot.period.code,
                "status": snapshot.status.value,
                "eligible_standard_minutes": str(snapshot.eligible_standard_minutes),
                "final_performance_index": (
                    str(snapshot.final_index) if snapshot.final_index is not None else None
                ),
            },
        )
        return row

    async def period_summary(self, *, actor: Actor, period_id: uuid.UUID) -> PerformanceSummary:
        """The month in one row of counts. ``PR_PERFORMANCE_REVIEW``.

        **Counts and averages, deliberately - not analytics.** How many people
        are still waiting to be reviewed, how many are agreed, and how the band
        distribution looks is what a head needs to run the month. Trends,
        rankings and forecasting are M7's, and building them here would turn a
        worklist into a dashboard nobody acts on.

        The average is over the months that **have** a final index. Treating an
        unreviewed month as zero would drag the department's average down to
        report that nobody had got round to reviewing somebody.
        """
        await self._capabilities.require(actor, PrCapability.PR_PERFORMANCE_REVIEW)
        period = await self._require_period(period_id)
        people = (
            (
                await self._session.execute(
                    select(User).where(User.active.is_(True)).order_by(User.full_name)
                )
            )
            .scalars()
            .all()
        )

        snapshots = [await self._compute(user_id=person.id, period=period) for person in people]
        scored = [one for one in snapshots if one.final_index is not None]
        bands: dict[str, int] = {}
        for one in scored:
            if one.band:
                bands[one.band] = bands.get(one.band, 0) + 1

        def mean(values: list[Decimal]) -> Decimal | None:
            if not values:
                return None
            return quantize(sum(values, Decimal("0")) / Decimal(len(values)), INDEX_QUANTUM)

        return PerformanceSummary(
            period=period,
            employees=len(snapshots),
            reviewed=len([one for one in snapshots if one.review is not None]),
            pending_review=len(
                [
                    one
                    for one in snapshots
                    if one.status is PrPerformanceCalculationStatus.PERFORMANCE_REVIEW_PENDING
                ]
            ),
            finalized=len([one for one in snapshots if one.is_finalized]),
            average_final_index=mean([one.final_index for one in scored if one.final_index]),
            average_workload=mean([one.workload for one in snapshots if one.workload]),
            average_quality=mean([one.quality for one in snapshots if one.quality]),
            average_timeliness=mean([one.timeliness for one in snapshots if one.timeliness]),
            average_business_contribution=mean(
                [one.business_contribution for one in snapshots if one.business_contribution]
            ),
            bands=bands,
        )

    async def set_target_override(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        period_id: uuid.UUID,
        monthly_target_override: Decimal,
        override_reason: str,
    ) -> PrPerformanceTargetOverride:
        """Set a month's workload target by hand. ``PR_WORK_CONFIGURE``.

        **The reason is mandatory**, refused here as well as by a ``NOT NULL``
        column: replacing a computed target is exactly the action somebody will
        be asked to justify, and "because it looked wrong" written at the time is
        worth more than a reconstruction six months later.

        The only number a person may put into M6 that the calendar did not
        produce. Everything else on a performance result is computed.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        period = await self._require_period(period_id)
        if period.status is not PrPeriodStatus.OPEN:
            raise PrConflictError(
                "Kỳ báo cáo đã đóng.",
                details={"reason": "period_not_open", "status": period.status.value},
            )
        reason = (override_reason or "").strip()
        if not reason:
            raise PrValidationError(
                "Ghi đè mục tiêu bắt buộc phải có lý do.",
                details={"field": "override_reason", "reason": "override_reason_required"},
            )
        if monthly_target_override <= 0:
            raise PrValidationError(
                "Mục tiêu phải lớn hơn 0.",
                details={"field": "monthly_target_override", "reason": "target_must_be_positive"},
            )

        row = await self._override(user_id=user_id, period_id=period_id)
        before = str(row.monthly_target_override) if row else None
        if row is None:
            row = PrPerformanceTargetOverride(
                user_id=user_id,
                reporting_period_id=period_id,
                monthly_target_override=monthly_target_override,
                override_reason=reason,
            )
            self._session.add(row)
        else:
            row.monthly_target_override = monthly_target_override
            row.override_reason = reason
        row.set_by_user_id = actor.user_id
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PERFORMANCE_TARGET_OVERRIDDEN,
            entity_type="pr_performance_target_override",
            entity_id=row.id,
            before={"monthly_target_override": before},
            after={
                "monthly_target_override": str(monthly_target_override),
                "override_reason": reason,
                "user_id": str(user_id),
                "period_code": period.code,
            },
        )
        return row

    # -----------------------------------------------------------------
    async def _planned_minutes(
        self, *, user_id: uuid.UUID, period: PrReportingPeriod
    ) -> Decimal | None:
        """The approved KPI plan, priced at the same rates the workload uses.

        **A diagnostic, not a decision.** It answers *"is this plan worth a
        month?"* before the month is spent - a plan totalling 5 200 standard
        minutes against a 7 500 target is a conversation somebody should have in
        week one rather than discover at month end.

        Reads M2's approved quotas and M6's rates and **writes neither**. The
        cap rather than the target is priced, because the cap is the most the
        plan could ever contribute; using the target would understate a plan
        that deliberately allows more.

        ``None`` when there is no approved plan, which is a different statement
        from zero and is rendered as one.
        """
        plan = (
            (
                await self._session.execute(
                    select(PrWorkPlan).where(
                        PrWorkPlan.user_id == user_id,
                        PrWorkPlan.period_id == period.id,
                        PrWorkPlan.status == PrWorkPlanStatus.APPROVED,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if plan is None:
            return None

        quotas = (
            (await self._session.execute(select(PrWorkQuota).where(PrWorkQuota.plan_id == plan.id)))
            .scalars()
            .all()
        )
        total = Decimal("0")
        rules: dict[uuid.UUID, PrWorkScoringRule | None] = {}
        for quota in quotas:
            if quota.work_type_id not in rules:
                rules[quota.work_type_id] = await self._rules.rule_for(
                    quota.work_type_id, on=period.date_end
                )
            rule = rules[quota.work_type_id]
            if rule is None or rule.mode is not PrWorkScoringMode.STANDARD_MINUTES:
                continue
            total += quota.eligibility_cap * (rule.standard_minutes_per_unit or Decimal("0"))
        return quantize(total, MINUTES_QUANTUM)

    async def _override(
        self, *, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PrPerformanceTargetOverride | None:
        return (
            (
                await self._session.execute(
                    select(PrPerformanceTargetOverride).where(
                        PrPerformanceTargetOverride.user_id == user_id,
                        PrPerformanceTargetOverride.reporting_period_id == period_id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

    async def _require_period(self, period_id: uuid.UUID) -> PrReportingPeriod:
        period = await self._session.get(PrReportingPeriod, period_id)
        if period is None:
            raise PrNotFoundError(
                "Không tìm thấy kỳ báo cáo.",
                details={"field": "period_id", "reason": "period_not_found"},
            )
        return period


def counted_amount_of(
    contribution: PrWorkContribution,
    item: PrWorkItem,
    work_type: PrWorkType,
    *,
    allocation: PrWorkQuotaAllocation | None,
) -> Decimal:
    """What one counted contribution amounts to, **before** any cap.

    **Always the live measurement of the counted work** - the contribution's
    share of ``item.quantity`` on the quota basis, through the one function
    M2 measures with. Never read back from ``PrWorkQuotaAllocation``: that
    row is M2's *classification* of the amount (eligible / over quota /
    no quota) and may be absent or stale - the savepointed hand-off swallowed
    an error, the period was not open, nobody has reconciled yet - and an
    actual that depended on it would read as zero for work that is counted.
    The allocation supplies only the basis it measured on, so the split and
    the total are measured the same way. ``UNMEASURABLE`` (a quantity-measured
    job with no quantity) prices as zero; it is a data gap M2 names on the KPI
    screen.
    """
    basis = allocation.basis if allocation is not None else work_type.default_quota_basis
    measurement = measure_counted(contribution, item, basis=basis)
    return measurement.amount if measurement.amount is not None else Decimal("0")


def price_amount(
    amount: Decimal, rule: PrWorkScoringRule | None
) -> tuple[Decimal, PrContributionScoreStatus, Decimal | None]:
    """**The one place a quantity becomes standard minutes.** Amount times rate.

    Public since the period-container patch, because a work card prices a
    stream's actual through the same function the month is priced through.
    Nothing caps ``amount`` here: a KPI target is compared elsewhere and is not
    a ceiling on what work is worth.

    The three outcomes are three different facts and none of them is "zero
    minutes": *priced*, *nobody has configured a rate* (which blocks
    finalisation), and *somebody deliberately excluded this work type* (which
    does not).
    """
    if rule is None:
        return Decimal("0"), PrContributionScoreStatus.NO_SCORING_RULE, None
    if rule.mode is PrWorkScoringMode.EXCLUDED_FROM_PERFORMANCE:
        return Decimal("0"), PrContributionScoreStatus.EXCLUDED_FROM_PERFORMANCE, None
    per_unit = rule.standard_minutes_per_unit or Decimal("0")
    return (
        quantize(amount * per_unit, MINUTES_QUANTUM),
        PrContributionScoreStatus.SCORED,
        per_unit,
    )


#: The pre-patch name, kept for readers that imported it.
_price = price_amount


def _gate_of(policy: PrPerformancePolicy) -> tuple[tuple[Decimal, Decimal | None], ...]:
    return tuple(
        (Decimal(str(floor)), None if cap is None else Decimal(str(cap)))
        for floor, cap in policy.quality_gate
    )


def _bands_of(policy: PrPerformancePolicy) -> tuple[tuple[Decimal, str], ...]:
    return tuple((Decimal(str(floor)), str(name)) for floor, name in policy.performance_bands)


__all__: list[str] = [
    "PerformanceSnapshot",
    "PerformanceSummary",
    "PrPerformanceService",
    "TimelinessEvidence",
    "WorkTypeBreakdown",
]
