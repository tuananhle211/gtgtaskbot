"""M4B - recurring work, and every way a scheduler loses or duplicates work.

Numbered against the milestone's own acceptance list, with the checkpoint's six
extra emphases as sections A-F.

What this file is really testing
---------------------------------

**There is still one ledger.** Every generated job is a ``PrWorkItem`` with
``source_type = RECURRING``, created through
:meth:`~meobot.application.pr_work_service.PrWorkService.generate_recurring_work`
- which is a sibling of ``assign_work``, not a private path around it. So M2 and
M6 need no recurring-specific code, and tests 20-22 assert that by driving a
generated job all the way to ``COUNTED`` through M1's own methods.

**Pause and downtime are different facts.** Section C, and the distinction is
the whole reason the cursor is called ``last_evaluated_occurrence_at`` rather
than ``last_generated_at``. Resuming moves the cursor forward and the paused
fortnight is never walked; an outage moves nothing and the missed firings are
caught up. Getting these the wrong way round either floods a manager who paused
over Tết or silently loses work the department owed.

**A closed month is refused once, not for ever.** Section D. Generating into a
locked period would move a number underneath a report that quotes it, and
retrying tomorrow would fail for the identical reason - so the occurrence is
settled rather than left in the retry set, and the reason is stored where a
person can read it.

**A transient failure loses nothing and blocks nothing.** Section E. The
occurrence row is the record of the obligation; the cursor may pass it because
the retry set comes back to it, and the work item's own unique index is what
stops the retry duplicating anything.

**The source key obeys the contract that actually exists.** Section F. The audit
found ``SOURCE_KEY_PATTERN`` is three segments, not four - so the occurrence
row's uuid names the firing and the third segment names the assignee, and
nothing global had to be widened.
"""

from __future__ import annotations

# The ``world`` fixture comes from the production-lifecycle suite, like every
# other Work suite.
# ruff: noqa: F811
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_recurring_generator import PrWorkRecurringGenerator
from meobot.application.pr_work_recurring_service import RecurringTemplateCommand
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_recurring import (
    PrWorkRecurringOccurrence,
    PrWorkRecurringTemplate,
)
from meobot.domain.pr.errors import PrPermissionDeniedError, PrValidationError
from meobot.domain.pr.recurring import (
    MAX_OCCURRENCES_PER_SWEEP,
    SHARED_SUBJECT,
    PrRecurringFrequency,
    PrRecurringOccurrenceState,
    PrRecurringTemplateStatus,
    RecurringSchedule,
    occurrence_key,
    recurring_source_key,
)
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import (
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkCountStatus,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

pytestmark = pytest.mark.asyncio

#: The department's own wall clock. Every instant in this file is stated in it,
#: because a recurring schedule that is reasoned about in UTC is a schedule that
#: fires an hour out the first time anybody moves the deployment.
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")


def local(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """One Vietnamese wall-clock moment, as the UTC instant the code sees."""
    return datetime(year, month, day, hour, minute, tzinfo=SAIGON).astimezone(UTC)


# ===========================================================================
# Helpers
# ===========================================================================
async def work_type(
    world: World,
    *,
    code: str = "DAILY_REPORT",
    name: str = "Báo cáo ngày",
    unit: PrWorkUnit = PrWorkUnit.ITEM,
    basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT,
) -> PrWorkType:
    """One work type, or the one already registered under ``code``."""
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


async def seeding_type(world: World) -> PrWorkType:
    """``SEEDING_COMMENT``: measured in comments, by quantity. The M4 archetype."""
    return await work_type(
        world,
        code="SEEDING_COMMENT",
        name="Seeding bình luận",
        unit=PrWorkUnit.COMMENT,
        basis=PrWorkQuotaBasis.QUANTITY,
    )


def command(
    *,
    type_row: PrWorkType,
    contributors: tuple[uuid.UUID, ...],
    mode: PrWorkAssignmentMode = PrWorkAssignmentMode.SHARED_WORK,
    frequency: PrRecurringFrequency = PrRecurringFrequency.DAILY,
    run_time: time = time(9, 0),
    start: date = date(2026, 9, 1),
    end: date | None = None,
    weekdays: tuple[int, ...] = (),
    day_of_month: int | None = None,
    quantity: Decimal | None = None,
    due_after_hours: int | None = None,
    name: str = "Báo cáo hằng ngày",
) -> RecurringTemplateCommand:
    return RecurringTemplateCommand(
        name=name,
        work_type_id=type_row.id,
        assignment_mode=mode,
        frequency=frequency,
        run_time=run_time,
        start_date=start,
        end_date=end,
        contributor_user_ids=contributors,
        weekdays=weekdays,
        day_of_month=day_of_month,
        quantity=quantity,
        due_after_hours=due_after_hours,
    )


async def template(
    world: World, *, activate: bool = True, **kwargs: object
) -> PrWorkRecurringTemplate:
    """A template, activated by the Trưởng nhóm unless told otherwise."""
    type_row = kwargs.pop("type_row", None) or await work_type(world)
    contributors = kwargs.pop("contributors", None) or (world.member.id,)
    row = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=command(type_row=type_row, contributors=contributors, **kwargs),  # type: ignore[arg-type]
    )
    if activate:
        await world.services.work_recurring.activate(
            actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
        )
    return row


def generator(world: World) -> PrWorkRecurringGenerator:
    return world.services.work_recurring_generator


async def sweep(world: World, row: PrWorkRecurringTemplate, *, at: datetime) -> object:
    return await generator(world).sweep_template(
        template_id=row.id, request_id=world.request_id, at=at
    )


def at_utc(value: datetime | None) -> datetime | None:
    """One stored instant as aware UTC.

    The offline fixture is SQLite, whose driver drops the offset a
    ``DateTime(timezone=True)`` column carries - so an instant read back from a
    committed row is naive there and aware on PostgreSQL. The services read
    stored instants through ``meobot.core.time.ensure_utc`` for exactly this
    reason, and the assertions have to do the same or they would be asserting
    about the driver rather than about the scheduler.
    """
    from meobot.core.time import ensure_utc

    return None if value is None else ensure_utc(value)


async def stamps(world: World, row: PrWorkRecurringTemplate) -> list[datetime]:
    """When a template's firings were scheduled for, oldest first."""
    return [at_utc(one.scheduled_for) for one in await occurrences(world, row)]  # type: ignore[misc]


async def occurrences(
    world: World, row: PrWorkRecurringTemplate
) -> list[PrWorkRecurringOccurrence]:
    return list(
        (
            await world.session.execute(
                select(PrWorkRecurringOccurrence)
                .where(PrWorkRecurringOccurrence.template_id == row.id)
                .order_by(PrWorkRecurringOccurrence.scheduled_for)
            )
        )
        .scalars()
        .all()
    )


async def generated_items(world: World) -> list[PrWorkItem]:
    return list(
        (
            await world.session.execute(
                select(PrWorkItem)
                .where(PrWorkItem.source_type == PrWorkSourceType.RECURRING)
                .order_by(PrWorkItem.created_at, PrWorkItem.code)
            )
        )
        .scalars()
        .all()
    )


async def set_cursor(world: World, row: PrWorkRecurringTemplate, moment: datetime) -> None:
    """Move a template's cursor directly.

    Test scaffolding, and written directly on purpose: **there is no service
    path that rewinds a cursor**, and inventing one for a test would be
    inventing the backfill button M4B deliberately does not build. Simulating an
    outage means "the sweeper did not run", which is exactly a cursor left where
    it was while time moved on.
    """
    row.last_evaluated_occurrence_at = moment
    await world.session.flush()


async def month_period(
    world: World, *, status: PrPeriodStatus, year: int = 2026, month: int = 9
) -> PrReportingPeriod:
    """One reporting period in a chosen state."""
    period = await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner), request_id=world.request_id, year=year, month=month
    )
    period.status = status
    await world.session.flush()
    return period


# ===========================================================================
# 1-4: THE CALENDAR
# ===========================================================================


async def test_01_daily_weekly_and_monthly_all_answer_next_only(world: World) -> None:
    """The three shapes, each asked the one question the engine answers.

    ``next_after`` is the **only** forward primitive in M4B, and it is strictly
    after its argument - which is what makes it safe to feed its own output back
    in. Everything the sweeper does is this call in a loop.
    """
    daily = RecurringSchedule(frequency=PrRecurringFrequency.DAILY, run_time=time(9, 0))
    first = daily.next_after(local(2026, 9, 4, 8, 0), tz=SAIGON)
    assert first == local(2026, 9, 4, 9, 0)
    # Strictly after: asking again from the answer moves on rather than
    # returning the same firing twice.
    assert daily.next_after(first, tz=SAIGON) == local(2026, 9, 5, 9, 0)

    # 4 September 2026 is a Friday. Monday=0, so Monday and Wednesday are 0, 2.
    weekly = RecurringSchedule(
        frequency=PrRecurringFrequency.WEEKLY, run_time=time(9, 0), weekdays=(0, 2)
    )
    assert weekly.next_after(local(2026, 9, 4, 10, 0), tz=SAIGON) == local(2026, 9, 7, 9, 0)
    assert weekly.next_after(local(2026, 9, 7, 9, 0), tz=SAIGON) == local(2026, 9, 9, 9, 0)

    monthly = RecurringSchedule(
        frequency=PrRecurringFrequency.MONTHLY, run_time=time(17, 0), day_of_month=5
    )
    assert monthly.next_after(local(2026, 9, 4, 0, 0), tz=SAIGON) == local(2026, 9, 5, 17, 0)
    assert monthly.next_after(local(2026, 9, 5, 17, 0), tz=SAIGON) == local(2026, 10, 5, 17, 0)


async def test_02_a_monthly_31st_is_clamped_rather_than_skipped(world: World) -> None:
    """*"Báo cáo ngày 31"* means the end of the month, not "not in February".

    Skipping would make a monthly report silently lose a month, which is the
    failure a monthly report exists to prevent.
    """
    monthly = RecurringSchedule(
        frequency=PrRecurringFrequency.MONTHLY, run_time=time(17, 0), day_of_month=31
    )
    assert monthly.next_after(local(2027, 2, 1, 0, 0), tz=SAIGON) == local(2027, 2, 28, 17, 0)
    assert monthly.next_after(local(2027, 4, 1, 0, 0), tz=SAIGON) == local(2027, 4, 30, 17, 0)


async def test_03_the_schedule_is_evaluated_in_the_local_wall_clock(world: World) -> None:
    """09:00 is 09:00 in Ho Chi Minh City, which is 02:00 UTC.

    The rule the reminders engine already followed and the reason this module
    composes it rather than reimplementing it: a schedule computed in UTC and
    converted afterwards is a schedule that moves the moment anything daylight
    saving is involved.
    """
    daily = RecurringSchedule(frequency=PrRecurringFrequency.DAILY, run_time=time(9, 0))
    firing = daily.next_after(local(2026, 9, 4, 0, 0), tz=SAIGON)
    assert firing.astimezone(UTC).hour == 2
    assert firing.astimezone(SAIGON).hour == 9


async def test_04_a_weekly_schedule_needs_weekdays_and_monthly_needs_a_day(
    world: World,
) -> None:
    """A routine that can never fire is refused where it is described.

    In the domain rather than only at the route, so a service-level caller that
    never touched a request body is refused too.
    """
    with pytest.raises(PrValidationError) as no_days:
        RecurringSchedule(frequency=PrRecurringFrequency.WEEKLY, run_time=time(9, 0))
    assert no_days.value.details["reason"] == "weekdays_required"

    with pytest.raises(PrValidationError) as no_day:
        RecurringSchedule(frequency=PrRecurringFrequency.MONTHLY, run_time=time(9, 0))
    assert no_day.value.details["reason"] == "day_of_month_out_of_range"


# ===========================================================================
# 5-9: TEMPLATE LIFECYCLE
# ===========================================================================


async def test_05_a_new_template_is_a_draft_and_generates_nothing(world: World) -> None:
    """``DRAFT`` is where a routine starts, and the extra click is the point.

    A template is a standing instruction to create work every day until somebody
    stops it. The person writing it should read the schedule preview before it
    starts running, and activation is a separate act with its own audit row.
    """
    row = await template(world, activate=False)
    assert row.status is PrRecurringTemplateStatus.DRAFT
    assert row.last_evaluated_occurrence_at is None
    assert row.activated_by_user_id is None

    # And the sweeper does not see it at all - which is what makes "a draft
    # generates nothing" structural rather than a check somebody could forget.
    assert row.id not in await generator(world).due_templates(at=local(2026, 9, 30, 12, 0))


async def test_06_activation_records_the_authorizing_manager(world: World) -> None:
    """An ``ACTIVE`` template is a manager's standing authorization, by name.

    Every job it generates is created by and assigned by this person, which is
    what makes the work legitimately ``ACCEPTED`` - *the assignment is the
    authorization* - and what stops a routine job's audit trail naming a worker
    process.
    """
    row = await template(world, activate=False)
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    assert row.status is PrRecurringTemplateStatus.ACTIVE
    assert row.activated_by_user_id == world.lead.id
    assert row.activated_at is not None


async def test_07_an_employee_cannot_create_or_activate_a_routine(world: World) -> None:
    """``PR_WORK_MANAGE``, and the reason is M1's anti-gaming rule extended.

    An employee who could activate a template could put work into their own
    workload every morning without any manager deciding anything - which is
    exactly what ``propose_work`` exists to prevent for a single job.
    """
    type_row = await work_type(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_recurring.create_template(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=command(type_row=type_row, contributors=(world.member.id,)),
        )

    row = await template(world, activate=False)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_recurring.activate(
            actor=world.actor(world.member), request_id=world.request_id, template_id=row.id
        )


async def test_08_an_ended_template_is_terminal_and_a_used_one_is_not_deleted(
    world: World,
) -> None:
    """A routine that is over is copied, not restarted - and never deleted.

    Restarting would resume a cursor that stopped meaning anything the day it
    ended. Deleting would remove the provenance of everything it produced, and
    the occurrence ledger is what explains a month in which it produced nothing.
    """
    row = await template(world)
    await sweep(world, row, at=local(2026, 9, 30, 12, 0))
    await world.services.work_recurring.end(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    assert row.status is PrRecurringTemplateStatus.ENDED

    with pytest.raises(PrValidationError) as restart:
        await world.services.work_recurring.activate(
            actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
        )
    assert restart.value.details["reason"] == "illegal_template_transition"

    with pytest.raises(PrValidationError) as removal:
        await world.services.work_recurring.delete_template(
            actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
        )
    assert removal.value.details["reason"] == "only_draft_deletable"


async def test_09_an_untouched_draft_may_be_deleted(world: World) -> None:
    """The one thing a hard delete is safe for: a draft nothing came from."""
    row = await template(world, activate=False)
    await world.services.work_recurring.delete_template(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    assert await world.session.get(PrWorkRecurringTemplate, row.id) is None


# ===========================================================================
# 10-13: ACTIVATION BOUNDARY, AND THE FLOOD THAT MUST NOT HAPPEN
# ===========================================================================


async def test_10_activation_never_generates_history(world: World) -> None:
    """**No Sep 1-14 flood.** The requirement, stated as its own test.

    A template whose schedule notionally began on 1 September and which is
    activated on 15 September must produce its first occurrence on or after the
    15th. The cursor is set to the later of local midnight on ``start_date`` and
    the activation instant, so the fortnight before it was switched on is not
    ground the sweeper ever walks.
    """
    row = await template(world, activate=False, start=date(2026, 9, 1))
    activated = local(2026, 9, 15, 10, 0)
    boundary = world.services.work_recurring.activation_boundary(row, at=activated)
    assert boundary == activated

    row.status = PrRecurringTemplateStatus.ACTIVE
    row.activated_at = activated
    row.activated_by_user_id = world.lead.id
    row.last_evaluated_occurrence_at = boundary
    await world.session.flush()

    await sweep(world, row, at=local(2026, 9, 15, 23, 0))
    assert await occurrences(world, row) == []

    await sweep(world, row, at=local(2026, 9, 16, 12, 0))
    firings = await stamps(world, row)
    assert firings == [local(2026, 9, 16, 9, 0)]
    assert all(one >= activated for one in firings)


async def test_11_a_future_start_date_is_honoured(world: World) -> None:
    """The boundary is the *later* of the two, so nothing fires early either."""
    row = await template(world, activate=False, start=date(2026, 10, 1))
    boundary = world.services.work_recurring.activation_boundary(row, at=local(2026, 9, 15, 10, 0))
    assert boundary == local(2026, 10, 1, 0, 0)


async def test_12_an_end_date_stops_generation(world: World) -> None:
    """A routine that ends on the 3rd produces nothing on the 4th.

    Inclusive of the end date itself - "đến ngày 3" includes everything that
    fires *on* the 3rd, which is what a person means by it.
    """
    row = await template(world, activate=False, start=date(2026, 9, 1), end=date(2026, 9, 3))
    row.status = PrRecurringTemplateStatus.ACTIVE
    row.activated_at = local(2026, 9, 1, 0, 0)
    row.activated_by_user_id = world.lead.id
    row.last_evaluated_occurrence_at = local(2026, 9, 1, 0, 0)
    await world.session.flush()

    await sweep(world, row, at=local(2026, 9, 20, 12, 0))
    firings = await stamps(world, row)
    assert firings == [
        local(2026, 9, 1, 9, 0),
        local(2026, 9, 2, 9, 0),
        local(2026, 9, 3, 9, 0),
    ]
    # And the template is no longer swept at all.
    assert row.id not in await generator(world).due_templates(at=local(2026, 9, 20, 12, 0))


async def test_13_nothing_is_generated_before_the_firing_instant(world: World) -> None:
    """09:00 work does not appear at 08:59. The cursor moves; the work does not."""
    row = await template(world, activate=False, start=date(2026, 9, 4))
    row.status = PrRecurringTemplateStatus.ACTIVE
    row.activated_by_user_id = world.lead.id
    row.last_evaluated_occurrence_at = local(2026, 9, 4, 0, 0)
    await world.session.flush()

    await sweep(world, row, at=local(2026, 9, 4, 8, 59))
    assert await occurrences(world, row) == []
    await sweep(world, row, at=local(2026, 9, 4, 9, 1))
    assert len(await occurrences(world, row)) == 1


# ===========================================================================
# 14-19: GENERATION - ONE LEDGER, M1'S OWN PATH
# ===========================================================================


async def test_14_shared_work_produces_one_item_for_everybody(world: World) -> None:
    """One job several people are on. One item, one contribution each.

    M4A's ``SHARED_WORK``, read off the template - where the mode genuinely is a
    stored fact, because it has to survive until the next occurrence.
    """
    row = await template(
        world,
        start=date(2026, 9, 4),
        mode=PrWorkAssignmentMode.SHARED_WORK,
        contributors=(world.member.id, world.other.id),
    )
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))

    items = await generated_items(world)
    assert len(items) == 1
    assert items[0].source_type is PrWorkSourceType.RECURRING
    assert items[0].status is PrWorkStatus.ACCEPTED
    contributions = (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == items[0].id)
            )
        )
        .scalars()
        .all()
    )
    assert {one.user_id for one in contributions} == {world.member.id, world.other.id}


async def test_15_separate_per_assignee_produces_one_item_each(world: World) -> None:
    """The same instruction to two people is **two** obligations.

    The dangerous direction is recording it as one shared job: one person would
    then complete it for both, one validation would count both, and neither
    could be late on their own work.
    """
    row = await template(
        world,
        start=date(2026, 9, 4),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
        contributors=(world.member.id, world.other.id),
    )
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))

    items = await generated_items(world)
    assert len(items) == 2
    owners: set[uuid.UUID] = set()
    for item in items:
        rows = (
            (
                await world.session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        owners.add(rows[0].user_id)
    assert owners == {world.member.id, world.other.id}
    assert len({item.source_key for item in items}) == 2


async def test_16_generated_work_is_accepted_and_never_counted(world: World) -> None:
    """**Activation is not validation.** The rule the whole milestone rests on.

    An ``ACTIVE`` template is management authorization for the routine, so the
    work enters M1's authorized operational state. It is emphatically not
    ``COMPLETED``, not ``APPROVED`` and not ``COUNTED``: nobody has done it and
    nobody has confirmed it.
    """
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))

    item = (await generated_items(world))[0]
    assert item.status is PrWorkStatus.ACCEPTED
    assert item.completed_at is None
    assert item.approved_at is None
    assert item.assigned_by_user_id == world.lead.id
    assert item.created_by_user_id == world.lead.id
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


async def test_17_generated_work_is_dated_to_the_occurrence(world: World) -> None:
    """Work owed on Tuesday says Tuesday, even when caught up on Thursday.

    Catch-up after an outage would otherwise date three days of work to the
    moment the worker came back, which puts it in the wrong week on every board
    and, at a month boundary, in the wrong reporting period.
    """
    row = await template(world, start=date(2026, 9, 1), due_after_hours=8)
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    await sweep(world, row, at=local(2026, 9, 3, 12, 0))

    items = await generated_items(world)
    assert [at_utc(one.accepted_at) for one in items] == [
        local(2026, 9, 1, 9, 0),
        local(2026, 9, 2, 9, 0),
        local(2026, 9, 3, 9, 0),
    ]
    assert at_utc(items[0].due_at) == local(2026, 9, 1, 17, 0)


async def test_18_a_generated_job_goes_all_the_way_through_m1(world: World) -> None:
    """**M2 and M6 need no recurring-specific path.** The exit-gate item.

    A generated job is completed by its contributor and validated by somebody
    who did not do it, through the same two methods a hand-typed job uses. No
    ``source_type`` is consulted anywhere on the way.
    """
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))
    item = (await generated_items(world))[0]

    await world.services.work.start(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    await world.services.work.complete(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    await world.services.work.approve(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    await world.session.refresh(item)
    assert item.status is PrWorkStatus.APPROVED
    contribution = (
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
            )
        )
        .scalars()
        .one()
    )
    assert contribution.count_status is PrWorkCountStatus.COUNTED


async def test_19_a_contributor_still_cannot_validate_their_own_routine_work(
    world: World,
) -> None:
    """The anti-gaming rule is untouched by where the work came from."""
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))
    item = (await generated_items(world))[0]

    await world.services.work.complete(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.approve(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        )


# ===========================================================================
# A: THE OPERATIONAL QUANTITY BOUNDARY
# ===========================================================================


async def test_a1_the_service_entry_points_reject_a_missing_quantity(world: World) -> None:
    """**Not only the route.** M4B moved the rule down one level, exactly one.

    M4A enforced it in the router on the reasoning that every way a person files
    work is an HTTP call. M4B made that false - the generator files work from a
    beat sweep - so the rule now lives at the service's operational entry points,
    where a caller who never touched FastAPI meets it too.
    """
    type_row = await seeding_type(world)
    for call in (
        world.services.work.assign_work,
        world.services.work.propose_work,
    ):
        with pytest.raises(PrValidationError) as error:
            await call(
                actor=world.actor(world.lead),
                request_id=world.request_id,
                command=CreateWorkCommand(
                    work_type_id=type_row.id,
                    title="Seeding không ghi số lượng",
                    contributor_user_ids=(world.member.id,),
                ),
            )
        assert error.value.details["reason"] == "quantity_required_for_basis"

    with pytest.raises(PrValidationError) as batch:
        await world.services.work.assign_work_batch(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=CreateWorkCommand(
                work_type_id=type_row.id,
                title="Seeding không ghi số lượng",
                contributor_user_ids=(world.member.id, world.other.id),
            ),
            mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
        )
    assert batch.value.details["reason"] == "quantity_required_for_basis"


async def test_a2_a_recurring_template_rejects_a_missing_quantity(world: World) -> None:
    """The rule reaches the template, so a manager finds out while writing it.

    Not at 4am, when the first sweep would otherwise refuse a routine nobody is
    watching.
    """
    type_row = await seeding_type(world)
    with pytest.raises(PrValidationError) as error:
        await world.services.work_recurring.create_template(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=command(type_row=type_row, contributors=(world.member.id,)),
        )
    assert error.value.details["reason"] == "quantity_required_for_basis"


async def test_a3_the_generator_refuses_an_invalid_quantity_defensively(
    world: World,
) -> None:
    """Enforced again at generation, and the repetition is deliberate.

    A work type's ``default_quota_basis`` can be changed after a template was
    written, so a routine that was valid in September can be invalid in October.
    A rule enforced in exactly one place is a rule one bug away from being
    unenforced, so ``generate_recurring_work`` checks it as well - and the
    occurrence records the refusal instead of silently filing a job that reads
    as zero comments.
    """
    type_row = await work_type(world, code="FLEXIBLE", name="Việc linh hoạt")
    row = await template(world, type_row=type_row, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    # The basis changes underneath the template - the case the second check
    # exists for. Written directly because M2.5 locks the field once the type is
    # in use, which is exactly why no service path can produce this.
    type_row.default_quota_basis = PrWorkQuotaBasis.QUANTITY
    await world.session.flush()

    outcome = await sweep(world, row, at=local(2026, 9, 4, 12, 0))
    assert outcome.generated == 0  # type: ignore[attr-defined]
    assert outcome.failed == 1  # type: ignore[attr-defined]
    assert await generated_items(world) == []
    settled = await occurrences(world, row)
    assert settled[0].state is PrRecurringOccurrenceState.FAILED_RETRYABLE
    assert "số" in (settled[0].last_error or "").lower()


async def test_a4_the_low_level_path_can_still_represent_a_missing_quantity(
    world: World,
) -> None:
    """**M2's ``MISSING_QUANTITY`` state survives.** The other half of the rule.

    The boundary M4B drew is between *"you may not ask for this"* and *"this
    cannot be described"*, and only the first is a rule about commands. A
    quantity-measured job filed years ago with no number is a row that can still
    be true, and M2 materialises an allocation naming the quota and saying what
    is missing - which is a repair path, not a bug. Pushing the rule down into
    ``_create`` would delete the system's ability to say what is wrong with data
    it already holds.
    """
    type_row = await seeding_type(world)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id,
            title="Seeding cũ",
            quantity=Decimal("40"),
            contributor_user_ids=(world.member.id,),
        ),
    )
    # The representation level. The pair is nullable together, by the CHECK
    # constraint, and nothing about the row is incoherent - it is incomplete,
    # which is the fact M2 exists to report.
    item.quantity = None
    item.unit = None
    await world.session.flush()

    from meobot.domain.pr.work_quota import PrWorkUnmeasurableReason, measure_contribution

    measurement = measure_contribution(
        PrWorkQuotaBasis.QUANTITY, quantity=item.quantity, credit_weight=Decimal("1.0000")
    )
    assert measurement.is_measurable is False
    assert measurement.reason is PrWorkUnmeasurableReason.MISSING_QUANTITY
    assert measurement.amount is None


async def test_a5_a_recurring_quantity_is_one_item_not_a_hundred(world: World) -> None:
    """100 comments a day is **one** work item with ``quantity = 100``.

    The M1 rule, restated because M4B is the milestone that types the number
    into a form that will repeat it every morning.
    """
    type_row = await seeding_type(world)
    row = await template(
        world,
        type_row=type_row,
        start=date(2026, 9, 4),
        quantity=Decimal("100"),
        name="100 comment seeding",
    )
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))

    items = await generated_items(world)
    assert len(items) == 1
    assert items[0].quantity == Decimal("100.00")
    # Copied from the work type, never from the template - so editing a type
    # never rewrites what historical work claimed to be.
    assert items[0].unit is PrWorkUnit.COMMENT


# ===========================================================================
# B: NEXT-ONLY TRAVERSAL AND CATCH-UP
# ===========================================================================


async def test_b1_several_missed_firings_are_caught_up_in_order(world: World) -> None:
    """The cursor walks forward one ``next_after`` at a time.

    There is no enumeration primitive anywhere in M4B - the engine answers
    *"what fires next"* and the sweeper asks it repeatedly, recording each
    occurrence durably before asking for the following one.
    """
    row = await template(world, start=date(2026, 9, 1))
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    await sweep(world, row, at=local(2026, 9, 5, 12, 0))

    firings = await stamps(world, row)
    assert firings == [local(2026, 9, day, 9, 0) for day in (1, 2, 3, 4, 5)]
    settled = await occurrences(world, row)
    assert all(one.state is PrRecurringOccurrenceState.GENERATED for one in settled)
    assert at_utc(row.last_evaluated_occurrence_at) == local(2026, 9, 5, 12, 0)


async def test_b2_the_batch_bound_is_respected_and_the_rerun_continues(
    world: World,
) -> None:
    """Catch-up is bounded per sweep and resumes exactly where it stopped.

    A template unswept for three months must not try to file ninety days of work
    in one batch. It catches up over several sweeps instead, which is slower and
    cannot time out - and the second sweep starts from the cursor rather than
    from the beginning.
    """
    row = await template(world, start=date(2026, 8, 1))
    start = local(2026, 8, 1, 0, 0)
    await set_cursor(world, row, start)
    now = start + timedelta(days=40)

    first = await sweep(world, row, at=now)
    assert first.evaluated == MAX_OCCURRENCES_PER_SWEEP  # type: ignore[attr-defined]
    after_first = await occurrences(world, row)
    assert len(after_first) == MAX_OCCURRENCES_PER_SWEEP

    second = await sweep(world, row, at=now)
    assert second.evaluated > 0  # type: ignore[attr-defined]
    after_second = await occurrences(world, row)
    assert len(after_second) > len(after_first)
    # No firing is reserved twice, which is what makes the resumption exact.
    keys = [one.occurrence_key for one in after_second]
    assert len(keys) == len(set(keys))


async def test_b3_catch_up_will_not_reach_indefinitely_far_back(world: World) -> None:
    """A cursor older than the catch-up bound is floored, not walked.

    Work owed four months ago is not work anybody wants filed today, and the
    reporting period it belonged to is closed anyway. This is a separate bound
    from the per-sweep batch because the two answer different questions: one
    limits a batch, this one limits *history*.
    """
    row = await template(world, start=date(2025, 1, 1))
    await set_cursor(world, row, local(2025, 1, 1, 0, 0))
    now = local(2026, 9, 4, 12, 0)
    await sweep(world, row, at=now)

    firings = await stamps(world, row)
    assert firings, "the sweep still generates - the floor moves the cursor, it does not stop it"
    assert min(firings) > now - timedelta(days=60)


async def test_b4_a_sweep_with_nothing_due_still_settles_the_cursor(
    world: World,
) -> None:
    """A fruitless walk is not repeated every thirty seconds.

    The cursor moves up to the ground examined - and never past the firing
    itself, which would skip it.
    """
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 6, 0))

    assert await occurrences(world, row) == []
    assert at_utc(row.last_evaluated_occurrence_at) == local(2026, 9, 4, 6, 0)
    # And the firing it stopped short of is still generated afterwards.
    await sweep(world, row, at=local(2026, 9, 4, 10, 0))
    assert len(await occurrences(world, row)) == 1


# ===========================================================================
# C: PAUSE IS NOT DOWNTIME
# ===========================================================================


async def test_c1_a_paused_interval_is_never_backfilled(world: World) -> None:
    """**The distinction the cursor's name exists for.**

    A pause is a decision that the work should not have happened. Resuming moves
    the cursor to the resume instant, so the suspended fortnight is never walked
    and produces no occurrences, no work and no rows at all. Modelling it as
    downtime would hand a manager who paused a routine over Tết a fortnight of
    backdated work the moment they turned it back on.
    """
    row = await template(world, start=date(2026, 9, 1))
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    await sweep(world, row, at=local(2026, 9, 1, 12, 0))
    assert len(await occurrences(world, row)) == 1

    await world.services.work_recurring.pause(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    assert row.status is PrRecurringTemplateStatus.PAUSED
    # A paused template is not swept at all - which is what makes "pause
    # generates nothing" structural.
    assert row.id not in await generator(world).due_templates(at=local(2026, 9, 10, 12, 0))
    await sweep(world, row, at=local(2026, 9, 10, 12, 0))
    assert len(await occurrences(world, row)) == 1

    await world.services.work_recurring.resume(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    # The cursor jumped to now, so the paused days are behind it for ever.
    assert row.last_evaluated_occurrence_at is not None
    assert at_utc(row.last_evaluated_occurrence_at) > local(2026, 9, 1, 12, 0)
    resumed = at_utc(row.last_evaluated_occurrence_at)
    assert resumed is not None
    await sweep(world, row, at=resumed + timedelta(days=1))
    firings = await stamps(world, row)

    # **The assertion, stated as the property rather than as two dates.**
    # ``resume`` reads the real clock - it is a decision somebody makes now, not
    # a simulated instant - so the suspended window is "the pause until the
    # resume", and what must be empty is every firing inside it. Firings *after*
    # the resume are ordinary and expected.
    suspended = [
        local(2026, 9, day, 9, 0)
        for day in range(2, 30)
        if local(2026, 9, 1, 12, 0) < local(2026, 9, day, 9, 0) <= resumed
    ]
    assert suspended, "the pause must cover at least one firing for this to prove anything"
    assert not set(suspended) & set(firings)
    # And the one firing from before the pause is still there - suppressing the
    # interval is not deleting what the routine already produced.
    assert firings[0] == local(2026, 9, 1, 9, 0)


async def test_c2_worker_downtime_while_active_is_caught_up(world: World) -> None:
    """The opposite case, and it must behave the opposite way.

    Nobody decided anything during an outage; the cursor stayed where it was,
    and the department genuinely owed that work. Sep 10-12 unswept means Sep
    10-12 generated when the worker returns on the 13th.
    """
    row = await template(world, start=date(2026, 9, 1))
    await set_cursor(world, row, local(2026, 9, 9, 12, 0))

    await sweep(world, row, at=local(2026, 9, 13, 12, 0))
    firings = await stamps(world, row)
    assert firings == [local(2026, 9, day, 9, 0) for day in (10, 11, 12, 13)]


async def test_c3_resuming_never_moves_the_cursor_backwards(world: World) -> None:
    """``max``, not assignment.

    A resume must not put settled ground back in front of the sweeper - which is
    what an unconditional assignment would do for a template whose cursor had
    somehow moved ahead of now.
    """
    row = await template(world, start=date(2026, 9, 1))
    ahead = datetime.now(tz=UTC) + timedelta(days=30)
    await set_cursor(world, row, ahead)
    await world.services.work_recurring.pause(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    await world.services.work_recurring.resume(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    assert at_utc(row.last_evaluated_occurrence_at) == ahead


# ===========================================================================
# D: A CLOSED OR LOCKED PERIOD IS REFUSED ONCE
# ===========================================================================


@pytest.mark.parametrize("shut", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_d1_a_shut_reporting_period_is_skipped_once(
    world: World, shut: PrPeriodStatus
) -> None:
    """Generating into a closed month would move a number under a report.

    And retrying tomorrow would fail for the identical reason for ever, so the
    occurrence is **settled** rather than left in the retry set. The reason is
    stored on the row, because *"the routine produced nothing in the last week of
    August"* is a question somebody asks in September.
    """
    period = await month_period(world, status=shut)
    row = await template(world, start=date(2026, 9, 1))
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))

    outcome = await sweep(world, row, at=local(2026, 9, 2, 12, 0))
    assert outcome.generated == 0  # type: ignore[attr-defined]
    assert outcome.skipped_closed_period == 2  # type: ignore[attr-defined]
    assert await generated_items(world) == []

    settled = await occurrences(world, row)
    assert [one.state for one in settled] == [PrRecurringOccurrenceState.SKIPPED_CLOSED_PERIOD] * 2
    assert all(one.reporting_period_id == period.id for one in settled)
    assert all(period.code in (one.last_error or "") for one in settled)

    # **And never retried.** A second sweep reaches the same rows and does not
    # touch them, which is what stops a permanent refusal becoming a loop.
    again = await sweep(world, row, at=local(2026, 9, 2, 13, 0))
    assert again.failed == 0  # type: ignore[attr-defined]
    assert [one.attempts for one in await occurrences(world, row)] == [1, 1]


async def test_d2_an_open_period_generates_retroactively(world: World) -> None:
    """Catch-up into an **open** month proceeds. The period is a lock, not a fence."""
    await month_period(world, status=PrPeriodStatus.OPEN)
    row = await template(world, start=date(2026, 9, 1))
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    outcome = await sweep(world, row, at=local(2026, 9, 3, 12, 0))
    assert outcome.generated == 3  # type: ignore[attr-defined]


async def test_d3_a_month_nobody_opened_still_generates(world: World) -> None:
    """``period_for`` returning ``None`` is a missing configuration, not a lock.

    M2 is explicit that ``None`` means nobody has set the month up - an
    operational fact. Refusing to file work because of it would let a missing
    configuration row silently delete the department's work.
    """
    row = await template(world, start=date(2026, 9, 1))
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    outcome = await sweep(world, row, at=local(2026, 9, 2, 12, 0))
    assert outcome.generated == 2  # type: ignore[attr-defined]
    assert all(one.reporting_period_id is None for one in await occurrences(world, row))


# ===========================================================================
# E: A TRANSIENT FAILURE LOSES NOTHING
# ===========================================================================


async def test_e1_a_transient_failure_is_retried_and_does_not_duplicate(
    world: World,
) -> None:
    """The occurrence row is the record of the obligation, so nothing is lost.

    The cursor may pass a failure because the retry set comes back to it - which
    is what avoids the head-of-line problem where one permanently unfixable
    firing stops a routine for ever. And the retry produces no second copy,
    because the work item's ``(source_type, source_key)`` unique index is keyed
    on the occurrence's own id, so a retry composes the identical key.
    """
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))

    # A failure the next sweep will not see: the assignee is away, and comes
    # back. Any exception from the generation path lands the same way.
    world.member.active = False
    await world.session.flush()
    failed = await sweep(world, row, at=local(2026, 9, 4, 12, 0))
    assert failed.failed == 1  # type: ignore[attr-defined]
    assert failed.generated == 0  # type: ignore[attr-defined]
    assert await generated_items(world) == []
    owed = await occurrences(world, row)
    assert [one.state for one in owed] == [PrRecurringOccurrenceState.FAILED_RETRYABLE]
    assert owed[0].attempts == 1

    world.member.active = True
    await world.session.flush()
    recovered = await sweep(world, row, at=local(2026, 9, 4, 13, 0))
    assert recovered.generated == 1  # type: ignore[attr-defined]
    settled = await occurrences(world, row)
    assert [one.state for one in settled] == [PrRecurringOccurrenceState.GENERATED]
    assert settled[0].last_error is None
    # One firing, one job - not two.
    assert len(await generated_items(world)) == 1


async def test_e2_a_settled_occurrence_is_not_regenerated(world: World) -> None:
    """Sweeping the same day twice produces one job, not two.

    The occurrence is settled and out of the retry set, and the cursor is past
    it - two independent reasons, which is what a guarantee looks like.
    """
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 30))
    await sweep(world, row, at=local(2026, 9, 4, 13, 0))
    assert len(await generated_items(world)) == 1
    assert len(await occurrences(world, row)) == 1


async def test_e3_work_that_already_exists_settles_its_occurrence(world: World) -> None:
    """A worker that died between filing the work and settling the row recovers.

    The occurrence is left ``PENDING`` with the work already in the ledger. The
    next sweep must settle it rather than file a second copy - which is what the
    read-before-write in ``_already_generated`` is for, with the unique index
    behind it as the actual guarantee.
    """
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))

    # Rewind the ledger row to the state a crash would have left, keeping the
    # work. Written directly because no service path produces it - which is the
    # point of testing it.
    settled = (await occurrences(world, row))[0]
    settled.state = PrRecurringOccurrenceState.PENDING
    settled.generated_at = None
    settled.work_item_count = 0
    await world.session.flush()

    recovered = await sweep(world, row, at=local(2026, 9, 4, 13, 0))
    assert recovered.failed == 0  # type: ignore[attr-defined]
    assert len(await generated_items(world)) == 1
    assert (await occurrences(world, row))[0].state is PrRecurringOccurrenceState.GENERATED


# ===========================================================================
# F: THE THREE-SEGMENT SOURCE KEY
# ===========================================================================


async def test_f1_the_source_key_obeys_the_actual_three_segment_contract(
    world: World,
) -> None:
    """``recurring:{occurrence-uuid}:{SUBJECT}`` - validated by M1's own assertion.

    The M0 document described a four-segment example. The audit found the real
    contract is three, so the occurrence row's uuid names the firing and the
    third segment names the assignee. **Nothing global was widened**, and the key
    goes through ``work_source_key`` like every other.
    """
    from meobot.domain.pr.work import SOURCE_KEY_PATTERN, assert_source_key

    occurrence_id = uuid.uuid4()
    shared = recurring_source_key(occurrence_id, user_id=None)
    assert shared == f"recurring:{occurrence_id}:{SHARED_SUBJECT}"
    assert_source_key(shared)
    assert SOURCE_KEY_PATTERN.match(shared)
    assert shared.count(":") == 2

    per_person = recurring_source_key(occurrence_id, user_id=world.member.id)
    assert_source_key(per_person)
    assert per_person.count(":") == 2
    # The assignee's **whole** id, upper-cased hex, not a hash of it: a truncated
    # id would make "one item per assignee" probabilistic, and the unique index
    # over it would then swallow somebody's work rather than duplicate it.
    assert world.member.id.hex.upper() in per_person


async def test_f2_shared_and_separate_keys_are_unique_per_person(world: World) -> None:
    """One occurrence, two assignees, two keys - and no collision anywhere."""
    occurrence_id = uuid.uuid4()
    keys = {
        recurring_source_key(occurrence_id, user_id=None),
        recurring_source_key(occurrence_id, user_id=world.member.id),
        recurring_source_key(occurrence_id, user_id=world.other.id),
        recurring_source_key(uuid.uuid4(), user_id=world.member.id),
    }
    assert len(keys) == 4


async def test_f3_the_occurrence_key_is_the_local_wall_clock(world: World) -> None:
    """``20260904T0900``, not the UTC instant.

    *"Báo cáo 9 giờ sáng ngày 4"* is a statement about a Vietnamese wall clock. A
    key written in UTC would name it ``20260904T0200`` and, on a deployment that
    ever moved timezone, would name two different firings the same thing.
    """
    assert occurrence_key(local(2026, 9, 4, 9, 0), tz=SAIGON) == "20260904T0900"


async def test_f4_generated_work_carries_the_key_the_occurrence_names(
    world: World,
) -> None:
    """End to end: the row, the key on the work, and the index that binds them."""
    row = await template(
        world,
        start=date(2026, 9, 4),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
        contributors=(world.member.id, world.other.id),
    )
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))

    settled = (await occurrences(world, row))[0]
    expected = {
        recurring_source_key(settled.id, user_id=world.member.id),
        recurring_source_key(settled.id, user_id=world.other.id),
    }
    assert {one.source_key for one in await generated_items(world)} == expected
    assert settled.work_item_count == 2


# ===========================================================================
# 20-24: TEMPLATE REVISIONS, AUDIT, AND WHAT IS NOT EXPOSED
# ===========================================================================


async def test_20_an_edit_bumps_the_revision_and_the_occurrence_records_it(
    world: World,
) -> None:
    """A routine edited mid-month legitimately produced two kinds of job.

    ``revision_no`` is what makes that provable rather than merely intended: the
    generator locks the template while it builds an occurrence and stamps the
    version it saw, so a sweep cannot produce a work item with the old work
    type, the new quantity and yesterday's assignee list.
    """
    row = await template(world, start=date(2026, 9, 1))
    assert row.revision_no == 1
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    await sweep(world, row, at=local(2026, 9, 1, 12, 0))
    assert (await occurrences(world, row))[0].template_revision_no == 1

    await world.services.work_recurring.update_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        template_id=row.id,
        command=command(
            type_row=await world.session.get(PrWorkType, row.work_type_id),  # type: ignore[arg-type]
            contributors=(world.member.id, world.other.id),
            start=date(2026, 9, 1),
            name="Báo cáo hằng ngày (bản mới)",
        ),
    )
    assert row.revision_no == 2
    await sweep(world, row, at=local(2026, 9, 2, 12, 0))
    stamped = await occurrences(world, row)
    assert [one.template_revision_no for one in stamped] == [1, 2]


async def test_21_editing_never_rewrites_work_already_generated(world: World) -> None:
    """A job created last Tuesday records what was asked for last Tuesday."""
    row = await template(world, start=date(2026, 9, 1), name="Báo cáo cũ")
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    await sweep(world, row, at=local(2026, 9, 1, 12, 0))
    before = (await generated_items(world))[0].title

    await world.services.work_recurring.update_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        template_id=row.id,
        command=command(
            type_row=await world.session.get(PrWorkType, row.work_type_id),  # type: ignore[arg-type]
            contributors=(world.member.id,),
            start=date(2026, 9, 1),
            name="Báo cáo mới",
        ),
    )
    assert (await generated_items(world))[0].title == before == "Báo cáo cũ"


async def test_22_generation_writes_an_audit_row_naming_the_authorizer(
    world: World,
) -> None:
    """*"Why does this job exist and who asked for it"* is a stored answer.

    The audit row carries the template, the revision, the occurrence key and the
    codes of the items, and its actor is the manager who activated the template -
    never a worker process.
    """
    row = await template(world, start=date(2026, 9, 4))
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))

    events = (
        (
            await world.session.execute(
                select(AuditLog).where(AuditLog.action == "pr.work_recurring.generated")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    payload = events[0].after_data or {}
    assert payload["template_id"] == str(row.id)
    assert payload["template_revision_no"] == 1
    assert payload["occurrence_key"] == "20260904T0900"
    assert len(payload["work_item_codes"]) == 1


async def test_23_a_template_appears_in_no_workload_of_its_own(world: World) -> None:
    """**A template is not work.** It generates work and is measured by nothing.

    No ``PrWorkItem``, no contribution, no allocation exists because a template
    does - only because a firing did.
    """
    row = await template(world, start=date(2026, 9, 4))
    assert (await world.session.scalar(select(func.count()).select_from(PrWorkItem)) or 0) == 0
    assert (
        await world.session.scalar(select(func.count()).select_from(PrWorkContribution)) or 0
    ) == 0
    assert row.status is PrRecurringTemplateStatus.ACTIVE


async def test_24_the_preview_comes_from_the_server(world: World) -> None:
    """The sentence and the dates are computed by the object that will fire them.

    A browser that worked them out would be a second implementation of the
    calendar, and the day the two disagreed the wrong one would be the one the
    manager had read before activating.
    """
    type_row = await work_type(world)
    label, firings = world.services.work_recurring.preview(
        command(
            type_row=type_row,
            contributors=(world.member.id,),
            frequency=PrRecurringFrequency.WEEKLY,
            weekdays=(0, 2),
            run_time=time(9, 0),
            start=date(2026, 9, 1),
        ),
        at=local(2026, 9, 4, 12, 0),
    )
    assert "09:00" in label
    assert "thứ Hai" in label
    assert firings[0] == local(2026, 9, 7, 9, 0)
    assert firings[1] == local(2026, 9, 9, 9, 0)
    assert len(firings) == 5


async def test_25_a_preview_stops_at_the_end_date(world: World) -> None:
    """A routine that ends on Friday does not promise a Saturday."""
    type_row = await work_type(world)
    _, firings = world.services.work_recurring.preview(
        command(
            type_row=type_row,
            contributors=(world.member.id,),
            start=date(2026, 9, 1),
            end=date(2026, 9, 6),
        ),
        at=local(2026, 9, 4, 12, 0),
    )
    assert firings == (local(2026, 9, 5, 9, 0), local(2026, 9, 6, 9, 0))


async def test_26_a_template_cannot_be_activated_onto_somebody_who_has_left(
    world: World,
) -> None:
    """Checked once, in front of the person who can fix it.

    Otherwise the routine fails every night at 4am, and the only record is a
    ``FAILED_RETRYABLE`` row nobody is watching.
    """
    row = await template(world, activate=False)
    world.member.active = False
    await world.session.flush()
    with pytest.raises(PrValidationError) as error:
        await world.services.work_recurring.activate(
            actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
        )
    assert error.value.details["reason"] == "inactive_user"


# ===========================================================================
# 27-31: OVER HTTP
# ===========================================================================


def _body(type_row: PrWorkType, member: uuid.UUID, **over: object) -> dict[str, object]:
    """The whole form a manager fills in, as a request body."""
    return {
        "name": "Báo cáo hằng ngày",
        "work_type_id": str(type_row.id),
        "assignment_mode": "SHARED_WORK",
        "contributor_user_ids": [str(member)],
        "frequency": "DAILY",
        "run_time": "09:00:00",
        "start_date": "2026-09-01",
        **over,
    }


async def test_27_an_employee_is_refused_by_the_route_as_well(world: World) -> None:
    """The service refuses, and so does a direct API call. **The only version that counts.**

    Hiding the tab is a courtesy; this is the rule. An employee who could
    activate a routine could put work into their own workload every morning
    without any manager deciding anything.
    """
    type_row = await work_type(world)
    await world.session.commit()
    world.act_as(world.member)

    assert world.client.get("/api/pr/work/recurring").status_code == 403
    created = world.client.post("/api/pr/work/recurring", json=_body(type_row, world.member.id))
    assert created.status_code == 403, created.text


async def test_28_a_manager_creates_a_draft_and_then_activates_it(world: World) -> None:
    """The two acts, over HTTP, in the order the screen performs them.

    Creating lands at ``Nháp`` and generates nothing; activating is the separate
    act that makes the caller the standing authorization.
    """
    type_row = await work_type(world)
    await world.session.commit()
    world.act_as(world.lead)

    created = world.client.post("/api/pr/work/recurring", json=_body(type_row, world.member.id))
    assert created.status_code == 201, created.text
    drafted = created.json()
    assert drafted["status"] == "DRAFT"
    assert drafted["status_label"] == "Nháp"
    assert drafted["can_activate"] is True
    assert drafted["activated_by_user_id"] is None
    # The sentence, and the dates, from the server.
    assert drafted["schedule_label"] == "09:00 mỗi ngày"

    started = world.client.post(f"/api/pr/work/recurring/{drafted['id']}/activate")
    assert started.status_code == 200, started.text
    running = started.json()
    assert running["status_label"] == "Đang chạy"
    assert running["activated_by_user_id"] == str(world.lead.id)
    assert running["can_pause"] is True
    assert running["can_activate"] is False


async def test_29_the_route_refuses_a_schedule_it_could_not_run(world: World) -> None:
    """A weekly routine with no weekdays is refused at the boundary.

    422 from the request model, so the form gets a field-level answer rather
    than a generic refusal - and the domain refuses it again, so a caller who
    never touched this model meets the same rule.
    """
    type_row = await work_type(world)
    await world.session.commit()
    world.act_as(world.lead)

    response = world.client.post(
        "/api/pr/work/recurring",
        json=_body(type_row, world.member.id, frequency="WEEKLY"),
    )
    assert response.status_code == 422, response.text
    assert "weekdays" in response.text


async def test_30_the_preview_route_writes_nothing(world: World) -> None:
    """``POST`` because the body is the whole form, not because it changes anything."""
    type_row = await work_type(world)
    await world.session.commit()
    world.act_as(world.lead)

    response = world.client.post(
        "/api/pr/work/recurring/preview",
        json=_body(
            type_row, world.member.id, frequency="WEEKLY", weekdays=[0, 2], run_time="09:00:00"
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "thứ Hai" in body["schedule_label"]
    assert len(body["next_occurrences"]) == 5

    listed = world.client.get("/api/pr/work/recurring")
    assert listed.status_code == 200
    assert listed.json()["items"] == []


async def test_31_no_route_generates_work(world: World) -> None:
    """**There is no "run now".**

    Generation happens on the sweep, under the rules that make it safe - the
    activation boundary, the un-backfilled pause window, the closed-period
    refusal, the catch-up bound. An endpoint that produced work on demand would
    be a second implementation of those rules and the one people reached for
    when the first said no.
    """
    from meobot.api.main import create_app

    paths = {
        route.path  # type: ignore[attr-defined]
        for route in create_app().routes
        if "recurring" in getattr(route, "path", "")
    }
    assert not [one for one in paths if one.endswith(("/run", "/generate", "/backfill"))]
    assert paths == {
        "/api/pr/work/recurring",
        "/api/pr/work/recurring/preview",
        "/api/pr/work/recurring/{template_id}",
        "/api/pr/work/recurring/{template_id}/occurrences",
        "/api/pr/work/recurring/{template_id}/activate",
        "/api/pr/work/recurring/{template_id}/pause",
        "/api/pr/work/recurring/{template_id}/resume",
        "/api/pr/work/recurring/{template_id}/end",
    }


async def test_32_the_list_narrows_by_state_and_the_history_reads_back(
    world: World,
) -> None:
    """The two read routes a screen actually uses. ``PR_WORK_MANAGE``.

    The state filter is a **filter**, not a permission: it narrows what the
    caller could already see. And the occurrence history is the one place the
    scheduler's own vocabulary surfaces, as a ``state_label`` the server
    composed - never as a raw token a browser would have to translate.
    """
    row = await template(world, start=date(2026, 9, 1))
    await set_cursor(world, row, local(2026, 9, 1, 0, 0))
    await sweep(world, row, at=local(2026, 9, 1, 12, 0))
    idle = await template(world, activate=False, name="Chưa chạy")
    await world.session.commit()
    world.act_as(world.lead)

    everything = world.client.get("/api/pr/work/recurring")
    assert everything.status_code == 200
    assert {one["id"] for one in everything.json()["items"]} == {str(row.id), str(idle.id)}

    running = world.client.get("/api/pr/work/recurring?status=ACTIVE")
    assert [one["id"] for one in running.json()["items"]] == [str(row.id)]
    drafts = world.client.get("/api/pr/work/recurring?status=DRAFT")
    assert [one["id"] for one in drafts.json()["items"]] == [str(idle.id)]

    history = world.client.get(f"/api/pr/work/recurring/{row.id}/occurrences")
    assert history.status_code == 200
    entries = history.json()["items"]
    assert len(entries) == 1
    assert entries[0]["state"] == "GENERATED"
    assert entries[0]["state_label"] == "Đã tạo việc"
    assert entries[0]["work_item_count"] == 1
    assert entries[0]["template_revision_no"] == 1
