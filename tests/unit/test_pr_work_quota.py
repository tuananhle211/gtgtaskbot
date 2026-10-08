"""M2 - quota eligibility, and the three words it keeps apart.

Numbered 1-33 against the milestone's own requirement list.

What this file is really testing
---------------------------------

**COUNTED != ELIGIBLE != SCORED.** M1 proved the first boundary; every test
below exists because collapsing the second would either let somebody's KPI grow
without a decision behind it, or make the shape of the paperwork worth more than
the work:

* **absence is not permission** - counted work with no approved quota is
  ``NO_QUOTA``, not unlimited. Tests 1-3 and 20-21. This is the milestone;
* **a draft decides nothing** - test 2, because a half-written plan setting
  somebody's KPI is the same failure one step earlier;
* **partial allocation** - 60 + 60 against a cap of 100 is 60 eligible and then
  40 eligible / 20 over, never 60 and then nothing. Tests 8-9, because
  all-or-nothing would make two people who did identical work score differently
  depending on how they split it;
* **``ITEM_COUNT`` ignores ``credit_weight`` and ``QUANTITY`` applies it** -
  tests 9-10 and 27-28, which is what makes a three-person shoot one job and
  three people's workload without either figure being divided;
* **an approved plan is never edited** - tests 17-19 and 23, so whoever raised a
  cap in the middle of a month stays visible;
* **a closed period is not rewritten** - tests 15-16, with no ``force`` flag to
  find;
* **``PR_WORK_MANAGE`` configures nothing** - test 24, the M1 scope model kept
  rather than regressed.

And **test 26**: no score, anywhere. ``ELIGIBLE`` is *"đủ điều kiện tính KPI"*
and never *"đã được tính điểm"*. M6 owns points and M2 does not pre-empt it.

Nothing here contacts a network.
"""

from __future__ import annotations

# The ``world`` fixture comes from the production-lifecycle suite, and the work
# helpers from M1's own file: a lookalike fixture would let the two drift, and
# M2 is meaningless without M1's ladder underneath it.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_period_service import month_bounds, month_period_code
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota, PrWorkQuotaAllocation
from meobot.db.models.user import User
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkPeriodNotOpenError,
    PrWorkPlanStateError,
)
from meobot.domain.pr.reporting import PrPeriodStatus, PrPeriodType
from meobot.domain.pr.work import PrWorkCategory, PrWorkCountStatus, PrWorkUnit
from meobot.domain.pr.work_quota import (
    PrWorkPlanStatus,
    PrWorkQuotaBasis,
    PrWorkQuotaStatus,
    PrWorkUnmeasurableReason,
    QuotaCandidate,
    allocate,
    candidate_sort_key,
    measure_contribution,
)
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)
from tests.unit.test_pr_work_core import take_to_completed

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("frozen_work_clock")]

#: Mid-September, so every helper below lands in one month whose bounds are
#: unambiguous in Asia/Ho_Chi_Minh as well as in UTC.
SEPTEMBER = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)

#: The department's own wall clock, +07:00 and no daylight saving. Used only by
#: the ordering tests, to build a timestamp whose *instant* and whose *text*
#: sort in opposite directions - which is the difference between normalising a
#: datetime and discarding its offset.
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")


# ===========================================================================
# Helpers
# ===========================================================================


async def month(
    world: World, *, year: int = 2026, number: int = 9, status: PrPeriodStatus | None = None
) -> PrReportingPeriod:
    """One month reporting period, created the way an administrator would."""
    row = await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner), request_id=world.request_id, year=year, month=number
    )
    if status is not None:
        row.status = status
        await world.session.flush()
    return row


async def work_type(
    world: World,
    *,
    code: str = "SHORT_SCRIPT",
    name: str = "Kịch bản ngắn",
    unit: PrWorkUnit = PrWorkUnit.ITEM,
    basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT,
) -> PrWorkType:
    return await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code=code,
        name=name,
        category=PrWorkCategory.CONTENT,
        default_unit=unit,
        default_quota_basis=basis,
    )


async def seeding_type(world: World) -> PrWorkType:
    """A quantity-measured type. "100 comments" is one item, not a hundred."""
    return await work_type(
        world,
        code="SEEDING_COMMENT",
        name="Seeding bình luận",
        unit=PrWorkUnit.COMMENT,
        basis=PrWorkQuotaBasis.QUANTITY,
    )


async def counted(
    world: World,
    *,
    type_row: PrWorkType,
    contributors: tuple[User, ...] | None = None,
    quantity: Decimal | None = None,
    title: str = "Việc",
    at: datetime | None = None,
) -> PrWorkItem:
    """Assign, complete and validate one job, so its contributions are COUNTED.

    Validated by the **owner**, who never contributes here - M1's rule, and
    every M2 test depends on it having been applied rather than bypassed.
    """
    people = contributors or (world.member,)
    # **M4B moved one line, and this is where M2 feels it.** The operational
    # commands now refuse a ``QUANTITY``-measured type filed with no number, so a
    # helper that wants the *historical* row - the one M2's ``MISSING_QUANTITY``
    # allocation exists to describe - files a number and then removes it. That is
    # the same thing test 37 already does for a mismatched unit, and for the same
    # reason: the state is representable and no command produces it, which is
    # exactly the boundary M4B drew. M2's semantics are untouched - the row, the
    # measurement and the allocation are identical either way.
    filed = quantity
    unmeasurable = quantity is None and type_row.default_quota_basis is PrWorkQuotaBasis.QUANTITY
    if unmeasurable:
        filed = Decimal("1")
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id,
            title=title,
            quantity=filed,
            contributor_user_ids=tuple(person.id for person in people),
        ),
    )
    if unmeasurable:
        # Nullable together, by the ``quantity_and_unit_together`` CHECK.
        item.quantity = None
        item.unit = None
        await world.session.flush()
    await take_to_completed(world, item, by=people[0])
    await world.services.work.approve(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    if at is not None:
        await stamp_counted_at(world, item, at)
    return item


async def stamp_counted_at(world: World, item: PrWorkItem, moment: datetime) -> None:
    """Move an item's contributions to a chosen instant.

    Test scaffolding only, and written directly rather than through a service
    on purpose: M1 has **no** path that changes ``counted_at`` after validation,
    and inventing one for a test would be inventing the correction workflow M2
    is explicitly not building.
    """
    for row in (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
            )
        )
        .scalars()
        .all()
    ):
        row.counted_at = moment
    await world.session.flush()


async def approved_plan(
    world: World,
    *,
    period: PrReportingPeriod,
    user: User | None = None,
    quotas: tuple[tuple[PrWorkType, Decimal, Decimal], ...] = (),
) -> PrWorkPlan:
    """A draft with quotas, approved. The whole administrator flow in one call."""
    subject = user or world.member
    detail = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=subject.id,
        period_id=period.id,
    )
    for type_row, target, cap in quotas:
        await world.services.work_plans.add_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=detail.plan.id,
            work_type_id=type_row.id,
            target_value=target,
            eligibility_cap=cap,
        )
    approved = await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=detail.plan.id
    )
    return approved.plan


async def allocations_for(
    world: World, *, user: User, period: PrReportingPeriod
) -> list[PrWorkQuotaAllocation]:
    result = await world.session.execute(
        select(PrWorkQuotaAllocation)
        .where(
            PrWorkQuotaAllocation.user_id == user.id,
            PrWorkQuotaAllocation.reporting_period_id == period.id,
        )
        .order_by(PrWorkQuotaAllocation.evaluated_at.asc())
    )
    return list(result.scalars().all())


async def summary_for(world: World, *, user: User, period: PrReportingPeriod):  # type: ignore[no-untyped-def]
    return await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=user.id, period_id=period.id
    )


def progress(summary, code: str):  # type: ignore[no-untyped-def]
    """One work type's row out of a summary, by its stable code."""
    for row in summary.types:
        if row.work_type_code == code:
            return row
    raise AssertionError(f"no progress row for {code}: {[r.work_type_code for r in summary.types]}")


# ===========================================================================
# 1-3: ABSENCE IS NOT PERMISSION
# ===========================================================================


async def test_01_counted_work_with_no_approved_quota_is_no_quota(world: World) -> None:
    """**The milestone.** Real work, valid workload, and *not* eligible.

    If missing quota meant unlimited eligibility, the cheapest route to an
    unbounded KPI would be to do work in a category nobody has set a target for
    - and the absence of a target is nearly always the absence of a decision.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)

    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    rows = await allocations_for(world, user=world.member, period=period)
    assert len(rows) == 1
    assert rows[0].quota_status is PrWorkQuotaStatus.NO_QUOTA
    # The real amount is recorded - the work happened - and none of it is
    # eligible, because nobody decided anything about it.
    assert rows[0].basis_amount == Decimal("1.00")
    assert rows[0].eligible_amount == Decimal("0.00")
    assert rows[0].over_quota_amount == Decimal("0.00")
    assert rows[0].work_quota_id is None and rows[0].work_plan_id is None


async def test_02_a_draft_plan_decides_nothing(world: World) -> None:
    """A quota that took effect while it was still being written would let a
    half-finished plan set somebody's KPI."""
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)

    draft = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        work_type_id=type_row.id,
        target_value=Decimal("20"),
        eligibility_cap=Decimal("20"),
    )
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    rows = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in rows] == [PrWorkQuotaStatus.NO_QUOTA]

    summary = await summary_for(world, user=world.member, period=period)
    assert summary.plan_id is None, "a draft is not the plan in force"


async def test_03_approving_the_plan_activates_eligibility(world: World) -> None:
    """The same contribution, before and after somebody decided about it."""
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    assert (await allocations_for(world, user=world.member, period=period))[
        0
    ].quota_status is PrWorkQuotaStatus.NO_QUOTA

    # Approving the plan recomputes the open period in the same transaction, so
    # the figures move when the decision does.
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))
    rows = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in rows] == [PrWorkQuotaStatus.ELIGIBLE]
    assert rows[0].eligible_amount == Decimal("1.00")
    assert rows[0].work_quota_id is not None


# ===========================================================================
# 4-7: ITEM_COUNT, TARGET vs CAP, AND DETERMINISM
# ===========================================================================


async def test_04_item_count_target_20_cap_20_with_23_contributions(world: World) -> None:
    """20 eligible, 3 over quota. The worked example from the brief."""
    period = await month(world)
    type_row = await work_type(world)
    for index in range(23):
        await counted(
            world,
            type_row=type_row,
            title=f"Kịch bản {index}",
            at=SEPTEMBER + timedelta(minutes=index),
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))

    rows = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert len(rows) == 23
    eligible = [row for row in rows if row.quota_status is PrWorkQuotaStatus.ELIGIBLE]
    over = [row for row in rows if row.quota_status is PrWorkQuotaStatus.OVER_QUOTA]
    assert len(eligible) == 20 and len(over) == 3
    assert all(row.basis_amount == Decimal("1.00") for row in rows)
    assert all(row.eligible_amount == Decimal("1.00") for row in eligible)
    assert all(row.eligible_amount == Decimal("0.00") for row in over)
    assert all(row.over_quota_amount == Decimal("1.00") for row in over)
    # Nothing is ``PARTIALLY_ELIGIBLE``: one item cannot be half an item.
    assert not [row for row in rows if row.quota_status is PrWorkQuotaStatus.PARTIALLY_ELIGIBLE]


async def test_05_target_20_cap_25_with_27_contributions(world: World) -> None:
    """**Target and cap are two different numbers.**

    25 eligible, 2 over, and the target is complete at 20 - so five units of
    real extra work stay eligible without the screen pretending the target was
    25. Neither figure is a point total.
    """
    period = await month(world)
    type_row = await work_type(world)
    for index in range(27):
        await counted(
            world,
            type_row=type_row,
            title=f"Kịch bản {index}",
            at=SEPTEMBER + timedelta(minutes=index),
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("25")),))

    row = progress(await summary_for(world, user=world.member, period=period), "SHORT_SCRIPT")
    assert row.counted_contributions == 27
    assert row.counted_amount == Decimal("27.00")
    assert row.eligible_amount == Decimal("25.00")
    assert row.over_quota_amount == Decimal("2.00")
    assert row.target_value == Decimal("20.00")
    assert row.eligibility_cap == Decimal("25.00")
    # Capped at the target: "25 / 20" is not what a met target looks like.
    assert row.target_progress == Decimal("20.00")
    assert row.extra_eligible_above_target == Decimal("5.00")


async def test_06_allocation_order_is_counted_at_then_created_at_then_id(world: World) -> None:
    """First validated, first inside the quota - and a total order under ties.

    ``counted_at`` is one instant for every contribution on one work item, so a
    three-person shoot ties three ways; without the second and third keys a
    repeated evaluation would not be reproducible.
    """
    early = QuotaCandidate(
        contribution_id=uuid.UUID(int=9),
        counted_at=SEPTEMBER,
        created_at=SEPTEMBER,
        basis_amount=Decimal("1.00"),
    )
    same_time_lower_id = QuotaCandidate(
        contribution_id=uuid.UUID(int=1),
        counted_at=SEPTEMBER,
        created_at=SEPTEMBER,
        basis_amount=Decimal("1.00"),
    )
    later = QuotaCandidate(
        contribution_id=uuid.UUID(int=0),
        counted_at=SEPTEMBER + timedelta(minutes=1),
        created_at=SEPTEMBER,
        basis_amount=Decimal("1.00"),
    )
    ordered = sorted([later, early, same_time_lower_id], key=candidate_sort_key)
    assert [one.contribution_id for one in ordered] == [
        same_time_lower_id.contribution_id,
        early.contribution_id,
        later.contribution_id,
    ]
    # And the rule that consumes it fills capacity in exactly that order.
    decided = allocate([later, early, same_time_lower_id], eligibility_cap=Decimal("2.00"))
    assert [one.quota_status for one in decided] == [
        PrWorkQuotaStatus.ELIGIBLE,
        PrWorkQuotaStatus.ELIGIBLE,
        PrWorkQuotaStatus.OVER_QUOTA,
    ]


def _candidate(
    *,
    number: int,
    counted_at: datetime,
    created_at: datetime | None = None,
    amount: str = "1.00",
) -> QuotaCandidate:
    """One candidate, spelled once so the timezone tests read as timezones."""
    return QuotaCandidate(
        contribution_id=uuid.UUID(int=number),
        counted_at=counted_at,
        created_at=created_at if created_at is not None else counted_at,
        basis_amount=Decimal(amount),
    )


async def test_06b_the_order_is_by_instant_and_not_by_wall_clock(world: World) -> None:
    """**Timezone normalisation, and why it is normalisation and not stripping.**

    A stored timestamp is aware UTC by convention, but the *driver* decides what
    comes back: SQLite has no timezone type and returns the same column naive,
    so one evaluation can hold a freshly flushed ``counted_at`` that is still
    aware beside one re-read from the database that is not. Python refuses to
    compare the two, and the exception took the whole evaluation with it.

    The fix is to normalise both through ``ensure_utc`` at the ordering
    boundary. The assertion that makes it a *fix* rather than a silencing is
    this one: 08:00+07:00 is **earlier** than 01:30+00:00, and anything that
    reached the same exception by discarding the offsets would order these two
    the other way round while raising nothing.
    """
    saigon = _candidate(number=1, counted_at=datetime(2026, 9, 15, 8, 0, tzinfo=SAIGON))
    london = _candidate(number=2, counted_at=datetime(2026, 9, 15, 1, 30, tzinfo=UTC))

    assert [one.contribution_id for one in sorted([london, saigon], key=candidate_sort_key)] == [
        saigon.contribution_id,  # 01:00 UTC
        london.contribution_id,  # 01:30 UTC
    ]
    # The rule that consumes the order agrees, which is the part that decides
    # somebody's KPI: a cap of one goes to the earlier *instant*.
    decided = allocate([london, saigon], eligibility_cap=Decimal("1.00"))
    assert [(one.contribution_id, one.quota_status) for one in decided] == [
        (saigon.contribution_id, PrWorkQuotaStatus.ELIGIBLE),
        (london.contribution_id, PrWorkQuotaStatus.OVER_QUOTA),
    ]


async def test_06c_naive_and_aware_timestamps_compare_without_raising(world: World) -> None:
    """The four mixtures a SQLite round-trip can produce, in both key positions.

    Each pair is *the same instant* written two ways, so the assertion is not
    only "it does not raise" but "the tie falls through to the next key" - which
    is what makes a repeated evaluation reproducible whichever way the driver
    happened to hand the row back.
    """
    aware = SEPTEMBER
    # ``ensure_utc`` reads a naive value as UTC, which is how it was stored.
    naive = SEPTEMBER.replace(tzinfo=None)

    # counted_at: naive/aware and aware/naive, both directions.
    for first, second in ((naive, aware), (aware, naive)):
        low = _candidate(number=1, counted_at=first)
        high = _candidate(number=9, counted_at=second)
        assert [one.contribution_id for one in sorted([high, low], key=candidate_sort_key)] == [
            low.contribution_id,
            high.contribution_id,
        ]

    # created_at: the second key, mixed while counted_at ties.
    for first, second in ((naive, aware), (aware, naive)):
        earlier = _candidate(number=9, counted_at=aware, created_at=first - timedelta(minutes=1))
        later = _candidate(number=1, counted_at=aware, created_at=second)
        assert [
            one.contribution_id for one in sorted([later, earlier], key=candidate_sort_key)
        ] == [earlier.contribution_id, later.contribution_id]


async def test_06d_the_id_is_still_the_last_tie_breaker_across_representations(
    world: World,
) -> None:
    """A naive and an aware spelling of one instant tie, and the id decides.

    The point of the third key is that the order is **total**. Normalising the
    first two must not accidentally make two rows compare unequal because of how
    they were spelled, or a repeated evaluation could return them either way
    round.
    """
    aware = _candidate(number=9, counted_at=SEPTEMBER)
    naive = _candidate(number=1, counted_at=SEPTEMBER.replace(tzinfo=None))
    assert candidate_sort_key(aware)[:2] == candidate_sort_key(naive)[:2]
    assert [one.contribution_id for one in sorted([aware, naive], key=candidate_sort_key)] == [
        naive.contribution_id,
        aware.contribution_id,
    ]


async def test_06e_allocation_amounts_are_unchanged_by_normalisation(world: World) -> None:
    """**The scope boundary, asserted.** Only the ordering keys were touched.

    Normalisation decides *which* candidate is served first; it must not change
    what any of them is worth. A partial split is the sharpest case, because it
    is the one where an amount is computed rather than copied - and it is
    computed identically whether the timestamps arrive naive or aware.
    """
    cap = Decimal("100.00")
    aware = [
        _candidate(number=1, counted_at=SEPTEMBER, amount="60.00"),
        _candidate(number=2, counted_at=SEPTEMBER + timedelta(minutes=1), amount="60.00"),
        _candidate(number=3, counted_at=SEPTEMBER + timedelta(minutes=2), amount="5.00"),
    ]
    naive = [
        _candidate(
            number=one.contribution_id.int,
            counted_at=one.counted_at.replace(tzinfo=None),
            amount=str(one.basis_amount),
        )
        for one in aware
    ]

    from_aware = allocate(aware, eligibility_cap=cap)
    from_naive = allocate(naive, eligibility_cap=cap)
    assert from_aware == from_naive
    assert [one.quota_status for one in from_aware] == [
        PrWorkQuotaStatus.ELIGIBLE,
        PrWorkQuotaStatus.PARTIALLY_ELIGIBLE,
        PrWorkQuotaStatus.OVER_QUOTA,
    ]
    assert [one.eligible_amount for one in from_aware] == [
        Decimal("60.00"),
        Decimal("40.00"),
        Decimal("0.00"),
    ]
    assert [one.over_quota_amount for one in from_aware] == [
        Decimal("0.00"),
        Decimal("20.00"),
        Decimal("5.00"),
    ]
    # And the no-cap branch, which sorts the same list down a different path.
    assert allocate(aware, eligibility_cap=None) == allocate(naive, eligibility_cap=None)
    assert {one.quota_status for one in allocate(naive, eligibility_cap=None)} == {
        PrWorkQuotaStatus.NO_QUOTA
    }


async def test_07_reconciling_twice_produces_an_identical_result(world: World) -> None:
    """**Idempotent by construction**, because the evaluator is a projection.

    Not "close enough": the same rows, the same statuses, the same amounts, and
    no duplicates - which is what ``uq_pr_work_quota_allocations_contribution``
    guarantees independently.
    """
    period = await month(world)
    type_row = await work_type(world)
    for index in range(5):
        await counted(
            world, type_row=type_row, title=f"K{index}", at=SEPTEMBER + timedelta(minutes=index)
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("3"), Decimal("3")),))

    def snapshot(rows: list[PrWorkQuotaAllocation]) -> list[tuple[object, ...]]:
        return sorted(
            (
                str(row.work_contribution_id),
                row.quota_status.value,
                str(row.basis_amount),
                str(row.eligible_amount),
                str(row.over_quota_amount),
            )
            for row in rows
        )

    first = snapshot(await allocations_for(world, user=world.member, period=period))
    for _ in range(2):
        await world.services.work_eligibility.reconcile_period(
            actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
        )
    after = await allocations_for(world, user=world.member, period=period)
    assert snapshot(after) == first
    assert len(after) == 5, "a repeated reconcile appends nothing"


# ===========================================================================
# 8-12: QUANTITY, PARTIAL ELIGIBILITY AND CREDIT WEIGHT
# ===========================================================================


async def test_08_quantity_60_and_60_under_a_cap_of_100_splits_the_second(
    world: World,
) -> None:
    """**Partial eligibility.** 60 eligible, then 40 eligible and 20 over.

    All-or-nothing here would make two employees who did the same 120 comments
    get different KPI results purely from how they split the job - which would
    make the shape of the paperwork worth more than the work.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    await counted(world, type_row=type_row, quantity=Decimal("60"), title="Seeding A", at=SEPTEMBER)
    await counted(
        world,
        type_row=type_row,
        quantity=Decimal("60"),
        title="Seeding B",
        at=SEPTEMBER + timedelta(minutes=1),
    )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))

    rows = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert [row.quota_status for row in rows] == [
        PrWorkQuotaStatus.ELIGIBLE,
        PrWorkQuotaStatus.PARTIALLY_ELIGIBLE,
    ]
    assert (rows[0].basis_amount, rows[0].eligible_amount, rows[0].over_quota_amount) == (
        Decimal("60.00"),
        Decimal("60.00"),
        Decimal("0.00"),
    )
    assert (rows[1].basis_amount, rows[1].eligible_amount, rows[1].over_quota_amount) == (
        Decimal("60.00"),
        Decimal("40.00"),
        Decimal("20.00"),
    )
    assert all(row.unit is PrWorkUnit.COMMENT for row in rows)


async def test_08b_a_third_contribution_after_the_cap_is_wholly_over_quota(
    world: World,
) -> None:
    """Once the capacity is gone, the next 30 comments are ``OVER_QUOTA``.

    Distinct from ``NO_QUOTA``: a cap exists and it is used up, which is a
    different sentence and a different management response.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    for index, amount in enumerate((60, 60, 30)):
        await counted(
            world,
            type_row=type_row,
            quantity=Decimal(amount),
            title=f"Seeding {index}",
            at=SEPTEMBER + timedelta(minutes=index),
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    rows = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert rows[2].quota_status is PrWorkQuotaStatus.OVER_QUOTA
    assert rows[2].eligible_amount == Decimal("0.00")
    assert rows[2].over_quota_amount == Decimal("30.00")


async def test_09_quantity_applies_credit_weight(world: World) -> None:
    """``quantity * credit_weight``, in Decimal.

    A half share of 100 comments is 50 comments of quota, and the arithmetic is
    exact rather than 49.999999999999996.
    """
    assert measure_contribution(
        PrWorkQuotaBasis.QUANTITY,
        quantity=Decimal("100"),
        credit_weight=Decimal("0.5000"),
    ).amount == Decimal("50.00")
    assert measure_contribution(
        PrWorkQuotaBasis.QUANTITY,
        quantity=Decimal("0.5"),
        credit_weight=Decimal("1.0000"),
    ).amount == Decimal("0.50")


async def test_10_item_count_ignores_credit_weight(world: World) -> None:
    """One valid contribution is one item, whatever the weight says.

    ``credit_weight`` records a *minor share*; using it to divide an item would
    make "how much of this counts towards my plan" depend on a field that means
    something else.
    """
    for weight in (Decimal("1.0000"), Decimal("0.5000"), Decimal("0.0001")):
        assert measure_contribution(
            PrWorkQuotaBasis.ITEM_COUNT,
            quantity=Decimal("100"),
            credit_weight=weight,
        ).amount == Decimal("1.00")


async def test_11_an_incompatible_quantity_unit_is_refused(world: World) -> None:
    """The **work type** is the semantic authority.

    A ``QUANTITY`` cap in ``VIDEO`` on a type that counts comments would
    silently never fill, so it is refused at configuration rather than
    discovered at the end of the month.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    draft = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_plans.add_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=draft.plan.id,
            work_type_id=type_row.id,
            target_value=Decimal("2000"),
            eligibility_cap=Decimal("2000"),
            basis=PrWorkQuotaBasis.QUANTITY,
            unit=PrWorkUnit.VIDEO,
        )
    assert caught.value.details["reason"] == "unit_does_not_match_work_type"

    # And an ITEM_COUNT cap on a quantity-measured type is refused too: it would
    # score a hundred comments as one.
    with pytest.raises(PrValidationError) as mismatch:
        await world.services.work_plans.add_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=draft.plan.id,
            work_type_id=type_row.id,
            target_value=Decimal("20"),
            eligibility_cap=Decimal("20"),
            basis=PrWorkQuotaBasis.ITEM_COUNT,
        )
    assert mismatch.value.details["reason"] == "basis_does_not_match_work_type"


async def test_12_a_missing_quantity_does_not_become_one(world: World) -> None:
    """A quantity-measured item with no quantity is **never read as 1**.

    Silently treating it as a single unit would turn a data-entry mistake into a
    hundred comments' worth of KPI.

    The domain function **returns** a reason rather than raising, and the
    evaluator materialises ``UNMEASURABLE`` rather than skipping the row.
    Skipping was the semantic bug this patch fixes: it left the contribution with
    no allocation at all, which the read path then reported as ``NO_QUOTA`` - the
    one thing it definitely was not.
    """
    measurement = measure_contribution(
        PrWorkQuotaBasis.QUANTITY, quantity=None, credit_weight=Decimal("1.0000")
    )
    assert measurement.amount is None
    assert measurement.reason is PrWorkUnmeasurableReason.MISSING_QUANTITY
    assert measurement.is_measurable is False

    period = await month(world)
    type_row = await seeding_type(world)
    await counted(world, type_row=type_row, quantity=None, title="Seeding chưa nhập", at=SEPTEMBER)
    await counted(
        world,
        type_row=type_row,
        quantity=Decimal("40"),
        title="Seeding có số",
        at=SEPTEMBER + timedelta(minutes=1),
    )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    outcome = await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    assert outcome.unmeasurable == 1
    rows = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert len(rows) == 2, "both are allocated - the unmeasurable one is not skipped"
    assert rows[0].quota_status is PrWorkQuotaStatus.UNMEASURABLE
    # Null, not 1 and not 0: there is no amount, and a zero would say the
    # employee produced nothing.
    assert rows[0].basis_amount is None
    assert rows[0].reason_code is PrWorkUnmeasurableReason.MISSING_QUANTITY
    assert rows[1].basis_amount == Decimal("40.00")
    assert rows[1].quota_status is PrWorkQuotaStatus.ELIGIBLE


# ===========================================================================
# 13-16: PLAN VALIDATION AND PERIOD IMMUTABILITY
# ===========================================================================


async def test_13_two_quotas_for_one_work_type_are_refused(world: World) -> None:
    """An ambiguous quota match would be a question with two answers."""
    period = await month(world)
    type_row = await work_type(world)
    draft = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        work_type_id=type_row.id,
        target_value=Decimal("20"),
        eligibility_cap=Decimal("20"),
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_plans.add_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=draft.plan.id,
            work_type_id=type_row.id,
            target_value=Decimal("5"),
            eligibility_cap=Decimal("5"),
        )
    assert caught.value.details["reason"] == "duplicate_work_type_quota"


async def test_13b_a_cap_below_the_target_is_refused(world: World) -> None:
    """A plan asking for more work than it would call eligible.

    Checked on the **merged** pair, so editing one field against an old value of
    the other cannot slip an invalid plan through.
    """
    period = await month(world)
    type_row = await work_type(world)
    draft = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_plans.add_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=draft.plan.id,
            work_type_id=type_row.id,
            target_value=Decimal("20"),
            eligibility_cap=Decimal("10"),
        )
    assert caught.value.details["reason"] == "cap_below_target"

    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        work_type_id=type_row.id,
        target_value=Decimal("10"),
        eligibility_cap=Decimal("20"),
    )
    quota = (
        (
            await world.session.execute(
                select(PrWorkQuota).where(PrWorkQuota.plan_id == draft.plan.id)
            )
        )
        .scalars()
        .one()
    )
    with pytest.raises(PrValidationError) as raised:
        await world.services.work_plans.update_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=draft.plan.id,
            quota_id=quota.id,
            target_value=Decimal("30"),
        )
    assert raised.value.details["reason"] == "cap_below_target"


async def test_14_a_plan_cannot_be_approved_for_a_closed_period(world: World) -> None:
    """Putting a target into force for a month whose numbers were agreed."""
    period = await month(world)
    type_row = await work_type(world)
    draft = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        work_type_id=type_row.id,
        target_value=Decimal("20"),
        eligibility_cap=Decimal("20"),
    )
    period.status = PrPeriodStatus.CLOSED
    await world.session.flush()
    with pytest.raises(PrWorkPeriodNotOpenError) as caught:
        await world.services.work_plans.approve(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.plan.id
        )
    assert caught.value.details["reason"] == "period_not_open"


async def test_14b_an_empty_plan_cannot_be_approved(world: World) -> None:
    """Approving nothing would put a plan in force that decides nothing.

    Refused here rather than approved and left for the evaluator to shrug at.
    """
    period = await month(world)
    draft = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_plans.approve(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.plan.id
        )
    assert caught.value.details["reason"] == "plan_has_no_quotas"


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_15_16_recompute_is_refused_on_a_closed_or_locked_period(
    world: World, state: PrPeriodStatus
) -> None:
    """**No automatic historical rewrite, and no ``force`` flag.**

    Refused rather than silently skipped: a no-op would hide somebody
    reconciling the wrong month and believing it worked.
    """
    period = await month(world, status=state)
    with pytest.raises(PrWorkPeriodNotOpenError) as caught:
        await world.services.work_eligibility.reconcile_period(
            actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
        )
    assert caught.value.details["period_status"] == state.value
    assert caught.value.code == "pr_work_period_not_open"


async def test_16b_a_closed_period_keeps_the_allocations_it_had(world: World) -> None:
    """20 eligible and #21 over quota stay that way once the period closes.

    The gap stays, and promoting #21 is a correction with its own trail rather
    than something a projection does on a timer.
    """
    period = await month(world)
    type_row = await work_type(world)
    for index in range(3):
        await counted(
            world, type_row=type_row, title=f"K{index}", at=SEPTEMBER + timedelta(minutes=index)
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("2"), Decimal("2")),))
    before = [
        (str(row.work_contribution_id), row.quota_status.value)
        for row in await allocations_for(world, user=world.member, period=period)
    ]

    period.status = PrPeriodStatus.LOCKED
    await world.session.flush()
    with pytest.raises(PrWorkPeriodNotOpenError):
        await world.services.work_eligibility.reconcile_period(
            actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
        )
    after = [
        (str(row.work_contribution_id), row.quota_status.value)
        for row in await allocations_for(world, user=world.member, period=period)
    ]
    assert after == before
    # And the read path still works on a locked period - it materialises
    # nothing, so reporting a reported month is safe.
    summary = await summary_for(world, user=world.member, period=period)
    assert progress(summary, "SHORT_SCRIPT").eligible_amount == Decimal("2.00")


# ===========================================================================
# 17-19: VERSIONS AND REVISIONS
# ===========================================================================


async def test_17_a_draft_revision_leaves_the_approved_plan_in_force(world: World) -> None:
    """Nothing changes for the employee until the revision is approved.

    Which is what somebody revising a plan actually wants - and if it is never
    approved, nothing changed at all.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    v1 = await approved_plan(world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),))

    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=v1.id
    )
    assert revision.plan.version_no == 2
    assert revision.plan.status is PrWorkPlanStatus.DRAFT
    assert revision.plan.supersedes_plan_id == v1.id
    # Quotas are copied by value, so editing the draft cannot reach the version
    # still in force.
    assert len(revision.quotas) == 1

    await world.session.refresh(v1)
    assert v1.status is PrWorkPlanStatus.APPROVED
    summary = await summary_for(world, user=world.member, period=period)
    assert summary.plan_id == v1.id and summary.plan_version_no == 1


async def test_18_approving_a_revision_atomically_supersedes_the_old_plan(
    world: World,
) -> None:
    """One transaction: v1 becomes SUPERSEDED and v2 becomes APPROVED.

    And the partial unique index means only one of them can ever be in force,
    whatever two concurrent approvals believe.
    """
    period = await month(world)
    type_row = await work_type(world)
    v1 = await approved_plan(world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),))
    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=v1.id
    )
    approved = await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=revision.plan.id
    )

    await world.session.refresh(v1)
    assert v1.status is PrWorkPlanStatus.SUPERSEDED
    assert v1.superseded_at is not None
    # A superseded plan keeps saying when it was approved: forgetting would lose
    # the answer the version chain exists to give.
    assert v1.approved_at is not None
    assert approved.plan.status is PrWorkPlanStatus.APPROVED
    in_force = (
        await world.session.execute(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(
                PrWorkPlan.user_id == world.member.id,
                PrWorkPlan.period_id == period.id,
                PrWorkPlan.status == PrWorkPlanStatus.APPROVED,
            )
        )
    ).scalar_one()
    assert in_force == 1


async def test_19_a_revision_recomputes_an_open_period(world: World) -> None:
    """Raising the cap promotes ``OVER_QUOTA`` work to ``ELIGIBLE``.

    Permitted **because the period is open**, and it is what makes "the numbers
    move when the decision does" true rather than aspirational.
    """
    period = await month(world)
    type_row = await work_type(world)
    for index in range(3):
        await counted(
            world, type_row=type_row, title=f"K{index}", at=SEPTEMBER + timedelta(minutes=index)
        )
    v1 = await approved_plan(world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),))
    rows = await allocations_for(world, user=world.member, period=period)
    assert sum(1 for row in rows if row.quota_status is PrWorkQuotaStatus.OVER_QUOTA) == 2

    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=v1.id
    )
    await world.services.work_plans.update_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=revision.plan.id,
        quota_id=revision.quotas[0].id,
        target_value=Decimal("3"),
        eligibility_cap=Decimal("3"),
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=revision.plan.id
    )

    rows = await allocations_for(world, user=world.member, period=period)
    assert all(row.quota_status is PrWorkQuotaStatus.ELIGIBLE for row in rows)
    # The provenance moved with the decision: the allocations now name v2.
    assert {row.work_plan_id for row in rows} == {revision.plan.id}


async def test_19b_an_approved_plan_cannot_be_edited_in_place(world: World) -> None:
    """The refusal names the status **and** the next step: revise it."""
    period = await month(world)
    type_row = await work_type(world)
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),)
    )
    quota = (
        (await world.session.execute(select(PrWorkQuota).where(PrWorkQuota.plan_id == plan.id)))
        .scalars()
        .one()
    )
    for call in (
        world.services.work_plans.update_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=plan.id,
            quota_id=quota.id,
            target_value=Decimal("99"),
        ),
        world.services.work_plans.remove_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=plan.id,
            quota_id=quota.id,
        ),
    ):
        with pytest.raises(PrWorkPlanStateError) as caught:
            await call
        assert caught.value.details["reason"] == "plan_not_draft"
        assert caught.value.details["next"] == "revise"


# ===========================================================================
# 20-21: WORK COUNTED BEFORE M2 EXISTED
# ===========================================================================


async def test_20_a_counted_row_with_no_allocation_reads_as_no_quota(world: World) -> None:
    """The state every contribution counted before M2 shipped is in.

    Read through a left join, so a missing allocation is ``NO_QUOTA`` rather
    than an error or an empty screen - and the read materialises nothing.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    # Simulate the pre-M2 world: the work is counted and nothing has evaluated
    # it. (The approval hook wrote nothing anyway - there was no plan - but the
    # allocation row it may have written is removed to make the case explicit.)
    for row in await allocations_for(world, user=world.member, period=period):
        await world.session.delete(row)
    await world.session.flush()

    _, subject, rows = await world.services.work_eligibility.eligibility(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert subject == world.member.id
    assert len(rows) == 1
    assert rows[0].quota_status is PrWorkQuotaStatus.NO_QUOTA
    assert rows[0].is_materialised is False
    assert rows[0].eligible_amount == Decimal("0.00")
    # Nothing was written by the read.
    assert await allocations_for(world, user=world.member, period=period) == []


async def test_21_reconcile_materialises_pre_existing_rows_idempotently(
    world: World,
) -> None:
    """The explicit path that makes the module converge, with no backfill."""
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    for row in await allocations_for(world, user=world.member, period=period):
        await world.session.delete(row)
    await world.session.flush()

    first = await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    assert first.created == 1 and first.users >= 1
    second = await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    assert second.created == 0 and second.updated == 0 and second.removed == 0
    assert len(await allocations_for(world, user=world.member, period=period)) == 1


# ===========================================================================
# 22-25: WHO MAY DO WHAT
# ===========================================================================


async def test_22_an_employee_reads_their_own_plan(world: World) -> None:
    """Own approved plan, own quota progress, own eligibility."""
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))

    mine = await world.services.work_plans.plan_in_force(
        actor=world.actor(world.member), user_id=None, period_id=period.id
    )
    assert mine is not None and mine.plan.user_id == world.member.id
    # And they may not edit it or approve it, whatever they can see. Since KPI
    # self-service they *may* propose a revision of it - ``can_revise`` - which
    # starts a draft the manager decides on, and changes nothing in force.
    assert mine.can_edit is False and mine.can_approve is False and mine.can_revise is True
    assert mine.is_subject is True and mine.can_submit is False

    summary = await world.services.work_eligibility.summary(
        actor=world.actor(world.member), user_id=None, period_id=period.id
    )
    assert summary.user_id == world.member.id
    assert progress(summary, "SHORT_SCRIPT").eligible_amount == Decimal("1.00")


async def test_23_an_employee_cannot_decide_their_own_quota(world: World) -> None:
    """The whole point, restated for KPI self-service: somebody may *propose*
    their own cap, and somebody else decides it.

    The manager's ``create_plan`` - the one that names a subject - stays behind
    ``PR_WORK_CONFIGURE``; an employee starts their own draft through
    ``self_create_plan``, which takes no subject at all. Approving is never the
    subject's, and a revision of somebody *else's* plan is not theirs to start.
    """
    period = await month(world)
    type_row = await work_type(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_plans.create_plan(
            actor=world.actor(world.member),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    # Not their plan: told it does not exist, the same answer the detail gives.
    with pytest.raises(PrNotFoundError):
        await world.services.work_plans.revise(
            actor=world.actor(world.other), request_id=world.request_id, plan_id=plan.id
        )
    # Their own proposal is theirs to start, and never theirs to approve.
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=plan.id
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_plans.approve(
            actor=world.actor(world.member), request_id=world.request_id, plan_id=draft.plan.id
        )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_eligibility.reconcile_period(
            actor=world.actor(world.member), request_id=world.request_id, period_id=period.id
        )


async def test_24_a_team_lead_gains_no_quota_administration(world: World) -> None:
    """``PR_WORK_MANAGE`` is not ``PR_WORK_CONFIGURE``. **The M1 scope model kept.**

    A Trưởng nhóm may assign work, accept proposals and validate what somebody
    finished. None of that implies deciding an arbitrary colleague's KPI
    targets, and MeoBot models no team that would make a narrower middle ground
    honest - so quota configuration stays at ADMIN and above.
    """
    period = await month(world)
    type_row = await work_type(world)
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    lead = world.actor(world.lead)

    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_plans.create_plan(
            actor=lead,
            request_id=world.request_id,
            user_id=world.other.id,
            period_id=period.id,
        )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_plans.approve(
            actor=lead, request_id=world.request_id, plan_id=plan.id
        )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_periods.ensure_month_period(
            actor=lead, request_id=world.request_id, year=2026, month=10
        )
    # Nor may they read a colleague's plan: that is ``PR_WORK_VIEW_ALL``, which
    # a TEAM_LEAD does not hold, and the refusal is a refusal rather than a
    # silent narrowing to their own figures.
    with pytest.raises(PrNotFoundError):
        await world.services.work_plans.detail(actor=lead, plan_id=plan.id)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work_eligibility.summary(
            actor=lead, user_id=world.member.id, period_id=period.id
        )
    assert caught.value.details["reason"] == "user_filter_not_permitted"


async def test_25_admin_and_owner_can_configure(world: World) -> None:
    """``PR_WORK_CONFIGURE`` is ``settings.write`` - ADMIN and OWNER."""
    period = await month(world)
    type_row = await work_type(world)
    detail = await world.services.work_plans.create_plan(
        actor=world.actor(world.head),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    assert detail.can_edit is True
    # An empty draft cannot be approved, so the flag says so too.
    assert detail.can_approve is False
    await world.services.work_plans.add_quota(
        actor=world.actor(world.head),
        request_id=world.request_id,
        plan_id=detail.plan.id,
        work_type_id=type_row.id,
        target_value=Decimal("5"),
        eligibility_cap=Decimal("5"),
    )
    approved = await world.services.work_plans.approve(
        actor=world.actor(world.head), request_id=world.request_id, plan_id=detail.plan.id
    )
    assert approved.plan.status is PrWorkPlanStatus.APPROVED
    assert approved.can_edit is False, "an approved plan is immutable for everybody"
    assert approved.can_revise is True


# ===========================================================================
# 26: NO POINTS
# ===========================================================================


async def test_26_no_scoring_field_or_word_appears_in_the_api(world: World) -> None:
    """**M2 decides eligibility and awards nothing.** M6 owns points.

    Structural, so adding ``base_score`` without deciding to is a red test
    rather than a quiet feature. The companion assertion is M1's ``test_40``,
    which sweeps the same words over both milestones' modules.
    """
    import pathlib

    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))

    world.act_as(world.member)
    response = world.client.get(f"/api/pr/work/eligibility/summary?period_id={period.id}")
    assert response.status_code == 200
    body = response.text
    for word in ("base_score", "awarded_score", "quality_multiplier", "score_total", "bonus"):
        assert word not in body, word
    # And the Vietnamese says "đủ điều kiện", never "đã được tính điểm".
    payload = response.json()
    assert payload["types"], payload
    assert "score" not in str(payload).lower()

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    for name in (
        "application/pr_work_quota_service.py",
        "application/pr_work_plan_service.py",
        "api/routers/pr_work_quota.py",
    ):
        code = "\n".join(
            line
            for line in (root / name).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith(("#", "*", '"'))
        )
        for word in ("base_score", "awarded_score", "points", "quality_multiplier", "bonus"):
            assert f"{word} =" not in code and f"{word}:" not in code, (name, word)


# ===========================================================================
# 27-28: ONE JOB IS NOT ONE PERSON'S WORKLOAD
# ===========================================================================


async def test_27_each_contributor_is_evaluated_independently(world: World) -> None:
    """A three-person shoot is one work item and three separate quota decisions.

    Each person has their own plan, their own cap and their own allocation; the
    department's count and each employee's count stay different numbers.
    """
    period = await month(world)
    type_row = await work_type(world, code="SHOOT", name="Quay")
    await counted(
        world,
        type_row=type_row,
        contributors=(world.member, world.other),
        title="Quay TVC",
        at=SEPTEMBER,
    )
    # Only one of the two has an approved plan.
    await approved_plan(
        world, period=period, user=world.member, quotas=((type_row, Decimal("1"), Decimal("1")),)
    )
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )

    mine = await allocations_for(world, user=world.member, period=period)
    theirs = await allocations_for(world, user=world.other, period=period)
    assert [row.quota_status for row in mine] == [PrWorkQuotaStatus.ELIGIBLE]
    assert [row.quota_status for row in theirs] == [PrWorkQuotaStatus.NO_QUOTA]
    # One job, two people's workload - and neither figure was divided.
    assert mine[0].basis_amount == Decimal("1.00")
    assert theirs[0].basis_amount == Decimal("1.00")
    summary = await summary_for(world, user=world.member, period=period)
    assert summary.counted_work_items == 1 and summary.counted_contributions == 1


async def test_28_one_hundred_comments_is_one_item_and_one_hundred_units(
    world: World,
) -> None:
    """The reason ``QUANTITY`` exists.

    A quota on ``ITEM_COUNT`` would score this as 1, and a department that
    noticed would start filing a hundred rows.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    item = await counted(
        world, type_row=type_row, quantity=Decimal("100"), title="Seeding 100", at=SEPTEMBER
    )
    await approved_plan(
        world, period=period, quotas=((type_row, Decimal("3000"), Decimal("3000")),)
    )

    items = (
        await world.session.execute(
            select(func.count()).select_from(PrWorkItem).where(PrWorkItem.id == item.id)
        )
    ).scalar_one()
    assert items == 1, "one job, not a hundred"
    row = progress(await summary_for(world, user=world.member, period=period), "SEEDING_COMMENT")
    assert row.counted_contributions == 1
    assert row.counted_amount == Decimal("100.00")
    assert row.eligible_amount == Decimal("100.00")
    assert row.unit is PrWorkUnit.COMMENT


# ===========================================================================
# 29-32: RETRIES, IDEMPOTENCY AND THE APPROVAL PATH
# ===========================================================================


async def test_29_the_cap_is_never_oversubscribed_by_a_repeated_evaluation(
    world: World,
) -> None:
    """Cap + 1 eligible must be unrepresentable, however often the projection
    runs.

    The offline half of the guarantee; the concurrent half is
    ``tests/integration/test_pr_work_quota_concurrency.py``, which needs real
    row locks.
    """
    period = await month(world)
    type_row = await work_type(world)
    for index in range(4):
        await counted(
            world, type_row=type_row, title=f"K{index}", at=SEPTEMBER + timedelta(minutes=index)
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("2"), Decimal("2")),))
    for _ in range(3):
        await world.services.work_eligibility.evaluate(user_id=world.member.id, period=period)

    rows = await allocations_for(world, user=world.member, period=period)
    assert len(rows) == 4
    assert sum(row.eligible_amount for row in rows) == Decimal("2.00")


async def test_30_quantity_capacity_is_never_exceeded_across_recomputes(
    world: World,
) -> None:
    """The same guarantee for partial allocation: the parts still sum to the cap."""
    period = await month(world)
    type_row = await seeding_type(world)
    for index, amount in enumerate((60, 60, 60)):
        await counted(
            world,
            type_row=type_row,
            quantity=Decimal(amount),
            title=f"S{index}",
            at=SEPTEMBER + timedelta(minutes=index),
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    for _ in range(3):
        await world.services.work_eligibility.evaluate(user_id=world.member.id, period=period)

    rows = await allocations_for(world, user=world.member, period=period)
    assert sum(row.eligible_amount for row in rows) == Decimal("100.00")
    assert sum(row.over_quota_amount for row in rows) == Decimal("80.00")
    # And every row reconciles on its own.
    for row in rows:
        assert row.basis_amount == row.eligible_amount + row.over_quota_amount


async def test_31_approving_the_same_plan_twice_is_refused(world: World) -> None:
    """A retried HTTP request is safe: the second attempt finds ``APPROVED``."""
    period = await month(world)
    type_row = await work_type(world)
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),)
    )
    with pytest.raises(PrWorkPlanStateError) as caught:
        await world.services.work_plans.approve(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
        )
    assert caught.value.details["reason"] == "plan_not_draft"


async def test_32_the_approval_path_projects_eligibility_without_depending_on_it(
    world: World, frozen_work_clock: datetime
) -> None:
    """Work becomes ``COUNTED`` whether or not a quota exists, and is projected
    in the same transaction when one does.

    Both halves matter: validation must never depend on a quota, and an
    administrator must not have to remember to reconcile after every approval.
    """
    # **This** month, because the approval path resolves the period from the
    # ``counted_at`` the service stamps - which is now. Every other test in this
    # file stamps a chosen instant afterwards and reconciles; this one is
    # precisely about the automatic path, so it cannot.
    today = frozen_work_clock.astimezone(world.settings.timezone).date()
    period = await month(world, year=today.year, number=today.month)
    type_row = await work_type(world)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),))

    item = await counted(world, type_row=type_row, title="Có hạn mức")
    rows = await allocations_for(world, user=world.member, period=period)
    assert len(rows) == 1 and rows[0].quota_status is PrWorkQuotaStatus.ELIGIBLE

    # A second kind of work, with no quota at all. It still counts.
    other_type = await work_type(world, code="RESEARCH_NOTE", name="Ghi chú nghiên cứu")
    second = await counted(world, type_row=other_type, title="Không có hạn mức")
    contributions = (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == second.id)
            )
        )
        .scalars()
        .all()
    )
    assert all(row.counted_at is not None for row in contributions)
    statuses = {
        row.quota_status for row in await allocations_for(world, user=world.member, period=period)
    }
    assert PrWorkQuotaStatus.NO_QUOTA in statuses
    assert item.id != second.id


async def test_32b_work_counted_into_a_month_with_no_period_still_counts(
    world: World,
) -> None:
    """No reporting period is an operational fact, not a reason to refuse work.

    The contribution is ``COUNTED``, no allocation is written, and the period is
    **not** invented on somebody's behalf - because inventing it would silently
    attach a KPI decision to a month nobody agreed to.
    """
    type_row = await work_type(world)
    item = await counted(world, type_row=type_row, title="Không có kỳ báo cáo")
    contributions = (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
            )
        )
        .scalars()
        .all()
    )
    assert all(row.count_status.value == "COUNTED" for row in contributions)
    total = (
        await world.session.execute(select(func.count()).select_from(PrWorkQuotaAllocation))
    ).scalar_one()
    assert total == 0
    periods = (
        await world.session.execute(select(func.count()).select_from(PrReportingPeriod))
    ).scalar_one()
    assert periods == 0, "no period was invented"


# ===========================================================================
# 33: NOTHING ELSE CHANGED, AND THE PERIOD MODEL
# ===========================================================================


async def test_33_no_content_or_task_semantics_changed(world: World) -> None:
    """M2 is additive. It reads no content item and no task, and writes neither."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    for name in (
        "application/pr_work_quota_service.py",
        "application/pr_work_plan_service.py",
        "application/pr_work_period_service.py",
        "db/models/pr_work_quota.py",
    ):
        # **Identifiers**, parsed rather than grepped. The *reasoning* for not
        # using a channel assignment as a team proxy is written down in these
        # modules on purpose, and a text sweep would forbid explaining the
        # decision it is meant to enforce.
        names = _identifiers(root / name)
        assert "PrContentItem" not in names, name
        assert "PrTask" not in names, name
        # And no team is invented, from channel assignments or otherwise.
        assert "PrChannelAssignment" not in names, name

    from meobot.db.base import Base

    # ``org_units`` is the PR / Ads unit registry from 0042 - a wall, not a
    # hierarchy - and the quota module still never touches it (see the
    # identifier sweep above).
    for forbidden in ("teams", "team_members", "departments"):
        assert forbidden not in Base.metadata.tables


async def test_33b_a_weekly_period_cannot_carry_a_kpi_plan(world: World) -> None:
    """One contribution, one decision.

    15 September is inside both ``2026-W38`` and ``2026-09``; if a plan could
    target either, one contribution could be claimed by two approved quotas -
    and there is exactly one allocation per contribution, deliberately. M2
    refuses the week with a stable error rather than inventing a precedence rule.
    """
    week = PrReportingPeriod(
        code="2026-W38",
        period_type=PrPeriodType.WEEK,
        date_start=SEPTEMBER.date(),
        date_end=SEPTEMBER.date(),
        status=PrPeriodStatus.OPEN,
    )
    world.session.add(week)
    await world.session.flush()
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_plans.create_plan(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=week.id,
        )
    assert caught.value.details["reason"] == "period_type_not_supported"


async def test_33c_opening_a_month_is_idempotent_and_reuses_the_calendar(
    world: World,
) -> None:
    """No second calendar, and a retried request does not reopen a closed month."""
    first = await month(world, number=9)
    assert first.code == month_period_code(2026, 9) == "2026-09"
    assert (first.date_start, first.date_end) == month_bounds(2026, 9)
    assert first.period_type is PrPeriodType.MONTH

    first.status = PrPeriodStatus.CLOSED
    await world.session.flush()
    again = await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner), request_id=world.request_id, year=2026, month=9
    )
    assert again.id == first.id
    assert again.status is PrPeriodStatus.CLOSED, "a retry does not reopen a closed period"

    # A following month links back explicitly, so a comparison survives a gap.
    october = await month(world, number=10)
    assert october.previous_period_id == first.id


async def test_33d_the_audit_trail_records_every_quota_decision(world: World) -> None:
    """Who set a target, who raised a cap, and who asked for a recompute.

    The version chain plus the allocation's own provenance answers *"which
    version decided this"*; the audit rows answer *"who did it and when"*. No
    second domain history table repeats either.
    """
    period = await month(world)
    type_row = await work_type(world)
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),)
    )
    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=revision.plan.id
    )
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )

    actions = {
        row
        for row in ((await world.session.execute(select(AuditLog.action))).scalars().all())
        if row.startswith("pr.work_p") or row == "pr.work.eligibility_reconciled"
    }
    assert {
        "pr.work_period.created",
        "pr.work_plan.created",
        "pr.work_plan.quota_added",
        "pr.work_plan.approved",
        "pr.work_plan.revised",
        "pr.work_plan.superseded",
        "pr.work.eligibility_reconciled",
    } <= actions, actions


# ===========================================================================
# 34-46: THE SEMANTIC PATCH - "no allocation row" is not "no quota"
# ===========================================================================
#
# The bug this section exists for: a counted contribution with no allocation was
# read back as ``NO_QUOTA``, whatever the reason it had no row. ``NO_QUOTA`` is a
# **business claim** - *nobody set a target for this kind of work* - and an
# absent row is not evidence for it. Three different things were being collapsed
# into one sentence, and two of them sent the reader to the wrong person.


async def test_34_no_approved_plan_at_all_is_no_quota(world: World) -> None:
    """Case A, unchanged and still the whole point of the status.

    Nothing approved anywhere, so the claim *"nobody set a target"* is simply
    true - and it stays true on the read path, which establishes it with a
    lookup rather than by defaulting to it.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)

    _, _, rows = await world.services.work_eligibility.eligibility(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert [row.quota_status for row in rows] == [PrWorkQuotaStatus.NO_QUOTA]
    assert rows[0].is_materialised is False

    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    stored = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in stored] == [PrWorkQuotaStatus.NO_QUOTA]
    assert stored[0].reason_code is None


async def test_34b_the_approval_hook_materialises_nothing_without_an_approved_plan(
    world: World,
) -> None:
    """**The distinction this rule exists for.**

    *Nobody has an approved plan for this person and month* and *a plan is in
    force and does not cover this work type* are two different states, and only
    the second is a decision. The incremental hook fires on every approval and
    knows nothing about what a manager will eventually plan, so writing a
    materialised ``NO_QUOTA`` row from it would record an evaluation that never
    happened - against a plan that does not exist.

    Skipping loses no sentence: the read below still says ``NO_QUOTA``, because
    the claim *"nobody set a target"* is true and establishable by a lookup. It
    says it with ``is_materialised = False``, which is the module's word for
    *nothing has formally assessed this*.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)

    # The hook ran - the approval went through it - and wrote nothing.
    assert await allocations_for(world, user=world.member, period=period) == []

    _, _, rows = await world.services.work_eligibility.eligibility(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert [row.quota_status for row in rows] == [PrWorkQuotaStatus.NO_QUOTA]
    assert rows[0].is_materialised is False


async def test_34c_counted_work_stays_counted_without_a_plan(world: World) -> None:
    """**No M1 regression.** M2 waits; it does not veto.

    A valid contribution that somebody independently validated is ``COUNTED``
    whether or not anybody has written a KPI plan. The hook can never refuse an
    approval, and the new guard is a *return*, not a raise.
    """
    period = await month(world)
    type_row = await work_type(world)
    item = await counted(world, type_row=type_row, at=SEPTEMBER)

    await world.session.refresh(item)
    contributions = (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
            )
        )
        .scalars()
        .all()
    )
    assert [one.count_status for one in contributions] == [PrWorkCountStatus.COUNTED]
    assert all(one.counted_at is not None for one in contributions)
    assert await allocations_for(world, user=world.member, period=period) == []


async def test_34d_explicit_reconcile_still_materialises_without_a_plan(
    world: World,
) -> None:
    """**Case C, unchanged.** Automatic projection is not administration.

    ``reconcile_period`` is a deliberate act with a request id and
    ``PR_WORK_CONFIGURE`` behind it, and it still writes the ``NO_QUOTA`` rows.
    The guard is on the incremental hook alone, because that is the one that
    fires by itself on somebody else's approval.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    assert await allocations_for(world, user=world.member, period=period) == []

    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    stored = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in stored] == [PrWorkQuotaStatus.NO_QUOTA]
    assert stored[0].work_plan_id is None
    assert stored[0].reason_code is None


async def test_34e_a_plan_approved_later_evaluates_work_counted_before_it(
    world: World,
) -> None:
    """**The hole this rule could have opened, closed.**

    Work counted before anybody planned the month must not become permanently
    unevaluated. It does not, and not because a reconcile rescues it:
    ``PrWorkPlanService.approve`` finishes by recomputing the whole period, so
    approving the plan is itself the evaluation. The order of the two acts does
    not change the answer.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, title="Trước kế hoạch", at=SEPTEMBER)
    assert await allocations_for(world, user=world.member, period=period) == []

    # The manager plans the month afterwards. No reconcile is called.
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))

    stored = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in stored] == [PrWorkQuotaStatus.ELIGIBLE]
    assert stored[0].eligible_amount == Decimal("1.00")
    assert stored[0].work_plan_id is not None

    # And it is still a projection: reconciling on top changes nothing.
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    again = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in again] == [PrWorkQuotaStatus.ELIGIBLE]
    assert [row.eligible_amount for row in again] == [Decimal("1.00")]


async def test_34f_work_counted_after_the_plan_is_evaluated_by_the_hook(
    world: World,
) -> None:
    """The ordinary order, and the proof the guard is a guard and not a mute.

    Once a plan is in force the incremental hook materialises immediately, which
    is what makes an approval visible on the screen without an administrator
    running anything.
    """
    period = await month(world)
    type_row = await work_type(world)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))
    await counted(world, type_row=type_row, at=SEPTEMBER)

    stored = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in stored] == [PrWorkQuotaStatus.ELIGIBLE]
    assert stored[0].work_quota_id is not None


async def test_35_an_approved_plan_without_a_quota_for_the_type_is_no_quota(
    world: World,
) -> None:
    """Case A again, and the subtler half.

    A plan exists and covers *other* work. For this work type nobody decided
    anything, so ``NO_QUOTA`` is still the right sentence - the claim is about
    the work type, not about whether the employee has a plan at all.
    """
    period = await month(world)
    planned = await work_type(world)
    unplanned = await work_type(world, code="RESEARCH_NOTE", name="Ghi chú nghiên cứu")
    await counted(world, type_row=unplanned, title="Ghi chú", at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((planned, Decimal("5"), Decimal("5")),))

    rows = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in rows] == [PrWorkQuotaStatus.NO_QUOTA]
    assert rows[0].work_quota_id is None
    # And the read agrees, without materialising anything.
    _, _, read = await world.services.work_eligibility.eligibility(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert [row.quota_status for row in read] == [PrWorkQuotaStatus.NO_QUOTA]


async def test_36_an_approved_quota_and_no_quantity_is_unmeasurable(world: World) -> None:
    """**The bug, fixed.** Case B: there *is* a target, and no number to measure.

    Before this patch the contribution was skipped, got no allocation, and read
    back as ``NO_QUOTA`` - which told the employee their manager set no target
    when their manager had. It is now a materialised row that names the quota and
    says what is missing.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    await counted(world, type_row=type_row, quantity=None, title="Seeding chưa nhập", at=SEPTEMBER)
    await approved_plan(
        world, period=period, quotas=((type_row, Decimal("3000"), Decimal("3000")),)
    )

    rows = await allocations_for(world, user=world.member, period=period)
    assert len(rows) == 1
    assert rows[0].quota_status is PrWorkQuotaStatus.UNMEASURABLE
    assert rows[0].quota_status is not PrWorkQuotaStatus.NO_QUOTA
    # It names the quota - which is exactly what distinguishes it from NO_QUOTA.
    assert rows[0].work_quota_id is not None
    assert rows[0].work_plan_id is not None
    assert rows[0].reason_code is PrWorkUnmeasurableReason.MISSING_QUANTITY


async def test_37_a_renamed_unit_is_the_same_measure(world: World) -> None:
    """**Reversed by the period-container patch.** A unit is what a result is
    *called*, and an administrator may rename it on a type in use - "sản phẩm"
    to "khách hàng". A job filed under the old word and a quota written under
    the new one describe one measure, so the evaluator measures rather than
    declaring a mismatch. Which work type a quota covers is still the join, and
    that is the check that matters. The pure function keeps the branch for any
    caller that wants it; the service no longer asks for it.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    item = await counted(
        world, type_row=type_row, quantity=Decimal("50"), title="Seeding", at=SEPTEMBER
    )
    await world.services.work.update_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=type_row.id,
        default_unit=PrWorkUnit.MESSAGE,
    )
    await world.session.refresh(item)
    assert item.unit is PrWorkUnit.COMMENT, "the filed job keeps the word it was filed with"

    await approved_plan(
        world, period=period, quotas=((type_row, Decimal("3000"), Decimal("3000")),)
    )
    rows = await allocations_for(world, user=world.member, period=period)
    assert rows[0].quota_status is PrWorkQuotaStatus.ELIGIBLE
    assert rows[0].basis_amount == Decimal("50.00")
    assert rows[0].unit is PrWorkUnit.MESSAGE, "the quota's word, as written"
    # And the pure rule still exists for a caller that passes both units.
    assert (
        measure_contribution(
            PrWorkQuotaBasis.QUANTITY,
            quantity=Decimal("50"),
            credit_weight=Decimal("1"),
            item_unit=PrWorkUnit.COMMENT,
            quota_unit=PrWorkUnit.VIDEO,
        ).reason
        is PrWorkUnmeasurableReason.UNIT_MISMATCH
    )


async def test_38_an_amount_that_rounds_away_is_unmeasurable(world: World) -> None:
    """Case B, third reason. A contribution worth ``0.00`` is not eligible for
    nothing - it is a measurement that failed.

    Purely a domain check: the database's own constraints keep quantity above
    zero and credit weight inside ``(0, 1]``, so the product only reaches zero
    through rounding at the two-place scale.
    """
    measurement = measure_contribution(
        PrWorkQuotaBasis.QUANTITY,
        quantity=Decimal("0.01"),
        credit_weight=Decimal("0.0001"),
    )
    assert measurement.amount is None
    assert measurement.reason is PrWorkUnmeasurableReason.INVALID_QUANTITY


async def test_39_unmeasurable_is_never_eligible_and_carries_no_amount(
    world: World,
) -> None:
    """The three amounts, and why one of them is null rather than zero.

    ``eligible`` and ``over_quota`` are really zero - nothing is eligible either
    way. ``basis_amount`` is **null**, because zero is a measurement and this is
    the absence of one: summing it would say the employee produced nothing.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    await counted(world, type_row=type_row, quantity=None, title="Seeding", at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    row = (await allocations_for(world, user=world.member, period=period))[0]
    assert row.eligible_amount == Decimal("0.00")
    assert row.over_quota_amount == Decimal("0.00")
    assert row.basis_amount is None


async def test_40_no_quota_and_unmeasurable_stay_distinct_over_http(
    world: World,
) -> None:
    """Two states, two codes, two sentences - all the way to the browser.

    A client that could not tell them apart would put the same message on both,
    and one of the two would send somebody to the wrong person.
    """
    period = await month(world)
    measured = await seeding_type(world)
    unplanned = await work_type(world, code="RESEARCH_NOTE", name="Ghi chú")
    await counted(world, type_row=measured, quantity=None, title="Seeding", at=SEPTEMBER)
    await counted(world, type_row=unplanned, title="Ghi chú", at=SEPTEMBER + timedelta(minutes=1))
    await approved_plan(world, period=period, quotas=((measured, Decimal("100"), Decimal("100")),))

    world.act_as(world.member)
    response = world.client.get(f"/api/pr/work/eligibility?period_id={period.id}")
    assert response.status_code == 200
    by_status = {row["quota_status"]: row for row in response.json()["contributions"]}
    assert set(by_status) == {"UNMEASURABLE", "NO_QUOTA"}

    unmeasurable = by_status["UNMEASURABLE"]
    assert unmeasurable["reason_code"] == "MISSING_QUANTITY"
    assert unmeasurable["reason_label"] == "Thiếu số lượng công việc."
    assert unmeasurable["quota_status_label"] == "Chưa thể tính hạn mức"
    assert unmeasurable["basis_amount"] is None

    no_quota = by_status["NO_QUOTA"]
    assert no_quota["reason_code"] is None
    assert no_quota["reason_label"] is None
    assert no_quota["quota_status_label"] == "Chưa có hạn mức KPI"
    # Two different sentences, which is the whole point.
    assert no_quota["quota_status_hint"] != unmeasurable["quota_status_hint"]


async def test_41_a_missing_allocation_under_a_quota_is_pending_not_no_quota(
    world: World,
) -> None:
    """Case C on the read path. **The other half of the bug.**

    An approved quota covers the work type and nothing has evaluated the
    contribution - because it predates M2, or because a projection failed, or
    because the period has not been reconciled since the plan was approved. The
    honest answer is *"nothing has looked at this yet"*, and the previous answer
    was *"nobody set you a target"*, which was false.
    """
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))
    # Simulate the pre-M2 world, or a projection that never ran.
    for row in await allocations_for(world, user=world.member, period=period):
        await world.session.delete(row)
    await world.session.flush()

    _, _, rows = await world.services.work_eligibility.eligibility(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert [row.quota_status for row in rows] == [PrWorkQuotaStatus.PENDING_EVALUATION]
    assert rows[0].is_materialised is False
    # The **amount** is the counted work, measured live - the actual never
    # waits for M2. The **split** is unknown: nothing decided, and a zero
    # would be a decision.
    assert rows[0].basis_amount == Decimal("1.00")
    assert rows[0].eligible_amount is None
    assert rows[0].over_quota_amount is None
    # It still names the quota that is waiting to be applied.
    assert rows[0].work_quota_id is not None
    # And the read wrote nothing.
    assert await allocations_for(world, user=world.member, period=period) == []


async def test_42_pending_evaluation_can_never_be_stored(world: World) -> None:
    """It describes the **absence** of a row, so a row holding it would
    contradict itself - and would be indistinguishable from a real decision."""
    from meobot.domain.pr.work_quota import MATERIALISABLE_QUOTA_STATUSES

    assert PrWorkQuotaStatus.PENDING_EVALUATION not in MATERIALISABLE_QUOTA_STATUSES
    assert set(MATERIALISABLE_QUOTA_STATUSES) == set(PrWorkQuotaStatus) - {
        PrWorkQuotaStatus.PENDING_EVALUATION
    }
    # Every evaluator path materialises something in the set.
    period = await month(world)
    type_row = await seeding_type(world)
    await counted(world, type_row=type_row, quantity=None, title="A", at=SEPTEMBER)
    await counted(
        world,
        type_row=type_row,
        quantity=Decimal("10"),
        title="B",
        at=SEPTEMBER + timedelta(minutes=1),
    )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))
    for row in await allocations_for(world, user=world.member, period=period):
        assert row.quota_status in MATERIALISABLE_QUOTA_STATUSES


async def test_43_fixing_the_quantity_converges_while_the_period_is_open(
    world: World,
) -> None:
    """Part E. ``UNMEASURABLE`` is a state somebody can get *out* of.

    Which is the difference between a business state and an error: the row says
    what is missing, a person supplies it, and the next recompute reaches a real
    eligibility answer. Deterministic order is unchanged, so the fixed
    contribution takes the slot its ``counted_at`` earns it.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    first = await counted(world, type_row=type_row, quantity=None, title="Seeding A", at=SEPTEMBER)
    await counted(
        world,
        type_row=type_row,
        quantity=Decimal("80"),
        title="Seeding B",
        at=SEPTEMBER + timedelta(minutes=1),
    )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    before = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert before[0].quota_status is PrWorkQuotaStatus.UNMEASURABLE
    assert before[1].quota_status is PrWorkQuotaStatus.ELIGIBLE

    # Somebody types the number in. Both fields, because M1's
    # ``quantity_and_unit_together`` refuses "100" of nothing.
    item = await world.session.get(PrWorkItem, first.id)
    assert item is not None
    item.quantity = Decimal("60")
    item.unit = PrWorkUnit.COMMENT
    await world.session.flush()

    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    after = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert len(after) == 2, "converged in place - no duplicate allocation"
    # First counted, first inside the quota: A now takes 60 of the 100 and B is
    # split, which is the order it always had.
    assert after[0].quota_status is PrWorkQuotaStatus.ELIGIBLE
    assert after[0].basis_amount == Decimal("60.00")
    assert after[0].reason_code is None, "a resolved reason does not survive"
    assert after[1].quota_status is PrWorkQuotaStatus.PARTIALLY_ELIGIBLE
    assert after[1].eligible_amount == Decimal("40.00")
    assert after[1].over_quota_amount == Decimal("40.00")
    assert sum(row.eligible_amount for row in after) == Decimal("100.00")


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_44_45_an_unmeasurable_row_is_not_repaired_in_a_shut_period(
    world: World, state: PrPeriodStatus
) -> None:
    """Part F, unchanged by this patch. **No silent repair, and no ``force``.**

    Fixing the quantity after the numbers were agreed does not quietly move an
    employee's figures; the reconcile is refused, and the allocation stays as the
    period recorded it.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    item = await counted(world, type_row=type_row, quantity=None, title="Seeding", at=SEPTEMBER)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    row = await world.session.get(PrWorkItem, item.id)
    assert row is not None
    row.quantity = Decimal("60")
    row.unit = PrWorkUnit.COMMENT
    period.status = state
    await world.session.flush()

    with pytest.raises(PrWorkPeriodNotOpenError) as caught:
        await world.services.work_eligibility.reconcile_period(
            actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
        )
    assert caught.value.details["period_status"] == state.value
    stored = await allocations_for(world, user=world.member, period=period)
    assert stored[0].quota_status is PrWorkQuotaStatus.UNMEASURABLE
    assert stored[0].basis_amount is None


async def test_46_an_unexpected_failure_is_never_an_eligibility_decision(
    world: World,
) -> None:
    """Part D. **The rule that keeps a bug out of somebody's KPI.**

    A missing quantity is a business state and becomes ``UNMEASURABLE``. A
    projection that *crashes* is not a business state at all: nothing is
    materialised, nothing is fabricated, and the M1 approval still commits -
    which is the savepoint M2 already had, asserted against the new statuses.
    """
    period = await month(world)
    type_row = await work_type(world)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),))

    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("projection is down")

    original = world.services.work_eligibility.on_contributions_counted
    world.services.work_eligibility.on_contributions_counted = explode  # type: ignore[method-assign]
    try:
        item = await counted(world, type_row=type_row, title="Việc khi hỏng", at=SEPTEMBER)
    finally:
        world.services.work_eligibility.on_contributions_counted = original  # type: ignore[method-assign]

    # M1's promise: the work is still counted.
    contribution = (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
            )
        )
        .scalars()
        .one()
    )
    assert contribution.count_status.value == "COUNTED"
    # And no eligibility decision was invented for it - in particular not
    # ``NO_QUOTA``, which would be a claim about the plan that nobody made.
    assert await allocations_for(world, user=world.member, period=period) == []
    _, _, rows = await world.services.work_eligibility.eligibility(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert [row.quota_status for row in rows] == [PrWorkQuotaStatus.PENDING_EVALUATION]

    # An operator reconciles and the module converges.
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    stored = await allocations_for(world, user=world.member, period=period)
    assert [row.quota_status for row in stored] == [PrWorkQuotaStatus.ELIGIBLE]


async def test_47_the_summary_reports_unmeasurable_separately(world: World) -> None:
    """Part H. Counted, eligible, over quota, no quota **and unmeasurable** -
    five figures, and the unmeasurable one is a **count** because there is no
    amount to sum.

    A quantity of zero folded into the totals would say the employee produced
    nothing. ``measured_contributions`` is what makes the gap legible instead.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    for index, quantity in enumerate((Decimal("60"), Decimal("60"), None)):
        await counted(
            world,
            type_row=type_row,
            quantity=quantity,
            title=f"Seeding {index}",
            at=SEPTEMBER + timedelta(minutes=index),
        )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))

    row = progress(await summary_for(world, user=world.member, period=period), "SEEDING_COMMENT")
    assert row.counted_contributions == 3
    assert row.measured_contributions == 2, "the third has no amount to state"
    assert row.unmeasurable_contributions == 1
    assert row.pending_contributions == 0
    assert row.no_quota_contributions == 0
    # 120 comments, not 120 + 0. The missing one is counted, never summed.
    assert row.counted_amount == Decimal("120.00")
    assert row.eligible_amount == Decimal("100.00")
    assert row.over_quota_amount == Decimal("20.00")
    assert row.counted_amount == row.eligible_amount + row.over_quota_amount

    summary = await summary_for(world, user=world.member, period=period)
    assert summary.contributions_by_status["UNMEASURABLE"] == 1
    assert summary.contributions_by_status["NO_QUOTA"] == 0
    # Every status has a key, so a client can render them without guessing.
    assert set(summary.contributions_by_status) == {one.value for one in PrWorkQuotaStatus}


async def test_48_a_measurable_row_can_degrade_to_unmeasurable(world: World) -> None:
    """The reverse of test 43, and the reason the reason is *cleared* as well as
    set.

    A quantity removed from a work item turns an ``ELIGIBLE`` row into an
    ``UNMEASURABLE`` one, and its cap capacity goes back to the pool. Both
    directions have to write cleanly, or a recompute would trip
    ``ck_..._reason_matches_status`` in one of them.
    """
    period = await month(world)
    type_row = await seeding_type(world)
    first = await counted(
        world, type_row=type_row, quantity=Decimal("60"), title="Seeding A", at=SEPTEMBER
    )
    await counted(
        world,
        type_row=type_row,
        quantity=Decimal("60"),
        title="Seeding B",
        at=SEPTEMBER + timedelta(minutes=1),
    )
    await approved_plan(world, period=period, quotas=((type_row, Decimal("100"), Decimal("100")),))
    before = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert [row.quota_status for row in before] == [
        PrWorkQuotaStatus.ELIGIBLE,
        PrWorkQuotaStatus.PARTIALLY_ELIGIBLE,
    ]

    # Somebody clears the number. Both fields, per M1's pairing rule.
    item = await world.session.get(PrWorkItem, first.id)
    assert item is not None
    item.quantity = None
    item.unit = None
    await world.session.flush()

    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    after = sorted(
        await allocations_for(world, user=world.member, period=period),
        key=lambda row: row.created_at,
    )
    assert after[0].quota_status is PrWorkQuotaStatus.UNMEASURABLE
    assert after[0].basis_amount is None
    assert after[0].reason_code is PrWorkUnmeasurableReason.MISSING_QUANTITY
    # And B, which was split, now fits entirely: the capacity A was holding is
    # back in the pool.
    assert after[1].quota_status is PrWorkQuotaStatus.ELIGIBLE
    assert after[1].eligible_amount == Decimal("60.00")


def _identifiers(path) -> set[str]:  # type: ignore[no-untyped-def]
    """Every name a module actually *uses*, with prose excluded.

    Parsed with :mod:`ast` rather than grepped, so a docstring explaining why a
    table is not reached for does not read as reaching for it. That distinction
    is the whole reason these modules can document the decision they enforce.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.update(node.module.split("."))
    return found
