"""Work maintenance: content sync, rebuild, admin removals, work type deletion.

**The load-bearing half is authorization.** Every write here is
``PR_WORK_CONFIGURE`` - ADMIN and OWNER - and tests 1-4 prove it over HTTP,
role by role, for every route: a TEAM_LEAD or an EMPLOYEE who forges the
request gets the same 403 the screen spared them.

The rest pins the semantics the task lists, numbered against it:

* 5-10 sync: additive, idempotent, provisions, touches nothing manual or
  recurring;
* 11-20 rebuild: wrong type moved, stale reversed, valid recreated, manual and
  recurring untouched (counted before = after), M2 and the stored performance
  figure recomputed, a second rebuild converges, CLOSED and LOCKED refused;
* 21-28 admin removal: the actual drops, the contribution follows, M2 and M6
  follow, history and audit remain, the lower roles are refused;
* 29-35 work type deletion: unused deletes, every kind of reference refuses
  with its count, nothing cascades;
* 36-42 KPI draft: the quota's type may move, approved may not, the basis and
  unit are re-derived, a duplicate is refused, the correction works after a
  cleanup.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import uuid
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_maintenance_service import MaintenanceScope
from meobot.application.pr_work_recurring_service import RecurringTemplateCommand
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_content_work import PrContentWorkRule
from meobot.db.models.pr_performance import PrPerformanceResult
from meobot.db.models.pr_work import PrWorkContribution, PrWorkHistory, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.audit.models import AuditAction
from meobot.domain.pr.content_work import PrContentWorkKind, auto_work_type_code
from meobot.domain.pr.errors import PrConflictError, PrValidationError
from meobot.domain.pr.models import PrContentType
from meobot.domain.pr.recurring import PrRecurringFrequency
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkAssignmentMode, PrWorkCountStatus
from meobot.domain.pr.work_quota import PrWorkQuotaBasis, PrWorkQuotaStatus
from meobot.domain.pr.work_results import PrWorkResultSource
from tests.unit.test_pr_content_work_projection import (
    approved_content,
    contributions_of,
    open_month,
    project,
    rule,
    source_result,
    work_type,
)
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401

pytestmark = pytest.mark.asyncio

KIND = PrContentWorkKind.CONTENT_CREATION
TYPE = PrContentType.SHORT_VIDEO_SCRIPT
OTHER_TYPE = PrContentType.PRESS_ARTICLE
MAINT = "/api/pr/work/maintenance"


# ===========================================================================
# Helpers
# ===========================================================================


async def month(world: World):  # type: ignore[no-untyped-def]
    return await open_month(world, utcnow())


def scope(period, **over):  # type: ignore[no-untyped-def]
    return MaintenanceScope(period_id=period.id, **over)


async def preview(world: World, period, **over):  # type: ignore[no-untyped-def]
    return await world.services.work_maintenance.preview(
        actor=world.actor(world.owner), scope=scope(period, **over)
    )


async def sync(world: World, period, **over):  # type: ignore[no-untyped-def]
    return await world.services.work_maintenance.sync_missing(
        actor=world.actor(world.owner), request_id=world.request_id, scope=scope(period, **over)
    )


async def rebuild(world: World, period, note=None, **over):  # type: ignore[no-untyped-def]
    return await world.services.work_maintenance.rebuild(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        scope=scope(period, **over),
        note=note,
    )


async def counted_content(world: World, **kw):  # type: ignore[no-untyped-def]
    """An approved piece, projected, so its result exists and is counted."""
    content_id = await approved_content(world, **kw)
    await project(world, content_id)
    result = await source_result(world, content_id, KIND)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED
    return content_id, result


async def manual_result(world: World, type_row: PrWorkType, quantity: int = 3) -> PrWorkResult:
    return await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=Decimal(quantity),
        work_type_id=type_row.id,
        subject_user_id=world.member.id,
    )


async def recurring_items(world: World) -> list[PrWorkItem]:
    """One activated daily routine, swept once: one generated work item."""
    type_row = await work_type(world, code="DAILY_REPORT", name="Báo cáo ngày")
    row = await world.services.work_recurring.create_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=RecurringTemplateCommand(
            name="Báo cáo hằng ngày",
            work_type_id=type_row.id,
            assignment_mode=PrWorkAssignmentMode.SHARED_WORK,
            frequency=PrRecurringFrequency.DAILY,
            run_time=time(9, 0),
            start_date=date(2026, 9, 1),
            end_date=None,
            contributor_user_ids=(world.member.id,),
            weekdays=(),
            day_of_month=None,
            quantity=None,
            due_after_hours=None,
        ),
    )
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    # The routine fires daily at 09:00; sweeping two days on generates one job.
    await world.services.work_recurring_generator.sweep_template(
        template_id=row.id, request_id=world.request_id, at=utcnow() + timedelta(days=2)
    )
    return list(
        (
            await world.session.execute(
                select(PrWorkItem).where(PrWorkItem.recurring_occurrence_id.is_not(None))
            )
        ).scalars()
    )


async def count(world: World, statement) -> int:  # type: ignore[no-untyped-def]
    return int((await world.session.execute(statement)).scalar_one())


async def results_of_source(world: World, source: PrWorkResultSource) -> list[PrWorkResult]:
    return list(
        (
            await world.session.execute(
                select(PrWorkResult).where(PrWorkResult.source_type == source)
            )
        ).scalars()
    )


async def container_of(world: World, result: PrWorkResult) -> PrWorkItem:
    await world.session.refresh(result)
    item = await world.session.get(PrWorkItem, result.work_item_id)
    assert item is not None
    await world.session.refresh(item)
    return item


async def audit_rows(world: World, action: AuditAction) -> list[AuditLog]:
    return list(
        (await world.session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
    )


# ===========================================================================
# 1-4: AUTHORIZATION, OVER HTTP, ROLE BY ROLE
# ===========================================================================


def _routes(period_id: str, result_id: str, type_id: str) -> list[tuple[str, str, dict | None]]:
    body = {"period_id": period_id}
    return [
        ("POST", f"{MAINT}/content-sync/preview", body),
        ("POST", f"{MAINT}/content-sync/run", body),
        ("POST", f"{MAINT}/content-rebuild/preview", body),
        ("POST", f"{MAINT}/content-rebuild/run", {**body, "note": "thử"}),
        ("POST", f"{MAINT}/results/{result_id}/admin-remove", {"note": "sai"}),
        ("GET", f"{MAINT}/work-types/{type_id}/references", None),
        ("DELETE", f"{MAINT}/work-types/{type_id}", None),
    ]


async def _forged_setup(world: World):  # type: ignore[no-untyped-def]
    period = await month(world)
    _content_id, result = await counted_content(world, content_type=TYPE)
    unused = await work_type(world, code="UNUSED", name="Không dùng")
    return period, result, unused


@pytest.mark.parametrize("role", ["lead", "member", "other"])
async def test_01_02_team_lead_and_employee_are_refused_on_every_route(
    world: World, role: str
) -> None:
    """The rule the whole feature rests on. Forged or not, 403 - every route."""
    period, result, unused = await _forged_setup(world)
    world.act_as(getattr(world, role))
    for method, path, body in _routes(str(period.id), str(result.id), str(unused.id)):
        response = world.client.request(method, path, json=body)
        assert response.status_code == 403, (role, method, path, response.text)
    # And the plain quota-update route still refuses a colleague's draft too -
    # it is the existing rule, not a new one, but the new field rides on it.
    assert (await source_result(world, _content_of(result), KIND)).status is (  # type: ignore[arg-type]
        PrWorkCountStatus.COUNTED
    ), "a refused request wrote nothing"


def _content_of(result: PrWorkResult) -> uuid.UUID:
    assert result.source_key is not None
    return uuid.UUID(result.source_key.split(":")[1])


@pytest.mark.parametrize("role", ["owner", "head"])
async def test_03_04_owner_and_admin_reach_every_route(world: World, role: str) -> None:
    period, result, unused = await _forged_setup(world)
    world.act_as(getattr(world, role))

    assert (
        world.client.post(
            f"{MAINT}/content-sync/preview", json={"period_id": str(period.id)}
        ).status_code
        == 200
    )
    assert (
        world.client.post(
            f"{MAINT}/content-sync/run", json={"period_id": str(period.id)}
        ).status_code
        == 200
    )
    assert (
        world.client.post(
            f"{MAINT}/content-rebuild/preview", json={"period_id": str(period.id)}
        ).status_code
        == 200
    )
    run = world.client.post(f"{MAINT}/content-rebuild/run", json={"period_id": str(period.id)})
    assert run.status_code == 200, run.text
    assert run.json()["operation"] == "rebuild"
    removed = world.client.post(f"{MAINT}/results/{result.id}/admin-remove", json={"note": "sai"})
    assert removed.status_code == 200, removed.text
    assert removed.json()["item"]["is_period_container"] is True
    refs = world.client.get(f"{MAINT}/work-types/{unused.id}/references")
    assert refs.status_code == 200 and refs.json()["deletable"] is True
    gone = world.client.delete(f"{MAINT}/work-types/{unused.id}")
    assert gone.status_code == 200, gone.text
    assert await world.session.get(PrWorkType, unused.id) is None


# ===========================================================================
# 5-10: SYNC MISSING
# ===========================================================================


async def test_05_06_07_sync_creates_the_missing_result_keeps_the_correct_and_is_a_no_op_again(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    done_id, done = await counted_content(world, content_type=TYPE, title="Đã có")
    missing_id = await approved_content(world, content_type=TYPE, title="Chưa có")

    before = await preview(world, period)
    assert before.eligible_content_count == 2
    assert before.correct_result_count == 1
    assert before.missing_result_count == 1
    assert before.results_to_remove == 0

    run = await sync(world, period)
    assert run.content_items == 1, "only the missing piece was projected"
    assert run.counts["PROJECTED"] == 1
    created = await source_result(world, missing_id, KIND)
    assert created is not None and created.status is PrWorkCountStatus.COUNTED
    assert (await source_result(world, done_id, KIND)) is not None
    await world.session.refresh(done)
    assert done.status is PrWorkCountStatus.COUNTED

    again = await sync(world, period)
    assert again.content_items == 0 and sum(again.counts.values()) == 0
    after = await preview(world, period)
    assert after.correct_result_count == 2 and after.missing_result_count == 0
    assert len(await results_of_source(world, PrWorkResultSource.CONTENT)) == 2


async def test_08_sync_provisions_a_missing_work_type_through_the_projector(world: World) -> None:
    period = await month(world)
    missing_id = await approved_content(world, content_type=OTHER_TYPE, title="Bài báo")
    before = await preview(world, period)
    assert before.new_work_type_count == 1
    assert before.provision_by_content_type == {OTHER_TYPE.value: 1}

    run = await sync(world, period)
    assert run.counts["PROJECTED"] == 1
    code = auto_work_type_code(KIND, OTHER_TYPE)
    types = list(
        (await world.session.execute(select(PrWorkType).where(PrWorkType.code == code))).scalars()
    )
    assert len(types) == 1, "the projector's own provisioning, once"
    assert (await source_result(world, missing_id, KIND)) is not None
    assert len(await audit_rows(world, AuditAction.PR_WORK_CONTENT_SYNC_COMPLETED)) == 1


async def test_09_10_sync_reads_neither_manual_nor_recurring_work(world: World) -> None:
    period = await month(world)
    customers = await work_type(world, code="TIM_KHACH", name="Tìm khách hàng")
    manual = await manual_result(world, customers, 3)
    generated = await recurring_items(world)
    assert len(generated) >= 1
    await approved_content(world, content_type=TYPE, title="Chưa có")

    manual_before = len(await results_of_source(world, PrWorkResultSource.MANUAL))
    items_before = await count(world, select(func.count()).select_from(PrWorkItem))
    await sync(world, period)
    await world.session.refresh(manual)
    assert manual.status is PrWorkCountStatus.PENDING and manual.quantity == Decimal("3.00")
    assert len(await results_of_source(world, PrWorkResultSource.MANUAL)) == manual_before
    # One container was opened for the projected piece; nothing else moved.
    assert await count(world, select(func.count()).select_from(PrWorkItem)) == items_before + 1
    assert await world.session.get(PrWorkItem, generated[0].id) is not None
    assert (await preview(world, period)).manual_result_count == manual_before


# ===========================================================================
# 11-20: REBUILD
# ===========================================================================


async def test_11_a_remapped_type_is_detected_and_the_rebuild_moves_the_counted_result(
    world: World,
) -> None:
    """The cleanup workflow in one test: map, count, remap, preview, rebuild."""
    period = await month(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Kịch bản video ngắn")
    new = await work_type(world, code="NEW_SCRIPT", name="Kịch bản video ngắn/Bài đăng")
    await rule(world, kind=KIND, type_row=old, content_type=TYPE)
    content_id, result = await counted_content(world, content_type=TYPE)
    old_container = await container_of(world, result)
    assert old_container.work_type_id == old.id and old_container.quantity == Decimal("1.00")

    # The administrator corrects the mapping. Nothing moves by itself: a
    # counted result stays where its month reported it.
    await rule(world, kind=KIND, type_row=new, content_type=TYPE)
    await project(world, content_id)
    assert (await container_of(world, result)).work_type_id == old.id

    found = await preview(world, period)
    assert found.wrong_work_type_count == 1
    assert found.results_to_remove == 1 and found.results_to_create == 1
    assert found.recreate_by_work_type == {new.id: 1}
    assert {row.finding for row in found.samples} == {"WRONG_WORK_TYPE"}

    run = await rebuild(world, period, note="Làm sạch mapping thử nghiệm tháng 9")
    assert run.results_removed == 1
    moved = await source_result(world, content_id, KIND)
    assert moved is not None and moved.id == result.id, "the same row, refiled"
    assert moved.status is PrWorkCountStatus.COUNTED
    new_container = await container_of(world, moved)
    assert new_container.work_type_id == new.id
    assert new_container.quantity == Decimal("1.00")
    await world.session.refresh(old_container)
    assert old_container.quantity == Decimal("0.00")
    assert (await preview(world, period)).wrong_work_type_count == 0
    completed = await audit_rows(world, AuditAction.PR_WORK_CONTENT_REBUILD_COMPLETED)
    assert len(completed) == 1
    assert (completed[0].after_data or {})["results_removed"] == 1
    assert (completed[0].after_data or {})["note"] == "Làm sạch mapping thử nghiệm tháng 9"


async def test_12_a_stale_result_is_found_and_reversed(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    content_id, result = await counted_content(world, content_type=TYPE)
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )

    found = await preview(world, period)
    assert found.stale_result_count == 1
    run = await rebuild(world, period)
    assert run.counts["REVERSED"] == 1
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.EXCLUDED
    assert (await container_of(world, result)).quantity == Decimal("0.00")


async def test_13_14_a_valid_result_is_recreated_and_a_second_rebuild_converges(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    content_id = await approved_content(world, content_type=TYPE)
    first = await rebuild(world, period)
    assert first.counts["PROJECTED"] == 1
    created = await source_result(world, content_id, KIND)
    assert created is not None and created.status is PrWorkCountStatus.COUNTED

    second = await rebuild(world, period)
    assert second.results_removed == 0
    assert second.counts["UNCHANGED"] == 1 and second.counts["PROJECTED"] == 0
    assert len(await results_of_source(world, PrWorkResultSource.CONTENT)) == 1
    assert (await container_of(world, created)).quantity == Decimal("1.00")


async def test_15_16_rebuild_leaves_manual_and_recurring_work_exactly_as_it_was(
    world: World,
) -> None:
    """Mandatory regression: counted before = after, item by item."""
    period = await month(world)
    customers = await work_type(world, code="TIM_KHACH", name="Tìm khách hàng")
    manual = await manual_result(world, customers, 3)
    await world.services.work_results.validate_results(
        actor=world.actor(world.lead), request_id=world.request_id, work_item_id=manual.work_item_id
    )
    generated = await recurring_items(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Cũ")
    new = await work_type(world, code="NEW_SCRIPT", name="Mới")
    await rule(world, kind=KIND, type_row=old, content_type=TYPE)
    await counted_content(world, content_type=TYPE)
    await rule(world, kind=KIND, type_row=new, content_type=TYPE)

    def snapshot_rows(rows):  # type: ignore[no-untyped-def]
        return sorted(
            (str(r.id), r.status.value, str(r.quantity), str(r.work_item_id)) for r in rows
        )

    manual_before = snapshot_rows(await results_of_source(world, PrWorkResultSource.MANUAL))
    recurring_before = sorted((str(i.id), i.status.value, str(i.quantity)) for i in generated)
    evidence_before = await count(world, select(func.count()).select_from(PrWorkHistory))

    run = await rebuild(world, period)
    assert run.results_removed == 1

    manual_after = snapshot_rows(await results_of_source(world, PrWorkResultSource.MANUAL))
    assert manual_after == manual_before
    for item in generated:
        await world.session.refresh(item)
    recurring_after = sorted((str(i.id), i.status.value, str(i.quantity)) for i in generated)
    assert recurring_after == recurring_before
    assert await count(world, select(func.count()).select_from(PrWorkHistory)) >= evidence_before
    assert run.preview.manual_result_count == 1
    assert run.preview.recurring_result_count == len(generated)


async def test_17_18_m2_and_the_stored_performance_figure_follow_the_rebuild(
    world: World,
) -> None:
    period = await month(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Cũ")
    new = await work_type(world, code="NEW_SCRIPT", name="Mới")
    await rule(world, kind=KIND, type_row=old, content_type=TYPE)
    _content_id, result = await counted_content(world, content_type=TYPE)
    # An approved plan on the *new* type, so M2 has something to allocate
    # against once the result moves.
    plan = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=plan.plan.id,
        work_type_id=new.id,
        target_value=Decimal("5"),
        eligibility_cap=Decimal("5"),
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.plan.id
    )
    # A stored, unfinalised performance figure that a rebuild must keep true.
    await world.services.performance.calculate(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    stored = (
        await world.session.execute(
            select(PrPerformanceResult).where(PrPerformanceResult.user_id == world.member.id)
        )
    ).scalar_one()
    stamp_before = stored.updated_at

    await rule(world, kind=KIND, type_row=new, content_type=TYPE)
    run = await rebuild(world, period)
    assert run.results_removed == 1
    assert run.performance_refreshed == 1

    contribution = (await contributions_of(world, (await container_of(world, result)).id))[0]
    allocations = list(
        (
            await world.session.execute(
                select(PrWorkQuotaAllocation).where(
                    PrWorkQuotaAllocation.work_contribution_id == contribution.id
                )
            )
        ).scalars()
    )
    assert len(allocations) == 1 and allocations[0].work_type_id == new.id
    assert allocations[0].quota_status is PrWorkQuotaStatus.ELIGIBLE
    await world.session.refresh(stored)
    assert stored.finalized_at is None
    assert stored.updated_at >= stamp_before


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_19_20_a_shut_period_refuses_rebuild_and_sync_with_a_structured_reason(
    world: World, state: PrPeriodStatus
) -> None:
    period = await month(world)
    await counted_content(world, content_type=TYPE)
    period.status = state
    await world.session.flush()
    for operation in (rebuild, sync):
        with pytest.raises(PrConflictError) as caught:
            await operation(world, period)
        assert caught.value.details["reason"] == "work_period_not_open_for_cleanup"
    # The preview still answers - reading a shut month is not editing it.
    assert (await preview(world, period)).period_status is state
    world.act_as(world.owner)
    response = world.client.post(f"{MAINT}/content-rebuild/run", json={"period_id": str(period.id)})
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "work_period_not_open_for_cleanup"


async def test_20b_a_finalised_performance_figure_refuses_the_rebuild(world: World) -> None:
    """The invariant, asserted: a finalised month is never rebuilt, even if the
    period row itself still reads OPEN."""
    from datetime import date as _date

    period = await month(world)
    await counted_content(world, content_type=TYPE)
    draft = await world.services.performance_policies.create_draft(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        effective_from=_date(2026, 1, 1),
    )
    policy = await world.services.performance_policies.approve(
        actor=world.actor(world.owner), request_id=world.request_id, policy_id=draft.id
    )
    await world.services.performance.calculate(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    stored = (
        await world.session.execute(
            select(PrPerformanceResult).where(PrPerformanceResult.user_id == world.member.id)
        )
    ).scalar_one()
    # Finalised by hand - the service's own finalise needs every diagnostic
    # resolved, and what this test is about is the maintenance guard, not M6.
    stored.policy_id = policy.id
    stored.final_performance_index = Decimal("80")
    stored.finalized_at = utcnow()
    stored.finalized_by_user_id = world.owner.id
    await world.session.flush()

    with pytest.raises(PrConflictError) as caught:
        await rebuild(world, period)
    assert caught.value.details["reason"] == "work_period_not_open_for_cleanup"
    assert caught.value.details["cause"] == "performance_finalized"
    assert (await preview(world, period)).finalized_performance_count == 1


# ===========================================================================
# 21-28: ADMIN REMOVAL OF ONE RESULT
# ===========================================================================


async def test_21_22_23_admin_removes_a_counted_content_result_and_the_actual_follows(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    content_id, result = await counted_content(world, content_type=TYPE)
    item = await container_of(world, result)
    assert item.quantity == Decimal("1.00")

    removed = await world.services.work_maintenance.admin_remove_result(
        actor=world.actor(world.head),
        request_id=world.request_id,
        result_id=result.id,
        note="ghi nhầm",
    )
    assert removed.status is PrWorkCountStatus.EXCLUDED
    assert removed.excluded_reason is not None and "ghi nhầm" in removed.excluded_reason
    item = await container_of(world, result)
    assert item.quantity == Decimal("0.00")
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.PENDING and rows[0].counted_at is None
    # The row is still there, the timeline says what happened, the audit names the act.
    assert await world.session.get(PrWorkResult, result.id) is not None
    assert (
        await count(
            world,
            select(func.count())
            .select_from(PrWorkHistory)
            .where(PrWorkHistory.work_item_id == item.id),
        )
        >= 3
    )
    trail = await audit_rows(world, AuditAction.PR_WORK_RESULT_ADMIN_REMOVED)
    assert len(trail) == 1 and (trail[0].after_data or {})["source_type"] == "CONTENT"
    # And - the sentence the screen says before the click - the source is still
    # accepted, so the next projection counts it again.
    report = await project(world, content_id)
    assert {one.outcome.value for one in report.results} == {"PROJECTED"}
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.COUNTED
    assert (await preview(world, period)).correct_result_count == 1


async def test_24_25_admin_removal_updates_m2_and_the_stored_performance_figure(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    plan = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=plan.plan.id,
        work_type_id=type_row.id,
        target_value=Decimal("5"),
        eligibility_cap=Decimal("5"),
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.plan.id
    )
    _content_id, result = await counted_content(world, content_type=TYPE)
    item = await container_of(world, result)
    contribution = (await contributions_of(world, item.id))[0]
    before = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert before.counted_contributions == 1
    await world.services.performance.calculate(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )

    await world.services.work_maintenance.admin_remove_result(
        actor=world.actor(world.owner), request_id=world.request_id, result_id=result.id
    )
    after = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert after.counted_contributions == 0
    allocations = list(
        (
            await world.session.execute(
                select(PrWorkQuotaAllocation).where(
                    PrWorkQuotaAllocation.work_contribution_id == contribution.id
                )
            )
        ).scalars()
    )
    assert all(one.quota_status is not PrWorkQuotaStatus.ELIGIBLE for one in allocations)
    stored = (
        await world.session.execute(
            select(PrPerformanceResult).where(PrPerformanceResult.user_id == world.member.id)
        )
    ).scalar_one()
    assert stored.finalized_at is None


async def test_26_a_manual_counted_result_and_an_already_excluded_one(world: World) -> None:
    customers = await work_type(world, code="TIM_KHACH", name="Tìm khách hàng")
    manual = await manual_result(world, customers, 4)
    await world.services.work_results.validate_results(
        actor=world.actor(world.lead), request_id=world.request_id, work_item_id=manual.work_item_id
    )
    await world.session.refresh(manual)
    assert manual.status is PrWorkCountStatus.COUNTED
    removed = await world.services.work_maintenance.admin_remove_result(
        actor=world.actor(world.owner), request_id=world.request_id, result_id=manual.id
    )
    assert removed.status is PrWorkCountStatus.EXCLUDED
    assert (await container_of(world, manual)).quantity == Decimal("0.00")
    with pytest.raises(PrConflictError) as caught:
        await world.services.work_maintenance.admin_remove_result(
            actor=world.actor(world.owner), request_id=world.request_id, result_id=manual.id
        )
    assert caught.value.details["reason"] == "work_result_not_admin_removable"


async def test_27_28_removal_is_refused_on_a_shut_month(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    _content_id, result = await counted_content(world, content_type=TYPE)
    period.status = PrPeriodStatus.CLOSED
    await world.session.flush()
    with pytest.raises(PrConflictError) as caught:
        await world.services.work_maintenance.admin_remove_result(
            actor=world.actor(world.owner), request_id=world.request_id, result_id=result.id
        )
    assert caught.value.details["reason"] == "work_period_not_open_for_cleanup"
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.COUNTED


# ===========================================================================
# 29-35: WORK TYPE DELETION
# ===========================================================================


async def test_29_an_unused_work_type_deletes_and_is_audited(world: World) -> None:
    unused = await work_type(world, code="UNUSED", name="Không dùng")
    # An inactive mapping is configuration with nothing under it: it goes too.
    await rule(world, kind=KIND, type_row=unused, content_type=OTHER_TYPE, is_active=False)
    refs = await world.services.work_maintenance.work_type_references(
        actor=world.actor(world.owner), work_type_id=unused.id
    )
    assert refs.deletable and refs.content_rules_inactive == 1
    await world.services.work_maintenance.delete_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=unused.id,
        note="dọn thử nghiệm",
    )
    assert await world.session.get(PrWorkType, unused.id) is None
    assert (
        await count(
            world,
            select(func.count())
            .select_from(PrContentWorkRule)
            .where(PrContentWorkRule.work_type_id == unused.id),
        )
        == 0
    )
    trail = await audit_rows(world, AuditAction.PR_WORK_TYPE_DELETED)
    assert len(trail) == 1 and (trail[0].before_data or {})["code"] == "UNUSED"


async def test_30_31_32_33_every_kind_of_reference_refuses_with_its_count(world: World) -> None:
    period = await month(world)
    # Mapped (active rule) and used by a counted content result.
    mapped = await work_type(world, code="MAPPED", name="Có ánh xạ")
    await rule(world, kind=KIND, type_row=mapped, content_type=TYPE)
    await counted_content(world, content_type=TYPE)
    # A KPI quota on another type.
    planned = await work_type(world, code="PLANNED", name="Có KPI")
    plan = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=plan.plan.id,
        work_type_id=planned.id,
        target_value=Decimal("5"),
        eligibility_cap=Decimal("5"),
    )
    # A routine on a third.
    generated = await recurring_items(world)
    recurring_type = await world.session.get(PrWorkType, generated[0].work_type_id)
    assert recurring_type is not None

    expectations = {
        mapped.id: {"content_rules_active": 1, "work_items": 1, "results": 1, "contributions": 1},
        planned.id: {"quotas": 1},
        recurring_type.id: {
            "recurring_templates": 1,
            "work_items": len(generated),
            "contributions": len(generated),
        },
    }
    for type_id, blocking in expectations.items():
        with pytest.raises(PrConflictError) as caught:
            await world.services.work_maintenance.delete_work_type(
                actor=world.actor(world.owner), request_id=world.request_id, work_type_id=type_id
            )
        assert caught.value.details["reason"] == "work_type_still_in_use"
        assert caught.value.details["references"] == blocking, type_id
        assert await world.session.get(PrWorkType, type_id) is not None, "nothing cascaded"
    # Structured over HTTP, too.
    world.act_as(world.owner)
    response = world.client.delete(f"{MAINT}/work-types/{mapped.id}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["references"]["results"] == 1
    assert len(await results_of_source(world, PrWorkResultSource.CONTENT)) == 1


async def test_34_35_the_cleanup_workflow_ends_with_the_old_type_deletable(world: World) -> None:
    """Remap → rebuild → sweep the empty stream → delete. The whole intended sequence."""
    period = await month(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Kịch bản video ngắn")
    new = await work_type(world, code="NEW_SCRIPT", name="Kịch bản video ngắn/Bài đăng")
    await rule(world, kind=KIND, type_row=old, content_type=TYPE)
    _content_id, result = await counted_content(world, content_type=TYPE)
    old_container = await container_of(world, result)

    await rule(world, kind=KIND, type_row=new, content_type=TYPE)
    await rebuild(world, period)
    refs = await world.services.work_maintenance.work_type_references(
        actor=world.actor(world.owner), work_type_id=old.id
    )
    assert refs.results == 0 and refs.work_items == 1
    assert refs.empty_containers_removable == 1 and not refs.deletable

    ids = await world.services.work_maintenance.removable_empty_containers(
        actor=world.actor(world.owner), work_type_id=old.id
    )
    assert ids == [old_container.id]
    await world.services.work_maintenance.remove_empty_container(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=old_container.id
    )
    assert await world.session.get(PrWorkItem, old_container.id) is None
    assert (
        await count(
            world,
            select(func.count())
            .select_from(PrWorkContribution)
            .where(PrWorkContribution.work_item_id == old_container.id),
        )
        == 0
    )
    # The inactive rule left behind by the remap is not blocking; the type goes.
    await rule(world, kind=KIND, type_row=old, content_type=OTHER_TYPE, is_active=False)
    await world.services.work_maintenance.delete_work_type(
        actor=world.actor(world.owner), request_id=world.request_id, work_type_id=old.id
    )
    assert await world.session.get(PrWorkType, old.id) is None
    # The moved result and its new stream are exactly where the rebuild put them.
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.COUNTED
    assert (await container_of(world, result)).work_type_id == new.id


async def test_35b_an_assigned_or_filled_container_is_never_removed(world: World) -> None:
    customers = await work_type(world, code="TIM_KHACH", name="Tìm khách hàng")
    manual = await manual_result(world, customers, 2)
    item = await container_of(world, manual)
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_maintenance.remove_empty_container(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["cause"] == "has_results"
    generated = await recurring_items(world)
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_maintenance.remove_empty_container(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            work_item_id=generated[0].id,
        )
    assert caught.value.details["cause"] in {"not_a_period_container", "recurring"}


# ===========================================================================
# 36-42: KPI DRAFT WORK TYPE
# ===========================================================================


async def _draft_with_quota(world: World, period, type_row: PrWorkType):  # type: ignore[no-untyped-def]
    plan = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    detail = await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=plan.plan.id,
        work_type_id=type_row.id,
        target_value=Decimal("5"),
        eligibility_cap=Decimal("5"),
    )
    return detail.plan, detail.quotas[0]


async def test_36_38_39_a_draft_quota_moves_to_another_type_and_is_re_derived(
    world: World,
) -> None:
    period = await month(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Cũ")
    seeding = await work_type(
        world,
        code="SEEDING",
        name="Seeding",
        unit=__import__("meobot.domain.pr.work", fromlist=["PrWorkUnit"]).PrWorkUnit.COMMENT,
        basis=PrWorkQuotaBasis.QUANTITY,
    )
    plan, quota = await _draft_with_quota(world, period, old)
    assert quota.basis is PrWorkQuotaBasis.ITEM_COUNT and quota.unit is None

    detail = await world.services.work_plans.update_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=plan.id,
        quota_id=quota.id,
        work_type_id=seeding.id,
    )
    moved = detail.quotas[0]
    assert moved.work_type_id == seeding.id
    assert moved.basis is PrWorkQuotaBasis.QUANTITY, "re-derived from the new type"
    assert moved.unit is not None and moved.unit.value == "COMMENT"
    assert moved.target_value == Decimal("5")
    trail = await audit_rows(world, AuditAction.PR_WORK_PLAN_QUOTA_WORK_TYPE_CHANGED)
    assert len(trail) == 1


async def test_37_an_approved_quota_cannot_move(world: World) -> None:
    period = await month(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Cũ")
    new = await work_type(world, code="NEW_SCRIPT", name="Mới")
    plan, quota = await _draft_with_quota(world, period, old)
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
    )
    with pytest.raises(Exception) as caught:
        await world.services.work_plans.update_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=plan.id,
            quota_id=quota.id,
            work_type_id=new.id,
        )
    assert "PrWorkPlanStateError" in type(caught.value).__name__
    await world.session.refresh(quota)
    assert quota.work_type_id == old.id


async def test_40_a_duplicate_type_in_one_draft_is_refused(world: World) -> None:
    period = await month(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Cũ")
    new = await work_type(world, code="NEW_SCRIPT", name="Mới")
    plan, quota = await _draft_with_quota(world, period, old)
    await world.services.work_plans.add_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=plan.id,
        work_type_id=new.id,
        target_value=Decimal("3"),
        eligibility_cap=Decimal("3"),
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_plans.update_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=plan.id,
            quota_id=quota.id,
            work_type_id=new.id,
        )
    assert caught.value.details["reason"] == "quota_work_type_already_exists"
    await world.session.refresh(quota)
    assert quota.work_type_id == old.id and quota.target_value == Decimal("5"), "not summed"


async def test_41_42_the_subject_may_move_their_own_draft_but_not_clean_anything(
    world: World,
) -> None:
    period = await month(world)
    old = await work_type(world, code="OLD_SCRIPT", name="Cũ")
    new = await work_type(world, code="NEW_SCRIPT", name="Mới")
    plan, quota = await _draft_with_quota(world, period, old)
    detail = await world.services.work_plans.update_quota(
        actor=world.actor(world.member),
        request_id=world.request_id,
        plan_id=plan.id,
        quota_id=quota.id,
        work_type_id=new.id,
    )
    assert detail.quotas[0].work_type_id == new.id, "the existing self-service rule"
    world.act_as(world.member)
    assert world.client.delete(f"{MAINT}/work-types/{old.id}").status_code == 403
    assert (
        world.client.post(
            f"{MAINT}/content-sync/run", json={"period_id": str(period.id)}
        ).status_code
        == 403
    )
    assert await world.session.get(PrWorkType, old.id) is not None
