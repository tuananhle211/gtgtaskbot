"""**One formula for what a KPI plan asks for, in standard minutes.**

Every screen that prints a plan's workload - the employee's editor, the
employee's card, the manager's list, the review before *Duyệt và áp dụng*, the
draft preview - reads the result of :class:`PrPlanWorkloadCalculator`. Nothing
in a browser multiplies a target by a rate; the browser prints
``contribution_minutes`` and the ``rule_label`` next to it.

The arithmetic, stated once
---------------------------

For each quota:

* the **rule** is the approved scoring rule in force on the period's **last
  day** - :meth:`PrWorkScoringRuleService.rules_for` with ``on=period.date_end``,
  the instant M6 prices work done at the end of the month at, and the same
  choice the self-service preview made when it was introduced. Not today's
  rule: a plan for September priced in October reads September's rate;
* ``contribution_minutes = quantize(target_value x standard_minutes_per_unit)``
  when the rule is ``STANDARD_MINUTES``. The rate is **per one quota unit**:
  per work item for an ``ITEM_COUNT`` quota, per unit of the work type (a
  comment, a post, an account) for a ``QUANTITY`` quota - exactly what
  :func:`meobot.application.pr_performance_service._price` multiplies an
  eligible amount by. A rule meaning "120 comments take 120 minutes" is
  stored as ``1.0000`` per comment; the model holds no batch size and this
  module invents none;
* a rule in ``EXCLUDED_FROM_PERFORMANCE`` mode contributes **zero by
  decision** and is not a gap;
* **no rule** contributes nothing and is a gap: the quota is reported as
  unpriced, named, and the plan's percentage is withheld rather than printed
  as if the sum were complete.

For the plan:

* ``projected_minutes`` is the sum of the priced contributions;
* ``target_minutes`` is the person's month as
  :class:`~meobot.application.pr_performance_target_service.PrPerformanceTargetService`
  resolves it - eligible workdays x the approved policy's daily minutes, or an
  owner's override - and ``None`` when the calendar cannot answer;
* ``percent`` exists only when the target resolved **and** every quota is
  priced or excluded. Four priced quotas and one unpriced one is not "90%".

Rounding follows M6: minutes to ``0.01`` per quota, then summed; percent to
``0.1``. Both quanta are :mod:`meobot.domain.pr.performance`'s.

Batching
--------

:meth:`PrPlanWorkloadCalculator.for_plans` prices any number of plans for one
period in a fixed number of queries - quotas, work types, rules, policy, then
the target resolver's four - so a manager's list of twenty employees does not
become twenty calendars and eighty rule lookups. :meth:`for_plan` is the
one-plan form of the same call.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_performance_config_service import (
    PrPerformancePolicyService,
    PrWorkScoringRuleService,
)
from meobot.application.pr_performance_target_service import (
    PrPerformanceTargetService,
    TargetResolution,
)
from meobot.db.models.pr_performance import PrWorkScoringRule
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota
from meobot.domain.pr.performance import (
    DEFAULT_DAILY_TARGET_MINUTES,
    MINUTES_QUANTUM,
    WORKLOAD_QUANTUM,
    PrWorkScoringMode,
    quantize,
)
from meobot.domain.pr.work import PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_quota_labels import (
    quota_target_unit_label,
    quota_workload_status_label,
    work_quota_basis_label,
    workload_rule_label,
)


class QuotaWorkloadStatus(StrEnum):
    """What the calculator concluded about one quota.

    The same three outcomes M6 reports per contribution
    (:class:`~meobot.domain.pr.performance.PrContributionScoreStatus`), under
    the same names, so a quota "chưa cấu hình quy tắc workload" on the KPI
    card is the work type that will block finalisation on the performance
    screen.
    """

    PRICED = "PRICED"
    NO_SCORING_RULE = "NO_SCORING_RULE"
    EXCLUDED_FROM_PERFORMANCE = "EXCLUDED_FROM_PERFORMANCE"


@dataclass(frozen=True, slots=True)
class QuotaWorkload:
    """One quota: its target, the rule that priced it, and the product."""

    quota_id: uuid.UUID
    work_type_id: uuid.UUID
    work_type_code: str | None
    work_type_name: str | None
    #: How the quota measures - ``ITEM_COUNT`` or ``QUANTITY``.
    basis: PrWorkQuotaBasis
    target_value: Decimal
    #: The quota's unit for ``QUANTITY``; ``None`` for a count of items.
    unit: PrWorkUnit | None
    status: QuotaWorkloadStatus
    #: Minutes per one unit, from the rule. ``None`` when there is no rule or
    #: the rule excludes the type.
    standard_minutes_per_unit: Decimal | None = None
    rule_id: uuid.UUID | None = None
    rule_version_no: int | None = None
    rule_effective_from: date | None = None
    #: ``target_value x standard_minutes_per_unit``, quantized. ``0`` for an
    #: excluded type; ``None`` when no rule prices it.
    contribution_minutes: Decimal | None = None

    @property
    def is_priced(self) -> bool:
        return self.status is QuotaWorkloadStatus.PRICED

    @property
    def target_unit_label(self) -> str:
        return quota_target_unit_label(self.basis, self.unit)

    @property
    def measurement_mode_label(self) -> str:
        return work_quota_basis_label(self.basis)

    @property
    def rule_label(self) -> str | None:
        if self.standard_minutes_per_unit is None:
            return None
        return workload_rule_label(self.standard_minutes_per_unit, self.basis, self.unit)

    @property
    def status_label(self) -> str:
        return quota_workload_status_label(self.status.value)


@dataclass(frozen=True, slots=True)
class PlanWorkload:
    """**What a plan asks for, in the month's own currency.** Advisory.

    Nothing here is a score, a band or a threshold: the manager decides, and
    this shows them the arithmetic. See the module docstring for the formula.
    """

    projected_minutes: Decimal
    target_minutes: Decimal | None = None
    #: Present only when the target resolved **and** the plan is fully priced.
    percent: Decimal | None = None
    #: Work types no approved rule prices, in quota order. Kept for the screens
    #: that only ever wanted a count.
    unscored_work_type_ids: tuple[uuid.UUID, ...] = ()
    quotas: tuple[QuotaWorkload, ...] = ()
    #: The day whose rules priced this plan - the period's last day.
    rules_effective_on: date | None = None
    target: TargetResolution | None = None

    @property
    def priced_quota_count(self) -> int:
        return sum(1 for row in self.quotas if row.status is QuotaWorkloadStatus.PRICED)

    @property
    def unpriced_quota_count(self) -> int:
        return sum(1 for row in self.quotas if row.status is QuotaWorkloadStatus.NO_SCORING_RULE)

    @property
    def excluded_quota_count(self) -> int:
        return sum(
            1 for row in self.quotas if row.status is QuotaWorkloadStatus.EXCLUDED_FROM_PERFORMANCE
        )

    @property
    def is_complete(self) -> bool:
        """Every quota has an answer - a price or a deliberate exclusion."""
        return self.unpriced_quota_count == 0

    @property
    def unpriced_work_types(self) -> tuple[tuple[uuid.UUID, str | None], ...]:
        seen: dict[uuid.UUID, str | None] = {}
        for row in self.quotas:
            if row.status is QuotaWorkloadStatus.NO_SCORING_RULE and row.work_type_id not in seen:
                seen[row.work_type_id] = row.work_type_name
        return tuple(seen.items())

    @property
    def target_unresolved_reason(self) -> str | None:
        return self.target.unresolved_reason if self.target is not None else None


@dataclass(frozen=True, slots=True)
class _Loaded:
    quotas_by_plan: dict[uuid.UUID, list[PrWorkQuota]] = field(default_factory=dict)
    types: dict[uuid.UUID, PrWorkType] = field(default_factory=dict)
    rules: dict[uuid.UUID, PrWorkScoringRule] = field(default_factory=dict)


class PrPlanWorkloadCalculator:
    """Prices plans through M6's own readers. Reads only; writes nothing."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        scoring_rules: PrWorkScoringRuleService,
        policies: PrPerformancePolicyService,
        targets: PrPerformanceTargetService,
    ) -> None:
        self._session = session
        self._scoring_rules = scoring_rules
        self._policies = policies
        self._targets = targets

    async def for_plan(
        self,
        plan: PrWorkPlan,
        period: PrReportingPeriod,
        *,
        quotas: Sequence[PrWorkQuota] | None = None,
    ) -> PlanWorkload:
        """One plan. ``quotas`` may be handed in by a caller that already has them."""
        found = await self.for_plans((plan,), period, quotas={plan.id: quotas} if quotas else None)
        return found[plan.id]

    async def for_plans(
        self,
        plans: Sequence[PrWorkPlan],
        period: PrReportingPeriod,
        *,
        quotas: dict[uuid.UUID, Sequence[PrWorkQuota] | None] | None = None,
    ) -> dict[uuid.UUID, PlanWorkload]:
        """Every plan at once, for one period. Keyed by plan id.

        Queries, whatever the number of plans: quotas (one), work types (one),
        rules in force (one), policy (one), targets (four). A caller that has
        the quotas already passes them and saves the first.
        """
        if not plans:
            return {}
        loaded = await self._load(plans, period, quotas)
        policy = await self._policies.policy_for(on=period.date_end)
        daily = policy.daily_target_minutes if policy else DEFAULT_DAILY_TARGET_MINUTES
        targets = await self._targets.resolve_many(
            user_ids=[plan.user_id for plan in plans], period=period, daily_target_minutes=daily
        )
        return {
            plan.id: self._price_plan(
                loaded.quotas_by_plan.get(plan.id, []),
                loaded,
                target=targets[plan.user_id],
                on=period.date_end,
            )
            for plan in plans
        }

    async def _load(
        self,
        plans: Sequence[PrWorkPlan],
        period: PrReportingPeriod,
        given: dict[uuid.UUID, Sequence[PrWorkQuota] | None] | None,
    ) -> _Loaded:
        by_plan: dict[uuid.UUID, list[PrWorkQuota]] = {}
        missing = [plan.id for plan in plans if not (given or {}).get(plan.id)]
        for plan_id, rows in (given or {}).items():
            if rows:
                by_plan[plan_id] = list(rows)
        if missing:
            statement = (
                select(PrWorkQuota)
                .where(PrWorkQuota.plan_id.in_(missing))
                .order_by(PrWorkQuota.created_at.asc())
            )
            for row in (await self._session.execute(statement)).scalars().all():
                by_plan.setdefault(row.plan_id, []).append(row)
        type_ids = {row.work_type_id for rows in by_plan.values() for row in rows}
        types: dict[uuid.UUID, PrWorkType] = {}
        if type_ids:
            type_rows = (
                (await self._session.execute(select(PrWorkType).where(PrWorkType.id.in_(type_ids))))
                .scalars()
                .all()
            )
            types = {row.id: row for row in type_rows}
        rules = await self._scoring_rules.rules_for(type_ids, on=period.date_end)
        return _Loaded(quotas_by_plan=by_plan, types=types, rules=rules)

    @staticmethod
    def _price_plan(
        quotas: Iterable[PrWorkQuota],
        loaded: _Loaded,
        *,
        target: TargetResolution,
        on: date,
    ) -> PlanWorkload:
        rows = tuple(
            price_quota(
                quota, loaded.rules.get(quota.work_type_id), loaded.types.get(quota.work_type_id)
            )
            for quota in quotas
        )
        projected = quantize(
            sum((row.contribution_minutes or Decimal("0") for row in rows), Decimal("0")),
            MINUTES_QUANTUM,
        )
        complete = all(row.status is not QuotaWorkloadStatus.NO_SCORING_RULE for row in rows)
        percent = (
            quantize(projected / target.target_standard_minutes * Decimal("100"), WORKLOAD_QUANTUM)
            if target.target_standard_minutes and complete
            else None
        )
        unscored: list[uuid.UUID] = []
        for row in rows:
            if (
                row.status is QuotaWorkloadStatus.NO_SCORING_RULE
                and row.work_type_id not in unscored
            ):
                unscored.append(row.work_type_id)
        return PlanWorkload(
            projected_minutes=projected,
            target_minutes=target.target_standard_minutes,
            percent=percent,
            unscored_work_type_ids=tuple(unscored),
            quotas=rows,
            rules_effective_on=on,
            target=target,
        )


def price_quota(
    quota: PrWorkQuota, rule: PrWorkScoringRule | None, work_type: PrWorkType | None
) -> QuotaWorkload:
    """One quota against the rule in force. **The** target-to-minutes step.

    Pure, so a test can hand it a quota and a rule and read the product without
    a database - and so nothing else in the codebase needs its own copy.
    """
    base = {
        "quota_id": quota.id,
        "work_type_id": quota.work_type_id,
        "work_type_code": work_type.code if work_type else None,
        "work_type_name": work_type.name if work_type else None,
        "basis": quota.basis,
        "target_value": quota.target_value,
        "unit": quota.unit,
    }
    if rule is None:
        return QuotaWorkload(status=QuotaWorkloadStatus.NO_SCORING_RULE, **base)  # type: ignore[arg-type]
    if rule.mode is not PrWorkScoringMode.STANDARD_MINUTES:
        return QuotaWorkload(
            status=QuotaWorkloadStatus.EXCLUDED_FROM_PERFORMANCE,
            rule_id=rule.id,
            rule_version_no=rule.version_no,
            rule_effective_from=rule.effective_from,
            contribution_minutes=Decimal("0.00"),
            **base,  # type: ignore[arg-type]
        )
    per_unit = rule.standard_minutes_per_unit or Decimal("0")
    return QuotaWorkload(
        status=QuotaWorkloadStatus.PRICED,
        standard_minutes_per_unit=per_unit,
        rule_id=rule.id,
        rule_version_no=rule.version_no,
        rule_effective_from=rule.effective_from,
        contribution_minutes=quantize(quota.target_value * per_unit, MINUTES_QUANTUM),
        **base,  # type: ignore[arg-type]
    )


__all__: list[str] = [
    "PlanWorkload",
    "PrPlanWorkloadCalculator",
    "QuotaWorkload",
    "QuotaWorkloadStatus",
    "price_quota",
]
