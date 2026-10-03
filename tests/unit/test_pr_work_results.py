"""Period containers and results: WORK is actual output, KPI is a target only.

Numbered against the brief's required cases (1-13), with the mechanics that
make them true as sections A-D. What this file is really testing:

**A stream is one row per employee, work type and month.** Every creation
path - a manager assigning a routine, an employee reporting, the content
projector - lands on the same container, and the partial unique index refuses
a second one.

**Results accumulate, and the target is read beside the sum.** "+3, +5, +2" is
three rows and an actual of 10. 27 against 20 is 135 % and 7 over; 8 against
nothing is 8 and no percentage; nothing is capped and nothing is refused.

**Points follow the actual.** M6 prices the whole counted amount at the rate in
force. 27 customers at 380 standard minutes each is 10 260 - not 7 600.

**M1's boundary holds at result grain.** Declaring is not being credited: a
result is ``PENDING`` until somebody who is not the subject counts it, and the
subject cannot count their own.
"""

from __future__ import annotations

# The ``world`` fixture comes from the production-lifecycle suite, like every
# other Work suite.
# ruff: noqa: F811
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import select

from meobot.application.pr_work_recurring_service import RecurringTemplateCommand
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.domain.pr.content_work import PrContentWorkKind
from meobot.domain.pr.errors import (
    PrConflictError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.recurring import PrRecurringFrequency
from meobot.domain.pr.work import (
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkCountStatus,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_results import PrWorkResultSource, compare_to_target
from tests.unit.test_pr_content_work_projection import (
    approved_content,
    content_results,
    project,
)
from tests.unit.test_pr_content_work_projection import (
    rule as mapping_rule,
)
from tests.unit.test_pr_performance import policy, schedule, snapshot
from tests.unit.test_pr_performance import rule as scoring_rule
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)
from tests.unit.test_pr_work_quota import approved_plan, month

pytestmark = pytest.mark.asyncio

#: 380 standard minutes a customer - the brief's "points per unit".
CUSTOMER_RATE = Decimal("380")


# ===========================================================================
# Helpers
# ===========================================================================


async def customers_type(world: World) -> PrWorkType:
    """*Tìm khách hàng*, measured by quantity, in customers."""
    return await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code="FIND_CUSTOMERS",
        name="Tìm khách hàng",
        category=PrWorkCategory.COMMUNITY,
        default_unit=PrWorkUnit.CUSTOMER,
        default_quota_basis=PrWorkQuotaBasis.QUANTITY,
    )


async def scripts_type(world: World) -> PrWorkType:
    """*Kịch bản video ngắn*, counted by item, in scripts."""
    return await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code="SHORT_VIDEO_SCRIPT",
        name="Kịch bản video ngắn",
        category=PrWorkCategory.CONTENT,
        default_unit=PrWorkUnit.SCRIPT,
        default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
    )


async def report(
    world: World,
    *,
    type_row: PrWorkType,
    quantity: Decimal | int,
    by: User | None = None,
    subject: User | None = None,
    period: PrReportingPeriod | None = None,
    label: str | None = None,
    link: str | None = None,
) -> PrWorkResult:
    """One declaration into the month's stream, by the subject unless told otherwise."""
    actor = by or subject or world.member
    return await world.services.work_results.report_result(
        actor=world.actor(actor),
        request_id=world.request_id,
        quantity=Decimal(quantity),
        label=label,
        link=link,
        work_type_id=type_row.id,
        subject_user_id=(subject or actor).id,
        period_id=period.id if period is not None else None,
    )


async def validate(world: World, item_id: uuid.UUID, *, by: User | None = None) -> None:
    await world.services.work_results.validate_results(
        actor=world.actor(by or world.owner), request_id=world.request_id, work_item_id=item_id
    )


async def container(world: World, item_id: uuid.UUID) -> PrWorkItem:
    row = await world.session.get(PrWorkItem, item_id)
    assert row is not None
    await world.session.refresh(row)
    return row


async def summary(world: World, item_id: uuid.UUID):  # type: ignore[no-untyped-def]
    row = await container(world, item_id)
    out = await world.services.work_results.summary(row)
    assert out is not None
    return out


async def contribution(world: World, item_id: uuid.UUID) -> PrWorkContribution:
    return (
        await world.session.execute(
            select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
        )
    ).scalar_one()


async def containers_for(world: World, type_row: PrWorkType, user: User) -> list[PrWorkItem]:
    return list(
        (
            await world.session.execute(
                select(PrWorkItem)
                .where(
                    PrWorkItem.work_type_id == type_row.id,
                    PrWorkItem.subject_user_id == user.id,
                )
                .order_by(PrWorkItem.execution_at)
            )
        )
        .scalars()
        .all()
    )


async def priced(world: World, type_row: PrWorkType, *, rate: Decimal = CUSTOMER_RATE) -> None:
    """A rate in force for the type, and the calendar M6 needs to make a month."""
    await schedule(world)
    await policy(world)
    await scoring_rule(world, type_row, minutes=rate)


# ===========================================================================
# 1-4: THE COMPARISON - UNDER, MET, OVER, AND NO TARGET
# ===========================================================================


async def test_01_kpi_20_actual_12_is_60_percent(world: World) -> None:
    period = await month(world)
    type_row = await customers_type(world)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))
    result = await report(world, type_row=type_row, quantity=12, period=period)
    await validate(world, result.work_item_id)

    figures = await summary(world, result.work_item_id)
    assert figures.counted_quantity == Decimal("12.00")
    assert figures.target_quantity == Decimal("20.00")
    assert figures.comparison.completion_percent == Decimal("60.0")
    assert figures.comparison.over_target == Decimal("0")
    assert figures.comparison.remaining == Decimal("8.00")
    assert figures.unit_label == "khách hàng"


async def test_02_kpi_20_actual_20_is_100_percent(world: World) -> None:
    period = await month(world)
    type_row = await customers_type(world)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))
    result = await report(world, type_row=type_row, quantity=20, period=period)
    await validate(world, result.work_item_id)

    figures = await summary(world, result.work_item_id)
    assert figures.comparison.completion_percent == Decimal("100.0")
    assert figures.comparison.is_met is True
    assert figures.comparison.over_target == Decimal("0")


async def test_03_kpi_20_actual_27_is_135_percent_and_points_follow_27(world: World) -> None:
    """**Example A of the brief.** Nothing capped: 27 actual, 10 260 points, 7 over."""
    period = await month(world)
    type_row = await customers_type(world)
    await priced(world, type_row)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))
    result = await report(world, type_row=type_row, quantity=27, period=period)
    await validate(world, result.work_item_id)

    figures = await summary(world, result.work_item_id)
    assert figures.counted_quantity == Decimal("27.00"), "not capped at 20"
    assert figures.comparison.completion_percent == Decimal("135.0")
    assert figures.comparison.over_target == Decimal("7.00")
    assert figures.standard_minutes == Decimal("10260.00"), "27 x 380, not 20 x 380"
    assert figures.standard_minutes_per_unit == CUSTOMER_RATE

    # And M6's month says the same number through its own projection.
    month_figure = await snapshot(world, period=period)
    assert month_figure.eligible_standard_minutes == Decimal("10260.00")
    row = month_figure.breakdown[0]
    assert row.counted_amount == Decimal("27.00")
    assert row.completion_percent == Decimal("135.0")
    assert row.over_target_amount == Decimal("7.00")
    # M2 still decomposes the actual against the cap - that is the KPI screen's
    # figure, and it decides nothing about the points above.
    assert row.eligible_amount == Decimal("20.00")


async def test_04_no_kpi_actual_8_is_8_with_points_and_no_percentage(world: World) -> None:
    """**Example C.** Reporting works, points are normal, completion is not applicable."""
    period = await month(world)
    type_row = await customers_type(world)
    await priced(world, type_row)
    result = await report(world, type_row=type_row, quantity=8, period=period)
    await validate(world, result.work_item_id)

    figures = await summary(world, result.work_item_id)
    assert figures.counted_quantity == Decimal("8.00")
    assert figures.target_quantity is None
    assert figures.comparison.completion_percent is None, "N/A, never 0 % and never an error"
    assert figures.comparison.over_target == Decimal("0")
    assert figures.standard_minutes == Decimal("3040.00"), "8 x 380"

    month_figure = await snapshot(world, period=period)
    assert month_figure.eligible_standard_minutes == Decimal("3040.00")


# ===========================================================================
# 5-6: ACCUMULATION
# ===========================================================================


async def test_05_manual_reports_accumulate_into_one_container(world: World) -> None:
    """+3, +5, +2 is one stream with an actual of 10 - never three jobs."""
    period = await month(world)
    type_row = await customers_type(world)
    first = await report(world, type_row=type_row, quantity=3, period=period)
    second = await report(world, type_row=type_row, quantity=5, period=period)
    third = await report(world, type_row=type_row, quantity=2, period=period)
    assert first.work_item_id == second.work_item_id == third.work_item_id

    figures = await summary(world, first.work_item_id)
    assert figures.declared_quantity == Decimal("10.00")
    assert figures.counted_quantity == Decimal("0.00"), "declared is not counted"
    await validate(world, first.work_item_id)
    figures = await summary(world, first.work_item_id)
    assert figures.counted_quantity == Decimal("10.00")
    assert figures.result_count == 3
    assert len(await containers_for(world, type_row, world.member)) == 1


async def test_06_reporting_after_the_target_is_reached_still_accumulates(world: World) -> None:
    """Target 20, actual 20, then +3: actual 23. The target refuses nothing."""
    period = await month(world)
    type_row = await customers_type(world)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))
    result = await report(world, type_row=type_row, quantity=20, period=period)
    await validate(world, result.work_item_id)
    assert (await summary(world, result.work_item_id)).comparison.is_met

    more = await report(world, type_row=type_row, quantity=3, period=period)
    assert more.work_item_id == result.work_item_id
    await validate(world, result.work_item_id)
    figures = await summary(world, result.work_item_id)
    assert figures.counted_quantity == Decimal("23.00")
    assert figures.comparison.completion_percent == Decimal("115.0")
    assert figures.comparison.over_target == Decimal("3.00")


# ===========================================================================
# 7-8: CONTENT MAPPING
# ===========================================================================


async def test_07_twenty_three_approved_scripts_are_one_container_with_23_results(
    world: World,
) -> None:
    """**Example B.** 23 / 20, 115 %, one recurring work item, 23 source-linked results."""
    period = await month(world)
    type_row = await scripts_type(world)
    await mapping_rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))

    content_ids = []
    for index in range(23):
        content_id = await approved_content(world, title=f"Kịch bản {index + 1}")
        content_ids.append(content_id)
        await project(world, content_id)

    streams = await containers_for(world, type_row, world.member)
    assert len(streams) == 1, "one recurring work item, not twenty-three"
    figures = await summary(world, streams[0].id)
    assert figures.counted_quantity == Decimal("23.00")
    assert figures.result_count == 23
    assert figures.comparison.completion_percent == Decimal("115.0")
    assert figures.comparison.over_target == Decimal("3.00")
    assert figures.unit_label == "kịch bản"
    rows = await world.services.work_results.results_of(streams[0].id)
    assert all(row.source_type is PrWorkResultSource.CONTENT for row in rows)
    assert len({row.source_key for row in rows}) == 23


async def test_08_replaying_a_content_projection_adds_no_quantity(world: World) -> None:
    period = await month(world)
    type_row = await scripts_type(world)
    await mapping_rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    for _ in range(5):
        await project(world, content_id)
    await world.services.content_work.reconcile(
        actor=world.actor(world.owner), request_id=world.request_id, content_ids=[content_id]
    )

    streams = await containers_for(world, type_row, world.member)
    assert len(streams) == 1
    assert (await summary(world, streams[0].id)).counted_quantity == Decimal("1.00")
    assert len(await content_results(world, content_id)) == 1
    assert period.status.value == "OPEN"


# ===========================================================================
# 9: PERIOD ROLLOVER
# ===========================================================================


async def test_09_october_starts_at_zero_and_september_keeps_27(world: World) -> None:
    september = await month(world, number=9)
    october = await month(world, number=10)
    type_row = await customers_type(world)
    await approved_plan(world, period=september, quotas=((type_row, Decimal("20"), Decimal("20")),))
    sept = await report(world, type_row=type_row, quantity=27, period=september)
    await validate(world, sept.work_item_id)

    octo = await report(world, type_row=type_row, quantity=1, period=october)
    assert octo.work_item_id != sept.work_item_id, "a new stream for the new month"
    fresh = await summary(world, octo.work_item_id)
    assert fresh.counted_quantity == Decimal("0.00"), "October starts at zero"
    assert fresh.declared_quantity == Decimal("1.00")

    kept = await summary(world, sept.work_item_id)
    assert kept.counted_quantity == Decimal("27.00"), "September is history and stays 27"
    assert kept.comparison.completion_percent == Decimal("135.0")
    assert (await container(world, sept.work_item_id)).reporting_period_id == september.id

    # And M2 attributes each stream to its own month, whatever day it was counted.
    sept_summary = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=september.id
    )
    assert [row.counted_amount for row in sept_summary.types] == [Decimal("27.00")]


# ===========================================================================
# 10: THE UNIT
# ===========================================================================


async def test_10_changing_a_used_types_unit_updates_the_open_stream_and_keeps_history(
    world: World,
) -> None:
    """ "Sản phẩm" to "khách hàng": the current stream reads the new word, a job
    filed under the old one keeps it, and the code and basis are untouched."""
    period = await month(world)
    type_row = await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code="FIND_CUSTOMERS",
        name="Tìm khách hàng",
        category=PrWorkCategory.COMMUNITY,
        default_unit=PrWorkUnit.ITEM,
        default_quota_basis=PrWorkQuotaBasis.QUANTITY,
    )
    # A one-off job under the old unit - history.
    legacy = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id,
            title="Việc cũ",
            quantity=Decimal("4"),
            contributor_user_ids=(world.member.id,),
        ),
    )
    result = await report(world, type_row=type_row, quantity=3, period=period)
    assert (await summary(world, result.work_item_id)).unit_label == "sản phẩm"

    updated = await world.services.work.update_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=type_row.id,
        default_unit=PrWorkUnit.CUSTOMER,
    )
    assert updated.default_unit is PrWorkUnit.CUSTOMER
    assert updated.code == "FIND_CUSTOMERS"
    assert updated.default_quota_basis is PrWorkQuotaBasis.QUANTITY

    assert (await summary(world, result.work_item_id)).unit_label == "khách hàng"
    await world.session.refresh(legacy)
    assert legacy.unit is PrWorkUnit.ITEM, "historical records keep their unit"

    # And a target written before the rename still compares - the unit is a
    # label, not a second measure.
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))
    await validate(world, result.work_item_id)
    figures = await summary(world, result.work_item_id)
    assert figures.comparison.completion_percent == Decimal("15.0")


# ===========================================================================
# 11-12: NO TARGET, AND ONE-OFF WORK
# ===========================================================================


async def test_11_reporting_needs_no_target_and_no_plan(world: World) -> None:
    """Nobody has written a KPI plan for anybody. Reporting still works, the
    stream is opened on first use, and the month is opened if nobody had."""
    type_row = await customers_type(world)
    result = await report(world, type_row=type_row, quantity=1)
    row = await container(world, result.work_item_id)
    assert row.is_period_container
    assert row.status is PrWorkStatus.IN_PROGRESS
    period = await world.session.get(PrReportingPeriod, row.reporting_period_id)
    assert period is not None and period.status.value == "OPEN"
    figures = await summary(world, result.work_item_id)
    assert figures.target_quantity is None
    assert figures.declared_quantity == Decimal("1.00")


async def test_12_one_off_work_keeps_its_lifecycle(world: World) -> None:
    """Assign, complete, validate - M1 exactly as it shipped, beside the streams."""
    period = await month(world)
    type_row = await customers_type(world)
    await priced(world, type_row)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id,
            title="Xử lý khủng hoảng",
            quantity=Decimal("2"),
            contributor_user_ids=(world.member.id,),
        ),
    )
    assert not item.is_period_container
    await world.services.work.start(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    await world.services.work.complete(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    detail = await world.services.work.approve(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    assert detail.item.status is PrWorkStatus.APPROVED
    assert detail.contributions[0].count_status is PrWorkCountStatus.COUNTED
    assert await world.services.work_results.summary(detail.item) is None
    # It is priced beside a stream in the same month, by the same rule.
    stream = await report(world, type_row=type_row, quantity=3, period=period)
    await validate(world, stream.work_item_id)
    figure = await snapshot(world, period=period)
    assert figure.eligible_standard_minutes == Decimal("1900.00"), "(2 + 3) x 380"
    # And a result cannot be reported into a one-off job.
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_results.report_result(
            actor=world.actor(world.member),
            request_id=world.request_id,
            quantity=Decimal("1"),
            work_item_id=item.id,
        )
    assert caught.value.details["reason"] == "not_period_container"


# ===========================================================================
# 13: THE DEFAULT SCOPE
# ===========================================================================


async def test_13_no_scope_means_everything_for_those_who_may_see_it(world: World) -> None:
    from meobot.application.pr_work_query_service import PrWorkScope

    queries = world.services.work_queries
    assert await queries.default_scope(world.actor(world.owner)) is PrWorkScope.ALL
    assert await queries.default_scope(world.actor(world.head)) is PrWorkScope.ALL
    assert await queries.default_scope(world.actor(world.member)) is PrWorkScope.MINE

    # Over HTTP, with no ``scope`` parameter at all.
    type_row = await customers_type(world)
    await report(world, type_row=type_row, quantity=2)
    await report(world, type_row=type_row, quantity=1, by=world.other, subject=world.other)
    world.act_as(world.owner)
    page = world.client.get("/api/pr/work").json()
    assert {row["subject_user_id"] for row in page["items"]} == {
        str(world.member.id),
        str(world.other.id),
    }, "the owner sees the whole department without naming a scope"
    world.act_as(world.member)
    mine = world.client.get("/api/pr/work").json()
    assert {row["subject_user_id"] for row in mine["items"]} == {str(world.member.id)}


# ===========================================================================
# A: ONE CONTAINER, HOWEVER IT IS CREATED
# ===========================================================================


async def test_a1_a_routine_a_report_and_the_projector_share_one_stream(world: World) -> None:
    period = await month(world)
    type_row = await scripts_type(world)
    await mapping_rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)

    # The routine, activated mid-month, opens this month's stream at once.
    template = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecurringTemplateCommand(
            name="Kịch bản hằng tháng",
            work_type_id=type_row.id,
            assignment_mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
            frequency=PrRecurringFrequency.MONTHLY,
            run_time=time(0, 0),
            start_date=date(2026, 1, 1),
            day_of_month=1,
            contributor_user_ids=(world.member.id,),
            accumulate_by_period=True,
        ),
    )
    assert template.quantity is None
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=template.id
    )
    streams = await containers_for(world, type_row, world.member)
    assert len(streams) == 1
    assert streams[0].reporting_period_id == period.id
    assert streams[0].title == f"Kịch bản hằng tháng — {period.code}"

    # A manual report and a projected content item both land in it.
    manual = await report(world, type_row=type_row, quantity=1, period=period)
    content_id = await approved_content(world)
    await project(world, content_id)
    assert manual.work_item_id == streams[0].id
    assert (await content_results(world, content_id))[0].work_item_id == streams[0].id
    assert len(await containers_for(world, type_row, world.member)) == 1


async def test_a1b_the_worker_actor_opens_a_stream_filed_by_its_subject(world: World) -> None:
    """The content sweeper acts as the system actor, which has no user row.

    A stream it opens is filed by the writer, exactly as ``create_source_work``
    filed source work - never refused for want of an actor id.
    """
    from meobot.domain.identity.models import Actor, Role

    type_row = await scripts_type(world)
    await mapping_rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    worker = Actor(
        user_id=None,
        telegram_user_id=424242,
        full_name="meobot-worker",
        role=Role.OWNER,
        active=True,
        is_bootstrap_owner=True,
    )
    report_ = await world.services.content_work.project_content(
        actor=worker, request_id=world.request_id, content_id=content_id
    )
    assert [one.outcome.value for one in report_.results] == ["PROJECTED"]
    streams = await containers_for(world, type_row, world.member)
    assert len(streams) == 1
    assert streams[0].created_by_user_id == world.member.id
    assert (await summary(world, streams[0].id)).counted_quantity == Decimal("1.00")


async def test_a1c_related_work_on_a_content_item_finds_its_stream(world: World) -> None:
    """*Công việc liên quan* on the content page keeps listing across the change."""
    from meobot.application.pr_work_query_service import PrWorkScope, WorkQuery

    type_row = await scripts_type(world)
    await mapping_rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)
    page = await world.services.work_queries.page(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, content_id=content_id),
    )
    assert [one.is_period_container for one in page.items] == [True]
    assert page.items[0].subject_user_id == world.member.id


async def test_a2_a_shared_accumulating_routine_is_refused(world: World) -> None:
    type_row = await scripts_type(world)
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_recurring.create_template(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=RecurringTemplateCommand(
                name="Chung",
                work_type_id=type_row.id,
                assignment_mode=PrWorkAssignmentMode.SHARED_WORK,
                frequency=PrRecurringFrequency.MONTHLY,
                run_time=time(0, 0),
                start_date=date(2026, 1, 1),
                day_of_month=1,
                contributor_user_ids=(world.member.id, world.other.id),
                accumulate_by_period=True,
            ),
        )
    assert caught.value.details["reason"] == "container_requires_separate"


async def test_a3_a_per_firing_routine_is_unchanged(world: World) -> None:
    """M4B as it shipped: the default is still one job per firing with a quantity."""
    type_row = await customers_type(world)
    template = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecurringTemplateCommand(
            name="Mỗi ngày",
            work_type_id=type_row.id,
            assignment_mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
            frequency=PrRecurringFrequency.DAILY,
            run_time=time(9, 0),
            start_date=date(2026, 9, 1),
            contributor_user_ids=(world.member.id,),
            quantity=Decimal("5"),
        ),
    )
    assert template.accumulate_by_period is False
    assert template.quantity == Decimal("5")
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=template.id
    )
    assert await containers_for(world, type_row, world.member) == [], "activation opens no stream"


# ===========================================================================
# B: THE BOUNDARY - DECLARING IS NOT BEING CREDITED
# ===========================================================================


async def test_b1_the_subject_cannot_count_their_own_results(world: World) -> None:
    type_row = await customers_type(world)
    # The head: holds PR_WORK_VALIDATE by role, so the refusal is the rule and
    # not a missing capability.
    result = await report(world, type_row=type_row, quantity=2, by=world.head, subject=world.head)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await validate(world, result.work_item_id, by=world.head)
    assert caught.value.details["reason"] == "self_validation"
    await validate(world, result.work_item_id, by=world.owner)
    assert (await summary(world, result.work_item_id)).counted_quantity == Decimal("2.00")


async def test_b2_an_employee_cannot_report_into_a_colleagues_stream(world: World) -> None:
    type_row = await customers_type(world)
    with pytest.raises(PrPermissionDeniedError):
        await report(world, type_row=type_row, quantity=1, by=world.member, subject=world.other)
    # A manager may.
    result = await report(world, type_row=type_row, quantity=1, by=world.lead, subject=world.other)
    row = await container(world, result.work_item_id)
    assert row.subject_user_id == world.other.id
    assert row.assigned_by_user_id == world.lead.id


async def test_b3_exclusion_and_withdrawal(world: World) -> None:
    type_row = await customers_type(world)
    first = await report(world, type_row=type_row, quantity=3)
    second = await report(world, type_row=type_row, quantity=5)
    await validate(world, first.work_item_id)
    assert (await summary(world, first.work_item_id)).counted_quantity == Decimal("8.00")

    # A validator takes one back, with a reason. The row stays.
    await world.services.work_results.exclude_result(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        result_id=second.id,
        reason="Trùng với báo cáo tuần trước",
    )
    figures = await summary(world, first.work_item_id)
    assert figures.counted_quantity == Decimal("3.00")
    assert figures.excluded_quantity == Decimal("5.00")
    assert figures.result_count == 2

    # The reporter withdraws their own pending mistake, and cannot withdraw a
    # counted one.
    third = await report(world, type_row=type_row, quantity=30)
    await world.services.work_results.withdraw_result(
        actor=world.actor(world.member), request_id=world.request_id, result_id=third.id
    )
    assert (await summary(world, first.work_item_id)).result_count == 2
    with pytest.raises(PrConflictError):
        await world.services.work_results.withdraw_result(
            actor=world.actor(world.member), request_id=world.request_id, result_id=first.id
        )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_results.withdraw_result(
            actor=world.actor(world.other), request_id=world.request_id, result_id=first.id
        )


async def test_b4_excluding_every_result_returns_the_stream_to_pending(world: World) -> None:
    type_row = await customers_type(world)
    result = await report(world, type_row=type_row, quantity=3)
    await validate(world, result.work_item_id)
    assert (await contribution(world, result.work_item_id)).count_status is (
        PrWorkCountStatus.COUNTED
    )
    await world.services.work_results.exclude_result(
        actor=world.actor(world.owner), request_id=world.request_id, result_id=result.id, reason="x"
    )
    row = await contribution(world, result.work_item_id)
    assert row.count_status is PrWorkCountStatus.PENDING
    assert row.counted_at is None
    assert (await container(world, result.work_item_id)).quantity == Decimal("0.00")


async def test_b5_a_container_refuses_the_one_off_lifecycle(world: World) -> None:
    type_row = await customers_type(world)
    result = await report(world, type_row=type_row, quantity=1)
    item_id = result.work_item_id
    for call in (
        world.services.work.complete(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item_id
        ),
        world.services.work.approve(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item_id
        ),
        world.services.work.add_contributor(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            work_item_id=item_id,
            user_id=world.other.id,
        ),
    ):
        with pytest.raises(PrValidationError) as caught:
            await call
        assert caught.value.details["reason"] == "period_container"
    # Cancel is refused for the same reason, filled or empty: a container is
    # the system's accounting stream, not a job somebody abandons. A cancelled
    # one would keep its unique slot with an ``EXCLUDED`` contribution under
    # it, and the next report into it would show on the card and nowhere else.
    with pytest.raises(PrValidationError) as refused:
        await world.services.work.cancel(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item_id
        )
    assert refused.value.details["reason"] == "period_container"


async def test_b6_a_result_quantity_must_be_positive(world: World) -> None:
    type_row = await customers_type(world)
    for bad in (Decimal("0"), Decimal("-1")):
        with pytest.raises(PrValidationError):
            await report(world, type_row=type_row, quantity=bad)
    # And an omitted quantity is one unit - the form's default.
    result = await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=None,
        work_type_id=type_row.id,
    )
    assert result.quantity == Decimal("1.00")


# ===========================================================================
# C: OVER HTTP
# ===========================================================================


async def test_c1_the_generic_report_form_over_http(world: World) -> None:
    period = await month(world)
    type_row = await customers_type(world)
    await priced(world, type_row)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("20"), Decimal("20")),))

    world.act_as(world.member)
    created = world.client.post(
        "/api/pr/work/results",
        json={
            "work_type_id": str(type_row.id),
            "period_id": str(period.id),
            "quantity": "27",
            "label": "Khách tháng 9",
            "link": "https://example.com/khach",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    item_id = body["item"]["id"]
    assert body["item"]["is_period_container"] is True
    assert body["item"]["status_label"] == "Đang ghi nhận kết quả"
    assert body["can_report_result"] is True
    assert body["can_validate_results"] is False, "the subject never counts their own"
    assert body["results"][0]["label"] == "Khách tháng 9"
    assert body["results"][0]["link"] == "https://example.com/khach"
    assert body["results"][0]["can_withdraw"] is True
    figures = body["item"]["period_container"]
    assert figures["declared_quantity"] == "27.00"
    assert figures["actual_quantity"] == "0.00"

    # The subject is refused, a validator is not.
    refused = world.client.post(f"/api/pr/work/{item_id}/results/validate", json={})
    assert refused.status_code == 403
    world.act_as(world.owner)
    validated = world.client.post(f"/api/pr/work/{item_id}/results/validate", json={})
    assert validated.status_code == 200, validated.text
    figures = validated.json()["item"]["period_container"]
    assert figures["actual_label"] == "27 / 20 khách hàng"
    assert figures["completion_percent"] == "135.0"
    assert figures["over_target_quantity"] == "7.00"
    assert figures["progress_percent"] == "100"
    assert figures["standard_minutes"] == "10260.00"
    assert figures["is_target_met"] is True

    # The list carries the same figures on the row.
    page = world.client.get("/api/pr/work", params={"period_id": str(period.id)}).json()
    row = next(one for one in page["items"] if one["id"] == item_id)
    assert row["period_container"]["actual_label"] == "27 / 20 khách hàng"


async def test_c2_no_kpi_over_http_reads_as_a_dash(world: World) -> None:
    type_row = await customers_type(world)
    world.act_as(world.member)
    created = world.client.post(
        "/api/pr/work/results", json={"work_type_id": str(type_row.id), "quantity": "3"}
    )
    assert created.status_code == 201, created.text
    figures = created.json()["item"]["period_container"]
    assert figures["has_target"] is False
    assert figures["target_quantity"] is None
    assert figures["completion_percent"] is None
    assert figures["actual_label"] == "0 khách hàng"


# ===========================================================================
# D: THE ARITHMETIC IS IN ONE PLACE
# ===========================================================================


async def test_d1_compare_to_target_is_pure_and_uncapped() -> None:
    over = compare_to_target(Decimal("27"), Decimal("20"))
    assert (over.completion_percent, over.over_target, over.remaining) == (
        Decimal("135.0"),
        Decimal("7"),
        Decimal("0"),
    )
    under = compare_to_target(Decimal("12"), Decimal("20"))
    assert (under.completion_percent, under.over_target, under.remaining) == (
        Decimal("60.0"),
        Decimal("0"),
        Decimal("8"),
    )
    none = compare_to_target(Decimal("8"), None)
    assert none.completion_percent is None and none.over_target == 0
    zero = compare_to_target(Decimal("8"), Decimal("0"))
    assert zero.completion_percent is None, "a zero target is no target, never a division"


async def test_d2_one_container_per_keys_is_a_database_rule(world: World) -> None:
    period = await month(world)
    type_row = await customers_type(world)
    result = await report(world, type_row=type_row, quantity=1, period=period)
    duplicate = PrWorkItem(
        code="WRK-DUP",
        title="Trùng",
        work_type_id=type_row.id,
        source_type=PrWorkSourceType.MANUAL,
        status=PrWorkStatus.ACCEPTED,
        quantity=Decimal("0"),
        unit=PrWorkUnit.CUSTOMER,
        created_by_user_id=world.member.id,
        reporting_period_id=period.id,
        subject_user_id=world.member.id,
    )
    world.session.add(duplicate)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with world.session.begin_nested():
            await world.session.flush()
    if duplicate in world.session:
        world.session.expunge(duplicate)
    assert result.work_item_id is not None


async def test_d3_a_content_result_is_unique_per_source(world: World) -> None:
    """The idempotency rule is a database fact, not a projector habit."""
    type_row = await scripts_type(world)
    await mapping_rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)
    existing = (await content_results(world, content_id))[0]
    clone = PrWorkResult(
        work_item_id=existing.work_item_id,
        user_id=existing.user_id,
        quantity=Decimal("1"),
        source_type=PrWorkResultSource.CONTENT,
        source_key=existing.source_key,
        status=PrWorkCountStatus.PENDING,
        reported_by_user_id=existing.user_id,
        reported_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    world.session.add(clone)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with world.session.begin_nested():
            await world.session.flush()
    if clone in world.session:
        world.session.expunge(clone)
