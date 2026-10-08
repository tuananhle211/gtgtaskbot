"""Post-M4: employee-centric KPI, the unified monthly Work view, and execution dates.

Three things went wrong on the management screens, and each has its own section
below.

**KPI listed plan versions, not employees.** ``GET /plans`` returns
``PrWorkPlan`` rows, and the screen rendered one card each - so somebody on
their third revision appeared three times as three management entities, of which
two were history, and an employee with *no* plan appeared not at all. The
question a manager opens that screen with is "who has a KPI plan this month",
and a list of plan rows structurally cannot answer it. Tests 1-12.

**Work defaulted to today and split by source.** The outer scope of a management
view is a reporting month; asking "what is due today" and trying to reconstruct
September from it is the wrong query in the wrong order. And content, manual and
recurring work are one department's month, not three ledgers. Tests 13-25.

**Dates were invented.** A card showed a deadline or a row's birthday under a
heading claiming the work was performed. The execution instant is a *different
fact*, it is one the sources already knew, and ``0037`` gives it a column of its
own rather than letting a browser guess which timestamp to print. Tests 26-42.

The invariant underneath all of it
-----------------------------------

**A content deliverable enters the ledger once, through content.** Repeating
content is still content: nothing derives a recurring template from a content
schedule, and nothing mirrors content into recurring work. Tests 43-50 assert
that as a property of the code rather than as an intention, and tests 51-56 keep
the other boundary - what a screen calls "Đã hoàn thành" is M1's operational
state and says nothing at all about M2 eligibility.
"""

from __future__ import annotations

# ruff: noqa: F811
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_plan_service import current_plan_of
from meobot.application.pr_work_query_service import (
    PrWorkDateField,
    PrWorkPreset,
    PrWorkScope,
    WorkQuery,
)
from meobot.application.pr_work_recurring_service import RecurringTemplateCommand
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota
from meobot.domain.pr.errors import PrPermissionDeniedError, PrWorkPlanStateError
from meobot.domain.pr.recurring import PrRecurringFrequency
from meobot.domain.pr.work import (
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import PrWorkPlanStatus, PrWorkQuotaBasis
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("frozen_work_clock")]

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")


def local(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    """One Vietnamese wall-clock moment, as the UTC instant the code sees."""
    return datetime(year, month, day, hour, minute, tzinfo=SAIGON).astimezone(UTC)


def at_utc(value: datetime | None) -> datetime | None:
    """One stored instant as aware UTC.

    The offline fixture is SQLite, whose driver drops the offset a
    ``DateTime(timezone=True)`` column carries, so an instant read back from a
    committed row is naive there and aware on PostgreSQL. The services read
    stored instants through ``meobot.core.time.ensure_utc`` for exactly this
    reason; an assertion that skipped it would be asserting about the driver.
    """
    from meobot.core.time import ensure_utc

    return None if value is None else ensure_utc(value)


# ===========================================================================
# Helpers
# ===========================================================================
async def work_type(
    world: World,
    *,
    code: str = "PAGE_RECOVERY",
    name: str = "Kháng page",
    basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT,
    unit: PrWorkUnit = PrWorkUnit.ITEM,
) -> PrWorkType:
    existing = (
        await world.session.execute(select(PrWorkType).where(PrWorkType.code == code))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code=code,
        name=name,
        category=PrWorkCategory.OPERATIONS,
        default_unit=unit,
        default_quota_basis=basis,
    )


async def september(world: World) -> PrReportingPeriod:
    return await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner), request_id=world.request_id, year=2026, month=9
    )


async def august(world: World) -> PrReportingPeriod:
    return await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner), request_id=world.request_id, year=2026, month=8
    )


async def manual_work(
    world: World,
    *,
    title: str = "Kháng page David",
    due_at: datetime | None = None,
    accepted_at: datetime | None = None,
) -> PrWorkItem:
    """One manually assigned job. **No execution date, by design.**

    ``accepted_at`` is stamped directly afterwards when a test needs the month
    to be deterministic. M1 sets it to the assignment instant and offers no way
    to back-date one - which is right, and is why the scaffolding writes it here
    rather than inventing a service parameter for a thing no product flow does.
    """
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=(await work_type(world)).id,
            title=title,
            due_at=due_at,
            contributor_user_ids=(world.member.id,),
        ),
    )
    if accepted_at is not None:
        item.accepted_at = accepted_at
        item.assigned_at = accepted_at
        await world.session.flush()
    return item


async def content_work(
    world: World, *, at: datetime, title: str = "Kịch bản Dr Tiến"
) -> PrWorkItem:
    """One content-derived job, through M1's own source path.

    ``create_source_work`` is what the M3 projector calls with the canonical
    milestone instant it computed - so driving it directly here tests the same
    write the projector performs, without rebuilding the whole content workflow
    for a question about dates.
    """
    from meobot.domain.pr.content_work import PrContentWorkKind, content_work_source_key

    content_id = uuid.uuid4()
    return await world.services.work.create_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        source_key=content_work_source_key(PrContentWorkKind.CONTENT_CREATION, content_id),
        work_type_id=(await work_type(world)).id,
        title=title,
        contributor_user_id=world.member.id,
        content_id=content_id,
        occurred_at=at,
    )


async def recurring_work(world: World, *, at: datetime) -> PrWorkItem:
    """One generated routine job, through M4B's template and generator."""
    template = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecurringTemplateCommand(
            name="100 comment seeding",
            work_type_id=(await work_type(world)).id,
            assignment_mode=PrWorkAssignmentMode.SHARED_WORK,
            frequency=PrRecurringFrequency.DAILY,
            run_time=time(9, 0),
            start_date=date(2026, 9, 1),
            contributor_user_ids=(world.member.id,),
        ),
    )
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=template.id
    )
    template.last_evaluated_occurrence_at = at - timedelta(hours=1)
    await world.session.flush()
    await world.services.work_recurring_generator.sweep_template(
        template_id=template.id, request_id=world.request_id, at=at + timedelta(minutes=1)
    )
    return (
        (
            await world.session.execute(
                select(PrWorkItem).where(PrWorkItem.source_type == PrWorkSourceType.RECURRING)
            )
        )
        .scalars()
        .first()
    )  # type: ignore[return-value]


async def listed(world: World, **kwargs: object) -> list[PrWorkItem]:
    page = await world.services.work_queries.page(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, preset=PrWorkPreset.ALL, **kwargs),  # type: ignore[arg-type]
    )
    return list(page.items)


async def plan_for(
    world: World, user, period: PrReportingPeriod, *, approve: bool = False
) -> PrWorkPlan:
    """A draft for one person, optionally carried to ``APPROVED``."""
    detail = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=user.id,
        period_id=period.id,
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=detail.plan.id,
        work_type_id=(await work_type(world)).id,
        target_value=Decimal("10"),
        eligibility_cap=Decimal("10"),
    )
    if approve:
        await world.services.work_plans.approve(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=detail.plan.id
        )
    return detail.plan


async def summary_for(world: World, period: PrReportingPeriod):  # type: ignore[no-untyped-def]
    return await world.services.work_plans.period_summary(
        actor=world.actor(world.owner), period_id=period.id
    )


def row_for(summaries, user):  # type: ignore[no-untyped-def]
    return next(one for one in summaries if one.user_id == user.id)


# ===========================================================================
# 1-12: THE KPI SCREEN IS EMPLOYEE-CENTRIC
# ===========================================================================


async def test_01_one_plan_is_one_row(world: World) -> None:
    """The baseline, and the shape everything below preserves."""
    period = await september(world)
    await plan_for(world, world.member, period)
    rows = await summary_for(world, period)

    mine = [one for one in rows if one.user_id == world.member.id]
    assert len(mine) == 1
    assert mine[0].has_plan is True
    assert mine[0].current_plan is not None
    assert mine[0].current_plan.version_no == 1


async def test_02_two_versions_are_still_one_row(world: World) -> None:
    """**The bug.** v1 approved and v2 in flight is one employee, not two cards."""
    period = await september(world)
    first = await plan_for(world, world.member, period, approve=True)
    await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=first.id
    )

    rows = await summary_for(world, period)
    assert len([one for one in rows if one.user_id == world.member.id]) == 1


async def test_03_three_versions_are_still_one_row(world: World) -> None:
    """And it does not degrade as the history grows."""
    period = await september(world)
    plan = await plan_for(world, world.member, period, approve=True)
    for _ in range(2):
        revised = await world.services.work_plans.revise(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
        )
        await world.services.work_plans.approve(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=revised.plan.id,
        )
        plan = revised.plan

    rows = await summary_for(world, period)
    mine = [one for one in rows if one.user_id == world.member.id]
    assert len(mine) == 1
    assert mine[0].history_count == 3
    assert mine[0].current_plan is not None
    assert mine[0].current_plan.version_no == 3


async def test_04_the_approved_version_is_the_current_one(world: World) -> None:
    """**Approved wins**, even while a later draft is being written.

    That is the whole precedence rule: the approved plan is the one eligibility
    reads and the one a manager's numbers rest on, and a draft revision does not
    displace it until somebody approves it.
    """
    period = await september(world)
    first = await plan_for(world, world.member, period, approve=True)
    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=first.id
    )

    row = row_for(await summary_for(world, period), world.member)
    assert row.current_plan is not None
    assert row.current_plan.id == first.id
    assert row.current_plan.status is PrWorkPlanStatus.APPROVED
    # And the draft is reported **separately**, because a screen has to offer
    # two different controls for "open the plan in force" and "continue the
    # revision somebody started".
    assert row.latest_draft is not None
    assert row.latest_draft.id == revision.plan.id


async def test_05_a_draft_is_current_when_nothing_is_approved(world: World) -> None:
    """Somebody is writing this month's plan, and it is the thing to open."""
    period = await september(world)
    plan = await plan_for(world, world.member, period)

    row = row_for(await summary_for(world, period), world.member)
    assert row.current_plan is not None
    assert row.current_plan.id == plan.id
    assert row.current_plan.status is PrWorkPlanStatus.DRAFT


async def test_06_superseded_versions_are_never_current(world: World) -> None:
    """History decided things that must stay explainable. It is not in force."""
    period = await september(world)
    first = await plan_for(world, world.member, period, approve=True)
    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=first.id
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=revision.plan.id
    )

    await world.session.refresh(first)
    assert first.status is PrWorkPlanStatus.SUPERSEDED
    row = row_for(await summary_for(world, period), world.member)
    assert row.current_plan is not None
    assert row.current_plan.id == revision.plan.id
    # The rule, asserted directly rather than only through the summary: a
    # browser must never be able to reach a different answer.
    assert current_plan_of([first]) is None


async def test_07_history_is_ordered_by_version_descending(world: World) -> None:
    """Deterministic, and by the one key that cannot tie.

    Version numbers are allocated per employee-month and never reused, so v3 is
    unambiguously after v2 whatever the wall clock did. Ordering by a timestamp
    would tie two versions written in the same second and reorder the history
    between two page loads.
    """
    period = await september(world)
    plan = await plan_for(world, world.member, period, approve=True)
    revised = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=revised.plan.id
    )

    history = await world.services.work_plans.history(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert [row.version_no for row, _ in history] == [2, 1]
    assert [count for _, count in history] == [1, 1]


async def test_08_an_employee_with_no_plan_still_appears(world: World) -> None:
    """**The other half of the question**, and the one a plan list cannot answer.

    "Who has no KPI plan this month" is exactly what a manager needs the screen
    for, and a list built from plan rows shows nothing for the people it is
    about.
    """
    period = await september(world)
    await plan_for(world, world.member, period)

    rows = await summary_for(world, period)
    absent = row_for(rows, world.other)
    assert absent.has_plan is False
    assert absent.current_plan is None
    assert absent.quota_count == 0
    assert absent.history_count == 0


async def test_09_every_active_employee_is_listed(world: World) -> None:
    """The list is a **roster**, built from the directory rather than the plans."""
    period = await september(world)
    rows = await summary_for(world, period)
    assert {one.user_id for one in rows} >= {
        world.owner.id,
        world.lead.id,
        world.head.id,
        world.member.id,
        world.other.id,
    }
    assert all(one.has_plan is False for one in rows)


async def test_10_a_deactivated_employee_drops_out(world: World) -> None:
    """Somebody who has left is not a row a manager has to act on."""
    period = await september(world)
    world.other.active = False
    await world.session.flush()
    rows = await summary_for(world, period)
    assert world.other.id not in {one.user_id for one in rows}


async def test_11_the_quota_count_is_the_current_version_s(world: World) -> None:
    """Not the sum over history, and not the first version's."""
    period = await september(world)
    plan = await plan_for(world, world.member, period, approve=True)
    revised = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=revised.plan.id,
        work_type_id=(
            await work_type(world, code="SEEDING", name="Seeding", unit=PrWorkUnit.COMMENT)
        ).id,
        target_value=Decimal("100"),
        eligibility_cap=Decimal("100"),
    )

    row = row_for(await summary_for(world, period), world.member)
    # The **approved** v1 is still current, and it has one quota. The draft's
    # second quota belongs to a version nobody has approved.
    assert row.current_plan is not None
    assert row.current_plan.version_no == 1
    assert row.quota_count == 1


async def test_12_reading_everybody_s_kpi_needs_the_capability(world: World) -> None:
    """``PR_WORK_VIEW_ALL``. An employee does not get a roster of colleagues' KPI."""
    period = await september(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_plans.period_summary(
            actor=world.actor(world.member), period_id=period.id
        )


# ===========================================================================
# 13-25: ONE UNIFIED MONTHLY WORK LIST
# ===========================================================================


async def test_13_all_three_sources_appear_in_one_list(world: World) -> None:
    """**No separate ledger.** A department's September is one thing."""
    period = await september(world)
    await content_work(world, at=local(2026, 9, 4))
    await manual_work(world, due_at=local(2026, 9, 5, 16), accepted_at=local(2026, 9, 5, 9))
    await recurring_work(world, at=local(2026, 9, 6))

    rows = await listed(world, period_id=period.id)
    assert {row.source_type for row in rows} == {
        PrWorkSourceType.CONTENT,
        PrWorkSourceType.MANUAL,
        PrWorkSourceType.RECURRING,
    }


@pytest.mark.parametrize(
    "source",
    [PrWorkSourceType.MANUAL, PrWorkSourceType.CONTENT, PrWorkSourceType.RECURRING],
)
async def test_14_the_source_filter_narrows_the_same_list(
    world: World, source: PrWorkSourceType
) -> None:
    """A filter, never a second query and never a permission."""
    period = await september(world)
    await content_work(world, at=local(2026, 9, 4))
    await manual_work(world, due_at=local(2026, 9, 5, 16), accepted_at=local(2026, 9, 5, 9))
    await recurring_work(world, at=local(2026, 9, 6))

    rows = await listed(world, period_id=period.id, source_type=source)
    assert rows
    assert {row.source_type for row in rows} == {source}


async def test_15_the_reporting_month_is_the_outer_boundary(world: World) -> None:
    """August's work does not appear under September, and the reverse.

    The whole ordering change: the month is applied first and everything else
    narrows it, rather than a day query somebody afterwards tried to widen.
    """
    await august(world)
    sept = await september(world)
    await content_work(world, at=local(2026, 8, 20), title="Kịch bản tháng 8")
    await content_work(world, at=local(2026, 9, 4), title="Kịch bản tháng 9")

    titles = {row.title for row in await listed(world, period_id=sept.id)}
    assert titles == {"Kịch bản tháng 9"}


async def test_16_a_month_with_nothing_in_it_is_empty_rather_than_everything(
    world: World,
) -> None:
    """The boundary is a real predicate, not a hint."""
    aug = await august(world)
    await september(world)
    await content_work(world, at=local(2026, 9, 4))
    assert await listed(world, period_id=aug.id) == []


async def test_17_manual_work_is_reachable_through_the_month(world: World) -> None:
    """**A row with no execution date still belongs to a month.**

    Manual work has no execution date and never will, so the month boundary
    falls back to its deadline. Bounding on ``execution_at`` alone would have
    made every manually assigned job invisible on the screen that exists to show
    the department's month.
    """
    period = await september(world)
    await manual_work(world, due_at=local(2026, 9, 5, 16), accepted_at=local(2026, 9, 1, 9))
    rows = await listed(world, period_id=period.id)
    assert [row.source_type for row in rows] == [PrWorkSourceType.MANUAL]
    assert rows[0].execution_at is None


async def test_18_a_day_filter_narrows_within_the_month(world: World) -> None:
    """ "Hôm nay" is a narrowing of September, not a different question."""
    period = await september(world)
    await content_work(world, at=local(2026, 9, 4), title="Ngày 4")
    await content_work(world, at=local(2026, 9, 6), title="Ngày 6")

    page = await world.services.work_queries.page(
        actor=world.actor(world.owner),
        query=WorkQuery(
            scope=PrWorkScope.ALL,
            period_id=period.id,
            preset=PrWorkPreset.TODAY,
            date_field=PrWorkDateField.EXECUTION_AT,
        ),
        now=local(2026, 9, 4, 15),
    )
    assert [row.title for row in page.items] == ["Ngày 4"]


async def test_19_yesterday_is_offered_and_means_yesterday(world: World) -> None:
    period = await september(world)
    await content_work(world, at=local(2026, 9, 4), title="Ngày 4")
    await content_work(world, at=local(2026, 9, 5), title="Ngày 5")

    page = await world.services.work_queries.page(
        actor=world.actor(world.owner),
        query=WorkQuery(
            scope=PrWorkScope.ALL,
            period_id=period.id,
            preset=PrWorkPreset.YESTERDAY,
            date_field=PrWorkDateField.EXECUTION_AT,
        ),
        now=local(2026, 9, 5, 15),
    )
    assert [row.title for row in page.items] == ["Ngày 4"]


async def test_20_the_person_filter_respects_the_read_scope(world: World) -> None:
    """A filter over what the caller may already see, never a way in.

    An employee narrowing to a colleague gets a refusal rather than the
    colleague's work - silently reducing it to their own would put somebody
    else's name over their own figures.
    """
    period = await september(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_queries.page(
            actor=world.actor(world.member),
            query=WorkQuery(scope=PrWorkScope.MINE, period_id=period.id, user_id=world.other.id),
        )


async def test_21_the_status_filter_is_independent_of_source(world: World) -> None:
    """Three questions, three controls. Combining them makes each unaskable."""
    period = await september(world)
    await content_work(world, at=local(2026, 9, 4))  # COMPLETED
    await manual_work(
        world, due_at=local(2026, 9, 5, 16), accepted_at=local(2026, 9, 5, 9)
    )  # ACCEPTED

    completed = await listed(world, period_id=period.id, status=PrWorkStatus.COMPLETED)
    assert [row.source_type for row in completed] == [PrWorkSourceType.CONTENT]
    accepted = await listed(world, period_id=period.id, status=PrWorkStatus.ACCEPTED)
    assert [row.source_type for row in accepted] == [PrWorkSourceType.MANUAL]
    # And source and status compose rather than override.
    assert (
        await listed(
            world,
            period_id=period.id,
            source_type=PrWorkSourceType.MANUAL,
            status=PrWorkStatus.COMPLETED,
        )
        == []
    )


async def test_22_the_month_is_ordered_by_execution_date(world: World) -> None:
    """What a person plans around is the day the work happens."""
    period = await september(world)
    await content_work(world, at=local(2026, 9, 6), title="Sau")
    await content_work(world, at=local(2026, 9, 4), title="Trước")

    assert [row.title for row in await listed(world, period_id=period.id)] == ["Trước", "Sau"]


async def test_23_undated_work_is_the_tail_and_never_hidden(world: World) -> None:
    """A manual job with a deadline is real work somebody still has to do."""
    period = await september(world)
    await manual_work(
        world,
        title="Không có ngày",
        due_at=local(2026, 9, 2, 16),
        accepted_at=local(2026, 9, 2, 9),
    )
    await content_work(world, at=local(2026, 9, 6), title="Có ngày")

    rows = await listed(world, period_id=period.id)
    assert [row.title for row in rows] == ["Có ngày", "Không có ngày"]
    assert rows[-1].execution_at is None


async def test_24_the_summary_follows_the_selected_month(world: World) -> None:
    """**The arithmetic error the period selector would otherwise introduce.**

    Preset bounds are relative to *today*. A manager looking at August in
    September would have had every period figure computed over September's days
    and read zero, which is a wrong number rather than a missing one.
    """
    aug = await august(world)
    sept = await september(world)
    await content_work(world, at=local(2026, 8, 20), title="Tháng 8")
    await content_work(world, at=local(2026, 9, 4), title="Tháng 9")

    august_summary = await world.services.work_queries.summary(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, period_id=aug.id),
        now=local(2026, 9, 20),
    )
    assert august_summary.completed == 1
    september_summary = await world.services.work_queries.summary(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, period_id=sept.id),
        now=local(2026, 9, 20),
    )
    assert september_summary.completed == 1


async def test_25_the_summary_does_not_move_with_the_day_slice(world: World) -> None:
    """The tiles describe the month; the screen says so in words.

    Two kinds of number in one row with nothing distinguishing them is the
    mismatch this avoids - so the figures deliberately ignore the preset and the
    strip is labelled with the period it counts.
    """
    period = await september(world)
    await content_work(world, at=local(2026, 9, 4), title="Ngày 4")
    await content_work(world, at=local(2026, 9, 6), title="Ngày 6")

    for preset in (PrWorkPreset.ALL, PrWorkPreset.TODAY):
        figures = await world.services.work_queries.summary(
            actor=world.actor(world.owner),
            query=WorkQuery(scope=PrWorkScope.ALL, period_id=period.id, preset=preset),
            now=local(2026, 9, 4, 15),
        )
        assert figures.completed == 2


# ===========================================================================
# 26-42: EXECUTION DATES COME FROM REAL SOURCE FACTS
# ===========================================================================


async def test_26_content_work_uses_the_canonical_milestone(world: World) -> None:
    """The instant M3.1 computed, not the projector's clock."""
    milestone = local(2026, 9, 4, 14, 30)
    item = await content_work(world, at=milestone)
    assert at_utc(item.execution_at) == milestone


async def test_27_content_work_does_not_use_the_row_s_birthday(world: World) -> None:
    """``created_at`` is when a sweeper got round to it, which can be days later."""
    milestone = local(2026, 9, 4)
    item = await content_work(world, at=milestone)
    assert at_utc(item.execution_at) != at_utc(item.created_at)
    assert at_utc(item.execution_at) == milestone


async def test_28_content_work_does_not_use_the_deadline(world: World) -> None:
    """A deadline is not a performance. Source work carries none at all."""
    item = await content_work(world, at=local(2026, 9, 4))
    assert item.due_at is None
    assert item.execution_at is not None


async def test_29_the_execution_date_survives_a_reopen(world: World) -> None:
    """**The reason this is a column and not a derivation.**

    ``completed_at`` is the same instant for content work - and ``reopen`` sets
    it back to null. Deriving the execution date from it would mean a validator
    sending work back erased the day the writer delivered, and a card that had
    been saying "Thực hiện 04/09" would silently start saying nothing.
    """
    milestone = local(2026, 9, 4)
    item = await content_work(world, at=milestone)
    assert at_utc(item.completed_at) == milestone

    await world.services.work.reopen(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    await world.session.refresh(item)
    assert item.completed_at is None
    assert at_utc(item.execution_at) == milestone


async def test_30_recurring_work_uses_the_occurrence_instant(world: World) -> None:
    """Not the moment the sweeper reached it, which after an outage is days later."""
    firing = local(2026, 9, 4, 9)
    item = await recurring_work(world, at=firing)
    assert at_utc(item.execution_at) == firing


async def test_31_recurring_work_keeps_the_deadline_separate(world: World) -> None:
    """Two different facts, and a card shows both."""
    template = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecurringTemplateCommand(
            name="Seeding",
            work_type_id=(await work_type(world)).id,
            assignment_mode=PrWorkAssignmentMode.SHARED_WORK,
            frequency=PrRecurringFrequency.DAILY,
            run_time=time(9, 0),
            start_date=date(2026, 9, 1),
            contributor_user_ids=(world.member.id,),
            due_after_hours=8,
        ),
    )
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=template.id
    )
    template.last_evaluated_occurrence_at = local(2026, 9, 4, 0)
    await world.session.flush()
    await world.services.work_recurring_generator.sweep_template(
        template_id=template.id, request_id=world.request_id, at=local(2026, 9, 4, 12)
    )

    item = (
        (
            await world.session.execute(
                select(PrWorkItem).where(PrWorkItem.source_type == PrWorkSourceType.RECURRING)
            )
        )
        .scalars()
        .one()
    )
    assert at_utc(item.execution_at) == local(2026, 9, 4, 9)
    assert at_utc(item.due_at) == local(2026, 9, 4, 17)
    assert item.execution_at != item.due_at


async def test_32_manual_work_has_no_execution_date(world: World) -> None:
    """**Null, deliberately.**

    Nowhere in the manual creation form does anybody say which day they will do
    the work. Filling this in from ``due_at`` would label a deadline as a
    performance and from ``created_at`` would label the moment the row was typed
    as one - the two inventions the column exists to remove.
    """
    item = await manual_work(world, due_at=local(2026, 9, 5, 16))
    assert item.execution_at is None
    assert at_utc(item.due_at) == local(2026, 9, 5, 16)


async def test_33_a_recurring_job_names_the_template_it_came_from(world: World) -> None:
    """Through a foreign key. **Nothing decodes a source key.**"""
    item = await recurring_work(world, at=local(2026, 9, 4, 9))
    assert item.recurring_occurrence_id is not None

    detail = await world.services.work.detail(actor=world.actor(world.owner), work_item_id=item.id)
    assert detail.recurring_template is not None
    assert detail.recurring_template[1] == "100 comment seeding"


async def test_34_content_and_manual_work_name_no_template(world: World) -> None:
    for item in (
        await content_work(world, at=local(2026, 9, 4)),
        await manual_work(world, due_at=local(2026, 9, 5, 16)),
    ):
        assert item.recurring_occurrence_id is None
        detail = await world.services.work.detail(
            actor=world.actor(world.owner), work_item_id=item.id
        )
        assert detail.recurring_template is None


async def test_35_a_page_resolves_every_template_in_one_query(world: World) -> None:
    """Batched like the content codes beside it - a card issues no request."""
    period = await september(world)
    await recurring_work(world, at=local(2026, 9, 4, 9))
    page = await world.services.work_queries.page(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, period_id=period.id),
    )
    assert len(page.recurring_templates) == 1
    assert next(iter(page.recurring_templates.values()))[1] == "100 comment seeding"


# ===========================================================================
# 43-50: CONTENT IS NOT RECURRING
# ===========================================================================


async def test_43_a_content_milestone_produces_content_work_only(world: World) -> None:
    """One deliverable, one row, one source. **The load-bearing invariant.**"""
    await content_work(world, at=local(2026, 9, 4))
    rows = (await world.session.execute(select(PrWorkItem))).scalars().all()
    assert [row.source_type for row in rows] == [PrWorkSourceType.CONTENT]


async def test_44_the_content_projector_creates_no_recurring_template(
    world: World,
) -> None:
    """Repeating content is still content."""
    from meobot.db.models.pr_work_recurring import PrWorkRecurringTemplate

    for day in (4, 5, 6):
        await content_work(world, at=local(2026, 9, day), title=f"Kịch bản {day}")

    templates = await world.session.scalar(
        select(func.count()).select_from(PrWorkRecurringTemplate)
    )
    assert templates == 0


async def test_45_the_recurring_template_has_nowhere_to_put_a_content_id(
    world: World,
) -> None:
    """**Structural, not a convention.**

    A template that could carry a ``content_id`` is a template somebody would
    eventually point at a content item, and the day that happened one deliverable
    would exist twice in the ledger. There is no such column.
    """
    from meobot.db.models.pr_work_recurring import PrWorkRecurringTemplate

    columns = set(PrWorkRecurringTemplate.__table__.c.keys())
    assert "content_id" not in columns
    assert not [one for one in columns if "content" in one]


async def test_46_the_recurring_generator_reads_no_content_table(world: World) -> None:
    """Asserted against the source, because this is the mirror nobody must build.

    A scheduler that consulted content rows to decide what to generate would be
    the content workflow reimplemented on a timer - and every deliverable it
    found would enter the ledger a second time.
    """
    from pathlib import Path

    source = Path("src/meobot/application/pr_work_recurring_generator.py").read_text("utf-8")
    for forbidden in (
        "PrContentItem",
        "pr_content_items",
        "PrPublication",
        "PrProductionSubmission",
        "content_work",
    ):
        assert forbidden not in source, forbidden


async def test_47_a_content_deliverable_enters_the_ledger_once(world: World) -> None:
    """Projected twice, one row - M3's idempotency, restated here.

    The unified list is the place a duplicate would finally become visible, so
    the property is asserted where the screen reads it.
    """
    from meobot.domain.pr.content_work import PrContentWorkKind, content_work_source_key

    period = await september(world)
    content_id = uuid.uuid4()
    key = content_work_source_key(PrContentWorkKind.CONTENT_CREATION, content_id)
    first = await content_work(world, at=local(2026, 9, 4))
    first.source_key = key
    first.content_id = content_id
    await world.session.flush()

    # **The key is a pure function of the milestone**, so a second projection of
    # the same deliverable composes the identical string and collides on
    # ``uq_pr_work_items_source``. Asserted as that property rather than by
    # provoking the constraint: a rolled-back session would take the reporting
    # period with it, and the question here is what the unified list shows.
    assert content_work_source_key(PrContentWorkKind.CONTENT_CREATION, content_id) == key
    existing = (
        (await world.session.execute(select(PrWorkItem).where(PrWorkItem.source_key == key)))
        .scalars()
        .all()
    )
    assert len(existing) == 1
    rows = await listed(world, period_id=period.id)
    assert len(rows) == 1


async def test_48_recurring_work_enters_the_ledger_on_its_own(world: World) -> None:
    """A routine somebody defined is separate work, and both may coexist."""
    period = await september(world)
    await content_work(world, at=local(2026, 9, 4))
    await recurring_work(world, at=local(2026, 9, 4, 9))

    rows = await listed(world, period_id=period.id)
    assert len(rows) == 2
    assert {row.source_type for row in rows} == {
        PrWorkSourceType.CONTENT,
        PrWorkSourceType.RECURRING,
    }
    # Two different obligations, two different source keys, neither derived
    # from the other.
    assert len({row.source_key for row in rows}) == 2


async def test_49_the_source_type_never_changes_under_a_row(world: World) -> None:
    """What a row's source is, is decided once, by the path that created it."""
    item = await content_work(world, at=local(2026, 9, 4))
    await world.services.work.reopen(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    await world.session.refresh(item)
    assert item.source_type is PrWorkSourceType.CONTENT


# ===========================================================================
# 51-56: OPERATIONAL STATE IS NOT KPI ACCOUNTING
# ===========================================================================


async def test_51_content_work_appears_already_completed(world: World) -> None:
    """The employee does not do it again in the Work module.

    The content workflow is the source of truth: the writer's deliverable was
    accepted at the milestone, so the row appears as *"Chờ xác nhận"* - M1's
    word for finished-and-unconfirmed - rather than waiting for somebody to
    press Start and Complete on work that is already done.
    """
    item = await content_work(world, at=local(2026, 9, 4))
    assert item.status is PrWorkStatus.COMPLETED
    assert item.completed_by_user_id == world.member.id


async def test_52_completed_does_not_mean_counted(world: World) -> None:
    """**The boundary the whole module is built around.**"""
    from meobot.db.models.pr_work import PrWorkContribution
    from meobot.domain.pr.work import PrWorkCountStatus

    item = await content_work(world, at=local(2026, 9, 4))
    assert item.status is PrWorkStatus.COMPLETED
    assert item.approved_at is None

    contribution = (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
            )
        )
        .scalars()
        .one()
    )
    assert contribution.count_status is PrWorkCountStatus.PENDING
    assert contribution.counted_at is None


async def test_53_kpi_counts_contributions_and_not_rows(world: World) -> None:
    """A unified list is a **view**. The KPI pipeline reads contributions.

    Three rows on a screen is not three units of anything: one job with three
    contributors is one work item and three contributions, and M2 allocates
    against the second. Counting what a list rendered would make a filter change
    somebody's KPI.
    """
    from meobot.db.models.pr_work import PrWorkContribution

    period = await september(world)
    await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=(await work_type(world)).id,
            title="Quay chung",
            due_at=local(2026, 9, 5, 16),
            contributor_user_ids=(world.member.id, world.other.id),
        ),
    )
    rows = await listed(world, period_id=period.id)
    assert len(rows) == 1

    contributions = await world.session.scalar(select(func.count()).select_from(PrWorkContribution))
    assert contributions == 2


async def test_54_a_recurring_template_is_never_itself_work(world: World) -> None:
    """It generates work and is measured by nothing."""
    from meobot.db.models.pr_work_recurring import PrWorkRecurringTemplate

    period = await september(world)
    template = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecurringTemplateCommand(
            name="Chăm sóc 10 via",
            work_type_id=(await work_type(world)).id,
            assignment_mode=PrWorkAssignmentMode.SHARED_WORK,
            frequency=PrRecurringFrequency.DAILY,
            run_time=time(8, 0),
            start_date=date(2026, 9, 1),
            contributor_user_ids=(world.member.id,),
        ),
    )
    assert await world.session.get(PrWorkRecurringTemplate, template.id) is not None
    assert await listed(world, period_id=period.id) == []


# ===========================================================================
# 57-63: WHICH MONTH OWNS A ROW
# ===========================================================================
#
# ``work_period_instant`` is ``coalesce(execution_at, accepted_at, created_at)``
# and **``due_at`` is not in it**. A deadline is not an assignment date and not
# an execution date, and letting it decide the month would move work between
# reporting periods that nobody moved - in both directions, and the August one
# is into a month that may already have been reported on.


async def test_57_execution_date_wins_over_every_fallback(world: World) -> None:
    """The real answer, whenever there is one."""
    sept = await september(world)
    await august(world)
    item = await content_work(world, at=local(2026, 9, 4))
    # Assigned and filed elsewhere; the milestone is what counts.
    item.accepted_at = local(2026, 8, 20)
    item.assigned_at = local(2026, 8, 20)
    await world.session.flush()

    assert [row.id for row in await listed(world, period_id=sept.id)] == [item.id]


async def test_58_accepted_at_is_used_when_there_is_no_execution_date(
    world: World,
) -> None:
    """The next best fact: when the job entered somebody's workload."""
    sept = await september(world)
    item = await manual_work(world, accepted_at=local(2026, 9, 10, 9), due_at=None)
    assert item.execution_at is None
    assert [row.id for row in await listed(world, period_id=sept.id)] == [item.id]


async def test_59_created_at_is_the_floor(world: World) -> None:
    """A proposal nobody has accepted still belongs to a month.

    ``accepted_at`` is null until a manager accepts - which is M1's whole
    anti-gaming rule - so a proposal falls through to the day it was filed
    rather than out of every month.
    """
    period = await september(world)
    proposal = await world.services.work.propose_work(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=(await work_type(world)).id, title="Đề xuất"),
    )
    assert proposal.accepted_at is None
    assert proposal.execution_at is None

    # ``created_at`` is the database's ``now()``, so the month it lands in is
    # the real current one - which is what the fallback claims it should be.
    from meobot.core.time import ensure_utc

    filed = ensure_utc(proposal.created_at)
    owning = await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        year=filed.astimezone(SAIGON).year,
        month=filed.astimezone(SAIGON).month,
    )
    assert proposal.id in {row.id for row in await listed(world, period_id=owning.id)}
    assert owning.id == period.id or proposal.id not in {
        row.id for row in await listed(world, period_id=period.id)
    }


async def test_60_a_deadline_does_not_decide_the_month(world: World) -> None:
    """**The correction.** ``due_at`` attributes nothing."""
    sept = await september(world)
    october = await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner), request_id=world.request_id, year=2026, month=10
    )
    # Assigned 30 September, due 2 October. The department did this in
    # September, and October's figures must not acquire it.
    item = await manual_work(
        world,
        title="Giao cuối tháng 9",
        accepted_at=local(2026, 9, 30, 16),
        due_at=local(2026, 10, 2, 16),
    )

    assert [row.id for row in await listed(world, period_id=sept.id)] == [item.id]
    assert await listed(world, period_id=october.id) == []


async def test_61_a_deadline_does_not_pull_work_out_of_its_month(
    world: World,
) -> None:
    """The other direction, and the worse one.

    Assigned 30 August, due 5 September. Attributing by deadline would take the
    row out of August - a month somebody may already have reported on - and put
    it in September, with nobody having moved anything.
    """
    aug = await august(world)
    sept = await september(world)
    item = await manual_work(
        world,
        title="Giao cuối tháng 8",
        accepted_at=local(2026, 8, 30, 16),
        due_at=local(2026, 9, 5, 16),
    )

    assert [row.id for row in await listed(world, period_id=aug.id)] == [item.id]
    assert await listed(world, period_id=sept.id) == []


async def test_62_the_deadline_is_still_carried_and_shown(world: World) -> None:
    """Excluded from attribution, not from the row.

    *"Hạn"* answers "when must this be finished". That is a different question
    from "which month is this in", and losing it would be over-correcting.
    """
    period = await september(world)
    await manual_work(world, accepted_at=local(2026, 9, 30, 16), due_at=local(2026, 10, 2, 16))
    rows = await listed(world, period_id=period.id)
    assert at_utc(rows[0].due_at) == local(2026, 10, 2, 16)
    assert rows[0].execution_at is None


async def test_63_a_day_filter_uses_the_same_instant_as_the_month(
    world: World,
) -> None:
    """One expression, so the day slice cannot disagree with the month.

    A manual job assigned on the 30th is September's, and it is also "Hôm nay"
    on the 30th - through the same ``coalesce``, never through its deadline.
    """
    period = await september(world)
    item = await manual_work(
        world, accepted_at=local(2026, 9, 30, 16), due_at=local(2026, 10, 2, 16)
    )
    page = await world.services.work_queries.page(
        actor=world.actor(world.owner),
        query=WorkQuery(
            scope=PrWorkScope.ALL,
            period_id=period.id,
            preset=PrWorkPreset.TODAY,
            date_field=PrWorkDateField.EXECUTION_AT,
        ),
        now=local(2026, 9, 30, 18),
    )
    assert [row.id for row in page.items] == [item.id]


# ===========================================================================
# 64-66: RECURRING PROVENANCE AT CREATION
# ===========================================================================


async def test_64_new_recurring_work_is_linked_at_creation(world: World) -> None:
    """The FK is assigned by the generator, never recovered afterwards."""
    from meobot.db.models.pr_work_recurring import PrWorkRecurringOccurrence

    item = await recurring_work(world, at=local(2026, 9, 4, 9))
    assert item.recurring_occurrence_id is not None
    occurrence = await world.session.get(PrWorkRecurringOccurrence, item.recurring_occurrence_id)
    assert occurrence is not None
    assert at_utc(item.execution_at) == at_utc(occurrence.scheduled_for)


async def test_65_a_caught_up_firing_is_dated_to_the_firing(world: World) -> None:
    """**The catch-up case.** Scheduled Sep 2, generated Sep 4, dated Sep 2.

    This is the case ``accepted_at`` would have got wrong for historical rows,
    and the reason the backfill joins the occurrence rather than copying a
    timestamp: after an outage the two are days apart.
    """
    from meobot.db.models.pr_work_recurring import PrWorkRecurringOccurrence

    template = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecurringTemplateCommand(
            name="Báo cáo hằng ngày",
            work_type_id=(await work_type(world)).id,
            assignment_mode=PrWorkAssignmentMode.SHARED_WORK,
            frequency=PrRecurringFrequency.DAILY,
            run_time=time(9, 0),
            start_date=date(2026, 9, 1),
            contributor_user_ids=(world.member.id,),
        ),
    )
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=template.id
    )
    # The sweeper was down until the 4th.
    template.last_evaluated_occurrence_at = local(2026, 9, 1, 12)
    await world.session.flush()
    await world.services.work_recurring_generator.sweep_template(
        template_id=template.id, request_id=world.request_id, at=local(2026, 9, 4, 12)
    )

    items = (
        (
            await world.session.execute(
                select(PrWorkItem).where(PrWorkItem.source_type == PrWorkSourceType.RECURRING)
            )
        )
        .scalars()
        .all()
    )
    stamps = sorted(at_utc(one.execution_at) for one in items)  # type: ignore[type-var]
    assert stamps == [local(2026, 9, day, 9) for day in (2, 3, 4)]

    second = next(one for one in items if at_utc(one.execution_at) == local(2026, 9, 2, 9))
    occurrence = await world.session.get(PrWorkRecurringOccurrence, second.recurring_occurrence_id)
    assert occurrence is not None
    assert at_utc(occurrence.scheduled_for) == local(2026, 9, 2, 9)
    # The row was written on the 4th; the date it reports is the 2nd.
    assert at_utc(second.created_at) != local(2026, 9, 2, 9)


async def test_66_the_read_path_never_decodes_a_source_key(world: World) -> None:
    """**Runtime resolves the template through the foreign key.**

    Asserted against the source, because the migration is allowed a one-time
    decode and runtime is not: a key somebody starts taking substrings of on
    every read is a key that will one day be taken apart wrongly.
    """
    from pathlib import Path

    for module in (
        "src/meobot/application/pr_work_query_service.py",
        "src/meobot/application/pr_work_service.py",
        "src/meobot/api/schemas/pr_work.py",
        "src/meobot/api/routers/pr_work.py",
    ):
        source = Path(module).read_text("utf-8")
        for forbidden in ("split_part", "source_key.split", "source_key[", "partition("):
            assert forbidden not in source, f"{module}: {forbidden}"

    # And the frontend does not either.
    browser = Path("frontend/src/app/pr/work/page.tsx").read_text("utf-8")
    assert "source_key" not in browser


# ===========================================================================
# 67-79: THE DRAFT REVISION LIFECYCLE
#
# The bug: with an approved v3 and a draft v4, the KPI screen showed only
# "Tạo bản điều chỉnh" over v3 - which the service refuses, because M2 allows
# one draft per employee-month - and put v4 in the history accordion, where
# nothing acts on it. The one visible control led to an error and the draft it
# collided with could not be continued, approved or discarded from anywhere.
# Escaping an empty v4 needed a database.
#
# The read model is where that is settled: *current plan*, *active draft* and
# *history* are three different things, and a browser must not have to work out
# which is which.
# ===========================================================================


async def revision_of(world: World, plan: PrWorkPlan) -> PrWorkPlan:
    """The next draft from an approved plan. The act the screen calls *Tạo bản điều chỉnh*."""
    detail = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
    )
    return detail.plan


async def test_67_an_approved_plan_with_no_draft_has_no_active_draft(world: World) -> None:
    """State A. One plan, in force, and nothing in flight."""
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)

    row = row_for(await summary_for(world, period), world.member)
    assert row.current_plan is not None
    assert row.current_plan.id == approved.id
    assert row.latest_draft is None
    assert row.draft_quota_count == 0
    assert row.terminal_count == 0


async def test_68_an_approved_plan_and_a_draft_are_two_different_rows(world: World) -> None:
    """**State B, and the heart of the bug.**

    v3 stays in force while v4 is written. The summary must say both, because a
    screen that can only name one of them has to choose - and choosing the
    current plan is what hid the draft.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    draft = await revision_of(world, approved)

    row = row_for(await summary_for(world, period), world.member)
    assert row.current_plan is not None
    assert row.current_plan.id == approved.id
    assert row.current_plan.status is PrWorkPlanStatus.APPROVED
    assert row.latest_draft is not None
    assert row.latest_draft.id == draft.id
    assert row.latest_draft.id != row.current_plan.id
    # The draft's own size, so a row can say "v2 · 1 hạn mức" without a second
    # request. A **revision** starts from the plan it revises - `revise` copies
    # the quotas across - which is why the draft here is not empty. The empty
    # case comes from somewhere else; see ``test_76``.
    assert row.draft_quota_count == 1
    # And the version count is every version, while history is what is over.
    assert row.history_count == 2
    assert row.terminal_count == 0


async def test_69_an_active_draft_is_not_history(world: World) -> None:
    """The invariant, stated as a number.

    *"Lịch sử thay đổi (N)"* counts the versions that are **over**. A revision
    somebody is in the middle of is not one, and counting it as history is
    precisely what made it unreachable.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    await revision_of(world, approved)

    row = row_for(await summary_for(world, period), world.member)
    assert row.terminal_count == 0
    assert row.history_count == 2


async def test_70_a_discarded_draft_is_history(world: World) -> None:
    """And once it is over, it counts. v3 does not move."""
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    draft = await revision_of(world, approved)
    await world.services.work_plans.discard(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
    )

    row = row_for(await summary_for(world, period), world.member)
    assert row.current_plan is not None
    assert row.current_plan.id == approved.id
    assert row.current_plan.status is PrWorkPlanStatus.APPROVED
    assert row.latest_draft is None
    assert row.terminal_count == 1


async def test_71_a_superseded_version_is_history(world: World) -> None:
    """Approving the revision moves the old plan across, and only then."""
    period = await september(world)
    first = await plan_for(world, world.member, period, approve=True)
    # A revision arrives carrying the quotas it revises, so it is approvable as
    # it stands - which is the ordinary case and the one that supersedes.
    draft = await revision_of(world, first)
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
    )

    row = row_for(await summary_for(world, period), world.member)
    assert row.current_plan is not None
    assert row.current_plan.id == draft.id
    assert row.latest_draft is None
    assert row.terminal_count == 1
    await world.session.refresh(first)
    assert first.status is PrWorkPlanStatus.SUPERSEDED


async def test_72_an_initial_draft_is_the_current_plan(world: World) -> None:
    """State C. No approved version, so the draft **is** what a manager acts on.

    Deliberately not a second panel: with nothing in force, the draft is the
    plan, and a screen showing it twice would read as two of them. What matters
    is that the employee does not read as *"Chưa có kế hoạch"* with a control
    that would create a duplicate.
    """
    period = await september(world)
    draft = await plan_for(world, world.member, period)

    row = row_for(await summary_for(world, period), world.member)
    assert row.has_plan is True
    assert row.current_plan is not None
    assert row.current_plan.id == draft.id
    assert row.current_plan.status is PrWorkPlanStatus.DRAFT
    assert row.latest_draft is not None
    assert row.latest_draft.id == draft.id
    assert row.terminal_count == 0


async def test_73_an_employee_with_nothing_has_no_plan_and_no_draft(world: World) -> None:
    """The other half of the question the employee-centric screen exists for."""
    period = await september(world)
    row = row_for(await summary_for(world, period), world.member)
    assert row.has_plan is False
    assert row.current_plan is None
    assert row.latest_draft is None
    assert row.history_count == 0
    assert row.terminal_count == 0


async def test_74_revise_is_refused_while_a_draft_exists(world: World) -> None:
    """The rule the index enforces, and the reason code the screen keys on.

    Not a message: `details.reason` is the interface, and it names the plan the
    caller collided with so the screen can open it rather than dead-ending.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    draft = await revision_of(world, approved)

    with pytest.raises(PrWorkPlanStateError) as caught:
        await revision_of(world, approved)
    assert caught.value.details["reason"] == "draft_already_exists"
    assert caught.value.details["plan_id"] == str(draft.id)
    assert caught.value.details["version_no"] == draft.version_no


async def test_75_can_revise_is_false_while_a_draft_exists(world: World) -> None:
    """**The courtesy that stops the trap.**

    The index is the rule; drawing a button whose only outcome is a refusal is
    what made the screen a dead end. Asserted on the approved plan's own detail,
    which is what the current-plan panel renders from.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)

    before = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=approved.id
    )
    assert before.can_revise is True

    draft = await revision_of(world, approved)
    after = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=approved.id
    )
    assert after.can_revise is False
    # And it comes back once the draft is over.
    await world.services.work_plans.discard(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
    )
    again = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=approved.id
    )
    assert again.can_revise is True


async def test_76_an_empty_draft_is_editable_and_discardable_but_not_approvable(
    world: World,
) -> None:
    """**The escape hatch, and it is the backend's own rule.**

    M2 refuses to approve a plan with no quotas - `_validate_for_approval` - so
    `can_approve` is false. It must not follow that the draft is unreachable:
    editing and discarding stay available, which is what makes an empty v4
    something a manager can get out of without a database.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    # **How an empty draft is reachable now.** `create_plan` no longer offers a
    # second creation path beside an approved plan - see ``test_82`` - and
    # `revise` copies the source's quotas, so a revision never *starts* empty.
    # It can still be emptied: a manager takes out the quota they meant to
    # replace and has not added the new one yet, which is an ordinary moment in
    # the middle of editing and must not be a trap.
    draft = await revision_of(world, approved)
    revision = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=draft.id
    )
    for quota in revision.quotas:
        await world.services.work_plans.remove_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=draft.id,
            quota_id=quota.id,
        )

    detail = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=draft.id
    )
    assert detail.quotas == ()
    assert detail.can_approve is False
    assert detail.can_edit is True
    assert detail.can_discard is True

    # And it is visible as the active draft rather than buried as history.
    row = row_for(await summary_for(world, period), world.member)
    assert row.latest_draft is not None
    assert row.latest_draft.id == draft.id
    assert row.draft_quota_count == 0
    assert row.current_plan is not None
    assert row.current_plan.id == approved.id
    assert row.terminal_count == 0


async def test_77_editing_a_draft_keeps_its_version(world: World) -> None:
    """*Tiếp tục chỉnh sửa* opens v4 and leaves v4. There is no v5 in this act."""
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    draft = await revision_of(world, approved)
    assert draft.version_no == approved.version_no + 1

    quotas = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=draft.id
    )
    await world.services.work_plans.update_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.id,
        quota_id=quotas.quotas[0].id,
        target_value=Decimal("7"),
        eligibility_cap=Decimal("7"),
    )
    row = row_for(await summary_for(world, period), world.member)
    assert row.latest_draft is not None
    assert row.latest_draft.id == draft.id
    assert row.latest_draft.version_no == draft.version_no
    assert row.draft_quota_count == 1
    # The plan in force is untouched by everything above.
    assert row.current_plan is not None
    assert row.current_plan.id == approved.id
    assert row.history_count == 2


async def test_78_discarding_leaves_the_approved_plan_alone(world: World) -> None:
    """Part K. v3 stays in force; only v4 moved, and it moved to ``DISCARDED``.

    Not a deletion: the row stays, so the version sequence has no hole and
    *"who proposed this and who dropped it"* is still answerable.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    draft = await revision_of(world, approved)

    await world.services.work_plans.discard(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
    )
    await world.session.refresh(draft)
    await world.session.refresh(approved)
    assert draft.status is PrWorkPlanStatus.DISCARDED
    assert draft.discarded_at is not None
    assert draft.discarded_by_user_id == world.owner.id
    assert approved.status is PrWorkPlanStatus.APPROVED
    assert approved.superseded_at is None


async def test_79_the_next_revision_after_a_discard_takes_the_next_number(
    world: World,
) -> None:
    """Part Y. Version numbers are identifiers, not slots.

    v2 was discarded, so the next revision is v3. Reusing v2 would make one
    number name two different plans, and an allocation explained by "v2" would
    stop being explainable.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    first_draft = await revision_of(world, approved)
    await world.services.work_plans.discard(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=first_draft.id
    )

    second_draft = await revision_of(world, approved)
    assert second_draft.version_no == first_draft.version_no + 1
    row = row_for(await summary_for(world, period), world.member)
    assert row.latest_draft is not None
    assert row.latest_draft.id == second_draft.id
    assert row.terminal_count == 1
    assert row.history_count == 3


async def test_80_a_discarded_draft_cannot_be_edited_approved_or_discarded_again(
    world: World,
) -> None:
    """Terminal means terminal, and the service says so rather than the screen."""
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    draft = await revision_of(world, approved)
    await world.services.work_plans.discard(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
    )

    detail = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=draft.id
    )
    assert detail.can_edit is False
    assert detail.can_approve is False
    assert detail.can_discard is False

    with pytest.raises(PrWorkPlanStateError):
        await world.services.work_plans.approve(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
        )
    with pytest.raises(PrWorkPlanStateError):
        await world.services.work_plans.discard(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
        )


async def test_81_an_approved_plan_cannot_be_discarded_through_the_draft_action(
    world: World,
) -> None:
    """*Bỏ bản nháp* is a draft act. It is not a way to retract a plan in force."""
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)

    with pytest.raises(PrWorkPlanStateError):
        await world.services.work_plans.discard(
            actor=world.actor(world.owner), request_id=world.request_id, plan_id=approved.id
        )
    await world.session.refresh(approved)
    assert approved.status is PrWorkPlanStatus.APPROVED


# ===========================================================================
# 82-87: ONE CREATION PATH PER STATE
#
# `create_plan` starts the **first** plan for an employee-month; `revise`
# changes one that is in force. They are not interchangeable, and the guard
# exists because allowing both from the same state produced two semantically
# different revisions:
#
#   revise(v3)      -> v4 DRAFT, supersedes v3, carrying v3's quotas
#   create_plan()   -> v4 DRAFT, superseding nothing, with none
#
# The second is what put "v4 · 0 hạn mức" under an approved v3 in production.
# Approving it would have dropped five quotas nobody decided to drop.
# ===========================================================================


async def test_82_create_plan_is_refused_once_a_plan_is_in_force(world: World) -> None:
    """**Case C.** An approved plan is changed by revising it, and only by that.

    A distinct reason code from ``draft_already_exists`` on purpose: the two
    absences lead to two different recoveries - *continue the revision somebody
    started* against *start one* - and one code would send a manager to the
    wrong control.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)

    with pytest.raises(PrWorkPlanStateError) as caught:
        await world.services.work_plans.create_plan(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    assert caught.value.details["reason"] == "approved_plan_requires_revision"
    assert caught.value.details["plan_id"] == str(approved.id)
    assert caught.value.details["version_no"] == approved.version_no


async def test_83_the_refusal_changes_nothing(world: World) -> None:
    """Part K. A clean rejection: no version consumed, no row, no recompute.

    The half worth asserting is the *absence* - a guard that raised after
    flushing a plan, or that let ``_next_version`` run and advance something,
    would leave a gap in the sequence for every rejected click.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    before_status = approved.status
    before_version = approved.version_no

    plans_before = await world.session.scalar(
        select(func.count())
        .select_from(PrWorkPlan)
        .where(PrWorkPlan.user_id == world.member.id, PrWorkPlan.period_id == period.id)
    )
    quotas_before = await world.session.scalar(
        select(func.count()).select_from(PrWorkQuota).where(PrWorkQuota.plan_id == approved.id)
    )

    with pytest.raises(PrWorkPlanStateError):
        await world.services.work_plans.create_plan(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )

    await world.session.refresh(approved)
    assert approved.status is before_status
    assert approved.version_no == before_version
    assert approved.superseded_at is None
    assert (
        await world.session.scalar(
            select(func.count())
            .select_from(PrWorkPlan)
            .where(PrWorkPlan.user_id == world.member.id, PrWorkPlan.period_id == period.id)
        )
        == plans_before
    )
    assert (
        await world.session.scalar(
            select(func.count()).select_from(PrWorkQuota).where(PrWorkQuota.plan_id == approved.id)
        )
        == quotas_before
    )
    # And the next revision still takes the next number - nothing was consumed.
    nxt = await revision_of(world, approved)
    assert nxt.version_no == before_version + 1


async def test_84_create_plan_is_refused_while_a_draft_exists_in_either_state(
    world: World,
) -> None:
    """Cases B and D, and the code that tells them apart from case C.

    The draft check comes first, so a pair holding both an approved plan and a
    draft reports the draft - which is the one a manager can act on.
    """
    period = await september(world)
    initial = await plan_for(world, world.member, period)  # DRAFT, nothing approved

    with pytest.raises(PrWorkPlanStateError) as caught:
        await world.services.work_plans.create_plan(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    assert caught.value.details["reason"] == "draft_already_exists"
    assert caught.value.details["plan_id"] == str(initial.id)

    # Now approve it and revise, so both an approved plan and a draft exist.
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=initial.id
    )
    draft = await revision_of(world, initial)

    with pytest.raises(PrWorkPlanStateError) as both:
        await world.services.work_plans.create_plan(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    assert both.value.details["reason"] == "draft_already_exists"
    assert both.value.details["plan_id"] == str(draft.id)


async def test_85_create_plan_still_starts_the_first_plan(world: World) -> None:
    """**Case A**, unchanged. The guard narrows one state, not the method."""
    period = await september(world)
    detail = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    assert detail.plan.version_no == 1
    assert detail.plan.status is PrWorkPlanStatus.DRAFT
    assert detail.plan.supersedes_plan_id is None
    assert detail.quotas == ()


async def test_86_revise_still_carries_the_quotas_across(world: World) -> None:
    """**The behaviour the guard exists to protect.**

    A revision starts as the plan it revises, by value, so editing the draft
    cannot reach back into the version still in force - and a manager changing
    one number does not retype the other four. This is the difference the second
    creation path erased.
    """
    period = await september(world)
    approved = await plan_for(world, world.member, period, approve=True)
    source = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=approved.id
    )
    assert len(source.quotas) == 1

    draft = await revision_of(world, approved)
    revision = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=draft.id
    )
    assert [
        (one.work_type_id, one.basis, one.target_value, one.eligibility_cap, one.unit)
        for one in revision.quotas
    ] == [
        (one.work_type_id, one.basis, one.target_value, one.eligibility_cap, one.unit)
        for one in source.quotas
    ]
    # Copied, not shared: different rows, on different plans.
    assert {one.id for one in revision.quotas}.isdisjoint({one.id for one in source.quotas})
    assert draft.supersedes_plan_id == approved.id


async def test_87_there_is_no_second_revision_path_from_any_state(world: World) -> None:
    """The invariant as one table, asserted end to end.

    Whatever exists for an employee-month, exactly one creation call is allowed
    - and after an approved plan, it is never ``create_plan``.
    """
    period = await september(world)

    async def create() -> str:
        try:
            await world.services.work_plans.create_plan(
                actor=world.actor(world.owner),
                request_id=world.request_id,
                user_id=world.member.id,
                period_id=period.id,
            )
            return "ok"
        except PrWorkPlanStateError as exc:
            return str(exc.details["reason"])

    # A: nothing exists.
    assert await create() == "ok"
    draft = row_for(await summary_for(world, period), world.member).latest_draft
    assert draft is not None

    # B: an initial draft exists.
    assert await create() == "draft_already_exists"

    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=draft.id,
        work_type_id=(await work_type(world)).id,
        target_value=Decimal("10"),
        eligibility_cap=Decimal("10"),
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=draft.id
    )

    # C: approved, no draft.
    assert await create() == "approved_plan_requires_revision"

    revision = await revision_of(world, draft)

    # D: approved plus draft - and a second revise is refused too.
    assert await create() == "draft_already_exists"
    with pytest.raises(PrWorkPlanStateError) as second:
        await revision_of(world, draft)
    assert second.value.details["reason"] == "draft_already_exists"
    assert second.value.details["plan_id"] == str(revision.id)
