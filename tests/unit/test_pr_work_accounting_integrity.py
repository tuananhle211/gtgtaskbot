"""Pre-deploy accounting integrity: the invariants this patch locks.

The business model, stated once:

* a **Work Result / contribution** determines the Actual;
* a **KPI** is a target and nothing else;
* an **M2 allocation** classifies counted Actual as eligible / over quota / no
  quota, and never owns it;
* **M6** prices counted work.

Numbered against the task's test matrix:

* 1-8 a human Work validation survives projector replay while the source
  still qualifies (``CONTENT_CREATION`` self-approved, ``PRODUCTION`` before
  acceptance);
* 9-18 a real source withdrawal still reverses, whoever counted;
* legacy one-off items obey the same rule, and a source redo cannot revive a
  person's exclusion;
* 19-24 a period container cannot be cancelled, and every reader agrees on
  its Actual;
* the Actual never depends on an M2 allocation having materialised - M2
  failure, a lowered cap, a removed quota;
* 25-30 a routine's accounting mode is fixed for a month it produced work in;
* a finalised performance figure refuses every accounting mutation under it;
* a result that is no longer counted names no counter.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import dataclasses
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from meobot.application import pr_work_recurring_service as recurring_module
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.time import utcnow
from meobot.db.models.pr_transition import PrContentTransitionEvent as TransitionEvent
from meobot.db.models.pr_work import PrWorkHistory, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.pr.content_work import PrContentWorkKind, PrContentWorkOutcome
from meobot.domain.pr.errors import PrConflictError, PrValidationError
from meobot.domain.pr.models import PrContentType, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import (
    PrWorkAssignmentMode,
    PrWorkCountStatus,
    PrWorkEventType,
    PrWorkSourceType,
    PrWorkStatus,
)
from meobot.domain.pr.work_quota import PrWorkQuotaStatus
from meobot.domain.pr.work_results import (
    PERFORMANCE_FINALIZED,
    PERIOD_CONTAINER_CANCELLED,
    PrWorkCountOrigin,
    PrWorkExclusionKind,
    source_may_reverse_count,
)
from tests.unit.test_pr_content_work_milestones import (
    accept_cut,
    creation_rule,
    production_rule,
    to_submitted,
)
from tests.unit.test_pr_content_work_projection import (
    approved_content,
    contributions_of,
    grant,
    outcome_for,
    project,
    rule,
    source_result,
    work_type,
)
from tests.unit.test_pr_content_work_resync import manual_sync
from tests.unit.test_pr_performance import ready_month, snapshot
from tests.unit.test_pr_performance import rule as scoring_rule
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_recurring_work import (
    command,
    generated_items,
    local,
    set_cursor,
    sweep,
    template,
)
from tests.unit.test_pr_work_core import take_to_completed
from tests.unit.test_pr_work_legacy_delete import legacy_item
from tests.unit.test_pr_work_maintenance import container_of, counted_content, rebuild, sync
from tests.unit.test_pr_work_quota import allocations_for, approved_plan, month
from tests.unit.test_pr_work_result_exclusion import (
    actual,
    admin_remove,
    customers,
    fresh,
    mapped_month,
    pending_manual,
    reject,
    self_approved_content,
    validate,
    withdraw,
    worker_runs,
)

pytestmark = pytest.mark.asyncio

KIND = PrContentWorkKind.CONTENT_CREATION
TYPE = PrContentType.SHORT_VIDEO_SCRIPT


# ===========================================================================
# Helpers
# ===========================================================================


def counted(row: PrWorkResult) -> None:
    assert row.status is PrWorkCountStatus.COUNTED, row.status
    assert row.counted_at is not None and row.exclusion_kind is None


def source_reversed(row: PrWorkResult) -> None:
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED
    assert row.counted_at is None and row.counted_by_user_id is None


async def human_counted(world: World, content_id: uuid.UUID, kind: PrContentWorkKind = KIND):  # type: ignore[no-untyped-def]
    """A pending source-derived result, confirmed by the head in the Work module."""
    result = await source_result(world, content_id, kind)
    assert result is not None and result.status is PrWorkCountStatus.PENDING
    await validate(world, result, by=world.head)
    row = await fresh(world, result)
    counted(row)
    assert row.counted_by_user_id == world.head.id
    return row


async def kpi_row(world: World, *, period, type_id: uuid.UUID, user=None):  # type: ignore[no-untyped-def]
    """The KPI screen's figures for one work type."""
    summary = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=(user or world.member).id, period_id=period.id
    )
    rows = [row for row in summary.types if row.work_type_id == type_id]
    assert len(rows) == 1, [row.work_type_code for row in summary.types]
    return rows[0]


async def breakdown_row(world: World, *, period, type_id: uuid.UUID, user=None):  # type: ignore[no-untyped-def]
    """The performance breakdown's figures for one work type."""
    result = await snapshot(world, period=period, user=user)
    rows = [row for row in result.breakdown if row.work_type_id == type_id]
    assert len(rows) == 1, [row.work_type_code for row in result.breakdown]
    return rows[0]


async def card_actual(world: World, result: PrWorkResult) -> Decimal:
    item = await container_of(world, result)
    summary = await world.services.work_results.summary(item)
    assert summary is not None
    return summary.counted_quantity


async def three_readers_agree(world: World, result: PrWorkResult, expected: Decimal):  # type: ignore[no-untyped-def]
    """Work card, KPI screen and performance breakdown: one Actual."""
    item = await container_of(world, result)
    assert item.reporting_period_id is not None
    period = await world.services.work_periods.require_period(item.reporting_period_id)
    assert item.quantity == expected, "the mirrored quantity"
    assert await card_actual(world, result) == expected, "the work card"
    kpi = await kpi_row(world, period=period, type_id=item.work_type_id)
    assert kpi.counted_amount == expected, f"the KPI screen: {kpi}"
    perf = await breakdown_row(world, period=period, type_id=item.work_type_id)
    assert perf.counted_amount == expected, f"the performance breakdown: {perf}"
    return period, kpi, perf


async def production_pending(world: World):  # type: ignore[no-untyped-def]
    """A cut handed in and not yet accepted: the PRODUCTION result is PENDING."""
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await project(world, content_id)
    result = await source_result(world, content_id, PrContentWorkKind.PRODUCTION)
    assert result is not None and result.status is PrWorkCountStatus.PENDING
    assert result.user_id == world.other.id
    return content_id, result


async def withdraw_acceptance(world: World, content_id: uuid.UUID) -> None:
    """The video approval undone the way an undo records it: no longer live."""
    event = (
        (
            await world.session.execute(
                select(TransitionEvent).where(
                    TransitionEvent.content_id == content_id,
                    TransitionEvent.to_stage == PrWorkflowStage.READY_TO_PUBLISH,
                )
            )
        )
        .scalars()
        .one()
    )
    event.reversed_by_event_id = event.id
    await world.session.flush()


# ===========================================================================
# 0: THE POLICY, AS A PURE FUNCTION
# ===========================================================================


def test_00_the_convergence_rule_is_one_function() -> None:
    keep, take = False, True
    # The milestone is gone: out, whoever counted it.
    for origin in (PrWorkCountOrigin.SOURCE, PrWorkCountOrigin.WORK_VALIDATOR, None):
        for independent in (True, False):
            assert (
                source_may_reverse_count(
                    source_eligible=False, source_independent=independent, origin=origin
                )
                is take
            )
    # The source still validates it independently: nothing changes.
    for origin in (PrWorkCountOrigin.SOURCE, PrWorkCountOrigin.WORK_VALIDATOR, None):
        assert (
            source_may_reverse_count(source_eligible=True, source_independent=True, origin=origin)
            is keep
        )
    # The source's own validation is not independent: only its own count goes.
    assert (
        source_may_reverse_count(
            source_eligible=True, source_independent=False, origin=PrWorkCountOrigin.SOURCE
        )
        is take
    )
    assert (
        source_may_reverse_count(
            source_eligible=True,
            source_independent=False,
            origin=PrWorkCountOrigin.WORK_VALIDATOR,
        )
        is keep
    )
    # An unattributable count is treated as the source's - never as a person's.
    assert (
        source_may_reverse_count(source_eligible=True, source_independent=False, origin=None)
        is take
    )


# ===========================================================================
# 1-8: A HUMAN WORK VALIDATION SURVIVES PROJECTOR REPLAY
# ===========================================================================


async def test_01_08_a_self_approved_script_confirmed_by_a_person_stays_counted(
    world: World,
) -> None:
    period, _type_row = await mapped_month(world)
    # 1-2. valid self-approved source; projection creates PENDING.
    content_id, result = await self_approved_content(world)
    # 3-4. an independent Work validator confirms.
    row = await human_counted(world, content_id)
    assert await world.services.work_results.count_origin(row) is (PrWorkCountOrigin.WORK_VALIDATOR)
    assert await actual(world, row) == Decimal("1.00")

    # 5-8. the projector runs, and runs again: COUNTED both times.
    for _ in range(2):
        report = await project(world, content_id)
        assert outcome_for(report, KIND) is PrContentWorkOutcome.UNCHANGED
        counted(await fresh(world, result))
        assert (await fresh(world, result)).counted_by_user_id == world.head.id
    assert await actual(world, row) == Decimal("1.00")

    # Every other replay path agrees: the worker, the button, the batch
    # sync, the rebuild and a dry run.
    report = await worker_runs(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.UNCHANGED
    assert (await manual_sync(world, content_id)).json()["outcome"] == "UNCHANGED"
    run = await sync(world, period)
    assert run.content_items == 0
    run = await rebuild(world, period)
    assert run.results_removed == 0
    report = await project(world, content_id, dry_run=True)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.UNCHANGED
    counted(await fresh(world, result))
    assert await actual(world, row) == Decimal("1.00")
    # And no reversal was ever written about it.
    excluded = [
        one
        for one in (
            await world.session.execute(
                select(PrWorkHistory).where(
                    PrWorkHistory.work_item_id == row.work_item_id,
                    PrWorkHistory.event_type == PrWorkEventType.RESULT_EXCLUDED,
                )
            )
        ).scalars()
        if (one.event_metadata or {}).get("result_id") == str(row.id)
    ]
    assert excluded == []


async def test_01_08b_a_cut_nobody_accepted_confirmed_by_a_person_stays_counted(
    world: World,
) -> None:
    content_id, result = await production_pending(world)
    row = await human_counted(world, content_id, PrContentWorkKind.PRODUCTION)
    for _ in range(2):
        report = await project(world, content_id)
        assert outcome_for(report, PrContentWorkKind.PRODUCTION) is (PrContentWorkOutcome.UNCHANGED)
        counted(await fresh(world, result))
    report = await worker_runs(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.UNCHANGED
    counted(await fresh(world, result))
    assert await actual(world, row) == Decimal("1.00")


async def test_01_08c_the_source_accepting_afterwards_keeps_the_persons_count(
    world: World,
) -> None:
    """The head accepts the cut after a Work validator already counted it:
    the row stays counted, once, by the person who counted it first."""
    content_id, result = await production_pending(world)
    row = await human_counted(world, content_id, PrContentWorkKind.PRODUCTION)
    before = row.counted_at
    await accept_cut(world, content_id, reviewer=world.head)
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.UNCHANGED
    row = await fresh(world, result)
    counted(row)
    assert row.counted_by_user_id == world.head.id and row.counted_at == before
    assert await actual(world, row) == Decimal("1.00")


# ===========================================================================
# 9-18: A REAL SOURCE WITHDRAWAL STILL REVERSES
# ===========================================================================


async def test_09_13_a_source_counted_result_goes_when_the_acceptance_is_withdrawn(
    world: World,
) -> None:
    # 9-10. valid source, auto-counted from the head's independent acceptance.
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await accept_cut(world, content_id, reviewer=world.head)
    await project(world, content_id)
    result = await source_result(world, content_id, PrContentWorkKind.PRODUCTION)
    assert result is not None
    counted(result)
    assert await world.services.work_results.count_origin(result) is PrWorkCountOrigin.SOURCE
    # 11-13. the acceptance is withdrawn; the projector takes the count out.
    await withdraw_acceptance(world, content_id)
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.REVERSED
    row = await fresh(world, result)
    source_reversed(row)
    assert await actual(world, row) == Decimal("0.00")
    # The submission still stands, so the next pass restores it to PENDING -
    # a validator may count it from there - and it never oscillates back.
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is (
        PrContentWorkOutcome.PENDING_VALIDATION
    )
    assert (await fresh(world, result)).status is PrWorkCountStatus.PENDING
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is (
        PrContentWorkOutcome.PENDING_VALIDATION
    )
    assert (await fresh(world, result)).status is PrWorkCountStatus.PENDING


async def test_09_13b_a_source_counted_script_goes_when_the_approval_is_undone(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    assert await world.services.work_results.count_origin(result) is PrWorkCountOrigin.SOURCE
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED
    source_reversed(await fresh(world, result))


async def test_14_18_a_person_counted_result_goes_when_the_milestone_itself_goes(
    world: World,
) -> None:
    await mapped_month(world)
    # 14-15. valid source, human Work-validated.
    content_id, result = await self_approved_content(world)
    row = await human_counted(world, content_id)
    # 16-18. the milestone itself is withdrawn: the person's count does not
    # keep invalid source work alive.
    await withdraw(world, content_id)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED
    row = await fresh(world, result)
    source_reversed(row)
    assert await actual(world, row) == Decimal("0.00")
    # Still out on every later pass, and the validator's path agrees.
    report = await worker_runs(world, content_id)
    assert outcome_for(report, KIND) is None or outcome_for(report, KIND) is not (
        PrContentWorkOutcome.PROJECTED
    )
    source_reversed(await fresh(world, result))
    with pytest.raises(PrConflictError):
        await world.services.work_results.reconsider_result(
            actor=world.actor(world.head), request_id=world.request_id, result_id=row.id
        )


async def test_14_18b_a_person_counted_cut_survives_a_withdrawn_acceptance(
    world: World,
) -> None:
    """The submission is the milestone and it still qualifies; the withdrawn
    acceptance was never what counted this row."""
    content_id, result = await production_pending(world)
    row = await human_counted(world, content_id, PrContentWorkKind.PRODUCTION)
    await accept_cut(world, content_id, reviewer=world.head)
    await project(world, content_id)
    await withdraw_acceptance(world, content_id)
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.UNCHANGED
    counted(await fresh(world, result))
    assert await actual(world, row) == Decimal("1.00")


async def test_a_validators_rejection_still_outranks_everything(world: World) -> None:
    """Regression guard: the new rule changes nothing about a rejection."""
    await mapped_month(world)
    content_id, result = await self_approved_content(world)
    await human_counted(world, content_id)
    await reject(world, result)
    for _ in range(2):
        report = await project(world, content_id)
        assert outcome_for(report, KIND) is PrContentWorkOutcome.HELD_BY_VALIDATOR
    row = await fresh(world, result)
    assert row.exclusion_kind is PrWorkExclusionKind.VALIDATOR_REJECTED


# ===========================================================================
# LEGACY ONE-OFF ITEMS OBEY THE SAME RULE
# ===========================================================================


async def test_l1_a_legacy_item_approved_by_a_person_stays_approved(world: World) -> None:
    await month(world)
    type_row = await work_type(world, code="TINY_SCRIPT", name="Kịch bản siêu ngắn")
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    await grant(world, world.member, PrCapability.PR_HEAD_REVIEW)
    content_id = await approved_content(
        world, writer=world.member, head=world.member, content_type=TYPE
    )
    item = await legacy_item(world, content_id=content_id, type_row=type_row, counted=False)
    assert item.status is PrWorkStatus.COMPLETED
    # The projector leaves a self-approved legacy row pending a person.
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PENDING_VALIDATION
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    await world.session.refresh(item)
    assert item.status is PrWorkStatus.APPROVED
    assert await world.services.work.count_origin(item) is PrWorkCountOrigin.WORK_VALIDATOR
    for _ in range(2):
        report = await project(world, content_id)
        assert outcome_for(report, KIND) is PrContentWorkOutcome.UNCHANGED
        await world.session.refresh(item)
        assert item.status is PrWorkStatus.APPROVED
        rows = await contributions_of(world, item.id)
        assert rows[0].count_status is PrWorkCountStatus.COUNTED
    # And the milestone going takes it out, as for a result.
    await withdraw(world, content_id)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.EXCLUDED


async def test_l2_a_source_counted_legacy_item_is_the_sources_to_take_back(
    world: World,
) -> None:
    await month(world)
    type_row = await work_type(world, code="TINY_SCRIPT", name="Kịch bản siêu ngắn")
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    content_id = await approved_content(world, content_type=TYPE)
    item = await legacy_item(world, content_id=content_id, type_row=type_row, counted=True)
    assert await world.services.work.count_origin(item) is PrWorkCountOrigin.SOURCE
    assert outcome_for(await project(world, content_id), KIND) is PrContentWorkOutcome.UNCHANGED


async def test_l3_a_source_redo_revives_its_own_exclusion_and_not_a_persons(
    world: World,
) -> None:
    await month(world)
    type_row = await work_type(world, code="TINY_SCRIPT", name="Kịch bản siêu ngắn")
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    content_id = await approved_content(world, content_type=TYPE)
    item = await legacy_item(world, content_id=content_id, type_row=type_row, counted=True)

    # The source's own reversal, then its redo: revived, as always.
    await world.services.work.reverse_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_item_id=item.id,
        reason="rút duyệt",
    )
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.EXCLUDED
    await world.services.work.count_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_item_id=item.id,
        validated_by_user_id=world.head.id,
        effective_validation_at=rows[0].excluded_at,  # type: ignore[arg-type]
    )
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED

    # A person's exclusion - written the way a manual act writes one, with no
    # source origin on its timeline row - is not the source's to undo.
    await world.services.work.reverse_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_item_id=item.id,
        reason="rút duyệt lần hai",
    )
    contribution = (await contributions_of(world, item.id))[0]
    world.session.add(
        PrWorkHistory(
            work_item_id=item.id,
            contribution_id=contribution.id,
            event_type=PrWorkEventType.EXCLUDED,
            actor_user_id=world.lead.id,
            note="Trưởng nhóm loại bỏ",
            event_metadata={"user_id": str(contribution.user_id)},
            # Strictly after the source's own exclusion row, so "the newest
            # decision on this contribution" is the person's.
            created_at=utcnow() + timedelta(seconds=5),
        )
    )
    contribution.excluded_by_user_id = world.lead.id
    contribution.excluded_reason = "Trưởng nhóm loại bỏ"
    await world.session.flush()
    await world.services.work.count_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_item_id=item.id,
        validated_by_user_id=world.head.id,
        effective_validation_at=contribution.excluded_at,  # type: ignore[arg-type]
    )
    await world.session.refresh(contribution)
    assert contribution.count_status is PrWorkCountStatus.EXCLUDED, "a person's decision stands"
    assert contribution.excluded_by_user_id == world.lead.id
    trail = list(
        (
            await world.session.execute(
                select(PrWorkHistory).where(
                    PrWorkHistory.work_item_id == item.id,
                    PrWorkHistory.event_type == PrWorkEventType.APPROVED,
                )
            )
        ).scalars()
    )
    assert trail, "the redo itself is still recorded"


# ===========================================================================
# 19-24: A PERIOD CONTAINER IS NOT AN ORDINARY WORK ITEM
# ===========================================================================


async def empty_container(world: World, type_row):  # type: ignore[no-untyped-def]
    """A stream that exists and holds nothing: reported into once, withdrawn."""
    result = await pending_manual(world, type_row)
    item_id = result.work_item_id
    await world.services.work_results.withdraw_result(
        actor=world.actor(world.member), request_id=world.request_id, result_id=result.id
    )
    item = await world.session.get(PrWorkItem, item_id)
    assert item is not None and item.is_period_container
    return item


async def test_19_24_a_container_cannot_be_cancelled_and_every_reader_agrees(
    world: World,
) -> None:
    type_row = await customers(world)
    # 19-20. an empty container; ordinary cancel is refused.
    item = await empty_container(world, type_row)
    for actor in (world.owner, world.lead):
        with pytest.raises(PrValidationError) as refused:
            await world.services.work.cancel(
                actor=world.actor(actor), request_id=world.request_id, work_item_id=item.id
            )
        assert refused.value.details["reason"] == "period_container"
    await world.session.refresh(item)
    assert item.status is not PrWorkStatus.CANCELLED
    # The other ordinary lifecycle acts are refused the same way.
    for call in (
        world.services.work.accept(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
        ),
        world.services.work.reject(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
        ),
        world.services.work.start(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        ),
        world.services.work.complete(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        ),
        world.services.work.approve(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
        ),
        world.services.work.reopen(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
        ),
    ):
        with pytest.raises(PrValidationError) as caught:
            await call
        assert caught.value.details["reason"] == "period_container"

    # 21. a result reported afterwards lands in the same, live container.
    result = await pending_manual(world, type_row, quantity=4)
    assert result.work_item_id == item.id
    await world.services.work_results.validate_results(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    # 22-23. the mirrored quantity and the contribution follow the results.
    await world.session.refresh(item)
    assert item.quantity == Decimal("4.00")
    contribution = (await contributions_of(world, item.id))[0]
    assert contribution.count_status is PrWorkCountStatus.COUNTED
    # 24. one Actual on the card, the KPI screen and the breakdown.
    await three_readers_agree(world, result, Decimal("4.00"))


async def test_19_24b_a_container_left_cancelled_is_refused_and_repairable(
    world: World,
) -> None:
    """Pre-existing data from the lifecycle this patch closes: refused with
    a structured reason, cleaned up by the existing administrator path, and
    the next report opens a live stream in the same slot."""
    type_row = await customers(world)
    item = await empty_container(world, type_row)
    # The state no current path produces, written directly - as an assigned
    # stream, which the cleanup would otherwise protect.
    item.status = PrWorkStatus.CANCELLED
    item.assigned_by_user_id = world.lead.id
    for row in await contributions_of(world, item.id):
        row.count_status = PrWorkCountStatus.EXCLUDED
        row.excluded_reason = "Công việc đã hủy"
    await world.session.flush()

    with pytest.raises(PrConflictError) as caught:
        await pending_manual(world, type_row)
    assert caught.value.details["reason"] == PERIOD_CONTAINER_CANCELLED
    assert caught.value.details["work_item_id"] == str(item.id)
    assert (
        await world.session.scalar(
            select(PrWorkResult.id).where(PrWorkResult.work_item_id == item.id).limit(1)
        )
        is None
    ), "nothing was recorded into it"

    # The repair: the existing cleanup removes a cancelled empty stream even
    # though it was assigned, and the next report opens a live one.
    await world.services.work_maintenance.remove_empty_container(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    result = await pending_manual(world, type_row, quantity=2)
    fresh_item = await world.session.get(PrWorkItem, result.work_item_id)
    assert fresh_item is not None and fresh_item.id != item.id
    assert fresh_item.status is not PrWorkStatus.CANCELLED


# ===========================================================================
# ACTUAL NEVER DEPENDS ON M2 MATERIALISATION
# ===========================================================================


async def counted_stream(world: World, *, type_row, period, quantity: Decimal) -> PrWorkResult:  # type: ignore[no-untyped-def]
    result = await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=quantity,
        work_type_id=type_row.id,
        period_id=period.id,
    )
    await world.services.work_results.validate_results(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_item_id=result.work_item_id,
    )
    return await fresh(world, result)


async def test_m2_failure_leaves_one_actual_on_every_screen(world: World) -> None:
    period = await month(world)
    type_row = await customers(world)
    await approved_plan(world, period=period, quotas=((type_row, Decimal("6"), Decimal("10")),))

    # 1-3. COUNTED work, with the savepointed M2 hand-off failing.
    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("M2 is down")

    original = world.services.work_eligibility.on_contributions_counted
    world.services.work_eligibility.on_contributions_counted = explode  # type: ignore[method-assign]
    try:
        result = await counted_stream(
            world, type_row=type_row, period=period, quantity=Decimal("10")
        )
    finally:
        world.services.work_eligibility.on_contributions_counted = original  # type: ignore[method-assign]

    # 4-5. the contribution is COUNTED and no allocation was invented.
    counted(result)
    contribution = (await contributions_of(world, result.work_item_id))[0]
    assert contribution.count_status is PrWorkCountStatus.COUNTED
    assert await allocations_for(world, user=world.member, period=period) == []

    # 6-8. Actual = 10 on the card, the KPI screen and the breakdown; the
    # comparison is 166.7 % against 6; only the classification is pending.
    _, kpi, perf = await three_readers_agree(world, result, Decimal("10.00"))
    assert kpi.target_value == Decimal("6.00")
    assert kpi.completion_percent == Decimal("166.7")
    assert kpi.pending_contributions == 1 and kpi.eligible_amount == Decimal("0")
    assert perf.target_value == Decimal("6.00")
    assert perf.completion_percent == Decimal("166.7")
    # Reconciling classifies it and changes no Actual.
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    _, kpi, perf = await three_readers_agree(world, result, Decimal("10.00"))
    assert kpi.eligible_amount == Decimal("10.00") and kpi.over_quota_amount == Decimal("0")
    assert kpi.completion_percent == Decimal("166.7")


async def test_lowering_the_cap_below_actual_changes_only_the_classification(
    world: World,
) -> None:
    period = await month(world)
    type_row = await customers(world)
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("6"), Decimal("10")),)
    )
    result = await counted_stream(world, type_row=type_row, period=period, quantity=Decimal("10"))
    _, kpi, _ = await three_readers_agree(world, result, Decimal("10.00"))
    assert kpi.eligible_amount == Decimal("10.00")

    # A revision lowers the cap to 6 and is approved by somebody else.
    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
    )
    quota = next(one for one in revision.quotas if one.work_type_id == type_row.id)
    await world.services.work_plans.update_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=revision.plan.id,
        quota_id=quota.id,
        target_value=Decimal("6"),
        eligibility_cap=Decimal("6"),
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=revision.plan.id
    )

    # Actual stays 10 everywhere; eligible 6, over quota 4; nothing on the
    # result or the item moved.
    _, kpi, perf = await three_readers_agree(world, result, Decimal("10.00"))
    assert kpi.eligible_amount == Decimal("6.00") and kpi.over_quota_amount == Decimal("4.00")
    assert kpi.completion_percent == Decimal("166.7")
    assert perf.completion_percent == Decimal("166.7")
    row = await fresh(world, result)
    counted(row)
    assert row.quantity == Decimal("10.00")


async def test_removing_the_quota_keeps_the_result_and_the_actual(world: World) -> None:
    period = await month(world)
    type_row = await customers(world)
    other_type = await work_type(world, code="SEEDING", name="Seeding")
    plan = await approved_plan(
        world,
        period=period,
        quotas=(
            (type_row, Decimal("6"), Decimal("10")),
            (other_type, Decimal("1"), Decimal("1")),
        ),
    )
    result = await counted_stream(world, type_row=type_row, period=period, quantity=Decimal("10"))

    revision = await world.services.work_plans.revise(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=plan.id
    )
    quota = next(one for one in revision.quotas if one.work_type_id == type_row.id)
    await world.services.work_plans.remove_quota(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        plan_id=revision.plan.id,
        quota_id=quota.id,
    )
    await world.services.work_plans.approve(
        actor=world.actor(world.owner), request_id=world.request_id, plan_id=revision.plan.id
    )

    row = await fresh(world, result)
    counted(row)
    _, kpi, perf = await three_readers_agree(world, result, Decimal("10.00"))
    assert kpi.target_value is None and kpi.completion_percent is None
    assert kpi.no_quota_amount == Decimal("10.00")
    assert perf.target_value is None and perf.completion_percent is None
    assert perf.counted_amount == Decimal("10.00")
    statuses = (
        await world.services.work_eligibility.summary(
            actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
        )
    ).contributions_by_status
    assert statuses[PrWorkQuotaStatus.NO_QUOTA.value] == 1


async def test_no_kpi_at_all_still_reads_the_actual_everywhere(world: World) -> None:
    period = await month(world)
    type_row = await customers(world)
    result = await counted_stream(world, type_row=type_row, period=period, quantity=Decimal("10"))
    _, kpi, perf = await three_readers_agree(world, result, Decimal("10.00"))
    assert kpi.target_value is None and perf.target_value is None
    summary = await world.services.work_results.summary(await container_of(world, result))
    assert summary is not None and summary.comparison.completion_percent is None


# ===========================================================================
# 25-30: A ROUTINE'S ACCOUNTING MODE IS FIXED FOR A MONTH IT PRODUCED WORK IN
# ===========================================================================


def freeze_recurring_clock(monkeypatch: pytest.MonkeyPatch, year: int, month_no: int, day: int):  # type: ignore[no-untyped-def]
    moment = local(year, month_no, day, 10, 0)
    monkeypatch.setattr(recurring_module, "utcnow", lambda: moment)
    return moment


def switch(row, *, accumulate: bool, type_row, contributors):  # type: ignore[no-untyped-def]
    base = command(
        type_row=type_row,
        contributors=contributors,
        start=date(2026, 9, 1),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
    )
    return dataclasses.replace(base, accumulate_by_period=accumulate)


async def test_25_26_a_per_firing_routine_with_work_this_month_cannot_switch(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_recurring_clock(monkeypatch, 2026, 9, 10)
    row = await template(
        world, start=date(2026, 9, 1), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    type_row = await _type_of(world, row)
    await set_cursor(world, row, local(2026, 9, 4, 0, 0))
    await sweep(world, row, at=local(2026, 9, 4, 12, 0))
    assert len(await generated_items(world)) == 1

    with pytest.raises(PrConflictError) as caught:
        await world.services.work_recurring.update_template(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            template_id=row.id,
            command=switch(
                row, accumulate=True, type_row=type_row, contributors=(world.member.id,)
            ),
        )
    assert caught.value.details["reason"] == "recurring_accounting_mode_locked_for_period"
    assert caught.value.details["period"] == "2026-09"
    assert "kỳ tiếp theo" in str(caught.value)
    await world.session.refresh(row)
    assert row.accumulate_by_period is False
    assert row.revision_no == 1, "a refused edit bumps nothing"


async def _type_of(world: World, row) -> PrWorkType:  # type: ignore[no-untyped-def]
    type_row = await world.session.get(PrWorkType, row.work_type_id)
    assert type_row is not None
    return type_row


async def test_27_28_an_accumulating_routine_with_results_this_month_cannot_switch(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_recurring_clock(monkeypatch, 2026, 9, 10)
    row = await template(
        world,
        activate=False,
        start=date(2026, 9, 1),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
    )
    type_row = await _type_of(world, row)
    await world.services.work_recurring.update_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        template_id=row.id,
        command=switch(row, accumulate=True, type_row=type_row, contributors=(world.member.id,)),
    )
    await world.services.work_recurring.activate(
        actor=world.actor(world.lead), request_id=world.request_id, template_id=row.id
    )
    # Activation opened the month's container; the member reports into it.
    period = await world.services.work_periods.period_for(local(2026, 9, 10, 10, 0))
    assert period is not None
    container = await world.services.work_results.container_for(
        work_type_id=type_row.id, subject_user_id=world.member.id, period_id=period.id
    )
    assert container is not None
    await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=Decimal("2"),
        work_item_id=container.id,
    )
    with pytest.raises(PrConflictError) as caught:
        await world.services.work_recurring.update_template(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            template_id=row.id,
            command=switch(
                row, accumulate=False, type_row=type_row, contributors=(world.member.id,)
            ),
        )
    assert caught.value.details["reason"] == "recurring_accounting_mode_locked_for_period"
    await world.session.refresh(row)
    assert row.accumulate_by_period is True


async def test_29_30_with_nothing_generated_or_in_the_next_period_the_mode_may_change(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 29. active, nothing generated yet: the switch is ordinary editing.
    freeze_recurring_clock(monkeypatch, 2026, 9, 10)
    row = await template(
        world, start=date(2026, 9, 1), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    type_row = await _type_of(world, row)
    await world.services.work_recurring.update_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        template_id=row.id,
        command=switch(row, accumulate=True, type_row=type_row, contributors=(world.member.id,)),
    )
    assert row.accumulate_by_period is True
    # An accumulating routine whose container is empty may switch back.
    await world.services.work_recurring.update_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        template_id=row.id,
        command=switch(row, accumulate=False, type_row=type_row, contributors=(world.member.id,)),
    )
    assert row.accumulate_by_period is False

    # 30. work generated in September locks September; October is free.
    await set_cursor(world, row, local(2026, 9, 12, 0, 0))
    await sweep(world, row, at=local(2026, 9, 12, 12, 0))
    assert len(await generated_items(world)) == 1
    with pytest.raises(PrConflictError):
        await world.services.work_recurring.update_template(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            template_id=row.id,
            command=switch(
                row, accumulate=True, type_row=type_row, contributors=(world.member.id,)
            ),
        )
    freeze_recurring_clock(monkeypatch, 2026, 10, 5)
    await world.services.work_recurring.update_template(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        template_id=row.id,
        command=switch(row, accumulate=True, type_row=type_row, contributors=(world.member.id,)),
    )
    assert row.accumulate_by_period is True
    # The September job is exactly as it was.
    items = await generated_items(world)
    assert len(items) == 1 and items[0].source_type is PrWorkSourceType.RECURRING


# ===========================================================================
# A FINALISED PERFORMANCE FIGURE REFUSES ACCOUNTING MUTATION
# ===========================================================================


async def finalized_month(world: World):  # type: ignore[no-untyped-def]
    period = await ready_month(world)
    await world.services.performance.finalize(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    return period


async def test_f1_a_finalised_month_refuses_result_mutations_with_a_reason(
    world: World,
) -> None:
    period = await finalized_month(world)
    type_row = await customers(world)
    # Reporting into the agreed month is refused.
    with pytest.raises(PrConflictError) as caught:
        await world.services.work_results.report_result(
            actor=world.actor(world.member),
            request_id=world.request_id,
            quantity=Decimal("1"),
            work_type_id=type_row.id,
            period_id=period.id,
        )
    assert caught.value.details["reason"] == PERFORMANCE_FINALIZED
    assert caught.value.details["period"] == period.code
    # A colleague's month is not agreed: their stream still takes results.
    other = await world.services.work_results.report_result(
        actor=world.actor(world.other),
        request_id=world.request_id,
        quantity=Decimal("1"),
        work_type_id=type_row.id,
        period_id=period.id,
    )
    assert other.status is PrWorkCountStatus.PENDING


async def test_f2_a_finalised_month_refuses_validation_rejection_and_removal(
    world: World,
) -> None:
    type_row = await customers(world)
    # The stream and its results exist before the month is agreed.
    period = await month(world)
    already = await counted_stream(world, type_row=type_row, period=period, quantity=Decimal("3"))
    pending = await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=Decimal("1"),
        work_type_id=type_row.id,
        period_id=period.id,
    )
    # The stream's type must be priced for the month to be finalisable.
    await scoring_rule(world, type_row, minutes=Decimal("30"))
    await finalized_month(world)

    for call, reason_code in (
        (
            world.services.work_results.validate_results(
                actor=world.actor(world.owner),
                request_id=world.request_id,
                work_item_id=pending.work_item_id,
                result_ids=[pending.id],
            ),
            PERFORMANCE_FINALIZED,
        ),
        (
            world.services.work_results.exclude_result(
                actor=world.actor(world.owner),
                request_id=world.request_id,
                result_id=already.id,
                reason="Sai số",
            ),
            PERFORMANCE_FINALIZED,
        ),
        (
            world.services.work_results.withdraw_result(
                actor=world.actor(world.member), request_id=world.request_id, result_id=pending.id
            ),
            PERFORMANCE_FINALIZED,
        ),
    ):
        with pytest.raises(PrConflictError) as caught:
            await call
        assert caught.value.details["reason"] == reason_code
    # The administrator's removal is refused by its own guard.
    with pytest.raises(PrConflictError) as caught:
        await admin_remove(world, already)
    assert caught.value.details.get("cause") == "performance_finalized"
    counted(await fresh(world, already))
    assert (await fresh(world, pending)).status is PrWorkCountStatus.PENDING
    assert await actual(world, already) == Decimal("3.00")


async def test_f3_a_finalised_month_refuses_one_off_approval_and_blocks_the_projector(
    world: World,
) -> None:
    period = await finalized_month(world)
    type_row = await customers(world)
    # A one-off job for the member, completed, may not be counted into the
    # agreed month.
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id,
            title="Việc sau khi chốt",
            quantity=Decimal("2"),
            contributor_user_ids=(world.member.id,),
        ),
    )
    await take_to_completed(world, item, by=world.member)
    with pytest.raises(PrConflictError) as caught:
        await world.services.work.approve(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == PERFORMANCE_FINALIZED
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.PENDING

    # The projector reports the block rather than counting.
    script = await work_type(world, code="SHORT_SCRIPT", name="Kịch bản ngắn")
    await rule(world, kind=KIND, type_row=script, content_type=TYPE)
    content_id = await approved_content(world, content_type=TYPE)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.BLOCKED_BY_PERIOD
    row = await source_result(world, content_id, KIND)
    assert row is None or row.status is PrWorkCountStatus.PENDING, "nothing counted"
    assert period.status.value == "OPEN", "no period-close semantics were invented"


# ===========================================================================
# A RESULT THAT IS NO LONGER COUNTED NAMES NO COUNTER
# ===========================================================================


async def test_c1_every_exclusion_clears_the_current_counter(world: World) -> None:
    await mapped_month(world)
    type_row = await customers(world)

    # A validator's rejection.
    rejected = await pending_manual(world, type_row)
    await validate(world, rejected, by=world.lead)
    assert (await fresh(world, rejected)).counted_by_user_id == world.lead.id
    await reject(world, rejected)
    row = await fresh(world, rejected)
    assert row.counted_at is None and row.counted_by_user_id is None
    assert row.excluded_by_user_id == world.lead.id

    # An administrator's removal.
    removed = await pending_manual(world, type_row)
    await validate(world, removed, by=world.lead)
    await admin_remove(world, removed)
    row = await fresh(world, removed)
    assert row.counted_at is None and row.counted_by_user_id is None

    # A source reversal.
    content_id, result = await counted_content(world, content_type=TYPE)
    assert result.counted_by_user_id == world.head.id
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    await project(world, content_id)
    row = await fresh(world, result)
    source_reversed(row)

    # The counter is still on the timeline: who counted it before is history,
    # not the row's current state.
    history = [
        one
        for one in (
            await world.session.execute(
                select(PrWorkHistory).where(
                    PrWorkHistory.event_type == PrWorkEventType.RESULT_COUNTED
                )
            )
        ).scalars()
        if (one.event_metadata or {}).get("result_id") == str(rejected.id)
    ]
    assert history and history[0].actor_user_id == world.lead.id
