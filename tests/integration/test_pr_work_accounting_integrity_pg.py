"""Accounting integrity under real concurrency. PostgreSQL 17.

The offline suite (``tests/unit/test_pr_work_accounting_integrity.py``) proves
the rules; this one proves them across committed transactions racing on real
row locks. Scenarios, numbered against the task's concurrency list:

31. a human validation racing a projector replay never loses the count;
32. a human validation racing a real source withdrawal never keeps it;
33. a result reported while somebody tries to cancel its container lands in a
    live container - the cancel is always refused;
34. an M2 hand-off that failed leaves one Actual on the card, the KPI screen
    and the performance breakdown, with a reconcile racing the reads;
35. a routine's accounting-mode change racing its own generation can never
    leave both per-firing jobs and an accumulating stream for one month;
36. a finalised performance figure refuses every accounting mutation racing it.

Run against a PostgreSQL you are willing to have scratch databases created on::

    export MEOBOT_TEST_DATABASE_URL=postgresql+asyncpg://meobot:PASS@localhost:5432/postgres
    uv run pytest tests/integration/test_pr_work_accounting_integrity_pg.py -m integration
"""

from __future__ import annotations

# ruff: noqa: F811 - `db` and `people` are fixtures imported from the atomicity suite
import asyncio
import dataclasses
import uuid
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_services import build_pr_services
from meobot.application.pr_work_quota_service import PrWorkQuotaEligibilityService
from meobot.application.pr_work_recurring_service import RecurringTemplateCommand
from meobot.core.time import utcnow
from meobot.db.models.pr_performance import PrPerformanceResult
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.pr_work_recurring import PrWorkRecurringOccurrence, PrWorkRecurringTemplate
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.session import Database
from meobot.domain.pr.performance import PrPerformanceCalculationStatus
from meobot.domain.pr.recurring import PrRecurringFrequency
from meobot.domain.pr.work import PrWorkAssignmentMode, PrWorkCountStatus, PrWorkStatus
from meobot.domain.pr.work_results import PrWorkCountOrigin, PrWorkExclusionKind
from tests.integration.test_dispatch_migrations import TEST_DATABASE_URL
from tests.integration.test_pr_content_work_atomicity import (  # noqa: F401 - fixtures
    People,
    approve_plan,
    db,
    open_month,
    people,
    settings,
)
from tests.integration.test_pr_content_work_resync_pg import _all_ok, _sync, _worker
from tests.integration.test_pr_work_result_exclusion_pg import (
    _describe,
    _pending_content,
    _pending_manual,
    _project,
    _row,
    _validate,
    _withdraw,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set MEOBOT_TEST_DATABASE_URL to run integration tests",
    ),
]


# ---------------------------------------------------------------------------
# Helpers - each is one committed transaction, returning 'ok' or the error class
# ---------------------------------------------------------------------------


async def _cancel(db: Database, people: People, item_id: uuid.UUID) -> str:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.work.cancel(
                actor=people.owner_actor, request_id=uuid.uuid4(), work_item_id=item_id
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _report(
    db: Database, people: People, *, subject: uuid.UUID, quantity: Decimal
) -> str | PrWorkResult:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            result = await services.work_results.report_result(
                actor=people.actor(subject),
                request_id=uuid.uuid4(),
                quantity=quantity,
                work_type_id=people.work_type,
            )
            await session.commit()
            await session.refresh(result)
            session.expunge(result)
            return result
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _reconcile(db: Database, people: People, period_id: uuid.UUID) -> str:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.work_eligibility.reconcile_period(
                actor=people.owner_actor, request_id=uuid.uuid4(), period_id=period_id
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _kpi_actual(
    db: Database, people: People, *, user_id: uuid.UUID, period_id: uuid.UUID
) -> Decimal:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        summary = await services.work_eligibility.summary(
            actor=people.owner_actor, user_id=user_id, period_id=period_id
        )
        rows = [row for row in summary.types if row.work_type_id == people.work_type]
        assert len(rows) == 1, [row.work_type_code for row in summary.types]
        return rows[0].counted_amount


async def _breakdown_actual(
    db: Database, people: People, *, user_id: uuid.UUID, period_id: uuid.UUID
) -> Decimal:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        snapshot = await services.performance.snapshot(
            actor=people.owner_actor, user_id=user_id, period_id=period_id
        )
        rows = [row for row in snapshot.breakdown if row.work_type_id == people.work_type]
        assert len(rows) == 1, [row.work_type_code for row in snapshot.breakdown]
        return rows[0].counted_amount


async def _card_actual(db: Database, item_id: uuid.UUID) -> Decimal:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        item = await session.get(PrWorkItem, item_id)
        assert item is not None
        summary = await services.work_results.summary(item)
        assert summary is not None
        assert item.quantity == summary.counted_quantity, "the mirror and the sum agree"
        return summary.counted_quantity


async def _origin(db: Database, result_id: uuid.UUID) -> PrWorkCountOrigin | None:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        row = await session.get(PrWorkResult, result_id)
        assert row is not None
        return await services.work_results.count_origin(row)


def _counted(row: PrWorkResult) -> None:
    assert row.status is PrWorkCountStatus.COUNTED, row.status
    assert row.counted_at is not None and row.exclusion_kind is None


def _source_reversed(row: PrWorkResult) -> None:
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED
    assert row.counted_at is None and row.counted_by_user_id is None


# ---------------------------------------------------------------------------
# 31: human validation vs projector replay
# ---------------------------------------------------------------------------


async def test_31_a_human_validation_racing_a_replay_is_never_undone(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _pending_content(db, people, "Xác nhận đua projector")
    _all_ok(
        await asyncio.gather(
            _validate(db, people, await _row(db, result_id), by="head"),
            _project(db, people, content_id),
        )
    )
    row = await _row(db, result_id)
    _counted(row)
    assert row.counted_by_user_id == people.head
    assert await _origin(db, result_id) is PrWorkCountOrigin.WORK_VALIDATOR
    # Every later pass - the button, the worker, a manual sync - keeps it.
    for _ in range(2):
        assert await _project(db, people, content_id) == "ok"
        _counted(await _row(db, result_id))
    assert await _worker(db, people, content_id) == "ok"
    assert await _sync(db, people, content_id) == "ok"
    row = await _row(db, result_id)
    _counted(row)
    assert row.counted_by_user_id == people.head


# ---------------------------------------------------------------------------
# 32: human validation vs a real source withdrawal
# ---------------------------------------------------------------------------


async def test_32_a_human_validation_racing_a_withdrawal_never_keeps_the_count(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    content_id, result_id = await _pending_content(db, people, "Xác nhận đua rút duyệt")
    outcomes = await asyncio.gather(
        _validate(db, people, await _row(db, result_id), by="head"),
        _withdraw(db, people, content_id),
    )
    assert outcomes[1] == "ok", outcomes
    assert outcomes[0] == "ok" or outcomes[0].startswith("PrConflictError"), outcomes
    # Source truth wins whichever landed first: the next projection takes the
    # row out, and it stays out.
    assert await _sync(db, people, content_id) == "ok"
    _source_reversed(await _row(db, result_id))
    assert await _worker(db, people, content_id) == "ok"
    _source_reversed(await _row(db, result_id))


# ---------------------------------------------------------------------------
# 33: result creation vs an attempted container cancel
# ---------------------------------------------------------------------------


async def test_33_a_report_racing_a_cancel_lands_in_a_live_container(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    first = await _pending_manual(db, people)
    item_id = first.work_item_id
    outcomes = await asyncio.gather(
        _report(db, people, subject=people.member, quantity=Decimal("2")),
        _cancel(db, people, item_id),
        _cancel(db, people, item_id),
    )
    reported, cancels = outcomes[0], outcomes[1:]
    assert isinstance(reported, PrWorkResult), reported
    assert reported.work_item_id == item_id, "the same stream, never a second one"
    assert all(one.startswith("PrValidationError") for one in cancels), cancels
    async with db.session() as session:
        item = await session.get(PrWorkItem, item_id)
        assert item is not None
        assert item.status is not PrWorkStatus.CANCELLED
        contributions = await session.scalar(
            select(func.count())
            .select_from(PrWorkItem)
            .where(PrWorkItem.id == item_id, PrWorkItem.status == PrWorkStatus.CANCELLED)
        )
        assert contributions == 0


# ---------------------------------------------------------------------------
# 34: an M2 hand-off failure vs the KPI and performance reads
# ---------------------------------------------------------------------------


async def test_34_a_failed_m2_handoff_leaves_one_actual_on_every_reader(
    db: Database, people: People, monkeypatch: pytest.MonkeyPatch
) -> None:
    period_id = await open_month(db, people, utcnow())
    await approve_plan(
        db,
        people,
        period_id=period_id,
        user_id=people.member,
        target=Decimal("5"),
        cap=Decimal("5"),
    )
    pending = await _pending_manual(db, people)

    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("M2 is down")

    monkeypatch.setattr(PrWorkQuotaEligibilityService, "on_contributions_counted", explode)
    try:
        assert await _validate(db, people, pending, by="head") == "ok"
    finally:
        monkeypatch.undo()

    async with db.session() as session:
        row = await session.get(PrWorkResult, pending.id)
        assert row is not None
        _counted(row)
        item = await session.get(PrWorkItem, pending.work_item_id)
        assert item is not None
        expected = Decimal(item.quantity or 0)
        # The plan's approval materialised an allocation for this stream from
        # its quantity *then*; the failed hand-off left that copy stale. The
        # readers must not prefer it.
        stale = list(
            (
                await session.execute(
                    select(PrWorkQuotaAllocation.basis_amount)
                    .join(
                        PrWorkContribution,
                        PrWorkContribution.id == PrWorkQuotaAllocation.work_contribution_id,
                    )
                    .where(PrWorkContribution.work_item_id == pending.work_item_id)
                )
            ).scalars()
        )
        assert all(amount != expected for amount in stale), (stale, expected)
    assert expected >= Decimal("3.00")

    # Before any reconcile: three readers, one number.
    assert await _card_actual(db, pending.work_item_id) == expected
    assert await _kpi_actual(db, people, user_id=people.member, period_id=period_id) == expected
    assert await _breakdown_actual(db, people, user_id=people.member, period_id=period_id) == (
        expected
    )
    # A reconcile racing the reads changes the classification and nothing else.
    outcomes = await asyncio.gather(
        _reconcile(db, people, period_id),
        _kpi_actual(db, people, user_id=people.member, period_id=period_id),
        _breakdown_actual(db, people, user_id=people.member, period_id=period_id),
    )
    assert outcomes[0] == "ok", outcomes
    assert outcomes[1] == expected and outcomes[2] == expected, outcomes
    assert await _card_actual(db, pending.work_item_id) == expected
    assert await _kpi_actual(db, people, user_id=people.member, period_id=period_id) == expected
    assert await _breakdown_actual(db, people, user_id=people.member, period_id=period_id) == (
        expected
    )


# ---------------------------------------------------------------------------
# 35: a recurring mode change vs the routine's own generation
# ---------------------------------------------------------------------------


async def _template(db: Database, people: People) -> uuid.UUID:
    """An active daily per-firing routine for the member, due today."""
    today = utcnow().astimezone(settings().timezone).date()
    async with db.session() as session:
        services = build_pr_services(session, settings())
        row = await services.work_recurring.create_template(
            actor=people.lead_actor,
            request_id=uuid.uuid4(),
            command=RecurringTemplateCommand(
                name=f"Báo cáo ngày {uuid.uuid4().hex[:6]}",
                work_type_id=people.work_type,
                assignment_mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
                frequency=PrRecurringFrequency.DAILY,
                run_time=time(0, 0),
                start_date=today - timedelta(days=3),
                contributor_user_ids=(people.member,),
            ),
        )
        await services.work_recurring.activate(
            actor=people.lead_actor, request_id=uuid.uuid4(), template_id=row.id
        )
        # Rewind the cursor so today's firing is due at the next sweep.
        row.last_evaluated_occurrence_at = utcnow() - timedelta(days=1)
        template_id = row.id
        await session.commit()
        return template_id


async def _sweep(db: Database, template_id: uuid.UUID) -> str:
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            await services.work_recurring_generator.sweep_template(
                template_id=template_id, request_id=uuid.uuid4(), at=utcnow()
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def _switch_to_accumulate(db: Database, people: People, template_id: uuid.UUID) -> str:
    today = utcnow().astimezone(settings().timezone).date()
    async with db.session() as session:
        services = build_pr_services(session, settings())
        row = await session.get(PrWorkRecurringTemplate, template_id)
        assert row is not None
        command = RecurringTemplateCommand(
            name=row.name,
            work_type_id=row.work_type_id,
            assignment_mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
            frequency=PrRecurringFrequency.DAILY,
            run_time=time(0, 0),
            start_date=today - timedelta(days=3),
            contributor_user_ids=(people.member,),
        )
        try:
            await services.work_recurring.update_template(
                actor=people.lead_actor,
                request_id=uuid.uuid4(),
                template_id=template_id,
                command=dataclasses.replace(command, accumulate_by_period=True),
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def test_35_a_mode_change_racing_generation_never_double_counts(
    db: Database, people: People
) -> None:
    await open_month(db, people, utcnow())
    template_id = await _template(db, people)
    outcomes = await asyncio.gather(
        _sweep(db, template_id), _switch_to_accumulate(db, people, template_id)
    )
    assert outcomes[0] == "ok", outcomes
    assert outcomes[1] == "ok" or outcomes[1].startswith("PrConflictError"), outcomes

    async with db.session() as session:
        row = await session.get(PrWorkRecurringTemplate, template_id)
        assert row is not None
        occurrence_ids = list(
            (
                await session.execute(
                    select(PrWorkRecurringOccurrence.id).where(
                        PrWorkRecurringOccurrence.template_id == template_id
                    )
                )
            ).scalars()
        )
        one_off_jobs = await session.scalar(
            select(func.count())
            .select_from(PrWorkItem)
            .where(
                PrWorkItem.recurring_occurrence_id.in_(occurrence_ids),
                PrWorkItem.reporting_period_id.is_(None),
            )
        )
        streams = await session.scalar(
            select(func.count())
            .select_from(PrWorkItem)
            .where(
                PrWorkItem.recurring_occurrence_id.in_(occurrence_ids),
                PrWorkItem.reporting_period_id.is_not(None),
            )
        )
    # One accounting mode per month: never a per-firing job **and** an
    # accumulating stream from the same routine.
    assert not (one_off_jobs and streams), (one_off_jobs, streams)
    if outcomes[1] == "ok":
        assert row.accumulate_by_period is True and not one_off_jobs
    else:
        assert row.accumulate_by_period is False and one_off_jobs == 1


# ---------------------------------------------------------------------------
# 36: a finalised performance figure vs validation and reporting
# ---------------------------------------------------------------------------


async def _finalize(
    db: Database, people: People, *, user_id: uuid.UUID, period_id: uuid.UUID
) -> str:
    """The stored, agreed figure - written directly, with the provenance the
    table demands (a policy and a final index). The full finalisation flow is
    the performance suite's; the guard reads only ``finalized_at``."""
    async with db.session() as session:
        services = build_pr_services(session, settings())
        try:
            draft = await services.performance_policies.create_draft(
                actor=people.owner_actor, request_id=uuid.uuid4(), effective_from=date(2026, 1, 1)
            )
            policy = await services.performance_policies.approve(
                actor=people.owner_actor, request_id=uuid.uuid4(), policy_id=draft.id
            )
            session.add(
                PrPerformanceResult(
                    user_id=user_id,
                    reporting_period_id=period_id,
                    policy_id=policy.id,
                    eligible_standard_minutes=Decimal("0"),
                    final_performance_index=Decimal("100"),
                    calculation_status=PrPerformanceCalculationStatus.FINALIZED,
                    calculated_at=utcnow(),
                    finalized_at=utcnow(),
                    finalized_by_user_id=people.owner,
                )
            )
            await session.commit()
            return "ok"
        except Exception as exc:
            await session.rollback()
            return _describe(exc)


async def test_36_a_finalised_month_refuses_mutations_racing_it(
    db: Database, people: People
) -> None:
    """The subject is ``other``, whose month no earlier scenario touches, so
    finalising it blocks nothing above."""
    period_id = await open_month(db, people, utcnow())
    reported = await _report(db, people, subject=people.other, quantity=Decimal("4"))
    assert isinstance(reported, PrWorkResult), reported
    pending = reported

    outcomes = await asyncio.gather(
        _finalize(db, people, user_id=people.other, period_id=period_id),
        _validate(db, people, pending, by="head"),
    )
    assert outcomes[0] == "ok", outcomes
    assert outcomes[1] == "ok" or outcomes[1].startswith("PrConflictError"), outcomes
    row = await _row(db, pending.id)
    if outcomes[1] == "ok":
        _counted(row)
    else:
        assert row.status is PrWorkCountStatus.PENDING and row.counted_at is None

    # From here on nothing moves under the agreed figure.
    later = await asyncio.gather(
        _validate(db, people, row, by="head"),
        _report(db, people, subject=people.other, quantity=Decimal("1")),
    )
    assert all(isinstance(one, str) and one.startswith("PrConflictError") for one in later), later
    again = await _row(db, pending.id)
    assert again.status is row.status and again.counted_at == row.counted_at
