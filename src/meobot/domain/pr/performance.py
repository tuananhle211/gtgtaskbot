"""Scoring, monthly review and performance arithmetic. M6.

Every rule that turns validated work into a performance figure, and none of the
SQL that stores it. The module is deliberately pure: a barem is an argument the
business has, and an argument is easier to have about a table of numbers than
about a query.

The seven things this module keeps apart
-----------------------------------------

They are not synonyms, and collapsing any adjacent pair is how a performance
system stops meaning anything:

``WORK`` → ``COUNTED`` → **eligible** (M2) → **standard minutes** (M6 workload)
→ **manager review** (quality, timeliness, contribution) → **performance index**
→ **performance band**.

M1 answers *did this work happen and did somebody independent confirm it*. M2
answers *is it inside an approved quota*. M6 answers *how much standard work was
that, how did the manager judge the month, and what does the pair come to*. No
step reaches back into the one before it.

**And the chain stops there.** M6 scores and reports performance; it does not
decide, calculate or allocate money. A performance index is an *evaluation
result*, not a salary multiplier, and the department head allocates performance
pay as a separate management decision outside MeoChat. Nothing in this module
converts an index into an amount, and nothing should be added that does - a
number that silently became a coefficient would turn a judgement about somebody's
month into a promise about their pay.

One workload point is one standard minute
------------------------------------------

The company's convention, and the reason nothing here is called ``score`` or
``points`` when it means workload. ``300`` standard minutes is a KPI workday;
twenty-five of them is ``7500``, which is 100% of a month's capacity. A field
called ``points`` would invite somebody to add a manager's ``105`` to an
editor's ``90`` and get ``195`` of nothing.

The one dimension that gates
-----------------------------

Quality, and only quality. A month of high-volume bad work is not a good month,
so quality caps the final index. Timeliness and contribution carry 10% each and
no gate: a person who was late but produced excellent work should lose ten
weighted points, not have their month capped - and a hard gate on three
dimensions is three ways for one bad judgement to erase a month.

Three judgements, once a month, per person
-------------------------------------------

**Not per work item.** Twenty employees is twenty review forms a month; the same
model applied per contribution would be two thousand, which is a system nobody
fills in and therefore a system that scores nothing. See
:class:`PrPerformanceReviewDimension`.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from types import MappingProxyType

#: The company's unit of workload. One point is one standard productive minute.
#: Named here once so a screen and a service quote the same sentence.
STANDARD_MINUTE_NOTE = "1 điểm workload = 1 phút chuẩn."

#: A KPI workday, in standard minutes. The policy's default, not a constant the
#: engine may assume - see :class:`PerformancePolicySnapshot`.
DEFAULT_DAILY_TARGET_MINUTES = 300

#: Minutes and scores are exact. Every quantity in this module is a ``Decimal``
#: and none of them is ever built from a ``float``: a workload figure that is a
#: hundredth out because 0.1 is not 0.1 is a figure somebody disputes.
MINUTES_QUANTUM = Decimal("0.01")
INDEX_QUANTUM = Decimal("0.01")
#: Workload is reported to **one** decimal, and the index to two.
#:
#: Not a cosmetic choice: it is the arithmetic the specification's own worked
#: example uses. 7820 eligible minutes against a 7500 target is 104.2666…, which
#: is reported as 104.3 and carried into the weighted sum as 104.3 - giving
#: 103.15 rather than 103.14. Carrying more precision than the figure shown would
#: make the screen's numbers fail to reproduce the screen's own total, and an
#: employee checking the arithmetic by hand would be right and the system wrong.
WORKLOAD_QUANTUM = Decimal("0.1")


#: **The one rounding mode in M6.** ``ROUND_HALF_UP``, everywhere, deliberately.
#:
#: ``ROUND_HALF_EVEN`` - Python's default - would send 1.005 and 1.015 in
#: opposite directions, which is defensible statistically and indefensible to an
#: employee comparing two payslips. Named here so that a service which needs to
#: round cannot quietly pick a different one.
ROUNDING = ROUND_HALF_UP


def quantize(value: Decimal, quantum: Decimal) -> Decimal:
    """**The** rounding function. Every quantization in M6 goes through it.

    Centralised rather than scattered, because the canonical-component rule
    depends on it: the figure a screen shows and the figure money is computed
    from must be the *same* Decimal, and that only holds if one place decides
    how many places each quantity has.
    """
    return value.quantize(quantum, rounding=ROUNDING)


#: Kept as the module-private alias the arithmetic below already reads with.
_round = quantize


# ===========================================================================
# Workload scoring rules
# ===========================================================================


class PrWorkScoringMode(StrEnum):
    """What a scoring rule says about a work type.

    Two, and the second exists because *silence and a decision look identical in
    a database*. A work type with no rule is unconfigured; a work type somebody
    deliberately kept out of performance is configured. Both produce zero
    minutes and they are not the same fact - see
    :class:`PrContributionScoreStatus`.
    """

    #: This work type is worth ``standard_minutes_per_unit`` per eligible unit.
    STANDARD_MINUTES = "STANDARD_MINUTES"
    #: Real work, deliberately outside the performance figure. ``OTHER_OPERATIONAL``
    #: is the usual first one: it is a fallback heading rather than a measurable
    #: job, and giving it minutes would reward filing work under it.
    EXCLUDED_FROM_PERFORMANCE = "EXCLUDED_FROM_PERFORMANCE"


class PrScoringRuleStatus(StrEnum):
    """Where a scoring rule version has got to.

    The same ladder a policy uses, and for the same reason: a rate that decides
    somebody's pay must be approved by a person, and must stop being editable the
    moment it can affect a number.
    """

    DRAFT = "DRAFT"
    #: Immutable. A change is a **new version** with its own effective date, so
    #: last September keeps being scored at last September's rate.
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class PrContributionScoreStatus(StrEnum):
    """What M6 concluded about one eligible contribution."""

    #: A rule applied and produced minutes.
    SCORED = "SCORED"
    #: **No rule covers this work type at this instant.** Configuration is
    #: incomplete, and this is emphatically *not* zero: treating it as zero would
    #: quietly deflate somebody's month because an owner has not finished setting
    #: the system up. It blocks finalisation.
    NO_SCORING_RULE = "NO_SCORING_RULE"
    #: A rule applied and said this work type is outside performance. Zero
    #: minutes, deliberately, and it does **not** block finalisation.
    EXCLUDED_FROM_PERFORMANCE = "EXCLUDED_FROM_PERFORMANCE"


# ===========================================================================
# The three monthly judgements
# ===========================================================================


class PrPerformanceReviewDimension(StrEnum):
    """The three things a manager judges, once a month, per person.

    Deliberately three and deliberately monthly. Per-item quality review is the
    design that looks rigorous and collapses on contact with twenty employees
    and a hundred deliverables each; this asks the manager the question they can
    actually answer - *how was this person's month* - and shows them the system's
    evidence while they answer it.
    """

    QUALITY = "QUALITY"
    TIMELINESS = "TIMELINESS"
    BUSINESS_CONTRIBUTION = "BUSINESS_CONTRIBUTION"


class PrPerformanceLevel(StrEnum):
    """One rung of a review barem.

    The same five rungs for all three dimensions, with different numbers behind
    them - so a manager learns one vocabulary rather than three.
    """

    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    MEETS_EXPECTATIONS = "MEETS_EXPECTATIONS"
    BELOW_EXPECTATIONS = "BELOW_EXPECTATIONS"
    POOR = "POOR"


#: **Đạt is 100.** The single most important number in the module.
#:
#: A person who did what the role expects scores exactly one hundred and is
#: neither rewarded nor punished by the quality term. The barem is not a
#: disciplinary instrument: fraud and misconduct are handled by people, not by
#: setting somebody's quality to zero, which is why the floor is 70 rather than 0.
DEFAULT_QUALITY_SCORES: Mapping[PrPerformanceLevel, Decimal] = MappingProxyType(
    {
        PrPerformanceLevel.EXCELLENT: Decimal("110"),
        PrPerformanceLevel.GOOD: Decimal("105"),
        PrPerformanceLevel.MEETS_EXPECTATIONS: Decimal("100"),
        PrPerformanceLevel.BELOW_EXPECTATIONS: Decimal("85"),
        PrPerformanceLevel.POOR: Decimal("70"),
    }
)

#: Timeliness, whose floor is higher than quality's because it does not gate.
#: Being late is a ten-percent problem; producing unusable work is a capped month.
DEFAULT_TIMELINESS_SCORES: Mapping[PrPerformanceLevel, Decimal] = MappingProxyType(
    {
        PrPerformanceLevel.EXCELLENT: Decimal("110"),
        PrPerformanceLevel.GOOD: Decimal("105"),
        PrPerformanceLevel.MEETS_EXPECTATIONS: Decimal("100"),
        PrPerformanceLevel.BELOW_EXPECTATIONS: Decimal("90"),
        PrPerformanceLevel.POOR: Decimal("80"),
    }
)

#: Business and common contribution, on the same shape as timeliness.
DEFAULT_BUSINESS_CONTRIBUTION_SCORES: Mapping[PrPerformanceLevel, Decimal] = MappingProxyType(
    {
        PrPerformanceLevel.EXCELLENT: Decimal("110"),
        PrPerformanceLevel.GOOD: Decimal("105"),
        PrPerformanceLevel.MEETS_EXPECTATIONS: Decimal("100"),
        PrPerformanceLevel.BELOW_EXPECTATIONS: Decimal("90"),
        PrPerformanceLevel.POOR: Decimal("80"),
    }
)

#: The level at which a note stops being optional.
#:
#: Anything other than *Đạt* moves a person's evaluation, up or down, and a
#: judgement without a sentence beside it is one nobody can defend three months
#: later - including the manager who gave it.
NOTE_OPTIONAL_LEVEL = PrPerformanceLevel.MEETS_EXPECTATIONS


def note_required(level: PrPerformanceLevel) -> bool:
    """Whether this rung must be explained. Every rung but *Đạt*."""
    return level is not NOTE_OPTIONAL_LEVEL


# ===========================================================================
# The quality gate
# ===========================================================================


#: Quality score floor → the highest final index it permits.
#:
#: Read as *"at or above this quality, the index may not exceed this"*, most
#: generous first. Only quality gates; see the module docstring.
DEFAULT_QUALITY_GATE: tuple[tuple[Decimal, Decimal | None], ...] = (
    (Decimal("90"), None),
    (Decimal("80"), Decimal("100")),
    (Decimal("70"), Decimal("90")),
    (Decimal("0"), Decimal("80")),
)


def quality_gate_cap(
    quality_score: Decimal,
    gate: tuple[tuple[Decimal, Decimal | None], ...] = DEFAULT_QUALITY_GATE,
) -> Decimal | None:
    """The ceiling this quality puts on the final index, or ``None`` for no cap.

    The rule the whole barem exists to make possible: **volume cannot rescue bad
    work.** A person who produced 120% of the standard workload badly is capped
    at 100, and the arithmetic says so rather than a manager having to argue it.
    """
    for floor, cap in gate:
        if quality_score >= floor:
            return cap
    return None


# ===========================================================================
# Performance bands
# ===========================================================================


#: Index floor → the Vietnamese name of the band.
#:
#: **A classification, and nothing more.** A band names how a month reads; it is
#: not mapped to money anywhere, and M6 has nothing to map it to. The boundary
#: between *Gần đạt* and *Đạt* is therefore a description somebody can argue
#: about on its merits rather than a threshold worth gaming.
DEFAULT_PERFORMANCE_BANDS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("110"), "Vượt kỳ vọng"),
    (Decimal("100"), "Đạt"),
    (Decimal("90"), "Gần đạt"),
    (Decimal("80"), "Chưa đạt"),
    (Decimal("0"), "Cần cải thiện"),
)


def performance_band(
    index: Decimal, bands: tuple[tuple[Decimal, str], ...] = DEFAULT_PERFORMANCE_BANDS
) -> str:
    """The band's Vietnamese name. A classification of the month, nothing else."""
    for floor, name in bands:
        if index >= floor:
            return name
    return bands[-1][1]


# ===========================================================================
# The arithmetic
# ===========================================================================


def workload_score(
    *,
    eligible_standard_minutes: Decimal,
    target_standard_minutes: Decimal,
    cap: Decimal = Decimal("120"),
) -> Decimal:
    """Eligible minutes as a percentage of the month's target, capped.

    Only M2's **eligible** amount reaches this - over-quota work contributes
    nothing, which is what stops a month being won by filing volume nobody
    planned.

    A zero target is not a division by zero to be papered over with a default: it
    means the target could not be resolved, and the caller must report
    ``TARGET_UNRESOLVED`` rather than score a month against a number nobody set.
    """
    if target_standard_minutes <= 0:
        raise ValueError("target_standard_minutes must be positive; resolve the target first")
    raw = eligible_standard_minutes / target_standard_minutes * Decimal("100")
    return _round(min(raw, cap), WORKLOAD_QUANTUM)


def raw_performance_index(
    *,
    workload: Decimal,
    quality: Decimal,
    timeliness: Decimal,
    business_contribution: Decimal,
    workload_weight: Decimal,
    quality_weight: Decimal,
    timeliness_weight: Decimal,
    business_contribution_weight: Decimal,
) -> Decimal:
    """The weighted sum, before the quality gate.

    Weights are percentages that total 100 - validated where a policy is written
    rather than trusted here, so a malformed policy cannot be saved rather than
    producing quiet nonsense at month end.
    """
    total = (
        workload * workload_weight
        + quality * quality_weight
        + timeliness * timeliness_weight
        + business_contribution * business_contribution_weight
    ) / Decimal("100")
    return _round(total, INDEX_QUANTUM)


def final_performance_index(raw: Decimal, cap: Decimal | None) -> Decimal:
    """The raw index with the quality gate applied. **After, never before.**

    Gating the components first would let a good workload term hide a capped
    quality term inside the average, which is precisely the arithmetic the gate
    exists to prevent.
    """
    return _round(min(raw, cap), INDEX_QUANTUM) if cap is not None else _round(raw, INDEX_QUANTUM)


# ===========================================================================
# Calculation status
# ===========================================================================


class PrPerformanceCalculationStatus(StrEnum):
    """Why a month's figure is or is not ready.

    Actionable rather than boolean: each value names the person who has to do
    something next, which is what makes an owner's performance table a worklist
    instead of a wall of "incomplete".
    """

    #: Everything resolved. Ready to finalise.
    READY = "READY"
    #: The month's target could not be resolved from the work schedule and the
    #: calendar. **Never silently 7500** - see the workday resolver.
    TARGET_UNRESOLVED = "TARGET_UNRESOLVED"
    #: Some eligible work has no scoring rule. Configuration, not zero.
    NO_SCORING_RULE = "NO_SCORING_RULE"
    #: The monthly manager review is missing or incomplete. Per-dimension detail
    #: travels alongside; a missing dimension is **never** defaulted to 100.
    PERFORMANCE_REVIEW_PENDING = "PERFORMANCE_REVIEW_PENDING"
    #: Agreed and closed to recalculation.
    FINALIZED = "FINALIZED"


#: The statuses that refuse finalisation. ``FINALIZED`` is absent because it is
#: the destination, not an obstacle.
BLOCKING_STATUSES: frozenset[PrPerformanceCalculationStatus] = frozenset(
    {
        PrPerformanceCalculationStatus.TARGET_UNRESOLVED,
        PrPerformanceCalculationStatus.NO_SCORING_RULE,
        PrPerformanceCalculationStatus.PERFORMANCE_REVIEW_PENDING,
    }
)


__all__: list[str] = [
    "BLOCKING_STATUSES",
    "DEFAULT_BUSINESS_CONTRIBUTION_SCORES",
    "DEFAULT_DAILY_TARGET_MINUTES",
    "DEFAULT_PERFORMANCE_BANDS",
    "DEFAULT_QUALITY_GATE",
    "DEFAULT_QUALITY_SCORES",
    "DEFAULT_TIMELINESS_SCORES",
    "INDEX_QUANTUM",
    "MINUTES_QUANTUM",
    "NOTE_OPTIONAL_LEVEL",
    "ROUNDING",
    "STANDARD_MINUTE_NOTE",
    "WORKLOAD_QUANTUM",
    "PrContributionScoreStatus",
    "PrPerformanceCalculationStatus",
    "PrPerformanceLevel",
    "PrPerformanceReviewDimension",
    "PrScoringRuleStatus",
    "PrWorkScoringMode",
    "final_performance_index",
    "note_required",
    "performance_band",
    "quality_gate_cap",
    "quantize",
    "raw_performance_index",
    "workload_score",
]
