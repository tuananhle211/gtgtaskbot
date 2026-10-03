"""The quota evaluator. **The only place eligibility is decided.** M2.

One service, because eligibility is one argument, and the failure this file
exists to prevent is the module answering *"is this eligible"* in three places
that slowly disagree - once in a router for a badge, once in a report, once in
the approval path. Nothing outside this file computes an eligible amount.

What it answers
----------------

Given one **person**, one **reporting period** and the approved plan in force
for them, which of their ``COUNTED`` contributions are inside the approved
quota - and by how much::

    COUNTED work  ─►  quota evaluation  ─►  NO_QUOTA
                                            UNMEASURABLE
                                            ELIGIBLE
                                            PARTIALLY_ELIGIBLE
                                            OVER_QUOTA

It computes no score, awards no point and knows no rate. ``ELIGIBLE`` means
*inside an approved quota*, which is a different sentence from *worth
something* - see :mod:`meobot.domain.pr.work_quota`.

Three failures, and only two of them are business states
---------------------------------------------------------

The distinction this file has to keep straight, because getting it wrong is how
a bug becomes an employee's KPI figure:

============================  ==========================================
*No approved quota*           ``NO_QUOTA``. A business state. Somebody has
                              to approve a plan
*A quota, and no measurable   ``UNMEASURABLE`` with a ``reason_code``. A
data*                         business state. Somebody has to type a
                              quantity onto a work item
*The evaluator broke*         **Neither.** Not a business state at all
============================  ==========================================

The third row is the rule. An unexpected exception - a bug, a lock timeout, a
column that is not there - must **never** be dressed as an eligibility decision:
it is raised, and the caller decides. :meth:`on_contributions_counted` runs
inside a savepoint the approval owns, so the approval survives and the failure is
logged; :meth:`reconcile_period` lets it out, so an operator sees a failure
rather than a period full of confident wrong answers.

That is why :func:`~meobot.domain.pr.work_quota.measure_contribution` **returns**
its two business outcomes rather than raising one of them. If it raised, the only
way to keep one bad row from aborting an employee's whole period would be a
``try/except`` around it - and an ``except`` wide enough to catch a missing
quantity is wide enough to catch a bug, which is exactly how "the evaluator
broke" would come to look like "no quota".

A projection, not a mutation loop
----------------------------------

:meth:`~PrWorkQuotaEligibilityService.evaluate` recomputes a whole
``(user, period)`` from the contributions and the approved plan, and writes the
result. It does not increment anything and it holds no running total, which is
what makes it **idempotent by construction**: an undo, a raised cap, a plan
revision and a re-run backfill all converge on the same answer without a repair
script, and running it twice produces the same rows rather than twice as many.

The database says the same thing independently:
``uq_pr_work_quota_allocations_contribution`` allows one current decision per
contribution.

Bounded by the reporting period's status
-----------------------------------------

============  ===========================================================
``OPEN``      Recompute allowed. An ``OVER_QUOTA`` contribution may become
              ``ELIGIBLE`` when capacity appears - a cap was raised, or an
              earlier contribution stopped qualifying
``CLOSED``    **Refused.** The numbers were agreed
``LOCKED``    **Refused.** The period has been reported
============  ===========================================================

Refused, not silently skipped: a no-op would hide somebody reconciling the
wrong month and believing it worked. There is deliberately **no ``force``
flag** - correcting a closed period is a deliberate administrative act with its
own trail, and it belongs to a milestone that has one.

Concurrency
------------

Every recompute takes a row lock on the **reporting period** first, so two
transactions cannot each conclude that one quota slot remains. That is coarser
than locking ``(user, period)`` - two approvals for two different employees in
the same month serialise - and it is the right trade at this department's
scale: contention is a handful of writes a day, and the alternative is an
advisory-lock scheme with no row to hang it on when the employee has no plan.

The lock order is always **work item, then period**, because
:meth:`~meobot.application.pr_work_service.PrWorkService.approve` holds the item
when it calls in here and nothing else takes the two in the other order.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, delete, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota, PrWorkQuotaAllocation
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkPeriodNotOpenError,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkUnit
from meobot.domain.pr.work_quota import (
    Measurement,
    PrWorkPlanStatus,
    PrWorkQuotaBasis,
    PrWorkQuotaStatus,
    PrWorkUnmeasurableReason,
    QuotaCandidate,
    allocate,
    measure_contribution,
    quantise_quota,
)
from meobot.domain.pr.work_results import TargetComparison, compare_to_target

logger = get_logger(__name__)

ZERO = Decimal("0.00")

#: How many employees one reconcile request may sweep.
#:
#: Bounded because reconciliation is a write over every counted contribution of
#: everybody it names, and an unbounded request is an unbounded transaction. The
#: department is ~20 people, so this is several times the whole of it.
MAX_RECONCILE_USERS = 200


def counted_in_period(
    period: PrReportingPeriod, lower: datetime, upper: datetime
) -> ColumnElement[bool]:
    """**The one predicate for "counted in this month".** Period-container patch.

    Two ways a counted contribution belongs to a period, and they are one
    ``OR`` so every reader - M2's evaluator and summary, M6's projection and
    evidence, the Work page's tiles - agrees:

    * a **period container** belongs to the month it *is*.
      ``pr_work_items.reporting_period_id`` names it, and ``counted_at`` is
      deliberately not consulted: the first result of a September stream may be
      validated on 2 October and the stream is still September's;
    * a **one-off job** belongs to the month containing its ``counted_at`` -
      M1's rule, unchanged: work assigned on 31 August and validated on
      2 September is September's.

    Requires the query to have joined ``PrWorkItem``.
    """
    return or_(
        PrWorkItem.reporting_period_id == period.id,
        and_(
            PrWorkItem.reporting_period_id.is_(None),
            PrWorkContribution.counted_at >= lower,
            PrWorkContribution.counted_at < upper,
        ),
    )


def measure_counted(
    contribution: PrWorkContribution,
    item: PrWorkItem,
    *,
    basis: PrWorkQuotaBasis,
    item_unit: PrWorkUnit | None = None,
    quota_unit: PrWorkUnit | None = None,
) -> Measurement:
    """**The one measuring call for a COUNTED contribution.** M2's evaluator,
    M2's read model and M6's pricing all measure through it, so the KPI
    screen, the performance breakdown and the work card cannot disagree
    about what one row of counted work amounts to.

    A one-off job measures as :func:`~meobot.domain.pr.work_quota.measure_contribution`
    always has: one item on ``ITEM_COUNT``, its quantity on ``QUANTITY``.

    A **period container** is different, and the difference is the whole
    point of it: its single contribution mirrors the **sum of its counted
    results** onto ``item.quantity``, and it stands for that many units on
    either basis. A stream of 23 counted scripts is 23 items, not one
    contribution - counting it as one would price a month of scripts as a
    single job and put 5 % on a KPI the card shows at 115 %. The unit
    comparison applies only where a quota names one (``QUANTITY``).
    """
    if item.is_period_container:
        quantity_basis = basis is PrWorkQuotaBasis.QUANTITY
        return measure_contribution(
            PrWorkQuotaBasis.QUANTITY,
            quantity=item.quantity,
            credit_weight=contribution.credit_weight,
            item_unit=item_unit if quantity_basis else None,
            quota_unit=quota_unit if quantity_basis else None,
        )
    return measure_contribution(
        basis,
        quantity=item.quantity,
        credit_weight=contribution.credit_weight,
        item_unit=item_unit,
        quota_unit=quota_unit,
    )


@dataclass(frozen=True, slots=True)
class ContributionEligibility:
    """One counted contribution and what the quota engine says about it.

    Assembled from a **left join**: the allocation may be absent, and that is a
    supported state rather than a bug. Every contribution counted before M2 was
    deployed is in it, and so is one counted into a month nobody has opened a
    reporting period for.

    **Absent does not mean ``NO_QUOTA``.** It means one of two things, and the
    read distinguishes them - see :meth:`PrWorkQuotaEligibilityService._read`.

    The three amounts are **optional**, and each ``None`` says something
    different from a zero:

    * ``basis_amount`` is null when the contribution cannot be measured. Zero
      would say the employee produced nothing;
    * ``eligible_amount`` and ``over_quota_amount`` are null only for
      ``PENDING_EVALUATION``: nothing has decided, and a zero would be a
      decision. For ``NO_QUOTA`` and ``UNMEASURABLE`` they really are zero.
    """

    contribution_id: uuid.UUID
    work_item_id: uuid.UUID
    work_item_code: str
    work_item_title: str
    user_id: uuid.UUID
    work_type_id: uuid.UUID
    work_type_code: str
    work_type_name: str
    counted_at: datetime
    quota_status: PrWorkQuotaStatus
    basis: PrWorkQuotaBasis
    unit: PrWorkUnit | None
    basis_amount: Decimal | None
    eligible_amount: Decimal | None
    over_quota_amount: Decimal | None
    #: ``True`` when a materialised allocation row produced this. ``False`` when
    #: it was derived on read from a contribution nothing has evaluated yet.
    #: The distinction matters to an operator deciding whether to reconcile; it
    #: changes nothing about what the figures mean.
    is_materialised: bool
    #: Why the contribution could not be measured. Set only for
    #: ``UNMEASURABLE``, and always set for it.
    reason_code: PrWorkUnmeasurableReason | None = None
    work_plan_id: uuid.UUID | None = None
    work_quota_id: uuid.UUID | None = None
    evaluated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class QuotaTypeProgress:
    """One work type's figures for one person and period. **Never merged.**

    Five different numbers that a KPI screen must not collapse, and the reason
    each is here rather than derived from another:

    * ``counted_contributions`` - how many of this person's shares were
      validated. M1's figure, unchanged;
    * ``counted_amount`` - what those shares are worth on this type's basis. For
      ``ITEM_COUNT`` it equals the count; for ``QUANTITY`` it is 3 150 comments
      from 30 work items, and the two are very different sentences;
    * ``eligible_amount`` - how much of it is inside the approved cap;
    * ``over_quota_amount`` - how much is not;
    * ``no_quota_amount`` - how much of it no approved quota has looked at.

    ``counted_amount = eligible_amount + over_quota_amount + no_quota_amount``
    **over the contributions that could be measured**.

    Which is why ``measured_contributions`` is here beside the count: when the
    two differ, some of this person's work has no amount, and the totals above
    understate the period by exactly those rows. They are **counted, never summed
    as zero** - a missing quantity is not a quantity of nothing, and adding it in
    as zero would say the employee did no work when they did work nobody wrote a
    number for.
    """

    work_type_id: uuid.UUID
    work_type_code: str
    work_type_name: str
    basis: PrWorkQuotaBasis
    unit: PrWorkUnit | None
    #: Absent when there is no approved quota for this type. **Not** unlimited.
    target_value: Decimal | None
    eligibility_cap: Decimal | None
    work_quota_id: uuid.UUID | None
    counted_contributions: int
    #: How many of those had an amount the quota engine could state. Equal to
    #: ``counted_contributions`` in the ordinary case; smaller when something is
    #: ``UNMEASURABLE`` or unevaluated.
    measured_contributions: int
    counted_amount: Decimal
    eligible_amount: Decimal
    over_quota_amount: Decimal
    no_quota_amount: Decimal
    no_quota_contributions: int
    #: An approved quota covers this work type and these contributions cannot be
    #: measured against it. **A count, never an amount** - there is no amount.
    unmeasurable_contributions: int = 0
    #: An approved quota covers this work type and nothing has evaluated these
    #: yet. Reconciling the period resolves them.
    pending_contributions: int = 0

    @property
    def target_progress(self) -> Decimal:
        """How much of the **target** the eligible amount has reached.

        Capped at the target, because "20 / 20" is what a target being met looks
        like and "25 / 20" is a different fact that
        :attr:`extra_eligible_above_target` says separately. Merging the two
        would make a progress bar read past its own end.
        """
        if self.target_value is None:
            return ZERO
        return min(self.eligible_amount, self.target_value)

    @property
    def extra_eligible_above_target(self) -> Decimal:
        """Eligible work beyond the target and still inside the cap.

        The reason ``target_value`` and ``eligibility_cap`` are two columns: 20
        was asked for, 25 may be eligible, so five units of real extra work stay
        eligible without pretending the target was 25. **Not points.**
        """
        if self.target_value is None:
            return ZERO
        return max(ZERO, self.eligible_amount - self.target_value)

    @property
    def comparison(self) -> TargetComparison:
        """**The KPI comparison**: the whole counted amount against the target.

        Through :func:`~meobot.domain.pr.work_results.compare_to_target`, the
        one place ``actual / target`` is computed. Uncapped - 27 against 20 is
        135 % and 7 over - and ``None`` rather than 0 % when there is no target.
        """
        return compare_to_target(self.counted_amount, self.target_value)

    @property
    def completion_percent(self) -> Decimal | None:
        return self.comparison.completion_percent

    @property
    def over_target_amount(self) -> Decimal:
        return self.comparison.over_target


@dataclass(frozen=True, slots=True)
class QuotaPeriodSummary:
    """One person's KPI eligibility for one period, per work type.

    The cross-type figures are **counts of contributions only**. Summing
    ``COMMENT`` + ``VIDEO`` + ``DAY`` into one quantity would produce a total
    that is not a quantity of anything, so this object refuses to have one - see
    ``docs/pr/WORK_QUOTA_M2.md`` § "Summary semantics".
    """

    user_id: uuid.UUID
    period_id: uuid.UUID
    period_code: str
    period_status: PrPeriodStatus
    #: The approved plan in force, if there is one. A ``DRAFT`` is never here:
    #: an unapproved plan decides nothing.
    plan_id: uuid.UUID | None = None
    plan_version_no: int | None = None
    plan_approved_at: datetime | None = None
    types: tuple[QuotaTypeProgress, ...] = ()
    #: How many contributions are in each quota status. A count, never a
    #: quantity.
    contributions_by_status: dict[str, int] = field(default_factory=dict)
    counted_contributions: int = 0
    counted_work_items: int = 0


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """What one recompute did. Returned so a caller can report and log it."""

    user_id: uuid.UUID
    period_id: uuid.UUID
    evaluated: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    #: Contributions an approved quota could not measure, materialised as
    #: ``UNMEASURABLE`` with a reason code: a ``QUANTITY``-measured work type
    #: with no quantity on the item, an amount that rounds away to nothing, or a
    #: unit that does not match the quota's.
    #:
    #: **Materialised, not skipped.** Until this patch they were skipped, which
    #: left them with no allocation row and made them read back as ``NO_QUOTA`` -
    #: the one thing they definitely were not. The figure is the operational
    #: signal: it is how many rows somebody has to put a number on.
    unmeasurable: int = 0


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """What one explicit reconciliation did, across everybody it swept."""

    period_id: uuid.UUID
    period_code: str
    users: int = 0
    evaluated: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    unmeasurable: int = 0


class PrWorkQuotaEligibilityService:
    """Decides which counted work is inside an approved quota. Nothing else does.

    Args:
        session: Unit of work. The caller owns the transaction boundary, which
            is what lets :meth:`evaluate` be part of an approval's transaction
            without this service knowing about transactions.
        audit: Event writer sharing that session.
        capabilities: Gates the read surface and the explicit reconcile.
        periods: The one place a date becomes a reporting period.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        periods: PrWorkPeriodService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._periods = periods

    # =====================================================================
    # The projection
    # =====================================================================
    async def evaluate(
        self,
        *,
        user_id: uuid.UUID,
        period: PrReportingPeriod,
        now: datetime | None = None,
    ) -> EvaluationOutcome:
        """Recompute one person's eligibility for one period. **Idempotent.**

        No capability check: this is the engine, and the four callers that reach
        it - work approval, plan approval, plan revision and the explicit
        reconcile - each check their own. Putting a check here as well would
        make the approval path require the approver to hold a quota capability,
        which is exactly the coupling
        :meth:`~PrWorkQuotaEligibilityService.on_contributions_counted`
        exists to avoid.

        In order:

        1. lock the period, so two transactions cannot both think one slot
           remains;
        2. re-read its status under the lock and refuse anything but ``OPEN``;
        3. read the approved plan and its quotas - a ``DRAFT`` is ignored,
           because an unapproved plan decides nothing;
        4. read every ``COUNTED`` contribution whose ``counted_at`` falls inside
           the period, in the deterministic order;
        5. fill each quota's cap, per work type;
        6. write the allocations, updating what exists and removing what is no
           longer a candidate.

        Raises:
            PrWorkPeriodNotOpenError: the period is ``CLOSED`` or ``LOCKED``.
        """
        moment = now or utcnow()
        locked = await lock_row(self._session, PrReportingPeriod, period.id)
        if locked is None:  # pragma: no cover - the caller loaded it a line ago
            raise PrValidationError(
                "The reporting period disappeared while it was being evaluated",
                details={"field": "period_id", "reason": "period_missing"},
            )
        assert_period_open(locked)

        lower, upper = self._periods.bounds(locked)
        quotas = await self._approved_quotas(user_id=user_id, period_id=locked.id)
        candidates = await self._counted_rows(
            user_id=user_id, period=locked, lower=lower, upper=upper
        )

        desired: dict[uuid.UUID, PrWorkQuotaAllocation] = {}
        unmeasurable = 0
        by_type: dict[uuid.UUID, list[tuple[PrWorkContribution, PrWorkItem, PrWorkType]]] = {}
        for contribution, item, work_type in candidates:
            by_type.setdefault(work_type.id, []).append((contribution, item, work_type))

        for work_type_id, group in by_type.items():
            quota = quotas.get(work_type_id)
            work_type = group[0][2]
            # The quota's basis when there is one, the **work type's** when there
            # is not. That is what lets a ``NO_QUOTA`` row still say "3.150 bình
            # luận" rather than "30 rows", and it is why the basis lives on the
            # type as well as on the quota.
            basis = quota.basis if quota is not None else work_type.default_quota_basis
            unit = self._unit_for(basis, quota=quota, work_type=work_type)

            def row(
                contribution_id: uuid.UUID,
                *,
                status: PrWorkQuotaStatus,
                basis_amount: Decimal | None,
                eligible: Decimal,
                over: Decimal,
                reason: PrWorkUnmeasurableReason | None = None,
                _type_id: uuid.UUID = work_type_id,
                _quota: PrWorkQuota | None = quota,
                _basis: PrWorkQuotaBasis = basis,
                _unit: PrWorkUnit | None = unit,
            ) -> PrWorkQuotaAllocation:
                """One allocation, with the provenance this group shares.

                The private defaults bind this iteration's group rather than the
                loop variables, so the closure cannot silently describe the last
                work type it saw.
                """
                return PrWorkQuotaAllocation(
                    work_contribution_id=contribution_id,
                    reporting_period_id=locked.id,
                    user_id=user_id,
                    work_type_id=_type_id,
                    work_plan_id=_quota.plan_id if _quota is not None else None,
                    work_quota_id=_quota.id if _quota is not None else None,
                    quota_status=status,
                    basis=_basis,
                    unit=_unit,
                    basis_amount=basis_amount,
                    eligible_amount=eligible,
                    over_quota_amount=over,
                    reason_code=reason,
                    evaluated_at=moment,
                )

            measured: list[QuotaCandidate] = []
            for contribution, item, _ in group:
                assert contribution.counted_at is not None  # count_status = COUNTED
                # **No unit comparison.** Both the item's unit and the quota's are
                # copies of the work type's unit at the moment each was written,
                # and since the period-container patch an administrator may
                # rename that unit - "sản phẩm" to "khách hàng" - on a type in
                # use. The two labels then differ and describe the same measure;
                # calling that ``UNIT_MISMATCH`` would strike real work out of a
                # plan because a word changed. Which work type a quota covers is
                # the join above, and that is the check that matters.
                measurement = measure_counted(contribution, item, basis=basis)
                if measurement.amount is None:
                    # **Not measurable, and that is a business state.**
                    #
                    # Which state depends on whether anybody set a target:
                    #
                    # * no approved quota -> ``NO_QUOTA``. The absence of a
                    #   decision is the more useful sentence, and asking somebody
                    #   to supply a quantity for a quota nobody wrote would send
                    #   them to do pointless work. The amount stays **null**
                    #   rather than zero: the work happened, and a report that
                    #   summed a zero would say it did not;
                    # * an approved quota -> ``UNMEASURABLE``, naming the quota
                    #   and the reason. A materialised row, not an absence, so a
                    #   read never has to guess what the missing one meant.
                    #
                    # Either way the contribution stays ``COUNTED`` and nothing
                    # is raised. It is not an error for valid completed work to
                    # be missing a number somebody can type in later.
                    if quota is None:
                        desired[contribution.id] = row(
                            contribution.id,
                            status=PrWorkQuotaStatus.NO_QUOTA,
                            basis_amount=None,
                            eligible=ZERO,
                            over=ZERO,
                        )
                        continue
                    assert measurement.reason is not None  # the Measurement invariant
                    unmeasurable += 1
                    desired[contribution.id] = row(
                        contribution.id,
                        status=PrWorkQuotaStatus.UNMEASURABLE,
                        basis_amount=None,
                        eligible=ZERO,
                        over=ZERO,
                        reason=measurement.reason,
                    )
                    continue
                measured.append(
                    QuotaCandidate(
                        contribution_id=contribution.id,
                        counted_at=contribution.counted_at,
                        created_at=contribution.created_at,
                        basis_amount=measurement.amount,
                    )
                )

            for decided in allocate(
                measured,
                eligibility_cap=quota.eligibility_cap if quota is not None else None,
            ):
                desired[decided.contribution_id] = row(
                    decided.contribution_id,
                    status=decided.quota_status,
                    basis_amount=decided.basis_amount,
                    eligible=decided.eligible_amount,
                    over=decided.over_quota_amount,
                )

        outcome = await self._write(
            user_id=user_id, period_id=locked.id, desired=desired, moment=moment
        )
        return EvaluationOutcome(
            user_id=user_id,
            period_id=locked.id,
            evaluated=outcome[0],
            created=outcome[1],
            updated=outcome[2],
            removed=outcome[3],
            unmeasurable=unmeasurable,
        )

    async def on_contributions_counted(
        self,
        contributions: Sequence[PrWorkContribution],
        *,
        now: datetime | None = None,
        period_of: PrReportingPeriod | None = None,
    ) -> None:
        """Evaluate the people whose work has just become ``COUNTED``.

        Called by :meth:`~meobot.application.pr_work_service.PrWorkService.approve`
        inside the approval's own transaction, and **it can never refuse an
        approval**. That is the requirement: a valid contribution must be
        allowed to become ``COUNTED`` even when its quota status will be
        ``NO_QUOTA``, so work validation must not depend on a quota existing.

        Three things are therefore swallowed here rather than raised, each with
        a log line an operator can find:

        * **no reporting period for that month.** Nobody has opened one. The
          contribution stays ``COUNTED`` and simply appears in no period's
          eligibility at all - every read is *per period*, and there is no period
          to read. Opening the month and reconciling brings it in;
        * **the period is ``CLOSED`` or ``LOCKED``.** Late validation into a
          reported month must not rewrite it - see the module docstring;
        * **the projection failed unexpectedly.** Isolated inside a ``SAVEPOINT``
          by the caller, so the approval survives it intact and the next
          reconcile converges.

          Note what this is **not**: it is not turned into an eligibility
          decision. A contribution whose projection crashed gets no allocation
          row, and a read reports it as ``PENDING_EVALUATION`` when a quota
          covers it - *nothing has looked at this yet*, which is true - rather
          than ``NO_QUOTA``, which would be a claim about the plan that nobody
          made. Missing measurable **data** is a different thing entirely and is
          materialised as ``UNMEASURABLE`` before it ever reaches here.

        The caller owns the savepoint rather than this method, because a
        savepoint opened and rolled back inside the object that also wrote the
        rows would be rolling back its own work while the caller believed the
        transaction was still clean.
        """
        moment = now or utcnow()
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for contribution in contributions:
            if contribution.counted_at is None:  # pragma: no cover - approve sets both
                continue
            period = period_of or await self._period_of_contribution(contribution)
            if period is None:
                logger.warning(
                    "pr_work_quota_period_missing",
                    extra={
                        "pr_work_contribution_id": str(contribution.id),
                        "pr_work_counted_at": contribution.counted_at.isoformat(),
                    },
                )
                continue
            if period.status is not PrPeriodStatus.OPEN:
                logger.info(
                    "pr_work_quota_period_not_open",
                    extra={
                        "pr_work_contribution_id": str(contribution.id),
                        "pr_reporting_period": period.code,
                        "pr_reporting_period_status": period.status.value,
                    },
                )
                continue
            key = (contribution.user_id, period.id)
            if key in seen:
                # A three-person shoot shares one ``counted_at``; each person is
                # evaluated once, and re-evaluating the same pair would be the
                # same recompute twice.
                continue
            seen.add(key)
            # **No approved plan is not a quota decision, so it does not get an
            # allocation row.**
            #
            # The two absences this module has to keep apart:
            #
            # * *nobody has an approved KPI plan for this person and month* -
            #   there is nothing to evaluate **against** yet. A materialised
            #   ``NO_QUOTA`` row here would record that eligibility was assessed
            #   and came out empty, when it was never assessed at all;
            # * *a plan is in force and no quota in it covers this work type* -
            #   somebody did decide, and this work fell outside every decision.
            #   That is actionable - "your plan does not cover the work you are
            #   doing" - and it stays materialised by :meth:`evaluate`, exactly
            #   as before. The two are different states and this guard exists to
            #   keep them different.
            #
            # No sentence is lost by skipping. The read path tells the two apart
            # without the row: a counted contribution with no allocation reads as
            # ``NO_QUOTA`` with ``is_materialised = False`` when nothing covers
            # it, and as ``PENDING_EVALUATION`` when a quota does.
            #
            # **The incremental hook only.** ``reconcile_period`` is a deliberate
            # administrative act with a request id behind it, and it still
            # materialises ``NO_QUOTA`` for somebody with no plan. Automatic
            # projection and explicit reconciliation are different acts, and only
            # the first is guessing at a decision nobody has taken.
            #
            # Nothing is stranded by returning early. An approved plan is
            # **monotone** per person and period - the only exit from
            # ``APPROVED`` is ``SUPERSEDED``, written inside the approval of its
            # own replacement - and ``PrWorkPlanService.approve`` finishes by
            # calling :meth:`evaluate` for the whole period. Work counted before
            # any plan existed is therefore evaluated the moment one is approved,
            # without waiting for a reconcile.
            plan = await self._approved_plan(user_id=contribution.user_id, period_id=period.id)
            if plan is None:
                logger.info(
                    "pr_work_quota_no_approved_plan",
                    extra={
                        "pr_work_contribution_id": str(contribution.id),
                        "pr_work_user_id": str(contribution.user_id),
                        "pr_reporting_period": period.code,
                    },
                )
                continue
            await self.evaluate(user_id=contribution.user_id, period=period, now=moment)

    async def on_contributions_uncounted(
        self,
        contributions: Sequence[PrWorkContribution],
        *,
        now: datetime | None = None,
        period_of: PrReportingPeriod | None = None,
    ) -> None:
        """Recompute the periods contributions have just **left**. M3.

        The mirror of :meth:`on_contributions_counted`, and deliberately almost
        the same method: the evaluator is a projection over the contributions
        that *are* counted, so a contribution leaving the set is not a
        subtraction it has to be told about - it recomputes the whole
        ``(user, period)`` and the released capacity flows to whatever comes
        next in the deterministic order. That is the promise M2 §11 makes about
        an ``OPEN`` period, kept: *"an ``OVER_QUOTA`` contribution may become
        ``ELIGIBLE`` when an earlier contribution stops qualifying"*.

        The period is resolved from the contribution's **``excluded_at``** here
        rather than its ``counted_at``, because by the time this is called the
        second has been cleared - the ``counted_at_matches_status`` CHECK
        refuses a row that is not counted and still carries a counting instant.
        That is not a change of attribution: the caller only ever reverses work
        whose period is still ``OPEN``, and a period that is open now contained
        the ``counted_at`` a moment ago.

        Like its mirror, this **can never refuse the reversal**. A month nobody
        has opened, a ``CLOSED`` month, a failure - all are logged and skipped,
        and the next reconcile converges.
        """
        moment = now or utcnow()
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for contribution in contributions:
            at = contribution.excluded_at or moment
            period = period_of or await self._container_period(contribution)
            if period is None:
                period = await self._periods.period_for(at)
            if period is None:
                logger.warning(
                    "pr_work_quota_period_missing",
                    extra={
                        "pr_work_contribution_id": str(contribution.id),
                        "pr_work_excluded_at": at.isoformat(),
                    },
                )
                continue
            if period.status is not PrPeriodStatus.OPEN:
                logger.info(
                    "pr_work_quota_period_not_open",
                    extra={
                        "pr_work_contribution_id": str(contribution.id),
                        "pr_reporting_period": period.code,
                        "pr_reporting_period_status": period.status.value,
                    },
                )
                continue
            key = (contribution.user_id, period.id)
            if key in seen:
                continue
            seen.add(key)
            await self.evaluate(user_id=contribution.user_id, period=period, now=moment)

    async def _container_period(self, contribution: PrWorkContribution) -> PrReportingPeriod | None:
        """The month a contribution's item *is*, when the item is a period container."""
        period_id = (
            await self._session.execute(
                select(PrWorkItem.reporting_period_id).where(
                    PrWorkItem.id == contribution.work_item_id
                )
            )
        ).scalar_one_or_none()
        if period_id is None:
            return None
        return await self._session.get(PrReportingPeriod, period_id)

    async def _period_of_contribution(
        self, contribution: PrWorkContribution
    ) -> PrReportingPeriod | None:
        """Which month a freshly counted contribution belongs to.

        The container's own month when there is one, else the month containing
        ``counted_at`` - the same two-way rule :func:`counted_in_period` states
        in SQL, applied to one row in Python.
        """
        container = await self._container_period(contribution)
        if container is not None:
            return container
        assert contribution.counted_at is not None
        return await self._periods.period_for(contribution.counted_at)

    async def reconcile_period(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        period_id: uuid.UUID,
        user_ids: Sequence[uuid.UUID] | None = None,
    ) -> ReconcileOutcome:
        """Recompute an **open** period, for one person or for everybody in it.

        ``PR_WORK_CONFIGURE``. The explicit, auditable path that makes the
        module converge:

        * it is how contributions counted **before M2 was deployed** get their
          allocations, without a migration-time backfill that would have written
          KPI decisions nobody asked for;
        * it is how a period recovers after a projection failed inside an
          approval;
        * it is how an operator sees whether anything is unmeasurable.

        **Idempotent.** Running it twice produces identical allocations, because
        :meth:`evaluate` is a projection rather than an increment.

        Refuses ``CLOSED`` and ``LOCKED`` with
        :class:`~meobot.domain.pr.errors.PrWorkPeriodNotOpenError`, loudly and
        with no escape hatch.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        period = await self._periods.require_period(period_id)
        # Checked before the sweep as well as under the lock, so the refusal is
        # the first thing that happens rather than the last.
        assert_period_open(period)

        subjects = list(dict.fromkeys(user_ids or []))
        if not subjects:
            subjects = list(await self._users_with_work_in(period))
        if len(subjects) > MAX_RECONCILE_USERS:
            raise PrValidationError(
                "Too many people in one reconciliation",
                details={
                    "field": "user_ids",
                    "reason": "too_many_users",
                    "maximum": MAX_RECONCILE_USERS,
                },
            )

        moment = utcnow()
        totals = {"evaluated": 0, "created": 0, "updated": 0, "removed": 0, "unmeasurable": 0}
        for user_id in subjects:
            outcome = await self.evaluate(user_id=user_id, period=period, now=moment)
            totals["evaluated"] += outcome.evaluated
            totals["created"] += outcome.created
            totals["updated"] += outcome.updated
            totals["removed"] += outcome.removed
            totals["unmeasurable"] += outcome.unmeasurable

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_ELIGIBILITY_RECONCILED,
            entity_type="pr_reporting_period",
            entity_id=period.id,
            after={
                "period_code": period.code,
                "users": len(subjects),
                "user_ids": [str(one) for one in subjects],
                **totals,
            },
        )
        logger.info(
            "pr_work_eligibility_reconciled",
            extra={"pr_reporting_period": period.code, "pr_work_users": len(subjects), **totals},
        )
        return ReconcileOutcome(
            period_id=period.id,
            period_code=period.code,
            users=len(subjects),
            evaluated=totals["evaluated"],
            created=totals["created"],
            updated=totals["updated"],
            removed=totals["removed"],
            unmeasurable=totals["unmeasurable"],
        )

    # =====================================================================
    # Reading
    # =====================================================================
    async def eligibility(
        self, *, actor: Actor, user_id: uuid.UUID | None, period_id: uuid.UUID
    ) -> tuple[PrReportingPeriod, uuid.UUID, tuple[ContributionEligibility, ...]]:
        """One person's counted contributions and what the quota says about each.

        A **read**, so it materialises nothing: a counted contribution with no
        allocation row is reported as ``NO_QUOTA`` **only when no approved quota
        covers its work type** - and as ``PENDING_EVALUATION`` when one does,
        because *"nobody set a target"* and *"nothing has looked at this yet"*
        are two different sentences and only one of them is true. Making a read
        write would mean a screen refresh could change somebody's KPI figures,
        and it would put a write on a path that has to work while a period is
        ``LOCKED``.
        """
        subject = await self._require_subject(actor, user_id)
        period = await self._periods.require_period(period_id)
        # The subject is returned rather than read back off the first row: an
        # employee with no counted work in the month has an empty list, and a
        # response that could not name whose empty list it was would be a screen
        # that might be headed with the wrong person.
        return period, subject, tuple(await self._read(user_id=subject, period=period))

    async def summary(
        self, *, actor: Actor, user_id: uuid.UUID | None, period_id: uuid.UUID
    ) -> QuotaPeriodSummary:
        """One person's KPI plan progress for one period, per work type.

        Includes quotas with **no counted work yet** - a target of 20 with
        nothing done is the most useful row on the screen - and work types with
        counted work and **no quota**, which is the ``NO_QUOTA`` case the
        employee needs to see so they can ask for one.

        ``unmeasurable_contributions`` and ``pending_contributions`` are reported
        **separately and as counts**, never folded into the amounts: there is no
        amount to fold, and inventing a zero would say the employee produced
        nothing.
        """
        subject = await self._require_subject(actor, user_id)
        period = await self._periods.require_period(period_id)
        rows = await self._read(user_id=subject, period=period)
        plan = await self._approved_plan(user_id=subject, period_id=period.id)
        quotas = await self.quotas_of(plan)
        types = await self._types_for({row.work_type_id for row in rows} | set(quotas))

        progress: list[QuotaTypeProgress] = []
        statuses: dict[str, int] = {status.value: 0 for status in PrWorkQuotaStatus}
        for row in rows:
            statuses[row.quota_status.value] += 1

        for work_type in types:
            quota = quotas.get(work_type.id)
            mine = [row for row in rows if row.work_type_id == work_type.id]
            basis = quota.basis if quota is not None else work_type.default_quota_basis
            no_quota = [row for row in mine if row.quota_status is PrWorkQuotaStatus.NO_QUOTA]
            progress.append(
                QuotaTypeProgress(
                    work_type_id=work_type.id,
                    work_type_code=work_type.code,
                    work_type_name=work_type.name,
                    basis=basis,
                    unit=self._unit_for(basis, quota=quota, work_type=work_type),
                    target_value=quota.target_value if quota is not None else None,
                    eligibility_cap=quota.eligibility_cap if quota is not None else None,
                    work_quota_id=quota.id if quota is not None else None,
                    counted_contributions=len(mine),
                    # Every sum below **skips the rows with no amount**, and the
                    # count beside it is how a reader knows the difference. A
                    # missing quantity added in as zero would say the employee
                    # produced nothing.
                    measured_contributions=sum(1 for row in mine if row.basis_amount is not None),
                    counted_amount=_total(row.basis_amount for row in mine),
                    eligible_amount=_total(row.eligible_amount for row in mine),
                    over_quota_amount=_total(row.over_quota_amount for row in mine),
                    no_quota_amount=_total(row.basis_amount for row in no_quota),
                    no_quota_contributions=len(no_quota),
                    unmeasurable_contributions=sum(
                        1 for row in mine if row.quota_status is PrWorkQuotaStatus.UNMEASURABLE
                    ),
                    pending_contributions=sum(
                        1
                        for row in mine
                        if row.quota_status is PrWorkQuotaStatus.PENDING_EVALUATION
                    ),
                )
            )

        return QuotaPeriodSummary(
            user_id=subject,
            period_id=period.id,
            period_code=period.code,
            period_status=period.status,
            plan_id=plan.id if plan is not None else None,
            plan_version_no=plan.version_no if plan is not None else None,
            plan_approved_at=plan.approved_at if plan is not None else None,
            types=tuple(progress),
            contributions_by_status=statuses,
            counted_contributions=len(rows),
            counted_work_items=len({row.work_item_id for row in rows}),
        )

    # =====================================================================
    # Internals
    # =====================================================================
    async def _require_subject(self, actor: Actor, user_id: uuid.UUID | None) -> uuid.UUID:
        """Whose eligibility this caller may read.

        Own always; anybody else's needs ``PR_WORK_VIEW_ALL`` - the **same**
        capability M1 uses for the department-wide work list, deliberately, so
        there is one answer to "may I see a colleague's record" rather than two
        that can drift apart. ``PR_WORK_MANAGE`` is **not** enough: managing the
        jobs you put somebody on is a different act from reading their KPI, and
        MeoBot models no team that would make a narrower middle ground honest.

        Refused rather than narrowed to the caller's own figures - a screen
        labelled with somebody else's name showing your numbers is worse than an
        error.
        """
        if actor.user_id is None:
            raise PrPermissionDeniedError(
                "This actor has no user row, so there is no work to report on",
                details={"reason": "actor_has_no_user_row"},
            )
        if user_id is None or user_id == actor.user_id:
            return actor.user_id
        if not await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL):
            raise PrPermissionDeniedError(
                "You may not read another person's KPI eligibility",
                details={"reason": "user_filter_not_permitted"},
            )
        return user_id

    async def approved_quota_for(
        self, *, user_id: uuid.UUID, period_id: uuid.UUID, work_type_id: uuid.UUID
    ) -> PrWorkQuota | None:
        """The approved quota this person has for this kind of work, if any. **M4A.**

        A **read**, and the only reason it is public: M4's creation screen needs
        to tell a manager *"this will be recorded, but there is no KPI quota for
        it this month"* before they assign it, and computing that by re-querying
        plans elsewhere would be a second implementation of the ``APPROVED``-only
        rule below. This delegates to the same two private readers eligibility
        itself uses, so the diagnostic and the decision can never disagree.

        ``None`` means no approved plan, or an approved plan with nothing for
        this work type. Both are ``NO_QUOTA``, and neither stops the work being
        created or counted - see :meth:`on_contributions_counted`.
        """
        return (await self._approved_quotas(user_id=user_id, period_id=period_id)).get(work_type_id)

    async def _approved_plan(
        self, *, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PrWorkPlan | None:
        """The one plan in force, or ``None``.

        ``APPROVED`` only. A ``DRAFT`` is somebody's working copy and decides
        nothing - if it did, a half-written plan would set somebody's KPI while
        it was still being argued about.
        """
        statement = select(PrWorkPlan).where(
            PrWorkPlan.user_id == user_id,
            PrWorkPlan.period_id == period_id,
            PrWorkPlan.status == PrWorkPlanStatus.APPROVED,
        )
        return (await self._session.execute(statement)).scalars().one_or_none()

    async def _approved_quotas(
        self, *, user_id: uuid.UUID, period_id: uuid.UUID
    ) -> dict[uuid.UUID, PrWorkQuota]:
        """The approved quotas for one person and period, keyed by work type."""
        return await self.quotas_of(await self._approved_plan(user_id=user_id, period_id=period_id))

    async def quotas_of(self, plan: PrWorkPlan | None) -> dict[uuid.UUID, PrWorkQuota]:
        """One plan version's quotas, keyed by work type.

        Keyed by work type because ``uq_pr_work_quotas_plan_type`` guarantees at
        most one per type per plan - which is what makes "which quota does this
        contribution match" a question with exactly one answer, in the database
        rather than in a validator somebody could forget to call.

        ``None`` yields an empty mapping, and an empty mapping is **not**
        unlimited eligibility: every candidate then allocates as ``NO_QUOTA``.
        """
        if plan is None:
            return {}
        rows = (
            await self._session.execute(select(PrWorkQuota).where(PrWorkQuota.plan_id == plan.id))
        ).scalars()
        return {row.work_type_id: row for row in rows}

    async def _counted_rows(
        self, *, user_id: uuid.UUID, period: PrReportingPeriod, lower: datetime, upper: datetime
    ) -> Sequence[tuple[PrWorkContribution, PrWorkItem, PrWorkType]]:
        """Every candidate, in the deterministic order, in one query.

        The ordering is ``counted_at``, then the contribution's ``created_at``,
        then its id - restated here in SQL so the rows arrive already ordered,
        and restated in :func:`~meobot.domain.pr.work_quota.candidate_sort_key`
        so the pure allocation function does not depend on a caller getting it
        right. The two agree, and a test asserts they do.
        """
        statement = (
            select(PrWorkContribution, PrWorkItem, PrWorkType)
            .join(PrWorkItem, PrWorkItem.id == PrWorkContribution.work_item_id)
            .join(PrWorkType, PrWorkType.id == PrWorkItem.work_type_id)
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
        return [(row[0], row[1], row[2]) for row in (await self._session.execute(statement)).all()]

    async def _write(
        self,
        *,
        user_id: uuid.UUID,
        period_id: uuid.UUID,
        desired: dict[uuid.UUID, PrWorkQuotaAllocation],
        moment: datetime,
    ) -> tuple[int, int, int, int]:
        """Make the stored allocations equal ``desired``. Returns the counters.

        Updates in place rather than delete-then-insert, so an allocation's
        ``created_at`` keeps saying when the contribution was first decided -
        which is half of "was this eligible yesterday".
        """
        # Matched on **either** key, and the second half is not belt and braces:
        # ``uq_pr_work_quota_allocations_contribution`` is global, so a
        # contribution whose ``counted_at`` moved into this period while its old
        # allocation still names the previous one would collide on insert. M1 has
        # no path that moves a ``counted_at``, so today this only ever matches
        # rows the first condition already found - but the projection has to stay
        # correct for the milestone that adds a correction workflow, and finding
        # out by way of an integrity error would be finding out too late.
        existing = {
            row.work_contribution_id: row
            for row in (
                await self._session.execute(
                    select(PrWorkQuotaAllocation).where(
                        or_(
                            and_(
                                PrWorkQuotaAllocation.user_id == user_id,
                                PrWorkQuotaAllocation.reporting_period_id == period_id,
                            ),
                            PrWorkQuotaAllocation.work_contribution_id.in_(desired)
                            if desired
                            else false(),
                        )
                    )
                )
            ).scalars()
        }
        created = updated = 0
        for contribution_id, wanted in desired.items():
            current = existing.pop(contribution_id, None)
            if current is None:
                self._session.add(wanted)
                created += 1
                continue
            changed = (
                current.quota_status is not wanted.quota_status
                or current.work_quota_id != wanted.work_quota_id
                or current.work_plan_id != wanted.work_plan_id
                or current.work_type_id != wanted.work_type_id
                or current.basis is not wanted.basis
                or current.unit is not wanted.unit
                or current.basis_amount != wanted.basis_amount
                or current.eligible_amount != wanted.eligible_amount
                or current.over_quota_amount != wanted.over_quota_amount
                or current.reason_code is not wanted.reason_code
            )
            changed = changed or current.reporting_period_id != wanted.reporting_period_id
            current.reporting_period_id = wanted.reporting_period_id
            current.user_id = wanted.user_id
            current.quota_status = wanted.quota_status
            current.work_quota_id = wanted.work_quota_id
            current.work_plan_id = wanted.work_plan_id
            current.work_type_id = wanted.work_type_id
            current.basis = wanted.basis
            current.unit = wanted.unit
            current.basis_amount = wanted.basis_amount
            current.eligible_amount = wanted.eligible_amount
            current.over_quota_amount = wanted.over_quota_amount
            # Cleared as well as set. A row that was ``UNMEASURABLE`` and is now
            # ``ELIGIBLE`` must not keep saying what used to be missing, and
            # ``ck_..._reason_matches_status`` refuses it if it tries.
            current.reason_code = wanted.reason_code
            current.evaluated_at = moment
            if changed:
                updated += 1

        # Whatever is left after the loop that also belongs to *this* pair. A row
        # matched only by the second condition above is another period's and is
        # not this recompute's to remove.
        existing = {
            key: row
            for key, row in existing.items()
            if row.user_id == user_id and row.reporting_period_id == period_id
        }
        removed = 0
        if existing:
            # Whatever is left is an allocation for a contribution that is no
            # longer a candidate - it stopped being ``COUNTED``, or its
            # ``counted_at`` moved out of the period. M1 has no path that does
            # either, which is why this is a handful of rows rather than a
            # correction workflow; the branch exists so the projection stays
            # correct when a later milestone adds one.
            await self._session.execute(
                delete(PrWorkQuotaAllocation).where(
                    PrWorkQuotaAllocation.id.in_([row.id for row in existing.values()])
                )
            )
            removed = len(existing)

        await self._session.flush()
        return len(desired), created, updated, removed

    async def _read(
        self, *, user_id: uuid.UUID, period: PrReportingPeriod
    ) -> list[ContributionEligibility]:
        """Every counted contribution in the period, with its decision or without.

        A **left outer join**, because the allocation may be missing - every
        contribution counted before M2 shipped is in that state until somebody
        reconciles the period.

        What a missing allocation means
        --------------------------------

        **Not ``NO_QUOTA``, automatically.** That was the semantic bug this patch
        exists to fix: ``NO_QUOTA`` is a business claim - *nobody set a target
        for this kind of work* - and an absent row is not evidence for it. So the
        read looks at the one thing it can establish without evaluating
        anything:

        * **no approved quota covers this work type** in this period -> the claim
          is true, and the row is reported as ``NO_QUOTA``. That is a lookup in a
          mapping the caller already loaded, not a second evaluator: nothing is
          ordered, no cap is filled and no amount is allocated;
        * **an approved quota does cover it** -> the honest answer is
          :attr:`~meobot.domain.pr.work_quota.PrWorkQuotaStatus.PENDING_EVALUATION`
          - *there is a target and nothing has looked at this yet*. Saying
          ``NO_QUOTA`` here would tell an employee their manager set no target
          when their manager did, and would send them to ask for a quota that
          already exists.

        A derived row carries **no amounts at all**. ``eligible_amount`` is not
        zero, it is unknown: nothing has decided, and a zero would be a decision.
        For the ``NO_QUOTA`` half the basis amount *is* known where the
        contribution is measurable, and null where it is not - the same rule the
        evaluator applies, so materialising the row changes the status and not
        the number.

        Reads materialise nothing. A screen refresh cannot change anybody's KPI,
        and this path has to keep working while a period is ``LOCKED``.
        """
        lower, upper = self._periods.bounds(period)
        # The quota map, for the one question above. Loaded once for the whole
        # read rather than per row, and used only to ask *"does a quota exist for
        # this work type"* - never to allocate.
        quotas = await self._approved_quotas(user_id=user_id, period_id=period.id)
        statement = (
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
        rows: list[ContributionEligibility] = []
        for contribution, item, work_type, allocation in (
            await self._session.execute(statement)
        ).all():
            assert contribution.counted_at is not None
            common = {
                "contribution_id": contribution.id,
                "work_item_id": item.id,
                "work_item_code": item.code,
                "work_item_title": item.title,
                "user_id": user_id,
                "work_type_id": work_type.id,
                "work_type_code": work_type.code,
                "work_type_name": work_type.name,
                "counted_at": contribution.counted_at,
            }
            if allocation is not None:
                # **The amount is the live measurement of the counted work**;
                # the allocation contributes the classification and the basis
                # it classified on. A stored ``basis_amount`` is M2's copy of
                # the same figure and can lag the ledger (a swallowed hand-off,
                # a period that was not open) - the actual must not.
                live = measure_counted(
                    contribution,
                    item,
                    basis=allocation.basis,
                    item_unit=item.unit,
                    quota_unit=allocation.unit,
                )
                rows.append(
                    ContributionEligibility(
                        **common,
                        quota_status=allocation.quota_status,
                        basis=allocation.basis,
                        unit=allocation.unit,
                        basis_amount=live.amount,
                        eligible_amount=allocation.eligible_amount,
                        over_quota_amount=allocation.over_quota_amount,
                        reason_code=allocation.reason_code,
                        is_materialised=True,
                        work_plan_id=allocation.work_plan_id,
                        work_quota_id=allocation.work_quota_id,
                        evaluated_at=allocation.evaluated_at,
                    )
                )
                continue

            quota = quotas.get(work_type.id)
            if quota is not None:
                # A target exists and nothing has evaluated this yet. The
                # **amount** is known - it is the counted work, measured on
                # the quota's basis - and belongs in the actual whether or not
                # M2 has classified it. The *split* is unknown: eligible and
                # over-quota stay ``None``, because a zero would be a decision
                # nobody took.
                live = measure_counted(
                    contribution,
                    item,
                    basis=quota.basis,
                    item_unit=item.unit,
                    quota_unit=quota.unit,
                )
                rows.append(
                    ContributionEligibility(
                        **common,
                        quota_status=PrWorkQuotaStatus.PENDING_EVALUATION,
                        basis=quota.basis,
                        unit=quota.unit,
                        basis_amount=live.amount,
                        eligible_amount=None,
                        over_quota_amount=None,
                        is_materialised=False,
                        work_plan_id=quota.plan_id,
                        work_quota_id=quota.id,
                    )
                )
                continue

            # No approved quota covers this work type: ``NO_QUOTA`` is the truth,
            # established by a lookup rather than an evaluation. The amount is
            # the real one where it can be stated and **null** where it cannot -
            # never zero, which would say the employee produced nothing.
            basis = work_type.default_quota_basis
            measurement = measure_counted(contribution, item, basis=basis, item_unit=item.unit)
            rows.append(
                ContributionEligibility(
                    **common,
                    quota_status=PrWorkQuotaStatus.NO_QUOTA,
                    basis=basis,
                    unit=work_type.default_unit if basis is PrWorkQuotaBasis.QUANTITY else None,
                    basis_amount=measurement.amount,
                    eligible_amount=ZERO,
                    over_quota_amount=ZERO,
                    is_materialised=False,
                )
            )
        return rows

    async def _types_for(self, type_ids: set[uuid.UUID]) -> Sequence[PrWorkType]:
        """The work types a summary has a row for, in the taxonomy's own order.

        The union of *what was counted* and *what was planned*, so a quota with
        no counted work yet - a target of 20 with nothing done - still appears.
        That row is usually the most useful one on the screen.
        """
        if not type_ids:
            return []
        statement = (
            select(PrWorkType)
            .where(PrWorkType.id.in_(type_ids))
            .order_by(
                PrWorkType.category.asc(), PrWorkType.display_order.asc(), PrWorkType.name.asc()
            )
        )
        return (await self._session.execute(statement)).scalars().all()

    async def _users_with_work_in(self, period: PrReportingPeriod) -> Sequence[uuid.UUID]:
        """Everybody with counted work in one period.

        The default subject list for a reconciliation, so an operator asking to
        reconcile September does not have to name twenty people - and so nobody
        is missed because their name was not on the list.
        """
        lower, upper = self._periods.bounds(period)
        statement = (
            select(PrWorkContribution.user_id)
            .join(PrWorkItem, PrWorkItem.id == PrWorkContribution.work_item_id)
            .where(
                PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
                counted_in_period(period, lower, upper),
            )
            .distinct()
        )
        return list((await self._session.execute(statement)).scalars().all())

    @staticmethod
    def _unit_for(
        basis: PrWorkQuotaBasis, *, quota: PrWorkQuota | None, work_type: PrWorkType
    ) -> PrWorkUnit | None:
        """What the amounts are in. ``None`` for ``ITEM_COUNT``, always.

        ``ITEM_COUNT`` amounts are counts of contributions, and putting a unit on
        them would print a number of comments that was really a number of jobs.
        """
        if basis is not PrWorkQuotaBasis.QUANTITY:
            return None
        if quota is not None:
            return quota.unit
        return work_type.default_unit


def _total(amounts: Iterable[Decimal | None]) -> Decimal:
    """Sum the amounts that exist, and **skip the ones that do not**.

    ``None`` is not zero here, and treating it as zero is the whole failure this
    patch is about: a contribution whose quantity nobody recorded is work that
    happened, and adding it in as nothing would put a smaller number on an
    employee's screen than the work they did. The count of measurable rows -
    ``measured_contributions`` - is what makes the gap visible instead.
    """
    return quantise_quota(sum((amount for amount in amounts if amount is not None), ZERO))


def assert_period_open(period: PrReportingPeriod) -> None:
    """Raise unless this period may still have its eligibility recomputed.

    The immutability rule, in one function so that every path refuses in the
    same words: ``OPEN`` recomputes, ``CLOSED`` and ``LOCKED`` do not, and there
    is no third answer and no flag that produces one.
    """
    if period.status is PrPeriodStatus.OPEN:
        return
    raise PrWorkPeriodNotOpenError(
        "Eligibility is not recalculated for a period that has been closed",
        details={
            "reason": "period_not_open",
            "period_id": str(period.id),
            "period_code": period.code,
            "period_status": period.status.value,
        },
    )


__all__: list[str] = [
    "MAX_RECONCILE_USERS",
    "ContributionEligibility",
    "EvaluationOutcome",
    "PrWorkQuotaEligibilityService",
    "QuotaPeriodSummary",
    "QuotaTypeProgress",
    "ReconcileOutcome",
    "assert_period_open",
    "counted_in_period",
]
