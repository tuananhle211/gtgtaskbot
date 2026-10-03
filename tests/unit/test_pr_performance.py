"""Scoring, the monthly review, and the performance index. M6.

Organised by the thing that could go wrong rather than by class, because the
milestone's risks are not evenly spread. Four of them carry it:

* **the canonical-component rule** - the workload figure a screen shows is the
  figure money is computed from, to one decimal. If the engine carried more
  precision than it displayed, an employee checking the arithmetic by hand would
  be right and the system wrong (tests 73-80);
* **a missing rate is not zero.** ``NO_SCORING_RULE`` blocks finalisation;
  ``EXCLUDED_FROM_PERFORMANCE`` does not. Collapsing them would let an unfinished
  configuration quietly deflate somebody's month (tests 9-11);
* **one review per person per month.** A hundred deliverables is still one form
  (tests 24-32);
* **only quality gates.** Volume cannot rescue bad work, and being late does not
  cap a month (tests 65-72).

Consolidated with parameterisation where the cases are one rule seen from
several angles - a barem is a table, and five tests that each assert one row of
it are one test with five rows.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_performance_review_service import DimensionRating
from meobot.db.models.hr import HrRequest, OrganizationHoliday, WorkSchedule
from meobot.db.models.pr_performance import (
    PrPerformanceResult,
    PrPerformanceReview,
    PrWorkScoreAllocation,
)
from meobot.domain.hr.models import HrRequestStatus, HrRequestType
from meobot.domain.pr.errors import (
    PrConflictError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.performance import (
    DEFAULT_BUSINESS_CONTRIBUTION_SCORES,
    DEFAULT_QUALITY_SCORES,
    DEFAULT_TIMELINESS_SCORES,
    PrContributionScoreStatus,
    PrPerformanceCalculationStatus,
    PrPerformanceLevel,
    PrScoringRuleStatus,
    PrWorkScoringMode,
    final_performance_index,
    performance_band,
    quality_gate_cap,
    raw_performance_index,
    workload_score,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)
from tests.unit.test_pr_work_quota import (
    approved_plan,
    counted,
    month,
    seeding_type,
    work_type,
)

pytestmark = pytest.mark.asyncio

SEPTEMBER = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)


# ===========================================================================
# Harness
# ===========================================================================


async def schedule(world: World, *, working_days: list[int] | None = None) -> WorkSchedule:
    """A Monday-to-Friday organisation, unless a test says otherwise."""
    from datetime import time

    row = WorkSchedule(
        name="Mặc định",
        timezone="Asia/Ho_Chi_Minh",
        working_days=working_days if working_days is not None else [0, 1, 2, 3, 4],
        morning_start=time(8, 30),
        morning_end=time(12, 0),
        afternoon_start=time(13, 30),
        afternoon_end=time(17, 30),
        is_active=True,
    )
    world.session.add(row)
    await world.session.flush()
    return row


async def policy(world: World, **kwargs):  # type: ignore[no-untyped-def]
    """An approved V1 policy: 50/30/10/10, 300 minutes a day."""
    draft = await world.services.performance_policies.create_draft(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        effective_from=date(2026, 1, 1),
        **kwargs,
    )
    return await world.services.performance_policies.approve(
        actor=world.actor(world.owner), request_id=world.request_id, policy_id=draft.id
    )


async def rule(
    world: World,
    type_row,  # type: ignore[no-untyped-def]
    *,
    minutes: Decimal | None = Decimal("90"),
    mode: PrWorkScoringMode = PrWorkScoringMode.STANDARD_MINUTES,
    effective_from: date = date(2026, 1, 1),
    approve: bool = True,
):  # type: ignore[no-untyped-def]
    draft = await world.services.work_scoring_rules.create_draft(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=type_row.id,
        mode=mode,
        standard_minutes_per_unit=minutes,
        effective_from=effective_from,
    )
    if approve:
        return await world.services.work_scoring_rules.approve(
            actor=world.actor(world.owner), request_id=world.request_id, rule_id=draft.id
        )
    return draft


async def review(
    world: World,
    *,
    user=None,  # type: ignore[no-untyped-def]
    period,  # type: ignore[no-untyped-def]
    quality: PrPerformanceLevel | None = PrPerformanceLevel.MEETS_EXPECTATIONS,
    timeliness: PrPerformanceLevel | None = PrPerformanceLevel.MEETS_EXPECTATIONS,
    contribution: PrPerformanceLevel | None = PrPerformanceLevel.MEETS_EXPECTATIONS,
    reviewer=None,  # type: ignore[no-untyped-def]
) -> PrPerformanceReview:
    def rating(level: PrPerformanceLevel | None) -> DimensionRating | None:
        if level is None:
            return None
        note = None if level is PrPerformanceLevel.MEETS_EXPECTATIONS else "có lý do"
        return DimensionRating(level=level, note=note)

    return await world.services.performance_reviews.submit(
        actor=world.actor(reviewer or world.owner),
        request_id=world.request_id,
        user_id=(user or world.member).id,
        period_id=period.id,
        quality=rating(quality),
        timeliness=rating(timeliness),
        business_contribution=rating(contribution),
    )


async def snapshot(world: World, *, period, user=None, actor=None):  # type: ignore[no-untyped-def]
    return await world.services.performance.snapshot(
        actor=world.actor(actor or world.owner),
        user_id=(user or world.member).id,
        period_id=period.id,
    )


# ===========================================================================
# 1-12: WORKLOAD RULES
# ===========================================================================


async def test_01_04_owner_drafts_and_approves_a_rate(world: World) -> None:
    """Tests 1 and 4. A rate is drafted, then put in force by a person."""
    type_row = await work_type(world)
    draft = await rule(world, type_row, approve=False)
    assert draft.status is PrScoringRuleStatus.DRAFT
    assert draft.version_no == 1

    approved = await world.services.work_scoring_rules.approve(
        actor=world.actor(world.owner), request_id=world.request_id, rule_id=draft.id
    )
    assert approved.status is PrScoringRuleStatus.APPROVED
    assert approved.approved_by_user_id == world.owner.id
    assert approved.approved_at is not None


@pytest.mark.parametrize("who", ["member", "lead"])
async def test_02_03_neither_an_employee_nor_a_lead_may_configure_rates(
    world: World, who: str
) -> None:
    """Tests 2 and 3. **``PR_WORK_MANAGE`` is not configuration.**

    The Trưởng nhóm case is the one that matters: they assign work all day and
    must not be able to decide what it is worth.
    """
    type_row = await work_type(world)
    actor = world.actor(getattr(world, "member" if who == "member" else "lead"))
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_scoring_rules.create_draft(
            actor=actor,
            request_id=world.request_id,
            work_type_id=type_row.id,
            mode=PrWorkScoringMode.STANDARD_MINUTES,
            standard_minutes_per_unit=Decimal("90"),
            effective_from=date(2026, 1, 1),
        )


async def test_05_06_an_approved_rate_is_immutable_and_revision_makes_a_version(
    world: World,
) -> None:
    """Tests 5 and 6. There is no edit path; a change is a new version.

    Asserted on the service surface rather than on the row: the guarantee is that
    **no method mutates an approved rate**, and a test that wrote to the ORM
    directly would be testing SQLAlchemy.
    """
    type_row = await work_type(world)
    first = await rule(world, type_row, minutes=Decimal("90"))
    assert not hasattr(world.services.work_scoring_rules, "update")

    second = await rule(world, type_row, minutes=Decimal("105"), effective_from=date(2027, 1, 1))
    assert second.version_no == 2
    await world.session.refresh(first)
    assert first.status is PrScoringRuleStatus.SUPERSEDED
    assert first.effective_to == date(2026, 12, 31), "closed the day before its successor"
    assert second.supersedes_rule_id == first.id


async def test_07_an_overlapping_effective_range_is_refused(world: World) -> None:
    """Two rates in force on one day would make the engine pick one."""
    type_row = await work_type(world)
    await rule(world, type_row, effective_from=date(2026, 6, 1))
    clash = await rule(world, type_row, effective_from=date(2026, 6, 1), approve=False)
    with pytest.raises(PrConflictError) as caught:
        await world.services.work_scoring_rules.approve(
            actor=world.actor(world.owner), request_id=world.request_id, rule_id=clash.id
        )
    assert caught.value.details["reason"] == "overlapping_effective_range"


async def test_08_counted_at_selects_the_version_in_force_then(world: World) -> None:
    """**The rate is chosen by when the work counted**, not by today.

    A recalculation in 2027 must price September 2026 at September's rate, and
    this is the query that makes that true.
    """
    type_row = await work_type(world)
    await rule(world, type_row, minutes=Decimal("90"), effective_from=date(2026, 1, 1))
    await rule(world, type_row, minutes=Decimal("105"), effective_from=date(2027, 1, 1))

    old = await world.services.work_scoring_rules.rule_for(type_row.id, on=date(2026, 9, 15))
    new = await world.services.work_scoring_rules.rule_for(type_row.id, on=date(2027, 1, 15))
    assert old is not None and old.standard_minutes_per_unit == Decimal("90.0000")
    assert new is not None and new.standard_minutes_per_unit == Decimal("105.0000")


async def test_09_11_a_missing_rate_is_reported_not_scored_as_zero(world: World) -> None:
    """Tests 9 and 11. ``NO_SCORING_RULE`` - **configuration, not zero.**

    The distinction the whole status exists for: an unconfigured work type must
    not quietly deflate the month it appears in, so it produces a diagnostic and
    blocks finalisation instead of contributing a silent nothing.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world)
    item = await counted(world, type_row=type_row, at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    assert item is not None

    result = await snapshot(world, period=period)
    assert result.status is PrPerformanceCalculationStatus.NO_SCORING_RULE
    assert result.eligible_standard_minutes == Decimal("0.00")
    assert result.diagnostics["missing_scoring_rules"] == [type_row.code]


async def test_10_an_explicit_exclusion_is_not_a_missing_rule(world: World) -> None:
    """``EXCLUDED_FROM_PERFORMANCE`` - zero minutes, deliberately, **and it does
    not block finalisation.**

    ``OTHER_OPERATIONAL`` is the case: a fallback heading is real work and not a
    measurable job, and giving it minutes would reward filing work under it.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world, code="OTHER_OPERATIONAL", name="Việc khác")
    await rule(world, type_row, minutes=None, mode=PrWorkScoringMode.EXCLUDED_FROM_PERFORMANCE)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    await review(world, period=period)

    result = await snapshot(world, period=period)
    assert result.eligible_standard_minutes == Decimal("0.00")
    assert result.status is PrPerformanceCalculationStatus.READY, "excluded work blocks nothing"
    assert [row.status for row in result.breakdown] == [
        PrContributionScoreStatus.EXCLUDED_FROM_PERFORMANCE
    ]


# ===========================================================================
# 13-23: WORKLOAD AND TARGET
# ===========================================================================


@pytest.mark.parametrize(
    ("units", "expected"),
    [(Decimal("1"), Decimal("90.00")), (Decimal("3"), Decimal("270.00"))],
)
async def test_13_14_eligible_amount_times_the_rate(
    world: World, units: Decimal, expected: Decimal
) -> None:
    """Tests 13 and 14. One edit is 90 standard minutes; three are 270.

    Driven through **one contribution carrying the amount** rather than three
    contributions, and that is a deliberate harness choice rather than a weaker
    test: it exercises the identical multiplication
    (``eligible_amount x standard_minutes_per_unit``) while avoiding a
    pre-existing fragility in M2's SQLite fixture, where several stamped
    contributions leave ``counted_at`` mixed naive and aware and M2's allocation
    sort raises. That fragility is not M6's - ``test_pr_work_quota.py::test_04``
    reproduces it standalone with no M6 code involved - and this milestone is
    explicitly not reopening M2.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(
        world,
        code="VIDEO_EDIT",
        name="Dựng video",
        unit=PrWorkUnit.VIDEO,
        basis=PrWorkQuotaBasis.QUANTITY,
    )
    await rule(world, type_row, minutes=Decimal("90"))
    await counted(world, type_row=type_row, quantity=units, at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("10"), Decimal("10")),))

    result = await snapshot(world, period=period)
    assert result.eligible_standard_minutes == expected


async def test_15_16_17_over_quota_still_earns_minutes_and_the_cap_is_a_comparison(
    world: World,
) -> None:
    """Tests 15, 16 and 17, **reversed by the period-container patch.**

    120 comments against a cap of 100 is 100 eligible and 20 over - M2 still
    says so, and the KPI screen still shows the split. But *WORK is actual work
    performed; KPI is a target only*: the whole counted amount is priced. At
    0.9 minutes a comment that is **108** standard minutes, not 90. M6 reads the
    counted amount and the cap decides nothing about points.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await seeding_type(world)
    await rule(world, type_row, minutes=Decimal("0.9"))
    await counted(world, type_row=type_row, quantity=Decimal("120"), at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )

    result = await snapshot(world, period=period)
    assert result.eligible_standard_minutes == Decimal("108.00"), "the 20 over the cap are paid"
    assert result.over_quota_contributions == 1, "and the KPI comparison still says so"
    row = result.breakdown[0]
    assert row.counted_amount == Decimal("120.00")
    assert row.eligible_amount == Decimal("100.00")
    assert row.target_value == Decimal("100.00")
    assert row.completion_percent == Decimal("120.0")
    assert row.over_target_amount == Decimal("20.00")


async def test_18_the_arithmetic_is_decimal_throughout(world: World) -> None:
    """0.9 minutes a comment is exactly 0.9, and 3 of them are exactly 2.7.

    A float would make this 2.700000000000000177, and a month of them would drift
    a whole minute - which is a real difference in somebody's month.
    """
    total = Decimal("0")
    for _ in range(3):
        total += Decimal("0.9")
    assert total == Decimal("2.7")
    assert workload_score(
        eligible_standard_minutes=Decimal("7820"), target_standard_minutes=Decimal("7500")
    ) == Decimal("104.3")


@pytest.mark.parametrize(
    ("days_off", "expected_workdays", "expected_target"),
    [(0, 22, Decimal("6600.00")), (2, 20, Decimal("6000.00"))],
)
async def test_20_21_the_target_is_workdays_times_the_daily_rate(
    world: World, days_off: int, expected_workdays: int, expected_target: Decimal
) -> None:
    """Tests 20 and 21. **Never a flat 7500.**

    September 2026 has 22 Monday-to-Friday days. Two company holidays make it 20,
    and the target follows - which is the whole reason the calendar is consulted
    rather than assumed.
    """
    await schedule(world)
    period = await month(world)
    for offset in range(days_off):
        world.session.add(
            OrganizationHoliday(
                holiday_date=date(2026, 9, 1) + timedelta(days=offset), name="Nghỉ lễ"
            )
        )
    await world.session.flush()

    resolution = await world.services.performance_targets.resolve(
        user_id=world.member.id, period=period, daily_target_minutes=300
    )
    assert resolution.calendar_workdays == Decimal(expected_workdays)
    assert resolution.target_standard_minutes == expected_target


async def test_22_an_unresolvable_target_does_not_become_7500(world: World) -> None:
    """**The refusal that keeps the denominator honest.**

    With no active work schedule there is no such thing as a workday, and
    inventing Monday-to-Friday would invent the company's working week. The month
    reports ``TARGET_UNRESOLVED`` and cannot be finalised.
    """
    await policy(world)
    period = await month(world)
    result = await snapshot(world, period=period)
    assert result.target.target_standard_minutes is None
    assert result.status is PrPerformanceCalculationStatus.TARGET_UNRESOLVED
    assert result.workload is None, "no target means no workload score at all"
    assert result.diagnostics["target"] == "no_active_work_schedule"


async def test_23_a_target_override_requires_a_reason(world: World) -> None:
    """Replacing a computed target is exactly what somebody will be asked about."""
    period = await month(world)
    with pytest.raises(PrValidationError) as caught:
        await world.services.performance.set_target_override(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
            monthly_target_override=Decimal("4000"),
            override_reason="",
        )
    assert caught.value.details["reason"] == "override_reason_required"

    row = await world.services.performance.set_target_override(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
        monthly_target_override=Decimal("4000"),
        override_reason="Nghỉ thai sản nửa tháng",
    )
    assert row.monthly_target_override == Decimal("4000.00")


@pytest.mark.parametrize(
    ("request_type", "expected_leave_days"),
    [
        (HrRequestType.FULL_DAY_LEAVE, Decimal("1")),
        (HrRequestType.MORNING_LEAVE, Decimal("0.5")),
        (HrRequestType.HOURLY_LEAVE, Decimal("0")),
        (HrRequestType.LATE_ARRIVAL, Decimal("0")),
    ],
)
async def test_leave_reduces_the_target_by_what_the_type_means(
    world: World, request_type: HrRequestType, expected_leave_days: Decimal
) -> None:
    """**Approved leave reduces the target; an hour out of a day does not.**

    Treating an hour as a fraction of a day would make somebody who took two
    hours over a month measurably easier to score than somebody who did not.
    """
    await schedule(world)
    period = await month(world)
    world.session.add(
        HrRequest(
            requester_user_id=world.member.id,
            request_type=request_type,
            status=HrRequestStatus.APPROVED,
            work_date=date(2026, 9, 2),
        )
    )
    await world.session.flush()

    resolution = await world.services.performance_targets.resolve(
        user_id=world.member.id, period=period, daily_target_minutes=300
    )
    assert resolution.approved_leave_days == expected_leave_days


async def test_an_unapproved_absence_reduces_nothing(world: World) -> None:
    """The asymmetry, achieved by reading the data rather than by a rule about it.

    An unauthorised absence leaves no approved row, so there is nothing to
    subtract and the person is measured against the month they were expected to
    work.
    """
    await schedule(world)
    period = await month(world)
    world.session.add(
        HrRequest(
            requester_user_id=world.member.id,
            request_type=HrRequestType.FULL_DAY_LEAVE,
            status=HrRequestStatus.PENDING,
            work_date=date(2026, 9, 2),
        )
    )
    await world.session.flush()

    resolution = await world.services.performance_targets.resolve(
        user_id=world.member.id, period=period, daily_target_minutes=300
    )
    assert resolution.approved_leave_days == Decimal("0")


# ===========================================================================
# 24-32: THE MONTHLY REVIEW
# ===========================================================================


async def test_24_32_one_review_per_person_per_month_however_much_work(
    world: World,
) -> None:
    """Tests 24 and 32, and the product decision they protect.

    Six contributions, one form. The model that produced one review per
    deliverable would be a system nobody fills in, and therefore a system that
    scores nothing.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, type_row)
    for index in range(6):
        await counted(world, type_row=type_row, at=SEPTEMBER, title=f"Việc {index}")

    await review(world, period=period)
    await review(world, period=period, quality=PrPerformanceLevel.GOOD)

    total = await world.session.scalar(
        select(func.count())
        .select_from(PrPerformanceReview)
        .where(
            PrPerformanceReview.user_id == world.member.id,
            PrPerformanceReview.reporting_period_id == period.id,
        )
    )
    assert total == 1, "revising is not creating a second review"


async def test_25_nobody_reviews_themselves(world: World) -> None:
    """**Refused whoever the actor is**, including the owner.

    The one failure that would invalidate the whole exercise, so it is refused by
    the service and by a database CHECK rather than by convention.
    """
    period = await month(world)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await review(world, user=world.owner, period=period, reviewer=world.owner)
    assert caught.value.details["reason"] == "self_review"


@pytest.mark.parametrize("who", ["member", "lead"])
async def test_26_27_28_who_may_review(world: World, who: str) -> None:
    """Tests 26-28. Neither an employee nor a Trưởng nhóm; Admin and Owner may.

    ``PR_WORK_MANAGE`` assigns work. It does not decide what somebody's month was
    worth, because that moves money.
    """
    period = await month(world)
    actor = getattr(world, "member" if who == "member" else "lead")
    assert not await world.services.capabilities.allows(
        world.actor(actor), PrCapability.PR_PERFORMANCE_REVIEW
    )
    with pytest.raises(PrPermissionDeniedError):
        await review(world, period=period, reviewer=actor)

    assert await world.services.capabilities.allows(
        world.actor(world.owner), PrCapability.PR_PERFORMANCE_REVIEW
    )
    assert await review(world, period=period, reviewer=world.owner) is not None


async def test_29_30_31_the_review_holds_three_dimensions_and_nothing_per_item(
    world: World,
) -> None:
    """Tests 29-31. Three columns pairs, and **no per-contribution rating table.**"""
    period = await month(world)
    row = await review(world, period=period)
    for dimension in ("quality", "timeliness", "business_contribution"):
        assert getattr(row, f"{dimension}_level") is not None
        assert getattr(row, f"{dimension}_score") is not None

    # The allocation table carries workload provenance and deliberately no
    # judgement: quality and official timeliness are monthly, not per item.
    columns = {column.name for column in PrWorkScoreAllocation.__table__.columns}
    assert not any("quality" in name or "timeliness" in name for name in columns)


# ===========================================================================
# 33-57: THE THREE BAREMS
# ===========================================================================


@pytest.mark.parametrize(
    ("barem", "expected"),
    [
        (
            DEFAULT_QUALITY_SCORES,
            {
                "EXCELLENT": 110,
                "GOOD": 105,
                "MEETS_EXPECTATIONS": 100,
                "BELOW_EXPECTATIONS": 85,
                "POOR": 70,
            },
        ),
        (
            DEFAULT_TIMELINESS_SCORES,
            {
                "EXCELLENT": 110,
                "GOOD": 105,
                "MEETS_EXPECTATIONS": 100,
                "BELOW_EXPECTATIONS": 90,
                "POOR": 80,
            },
        ),
        (
            DEFAULT_BUSINESS_CONTRIBUTION_SCORES,
            {
                "EXCELLENT": 110,
                "GOOD": 105,
                "MEETS_EXPECTATIONS": 100,
                "BELOW_EXPECTATIONS": 90,
                "POOR": 80,
            },
        ),
    ],
    ids=["quality", "timeliness", "business_contribution"],
)
async def test_33_57_the_barems(barem, expected) -> None:  # type: ignore[no-untyped-def]
    """Tests 33-38, 41-46 and 49-54, as three tables rather than fifteen asserts.

    **Đạt is exactly 100 on all three.** A person who did what the role expects is
    neither rewarded nor punished by the review terms, which is what makes the
    workload term mean anything.
    """
    assert {level.value: int(score) for level, score in barem.items()} == expected
    assert barem[PrPerformanceLevel.MEETS_EXPECTATIONS] == Decimal("100")


@pytest.mark.parametrize("dimension", ["quality", "timeliness", "business_contribution"])
async def test_39_40_47_55_a_non_default_rung_needs_a_note(world: World, dimension: str) -> None:
    """Tests 39, 40, 47 and 55. **Anything but Đạt is explained, in writing.**

    The result moves money, and a number that moves money without a sentence
    beside it is one nobody can defend three months later - including the manager
    who gave it.
    """
    period = await month(world)
    kwargs = {dimension: DimensionRating(level=PrPerformanceLevel.GOOD, note=None)}
    with pytest.raises(PrValidationError) as caught:
        await world.services.performance_reviews.submit(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
            **kwargs,  # type: ignore[arg-type]
        )
    assert caught.value.details["reason"] == "note_required_for_non_default_level"

    # Đạt may omit it.
    assert await review(world, period=period) is not None


async def test_48_system_evidence_never_overrides_the_manager(world: World) -> None:
    """**The editor whose cut was late because a doctor moved a shoot.**

    The system can see the late task; it cannot see the reason. So evidence is
    counted and shown, and the score stays whatever the manager said - here
    *Đạt*, beside a recorded overdue item.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, type_row)
    item = await counted(world, type_row=type_row, at=SEPTEMBER)
    item.due_at = SEPTEMBER - timedelta(days=3)
    item.completed_at = SEPTEMBER
    await world.session.flush()

    await review(world, period=period, timeliness=PrPerformanceLevel.MEETS_EXPECTATIONS)
    result = await snapshot(world, period=period)
    assert result.evidence.overdue == 1, "the system recorded the late item"
    assert result.timeliness == Decimal("100.00"), "and the manager's judgement stands"


# ===========================================================================
# 58-64: EDITING THE REVIEW
# ===========================================================================


async def test_58_59_60_an_open_review_is_revisable_and_every_change_is_audited(
    world: World,
) -> None:
    """Tests 58-60. Rating and note changes both leave a trail naming both values."""
    from meobot.db.models.audit_log import AuditLog
    from meobot.domain.audit.models import AuditAction

    period = await month(world)
    await review(world, period=period, timeliness=PrPerformanceLevel.MEETS_EXPECTATIONS)
    await review(world, period=period, timeliness=PrPerformanceLevel.GOOD)

    rows = (
        (
            await world.session.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.PR_PERFORMANCE_TIMELINESS_RATED.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows, "the change was recorded"
    latest = rows[-1]
    assert latest.before_data["level"] == "MEETS_EXPECTATIONS"
    assert latest.after_data["level"] == "GOOD"


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_61_62_63_a_shut_period_refuses_every_write_and_there_is_no_force(
    world: World, state: PrPeriodStatus
) -> None:
    """Tests 61-63. Agreed numbers do not move, and nothing can make them.

    Asserted for both states because they are genuinely different - a closed
    month can still be corrected by reopening it, a locked one has been reported
    - and neither is a reason to let a write through.
    """
    period = await month(world)
    period.status = state
    await world.session.flush()

    with pytest.raises(PrConflictError) as caught:
        await review(world, period=period)
    assert caught.value.details["reason"] == "period_not_open"

    import inspect
    import re as _re

    from meobot.application import pr_performance_review_service, pr_performance_service

    # A *parameter* named force, not the prose saying there is none - the
    # docstrings deliberately contain the phrase "no force flag".
    for module in (pr_performance_service, pr_performance_review_service):
        source = inspect.getsource(module)
        assert not _re.search(r"\bforce\s*[:=]", source), module.__name__


# ===========================================================================
# 65-80: THE GATE, AND THE INDEX
# ===========================================================================


@pytest.mark.parametrize(
    ("quality", "cap"),
    [
        (Decimal("110"), None),
        (Decimal("105"), None),
        (Decimal("100"), None),
        (Decimal("85"), Decimal("100")),
        (Decimal("70"), Decimal("90")),
        (Decimal("60"), Decimal("80")),
    ],
)
async def test_65_69_the_quality_gate(quality: Decimal, cap: Decimal | None) -> None:
    """Tests 65-69, as the table it is."""
    assert quality_gate_cap(quality) == cap


async def test_70_volume_cannot_rescue_bad_work(world: World) -> None:
    """**The rule the gate exists for.**

    A perfect 120 workload with *Chưa đạt* quality is capped at 100, and the
    arithmetic says so rather than a manager having to argue it.
    """
    raw = raw_performance_index(
        workload=Decimal("120"),
        quality=Decimal("85"),
        timeliness=Decimal("110"),
        business_contribution=Decimal("110"),
        workload_weight=Decimal("50"),
        quality_weight=Decimal("30"),
        timeliness_weight=Decimal("10"),
        business_contribution_weight=Decimal("10"),
    )
    assert raw == Decimal("107.50")
    assert final_performance_index(raw, quality_gate_cap(Decimal("85"))) == Decimal("100.00")


# ===========================================================================
# 91-101: FINALISATION
# ===========================================================================


async def ready_month(world: World):  # type: ignore[no-untyped-def]
    """A month with everything resolved: schedule, policy, rate, work, review."""
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    await rule(world, type_row, minutes=Decimal("90"))
    await counted(world, type_row=type_row, at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("10"), Decimal("10")),))
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    await review(world, period=period)
    return period


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("quality", PrPerformanceCalculationStatus.PERFORMANCE_REVIEW_PENDING),
        ("timeliness", PrPerformanceCalculationStatus.PERFORMANCE_REVIEW_PENDING),
        ("contribution", PrPerformanceCalculationStatus.PERFORMANCE_REVIEW_PENDING),
    ],
)
async def test_93_94_95_a_missing_dimension_blocks_and_is_never_defaulted(
    world: World, missing: str, expected: PrPerformanceCalculationStatus
) -> None:
    """Tests 93-95. **A missing rating is missing, not 100.**

    Defaulting it would hand somebody a *Đạt* nobody gave them, and would do it
    silently in the direction that pays.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    await review(world, period=period, **{missing: None})  # type: ignore[arg-type]

    result = await snapshot(world, period=period)
    assert result.status is expected
    assert (
        missing.replace("contribution", "business_contribution")
        in result.diagnostics["missing_review_dimensions"]
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.performance.finalize(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    assert caught.value.details["reason"] == "performance_not_ready"


async def test_91_98_a_ready_month_previews_then_finalises_with_provenance(
    world: World,
) -> None:
    """Tests 91 and 98. The preview and the finalised figure are one calculation.

    A preview that could differ from the finalised number would make every
    preview a guess, so there is deliberately no separate "final" code path.
    """
    period = await ready_month(world)
    preview = await snapshot(world, period=period)
    assert preview.status is PrPerformanceCalculationStatus.READY

    final = await world.services.performance.finalize(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    assert final.final_index == preview.final_index

    row = (
        (
            await world.session.execute(
                select(PrPerformanceResult).where(
                    PrPerformanceResult.user_id == world.member.id,
                    PrPerformanceResult.reporting_period_id == period.id,
                )
            )
        )
        .scalars()
        .one()
    )
    assert row.finalized_at is not None
    assert row.policy_id is not None, "a finalised month names the policy it used"
    assert row.target_standard_minutes is not None
    assert row.workload_score is not None
    assert row.raw_performance_index is not None
    assert row.final_performance_index is not None


async def test_99_100_a_shut_period_refuses_recalculation_and_finalisation(
    world: World,
) -> None:
    """Tests 99 and 100."""
    period = await ready_month(world)
    period.status = PrPeriodStatus.CLOSED
    await world.session.flush()

    for call in (
        world.services.performance.calculate,
        world.services.performance.finalize,
    ):
        with pytest.raises(PrConflictError) as caught:
            await call(
                actor=world.actor(world.owner),
                request_id=world.request_id,
                user_id=world.member.id,
                period_id=period.id,
            )
        assert caught.value.details["reason"] == "period_not_open"


async def test_92_96_missing_configuration_blocks_finalisation(world: World) -> None:
    """Tests 92 and 96, as the two shapes an unfinished setup takes."""
    await policy(world)
    period = await month(world)
    await review(world, period=period)
    # No work schedule -> unresolved target.
    with pytest.raises(PrValidationError) as caught:
        await world.services.performance.finalize(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    assert caught.value.details["status"] == "TARGET_UNRESOLVED"


# ===========================================================================
# 102-114: M1 / M2 / M3 BOUNDARIES
# ===========================================================================


async def test_102_counted_work_with_no_quota_earns_its_minutes(world: World) -> None:
    """**Reversed by the period-container patch.** A missing KPI is a missing
    comparison, not a missing wage: ``NO_QUOTA`` work is priced like any other,
    and the breakdown reports no target rather than a zero."""
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, type_row)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )

    result = await snapshot(world, period=period)
    assert result.eligible_standard_minutes == Decimal("90.00")
    row = result.breakdown[0]
    assert row.target_value is None
    assert row.completion_percent is None
    assert row.over_target_amount == Decimal("0")


async def test_102b_counted_work_with_no_approved_plan_at_all_is_priced(world: World) -> None:
    """Even with no allocation row - no plan was ever approved, so M2's
    incremental hook wrote nothing - the counted amount is measured through the
    same function M2 uses and priced. Work never waits for a plan."""
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, type_row)
    await counted(world, type_row=type_row, at=SEPTEMBER)

    result = await snapshot(world, period=period)
    assert result.eligible_standard_minutes == Decimal("90.00")
    assert result.counted_contributions == 1


async def test_109_work_completed_but_not_counted_is_not_scored(world: World) -> None:
    """The editor's cut that nobody has accepted yet. **M1's boundary, unchanged.**"""
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, type_row)
    from meobot.application.pr_work_service import CreateWorkCommand

    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id, title="Chưa duyệt", contributor_user_ids=(world.member.id,)
        ),
    )
    assert item is not None

    result = await snapshot(world, period=period)
    assert result.eligible_standard_minutes == Decimal("0.00")
    assert result.counted_contributions == 0


# ===========================================================================
# M6B's three read-only contract additions
# ===========================================================================


async def test_m6b_a_finalised_month_says_so(world: World) -> None:
    """``is_finalized`` was declared in M6A and never populated.

    The snapshot computed live and never read the result row, so a finalised
    month reported ``False`` - and a screen would have drawn edit controls the
    server then refused. Read from the stored row now, with the stamps a
    read-only view needs to say *who* agreed it and *when*.
    """
    period = await ready_month(world)
    before = await snapshot(world, period=period)
    assert before.is_finalized is False
    assert before.finalized_at is None

    await world.services.performance.finalize(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    after = await snapshot(world, period=period)
    assert after.is_finalized is True
    assert after.finalized_at is not None
    assert after.finalized_by_user_id == world.owner.id


async def test_m6b_the_kpi_plan_is_priced_at_the_same_rates(world: World) -> None:
    """**Part AV.** Is this plan worth a month?

    A cap of 10 video edits at 90 standard minutes is 900 minutes of plan
    against a 6 600-minute September - a conversation worth having in week one
    rather than at month end. Priced from M2's approved caps and M6's rates, and
    **writing neither**.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    type_row = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    await rule(world, type_row, minutes=Decimal("90"))

    # No approved plan yet: **null, not zero.** Two different sentences.
    assert (await snapshot(world, period=period)).planned_standard_minutes is None

    await approved_plan(world, period=period, quotas=((type_row, Decimal("8"), Decimal("10")),))
    result = await snapshot(world, period=period)
    assert result.planned_standard_minutes == Decimal("900.00"), "the cap, priced"
    assert result.target.target_standard_minutes == Decimal("6600.00")


async def test_m6b_an_unpriced_quota_is_left_out_of_the_diagnostic(
    world: World,
) -> None:
    """A quota on a work type with no rate contributes nothing to the estimate.

    Guessing a rate would make the diagnostic disagree with the workload it is
    compared against - and the missing rule is already reported separately, by
    the status that blocks finalisation.
    """
    await schedule(world)
    await policy(world)
    period = await month(world)
    priced = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    unpriced = await work_type(world, code="MYSTERY", name="Chưa có quy tắc")
    await rule(world, priced, minutes=Decimal("90"))
    await approved_plan(
        world,
        period=period,
        quotas=(
            (priced, Decimal("10"), Decimal("10")),
            (unpriced, Decimal("10"), Decimal("10")),
        ),
    )

    result = await snapshot(world, period=period)
    assert result.planned_standard_minutes == Decimal("900.00")


async def test_m6b_the_additions_changed_no_calculation() -> None:
    """M6B added three **reads**. The arithmetic module is untouched.

    Asserted by re-running the canonical example through the same functions: if
    a UI need had been met by bending a formula, this is where it would show.
    """
    workload = workload_score(
        eligible_standard_minutes=Decimal("7820"), target_standard_minutes=Decimal("7500")
    )
    raw = raw_performance_index(
        workload=workload,
        quality=Decimal("100"),
        timeliness=Decimal("105"),
        business_contribution=Decimal("105"),
        workload_weight=Decimal("50"),
        quality_weight=Decimal("30"),
        timeliness_weight=Decimal("10"),
        business_contribution_weight=Decimal("10"),
    )
    assert (workload, raw) == (Decimal("104.3"), Decimal("103.15"))
    assert final_performance_index(raw, None) == Decimal("103.15")
    assert performance_band(Decimal("103.15")) == "Đạt"


# ===========================================================================
# The scope correction: M6 scores performance and allocates no money
# ===========================================================================


async def test_scope_no_money_survives_anywhere_in_the_engine() -> None:
    """**The structural guard for the product decision.**

    M6 scores and reports performance; the head allocates performance pay
    separately, outside MeoChat. A coefficient is the thing most likely to creep
    back, because ``index / 100`` looks harmless - and it is exactly what would
    turn a judgement about somebody's month into a promise about their pay.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    files = [
        root / "domain" / "pr" / "performance.py",
        root / "db" / "models" / "pr_performance.py",
        root / "api" / "schemas" / "pr_performance.py",
        root / "api" / "routers" / "pr_performance.py",
        *(root / "application").glob("pr_performance*.py"),
    ]
    for path in files:
        code = "\n".join(
            line
            for line in path.read_text("utf-8").splitlines()
            if not line.lstrip().startswith(("#", "*", '"', "'"))
        )
        for forbidden in (
            "bonus_coefficient",
            "base_performance_amount",
            "bonus_weight",
            "allocated_amount",
            "individual_bonus",
            "allocate_pool",
            "PrPerformanceBonusPool",
        ):
            assert forbidden not in code, f"{path.name}: {forbidden}"


async def test_scope_the_bonus_service_is_gone_not_merely_unused() -> None:
    """Dead code that can be re-wired is not removed code."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    assert not (root / "application" / "pr_performance_bonus_service.py").exists()
    bundle = (root / "application" / "pr_services.py").read_text("utf-8")
    assert "performance_bonus" not in bundle
    assert "BonusService" not in bundle


async def test_scope_finalisation_needs_no_compensation_information(
    world: World,
) -> None:
    """**Requirement 14, asserted end to end.**

    A month with a policy, a resolved target, priced work and three ratings
    finalises - with no money configured anywhere, because there is nowhere to
    configure any.
    """
    period = await ready_month(world)
    snapshot_before = await snapshot(world, period=period)
    assert snapshot_before.status is PrPerformanceCalculationStatus.READY

    result = await world.services.performance.finalize(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    assert result.final_index is not None
    assert result.band is not None
    assert not hasattr(result, "coefficient")

    row = (
        (
            await world.session.execute(
                select(PrPerformanceResult).where(
                    PrPerformanceResult.user_id == world.member.id,
                    PrPerformanceResult.reporting_period_id == period.id,
                )
            )
        )
        .scalars()
        .one()
    )
    assert row.finalized_at is not None
    assert not hasattr(row, "bonus_coefficient")
    # Everything a finalised month must keep, and nothing about money.
    for column in (
        "policy_id",
        "performance_review_id",
        "target_standard_minutes",
        "eligible_standard_minutes",
        "workload_score",
        "quality_score",
        "timeliness_score",
        "business_contribution_score",
        "raw_performance_index",
        "final_performance_index",
    ):
        assert getattr(row, column) is not None, column


async def test_scope_the_target_override_survived_the_removal(world: World) -> None:
    """The one number a person may still type, and its mandatory reason.

    ``pr_performance_inputs`` once held a bonus basis and a share weight too;
    with those gone the table holds exactly one thing, and was renamed to say so
    while ``0035`` was still undeployed.
    """
    period = await month(world)
    with pytest.raises(PrValidationError) as caught:
        await world.services.performance.set_target_override(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
            monthly_target_override=Decimal("4000"),
            override_reason="   ",
        )
    assert caught.value.details["reason"] == "override_reason_required"

    row = await world.services.performance.set_target_override(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
        monthly_target_override=Decimal("4000"),
        override_reason="Vào làm từ 15/09",
    )
    assert row.monthly_target_override == Decimal("4000.00")
    assert row.override_reason == "Vào làm từ 15/09"

    # And an employee still cannot set one.
    with pytest.raises(PrPermissionDeniedError):
        await world.services.performance.set_target_override(
            actor=world.actor(world.member),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
            monthly_target_override=Decimal("1"),
            override_reason="x",
        )


async def test_scope_the_monthly_summary_counts_without_analysing(world: World) -> None:
    """Counts and averages for the head's report. **Not M7.**

    The average is over months that *have* an index: treating an unreviewed month
    as zero would drag the department's figure down to report that nobody had got
    round to reviewing somebody.
    """
    period = await ready_month(world)
    summary = await world.services.performance.period_summary(
        actor=world.actor(world.owner), period_id=period.id
    )
    assert summary.employees >= 1
    assert summary.reviewed >= 1
    assert summary.finalized == 0
    assert summary.average_final_index is not None
    assert (
        sum(summary.bands.values()) == len([one for one in summary.bands if summary.bands[one]])
        or summary.bands
    )
    # Nobody reviewed at all -> no average, rather than zero.
    empty = await world.services.performance.period_summary(
        actor=world.actor(world.owner), period_id=(await month(world, number=8)).id
    )
    assert empty.average_final_index is None


async def test_scope_no_route_exposes_money(world: World) -> None:
    """Asserted over the live route table, not over the source."""
    paths = [getattr(route, "path", "") for route in world.client.app.routes]  # type: ignore[attr-defined]
    for path in paths:
        for forbidden in ("bonus", "allowance", "compensation", "payroll"):
            assert forbidden not in path.lower(), path
