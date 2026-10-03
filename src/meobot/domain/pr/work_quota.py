"""Quota eligibility: which counted work belongs to an approved KPI plan. M2.

This module is the whole of M2's *arithmetic and vocabulary*. It knows nothing
about SQL, HTTP or Telegram, which is what lets the allocation rule be argued
about in one place and tested without a database - the same split
:mod:`meobot.domain.pr.work` already makes for M1.

The one idea this module exists to protect
-------------------------------------------

**COUNTED != ELIGIBLE != SCORED.**

* ``COUNTED`` is M1's word. Real, valid, completed work that somebody who did
  not do it independently validated;
* ``ELIGIBLE`` is M2's word. Counted work that falls inside an **approved**
  quota for that person, that work type and that reporting period;
* ``SCORED`` **does not exist**. No milestone has awarded a point, and this
  module contains no ``base_score``, no multiplier, no ``awarded_score`` and no
  ``points``. That is M6's, and naming eligible work "scored" now would be
  shipping the confusion the three-way split exists to prevent.

Absence is not permission
--------------------------

The single most important rule here is :attr:`PrWorkQuotaStatus.NO_QUOTA`.
Counted work with **no approved quota** for its person, type and period is not
unlimited eligibility - it is real work with no KPI decision attached to it. If
missing quota meant unlimited, the cheapest route to an unbounded KPI would be
to do work in a category nobody set a target for, and the absence of a target
is nearly always the absence of a decision rather than permission.

``NO_QUOTA`` and ``OVER_QUOTA`` are therefore different values with different
sentences on screen: the second means *there is a cap and it is used up*, the
first means *nobody set a cap*.

Partial allocation, and why it is not optional
-----------------------------------------------

A ``QUANTITY`` quota is consumed by amounts, not by rows, so a contribution
that straddles the cap is **split**: the part that fits is eligible, the rest is
over quota, and its status is
:attr:`PrWorkQuotaStatus.PARTIALLY_ELIGIBLE`. Without that, two employees who
did the same 120 comments would get different KPI results purely from how they
chose to split the job into work items - which would make the shape of the
paperwork worth more than the work.

Every amount is a :class:`~decimal.Decimal`. Quota capacity is money-adjacent
arithmetic and binary floating point does not add up.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from meobot.core.time import ensure_utc
from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.work import PrWorkUnit


class PrWorkQuotaBasis(StrEnum):
    """How a quota measures the work it caps.

    Two, and the pair is the reason M2 can measure the work the department
    actually does rather than the shape of its paperwork.

    The basis belongs to the **work type** first - see
    :attr:`~meobot.db.models.pr_work.PrWorkType.default_quota_basis` - and a
    quota merely restates it. That ordering is deliberate: how a kind of work is
    measured is a fact about the work, not a per-employee negotiation, and two
    people's plans measuring one work type differently would make a
    department-wide figure meaningless.
    """

    #: One **counted contribution** is one quota unit, whatever the work item's
    #: ``quantity`` says and **whatever ``credit_weight`` says**. Twenty
    #: short-video scripts is ``target_value = 20``.
    #:
    #: ``credit_weight`` is deliberately not applied here: a shared shoot is one
    #: work item and three people's contributions, and each of those three did
    #: one job. Multiplying by a weight would make "how much of this counts
    #: towards my plan" depend on a field whose purpose is to record a *minor*
    #: share, not to divide an item.
    ITEM_COUNT = "ITEM_COUNT"
    #: The work item's ``quantity``, in its unit, times the contributor's
    #: ``credit_weight``. "100 comments" is **one** work item with
    #: ``quantity = 100``, and this basis reads 100 rather than 1 - which is
    #: exactly what stops a department noticing that filing a hundred rows pays
    #: better than filing one honest one.
    QUANTITY = "QUANTITY"


class PrWorkPlanStatus(StrEnum):
    """How far one employee's KPI plan for one period has got.

    Four states, and the important property is that **only one of them
    participates in eligibility**. A ``DRAFT`` plan is somebody's working copy:
    it may be edited freely and it decides nothing, because a quota that took
    effect while it was still being written would let a half-finished plan set
    somebody's KPI.
    """

    #: Being written. Editable, and **invisible to the evaluator**.
    DRAFT = "DRAFT"
    #: Approved and in force. At most one per employee per period, enforced by a
    #: partial unique index rather than by a service everybody has to remember.
    #: **Immutable** - a change is a revision, never an edit.
    APPROVED = "APPROVED"
    #: Replaced by a later approved version. Kept for ever, because an
    #: allocation made under it has to stay explainable.
    SUPERSEDED = "SUPERSEDED"
    #: A draft somebody abandoned. Not a deletion: the row and its audit trail
    #: stay, so "who proposed this and who dropped it" is still answerable.
    DISCARDED = "DISCARDED"


class PrPlanReviewState(StrEnum):
    """**Where a draft stands between its author and its approver.**

    KPI self-service. A ``DRAFT`` is one lifecycle status and three working
    situations, and the three are told apart by two timestamps rather than by
    widening the status - see migration ``0038`` for why. Derived, never
    stored: :func:`plan_review_state` is the one place the reading lives.

    ``None`` for anything that is not a draft: an approved plan is not "under
    review", it is in force.
    """

    #: Being written by the employee or a manager. Editable by both.
    EDITING = "EDITING"
    #: Handed to the manager. The employee may not edit; a manager may correct
    #: it and approve, or send it back.
    SUBMITTED = "SUBMITTED"
    #: Sent back with a note. Editable by the employee again; the same version.
    RETURNED = "RETURNED"


def plan_review_state(
    status: PrWorkPlanStatus, *, submitted_at: object | None, returned_at: object | None
) -> PrPlanReviewState | None:
    """The draft's working situation, from its status and the two instants.

    A submission always wins over a stale return: the service clears one when
    it sets the other, so a row carrying both is a row written by something
    other than the service, and reading it as *submitted* is the safe
    direction - it locks the employee out rather than letting a plan a manager
    is reviewing change under them.
    """
    if status is not PrWorkPlanStatus.DRAFT:
        return None
    if submitted_at is not None:
        return PrPlanReviewState.SUBMITTED
    if returned_at is not None:
        return PrPlanReviewState.RETURNED
    return PrPlanReviewState.EDITING


class PrPlanReadinessBlocker(StrEnum):
    """Why a draft may not be submitted or approved yet. Stable reason codes.

    **One list for both acts.** Submission asks the readiness question so an
    employee cannot send a manager a plan nobody could approve; approval asks
    it again under the lock, because a work type may have been retired or a
    period closed in between. Two lists would drift, and the drift would be a
    plan that was accepted for review and then refused for a reason the
    employee was never shown.

    The values are the ``details.reason`` codes the plan service has always
    raised for the same conditions, so a client that already maps them keeps
    mapping them.
    """

    PERIOD_NOT_OPEN = "period_not_open"
    SUBJECT_INACTIVE = "subject_inactive"
    PLAN_HAS_NO_QUOTAS = "plan_has_no_quotas"
    WORK_TYPE_INACTIVE = "work_type_inactive"
    QUOTA_UNIT_MISMATCH = "quota_unit_mismatch"
    QUOTA_BOUNDS_INVALID = "quota_bounds_invalid"


#: Plan statuses nothing moves out of.
TERMINAL_PLAN_STATUSES: frozenset[PrWorkPlanStatus] = frozenset(
    {PrWorkPlanStatus.SUPERSEDED, PrWorkPlanStatus.DISCARDED}
)


class PrWorkQuotaStatus(StrEnum):
    """What the quota engine says about one counted contribution.

    Six values. **Five are stored**; the sixth is something only a *read* can
    report, and the database refuses to hold it - see
    :data:`MATERIALISABLE_QUOTA_STATUSES`.

    There is no ``SCORED``, because nothing has been scored - see the module
    docstring. M6 owns points.

    Why "no allocation row" is not a status
    ----------------------------------------

    Until this patch there were four values and the read path filled the gap by
    treating **any** missing allocation as :attr:`NO_QUOTA`. That was wrong, and
    wrong in the direction that matters: ``NO_QUOTA`` is a **business claim** -
    *nobody has set a target for this kind of work* - and an absent row is not
    evidence for it. An absent row means one of three different things:

    * no approved quota covers the work type, so ``NO_QUOTA`` is genuinely
      right - and a read can establish that with a lookup rather than an
      evaluation;
    * a quota **does** cover it and nothing has evaluated the contribution yet.
      That is :attr:`PENDING_EVALUATION`, and calling it ``NO_QUOTA`` would tell
      an employee their manager set no target when their manager did;
    * a quota covers it and the contribution **cannot be measured**. That is
      :attr:`UNMEASURABLE`, and it is a materialised row rather than an absence.
    """

    #: Real, counted, valid work with **no approved quota** for this person,
    #: work type and period. ``eligible_amount`` is zero, and that is a decision
    #: nobody has taken rather than a cap somebody exceeded.
    #:
    #: Wins over :attr:`UNMEASURABLE` when both would apply: if nobody set a
    #: target, *why* the work cannot be measured against it is not the useful
    #: sentence, and asking somebody to fix a quantity for a quota that does not
    #: exist would be sending them to do pointless work.
    NO_QUOTA = "NO_QUOTA"
    #: An approved quota covers this work type, and the contribution **cannot be
    #: measured against it** - the work item has no quantity, or its quantity is
    #: not a positive amount, or it is recorded in a different unit from the one
    #: the quota caps.
    #:
    #: **Still legitimate completed workload.** Not rejected, not invalid, not
    #: excluded and not unapproved: the contribution is ``COUNTED`` and stays
    #: ``COUNTED``. What is missing is a number, and somebody can supply it -
    #: which is why ``reason_code`` is required on the row and the screen says
    #: *"Chưa thể tính hạn mức — thiếu số lượng công việc."*
    #:
    #: A **business state**, never an infrastructure one. An evaluator that
    #: crashes unexpectedly does not produce this - see
    #: :func:`measure_contribution`.
    UNMEASURABLE = "UNMEASURABLE"
    #: Entirely inside the approved eligibility cap.
    ELIGIBLE = "ELIGIBLE"
    #: ``QUANTITY`` only. Part of this contribution fitted inside the remaining
    #: capacity and part did not.
    PARTIALLY_ELIGIBLE = "PARTIALLY_ELIGIBLE"
    #: An approved quota exists and its eligibility capacity is exhausted.
    #: Still ``COUNTED``, still real work, still in the workload.
    OVER_QUOTA = "OVER_QUOTA"
    #: **Read-only.** An approved quota covers this work type and no allocation
    #: has been written yet: the contribution predates M2, or its period has not
    #: been reconciled since the plan was approved.
    #:
    #: Never stored. The evaluator materialises one of the five above and
    #: nothing else, and
    #: ``ck_pr_work_quota_allocations_status_is_materialisable`` is the database
    #: saying so - so this value cannot leak into a row and be mistaken for a
    #: decision. It exists because the honest answer to *"what does the quota say
    #: about this?"* is sometimes **"nothing yet"**, and the previous answer -
    #: ``NO_QUOTA`` - was a different sentence that happened to look the same.
    PENDING_EVALUATION = "PENDING_EVALUATION"


#: The statuses an allocation row may hold. Everything except
#: :attr:`PrWorkQuotaStatus.PENDING_EVALUATION`, which describes the **absence**
#: of a row and could not describe one without contradicting itself.
MATERIALISABLE_QUOTA_STATUSES: frozenset[PrWorkQuotaStatus] = frozenset(
    {
        PrWorkQuotaStatus.NO_QUOTA,
        PrWorkQuotaStatus.UNMEASURABLE,
        PrWorkQuotaStatus.ELIGIBLE,
        PrWorkQuotaStatus.PARTIALLY_ELIGIBLE,
        PrWorkQuotaStatus.OVER_QUOTA,
    }
)

#: The statuses that mean *an approved quota measured this and reached a
#: number*. ``NO_QUOTA`` is outside it because nothing decided;
#: ``UNMEASURABLE`` because a decision was attempted and could not be reached;
#: ``PENDING_EVALUATION`` because nothing has looked yet.
DECIDED_QUOTA_STATUSES: frozenset[PrWorkQuotaStatus] = frozenset(
    {
        PrWorkQuotaStatus.ELIGIBLE,
        PrWorkQuotaStatus.PARTIALLY_ELIGIBLE,
        PrWorkQuotaStatus.OVER_QUOTA,
    }
)

#: The statuses that carry no measurable amount, so a report must **count** them
#: rather than sum them. Summing a missing quantity as zero would say the
#: employee produced nothing, which is exactly what they did not do.
UNMEASURED_QUOTA_STATUSES: frozenset[PrWorkQuotaStatus] = frozenset(
    {PrWorkQuotaStatus.UNMEASURABLE, PrWorkQuotaStatus.PENDING_EVALUATION}
)


class PrWorkUnmeasurableReason(StrEnum):
    """Why an approved quota could not measure a counted contribution.

    Stable machine codes, and **only failure modes the evaluator actually
    produces**. Each one below has a branch in :func:`measure_contribution`; a
    code with no branch would be a promise the module cannot keep, and a client
    would draw a sentence nobody can ever see.

    Deliberately **not** a place for an exception message. A raw error string is
    an implementation detail that changes when somebody rewords a docstring, and
    storing one as business state would put a stack trace on an employee's KPI
    screen. There is also **no ``EVALUATION_ERROR``**: an unexpected failure is
    not a business state and must not be dressed as one - see
    :mod:`meobot.application.pr_work_quota_service` on the split.
    """

    #: A ``QUANTITY``-measured work type, and the work item carries no quantity
    #: at all. The commonest case by far, and the one somebody can fix.
    MISSING_QUANTITY = "MISSING_QUANTITY"
    #: A quantity exists but the contribution's share of it is not a positive
    #: amount - a tiny quantity against a tiny credit weight that rounds away to
    #: nothing at :data:`QUOTA_SCALE`. Recording it as zero would let a
    #: contribution be "eligible" for nothing at all.
    INVALID_QUANTITY = "INVALID_QUANTITY"
    #: The work item's unit is not the unit the quota caps. M1 copies the work
    #: type's unit onto each item at creation and the quota's unit is validated
    #: against the same type, so the two agree for everything either milestone
    #: writes - and the evaluator checks rather than assumes, because *"do not
    #: let a work item's unit bypass the quota"* has to be a comparison
    #: somewhere or it is only a comment.
    UNIT_MISMATCH = "UNIT_MISMATCH"


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

#: The scale every quota and allocation amount is stored and computed at.
#:
#: Two decimal places, matching ``PrWorkItem.quantity``'s ``Numeric(12, 2)``:
#: a half-day shoot is ``0.5 DAY`` and nothing the department does is measured
#: finer than a hundredth of a unit. Products of a quantity and a
#: ``Numeric(5, 4)`` credit weight are quantised back to this, once, at the
#: point the amount is computed - so the number in the column is the number the
#: arithmetic used.
QUOTA_SCALE = Decimal("0.01")

#: The largest target or cap a quota may carry.
#:
#: Bounded for the reason ``MAX_WORK_QUANTITY`` is: a cap is what decides how
#: much of somebody's work is eligible, and an unbounded one is not a decision.
#: A million comments a month is already three times what the department could
#: physically do.
MAX_QUOTA_VALUE = Decimal("1000000")

#: The smallest meaningful target or cap. Zero is not a small quota - it is
#: "none of this work is eligible", which is what *not approving a quota* says,
#: and saying it twice in two different ways would make the two disagree.
MIN_QUOTA_VALUE = Decimal("0.01")


def quantise_quota(value: Decimal) -> Decimal:
    """Round one amount to :data:`QUOTA_SCALE`, half up.

    Applied **once**, where an amount is first computed, and never again on the
    way out. Rounding at read time is how two screens end up disagreeing about
    the same row by a hundredth.
    """
    return value.quantize(QUOTA_SCALE, rounding=ROUND_HALF_UP)


def assert_quota_bounds(target_value: Decimal, eligibility_cap: Decimal) -> None:
    """Raise unless the pair is a quota somebody could act on.

    Three rules, and the third is the one worth stating out loud:

    * ``target_value > 0`` - a target of nothing is not a target;
    * ``eligibility_cap > 0`` - likewise, and a cap of zero would be an
      unauditable way of saying "none of your work counts";
    * ``eligibility_cap >= target_value`` - a cap **below** the target would
      mean the plan asks for more work than it is willing to call eligible,
      which is not a KPI plan anybody could satisfy.

    Neither value may be ``NULL``: see :data:`MIN_QUOTA_VALUE`. M2 never reads
    an absent cap as unlimited.
    """
    for field, value in (("target_value", target_value), ("eligibility_cap", eligibility_cap)):
        if value < MIN_QUOTA_VALUE:
            raise PrValidationError(
                f"{field} must be greater than zero",
                details={"field": field, "reason": "quota_value_not_positive"},
            )
        if value > MAX_QUOTA_VALUE:
            raise PrValidationError(
                f"{field} is above the maximum a quota may carry",
                details={
                    "field": field,
                    "reason": "quota_value_too_large",
                    "maximum": str(MAX_QUOTA_VALUE),
                },
            )
    if eligibility_cap < target_value:
        raise PrValidationError(
            "eligibility_cap must be at least target_value",
            details={
                "field": "eligibility_cap",
                "reason": "cap_below_target",
                "target_value": str(target_value),
                "eligibility_cap": str(eligibility_cap),
            },
        )


def assert_quota_unit(
    basis: PrWorkQuotaBasis,
    unit: PrWorkUnit | None,
    *,
    type_basis: PrWorkQuotaBasis,
    type_unit: PrWorkUnit,
    work_type_code: str,
) -> None:
    """Raise unless this quota measures its work type the way the type is measured.

    **The work type is the semantic authority**, and this is where that sentence
    becomes a refusal rather than a convention. Two checks:

    * the quota's basis must be the work type's basis. A ``QUANTITY`` cap on a
      type nobody measures by quantity has no unit that means anything, and an
      ``ITEM_COUNT`` cap on seeding comments would score a hundred comments as
      one;
    * a ``QUANTITY`` quota's unit must be the type's ``default_unit``. That unit
      is copied onto each work item at creation and is not the filer's choice,
      so pinning the quota to it is what stops a quota being written in units
      the work is never recorded in - ``2000 VIDEO`` against a type that counts
      comments would silently never fill.

    ``ITEM_COUNT`` carries **no unit at all**. Its amounts are counts of
    contributions, and a unit on them would be a number of comments that was
    really a number of jobs.
    """
    if basis is not type_basis:
        raise PrValidationError(
            "The quota basis does not match how this work type is measured",
            details={
                "field": "basis",
                "reason": "basis_does_not_match_work_type",
                "work_type_code": work_type_code,
                "expected": type_basis.value,
                "requested": basis.value,
            },
        )
    if basis is PrWorkQuotaBasis.ITEM_COUNT:
        if unit is not None:
            raise PrValidationError(
                "An ITEM_COUNT quota counts contributions and takes no unit",
                details={"field": "unit", "reason": "unit_not_allowed_for_item_count"},
            )
        return
    if unit is None:
        raise PrValidationError(
            "A QUANTITY quota must say what it counts",
            details={
                "field": "unit",
                "reason": "unit_required_for_quantity",
                "expected": type_unit.value,
            },
        )
    if unit is not type_unit:
        raise PrValidationError(
            "The quota unit does not match the work type's unit",
            details={
                "field": "unit",
                "reason": "unit_does_not_match_work_type",
                "work_type_code": work_type_code,
                "expected": type_unit.value,
                "requested": unit.value,
            },
        )


# ---------------------------------------------------------------------------
# The allocation rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuotaCandidate:
    """One counted contribution, reduced to what the allocation rule needs.

    Deliberately not a database row. The ordering keys are carried explicitly
    so that :func:`allocate` can be argued with - and tested - without a
    session, and so the ordering is stated in one place rather than implied by
    whatever a query happened to return.
    """

    contribution_id: uuid.UUID
    #: **The period-attribution instant.** M1 stamps one reading for every
    #: contribution on an item, so a three-person shoot cannot straddle two
    #: months.
    counted_at: datetime
    #: The contribution row's own birthday. The second ordering key.
    created_at: datetime
    #: What this contribution is worth against the quota, already quantised.
    #: ``1`` for ``ITEM_COUNT``; ``quantity * credit_weight`` for ``QUANTITY``.
    basis_amount: Decimal


@dataclass(frozen=True, slots=True)
class QuotaAllocation:
    """What the rule decided about one candidate.

    ``basis_amount == eligible_amount + over_quota_amount`` always holds, which
    is the invariant that makes a report add up: every unit of counted work is
    on exactly one side of the cap.
    """

    contribution_id: uuid.UUID
    quota_status: PrWorkQuotaStatus
    basis_amount: Decimal
    eligible_amount: Decimal
    over_quota_amount: Decimal


def candidate_sort_key(candidate: QuotaCandidate) -> tuple[datetime, datetime, str]:
    """The deterministic order quota capacity is filled in.

    ``counted_at`` first, then the contribution's ``created_at``, then its id.

    The first key is the rule an employee can predict and the department can
    explain: **first validated, first inside the quota**. The second and third
    exist only to make the order *total* - ``counted_at`` is one instant for
    every contribution on one work item, so a three-person shoot would otherwise
    tie three ways, and a tie is where a repeated evaluation stops being
    reproducible. The id is a last resort that is guaranteed to be unique.

    Sorting on the id's string rather than on the UUID keeps SQL's
    ``ORDER BY id`` and Python's ``sorted`` agreeing on PostgreSQL, which
    compares ``uuid`` values in that same textual order.

    **Both instants are normalised through :func:`~meobot.core.time.ensure_utc`
    first**, and this is the one place in M2 that has to be. The module's
    convention is that a stored timestamp is aware UTC, but a driver decides
    what comes back: SQLite has no timezone type and returns the same column
    naive, so one evaluation can hold a freshly written ``counted_at`` that is
    still the aware value Python flushed beside one re-read from the database
    that is not. Comparing those two raises ``TypeError`` and the whole
    evaluation is lost.

    Normalising here rather than at each call site is deliberate: this function
    **is** the ordering boundary, and both callers reach the datetimes only
    through it. Normalising in the ORM or on the way in would change what is
    persisted to fix a comparison, and stripping the offsets instead would fix
    the exception by throwing away the fact that makes the order right - see the
    two-timezone case in ``test_06b``.
    """
    return (
        ensure_utc(candidate.counted_at),
        ensure_utc(candidate.created_at),
        str(candidate.contribution_id),
    )


def allocate(
    candidates: Sequence[QuotaCandidate], *, eligibility_cap: Decimal | None
) -> list[QuotaAllocation]:
    """Fill ``eligibility_cap`` from ``candidates`` in the deterministic order.

    The whole of M2's arithmetic, and it is a pure function on purpose: given
    the same candidates and the same cap it returns the same allocations,
    however many times it runs and whatever else has happened to the database.
    That is what makes reconciliation idempotent by construction rather than by
    a repair script.

    Args:
        candidates: Counted contributions for one person, one period and one
            work type. Sorted here rather than by the caller, so no caller can
            get the order subtly wrong.
        eligibility_cap: The approved cap, or ``None`` when **no approved
            quota** exists. ``None`` is not unlimited - it produces
            :attr:`PrWorkQuotaStatus.NO_QUOTA` for every candidate, with the
            real amount recorded and nothing eligible. See the module
            docstring.

    Returns:
        One allocation per candidate, in the order the cap was filled.
    """
    if eligibility_cap is None:
        return [
            QuotaAllocation(
                contribution_id=candidate.contribution_id,
                quota_status=PrWorkQuotaStatus.NO_QUOTA,
                # The real amount, not zero. The work happened, it is in the
                # workload, and a screen that showed it as nothing would be
                # describing a different fact from the one M2 is recording.
                basis_amount=candidate.basis_amount,
                eligible_amount=Decimal("0.00"),
                over_quota_amount=Decimal("0.00"),
            )
            for candidate in sorted(candidates, key=candidate_sort_key)
        ]

    remaining = eligibility_cap
    allocations: list[QuotaAllocation] = []
    for candidate in sorted(candidates, key=candidate_sort_key):
        amount = candidate.basis_amount
        # `min` on Decimals, spelled out because the partial case is the point:
        # what fits, fits. All-or-nothing here would make two employees who did
        # the same work get different KPI results from how they split it up.
        eligible = amount if amount <= remaining else remaining
        if eligible < Decimal("0"):
            eligible = Decimal("0.00")
        over = amount - eligible
        remaining -= eligible
        allocations.append(
            QuotaAllocation(
                contribution_id=candidate.contribution_id,
                quota_status=_status_for(eligible=eligible, over=over),
                basis_amount=amount,
                eligible_amount=quantise_quota(eligible),
                over_quota_amount=quantise_quota(over),
            )
        )
    return allocations


def _status_for(*, eligible: Decimal, over: Decimal) -> PrWorkQuotaStatus:
    """Which of the three *decided* statuses an eligible/over split is.

    Never ``NO_QUOTA``: reaching here means an approved quota looked at this
    contribution and said something, even if what it said was "none of it".
    """
    if over <= Decimal("0"):
        return PrWorkQuotaStatus.ELIGIBLE
    if eligible <= Decimal("0"):
        return PrWorkQuotaStatus.OVER_QUOTA
    return PrWorkQuotaStatus.PARTIALLY_ELIGIBLE


@dataclass(frozen=True, slots=True)
class Measurement:
    """What one counted contribution is worth, or why it cannot be said.

    Exactly one of the two fields is set, and the pair is the whole of this
    patch's point: measuring a contribution has **two** honest outcomes, and the
    previous signature - a ``Decimal`` or an exception - had room for only one
    of them.

    An exception was the wrong shape. A work item filed under a quantity-measured
    type with no quantity is not a *failure*: it is real, validated, counted work
    whose eligibility nobody can compute yet, and somebody can fix it by typing a
    number. Raising made the caller choose between aborting an employee's whole
    period and swallowing the row, and it took the second - which is how the
    contribution ended up with no allocation at all and was read back as
    ``NO_QUOTA``, the one thing it definitely was not.
    """

    #: The contribution's share, quantised, when it can be stated. ``None``
    #: exactly when :attr:`reason` is set.
    amount: Decimal | None = None
    #: Why it cannot. ``None`` exactly when :attr:`amount` is set.
    reason: PrWorkUnmeasurableReason | None = None

    @property
    def is_measurable(self) -> bool:
        return self.amount is not None


def measure_contribution(
    basis: PrWorkQuotaBasis,
    *,
    quantity: Decimal | None,
    credit_weight: Decimal,
    item_unit: PrWorkUnit | None = None,
    quota_unit: PrWorkUnit | None = None,
) -> Measurement:
    """What one counted contribution is worth against its quota basis.

    **Returns rather than raises**, whatever the data says. Every branch below is
    a state an employee's row can legitimately be in, and none of them is an
    error - see :class:`Measurement`.

    ``ITEM_COUNT`` is **one**, flatly, and ``credit_weight`` is not consulted -
    see :attr:`PrWorkQuotaBasis.ITEM_COUNT` for why. It is always measurable:
    one valid contribution is one item, whatever else is missing from the row.

    ``QUANTITY`` is ``quantity * credit_weight``, quantised once, and three
    things can stop it:

    * **no quantity** - :attr:`~PrWorkUnmeasurableReason.MISSING_QUANTITY`.
      Never read as one: silently treating a data-entry mistake as a single unit
      would turn it into a hundred comments' worth of KPI, which is the incentive
      the whole basis split exists to remove;
    * **the wrong unit** - :attr:`~PrWorkUnmeasurableReason.UNIT_MISMATCH`.
      Checked rather than assumed. M1 copies the work type's unit onto the item
      and M2 validates the quota's unit against the same type, so they agree for
      everything either milestone writes; the comparison is here so that *"a work
      item's unit cannot bypass the quota"* is a branch rather than a comment;
    * **an amount that rounds away to nothing** -
      :attr:`~PrWorkUnmeasurableReason.INVALID_QUANTITY`. A contribution worth
      ``0.00`` is not eligible for zero; it is a row whose measurement failed,
      and calling it ``ELIGIBLE`` for nothing would put a meaningless number in a
      cap.

    Args:
        basis: How the quota measures this work type.
        quantity: The **work item's** quantity. ``None`` is a supported input.
        credit_weight: This contributor's share. Applied only for ``QUANTITY``.
        item_unit: The unit on the work item. Compared with ``quota_unit``.
        quota_unit: The unit the quota caps. When either is ``None`` the
            comparison is skipped - there is nothing to disagree about, and a
            ``QUANTITY`` quota always has one by construction.
    """
    if basis is PrWorkQuotaBasis.ITEM_COUNT:
        return Measurement(amount=Decimal("1.00"))
    if quantity is None:
        return Measurement(reason=PrWorkUnmeasurableReason.MISSING_QUANTITY)
    if item_unit is not None and quota_unit is not None and item_unit is not quota_unit:
        return Measurement(reason=PrWorkUnmeasurableReason.UNIT_MISMATCH)
    amount = quantise_quota(quantity * credit_weight)
    if amount <= Decimal("0"):
        return Measurement(reason=PrWorkUnmeasurableReason.INVALID_QUANTITY)
    return Measurement(amount=amount)


__all__: list[str] = [
    "DECIDED_QUOTA_STATUSES",
    "MATERIALISABLE_QUOTA_STATUSES",
    "MAX_QUOTA_VALUE",
    "MIN_QUOTA_VALUE",
    "QUOTA_SCALE",
    "TERMINAL_PLAN_STATUSES",
    "UNMEASURED_QUOTA_STATUSES",
    "Measurement",
    "PrPlanReadinessBlocker",
    "PrPlanReviewState",
    "PrWorkPlanStatus",
    "PrWorkQuotaBasis",
    "PrWorkQuotaStatus",
    "PrWorkUnmeasurableReason",
    "QuotaAllocation",
    "QuotaCandidate",
    "allocate",
    "assert_quota_bounds",
    "assert_quota_unit",
    "candidate_sort_key",
    "measure_contribution",
    "plan_review_state",
    "quantise_quota",
]
