"""Why a result is out, and who may put it back. ``0041``.

The load-bearing distinction: an administrator's *Xóa kết quả* takes the
accounting out and the next projection re-evaluates it; a validator's
*Từ chối / Không ghi nhận* is a reviewed decision that **no projection
reverses** - the worker, *Đồng bộ lại từ Nội dung*, *Đồng bộ thiếu* and
*Xây dựng lại* all hold it - and *Xem xét lại* is the one release.

Numbered against the task's test matrix:

* 1-8 validator reject; 9-16 projection preserves the rejection;
* 17-22 reconsider; 23-28 admin remove; 29-32 admin remove of a self-approved
  source; 33-36 reject vs admin remove; 37-40 source invalid;
* 41-46 authorization and independent validation;
* then the read model, history, legacy rows and the reason rule.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from meobot.application.pr_content_work_projector import request_content_work_projection
from meobot.application.pr_work_maintenance_service import MaintenanceScope
from meobot.core.time import utcnow
from meobot.db.models.pr_work import PrWorkHistory, PrWorkType
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.content_work import PrContentWorkKind, PrContentWorkOutcome
from meobot.domain.pr.errors import (
    PrConflictError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import PrApprovalStage, PrContentType
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkEventType
from meobot.domain.pr.work_results import (
    PrWorkExclusionKind,
    may_reconsider,
    source_may_restore,
)
from tests.unit.test_pr_content_work_projection import (
    approve_at,
    approved_content,
    grant,
    open_month,
    outcome_for,
    project,
    rule,
    source_result,
    work_type,
)
from tests.unit.test_pr_content_work_resync import (
    manual_sync,
    queue_row,
    worker_settles,
)
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_maintenance import (
    audit_rows,
    container_of,
    counted_content,
    rebuild,
    sync,
)

pytestmark = pytest.mark.asyncio

KIND = PrContentWorkKind.CONTENT_CREATION
TYPE = PrContentType.SHORT_VIDEO_SCRIPT
REASON = "Không đủ minh chứng"
REJECT = "/api/pr/work/results/{}/exclude"
RECONSIDER = "/api/pr/work/results/{}/reconsider"
ADMIN_REMOVE = "/api/pr/work/maintenance/results/{}/admin-remove"


# ===========================================================================
# Helpers
# ===========================================================================


async def mapped_month(world: World):  # type: ignore[no-untyped-def]
    period = await open_month(world, utcnow())
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    return period, type_row


async def customers(world: World) -> PrWorkType:
    return await work_type(world, code="TIM_KHACH", name="Tìm khách hàng")


async def pending_manual(world: World, type_row: PrWorkType, quantity: int = 3) -> PrWorkResult:
    """The member reports into their own stream: ``PENDING``, subject = member."""
    return await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=Decimal(quantity),
        work_type_id=type_row.id,
        label="Khách tuần 2",
    )


async def reject(world: World, result: PrWorkResult, *, by=None, reason: str = REASON):  # type: ignore[no-untyped-def]
    return await world.services.work_results.exclude_result(
        actor=world.actor(by or world.lead),
        request_id=world.request_id,
        result_id=result.id,
        reason=reason,
    )


async def reconsider(world: World, result: PrWorkResult, *, by=None, note=None):  # type: ignore[no-untyped-def]
    return await world.services.work_results.reconsider_result(
        actor=world.actor(by or world.head),
        request_id=world.request_id,
        result_id=result.id,
        note=note,
    )


async def validate(world: World, result: PrWorkResult, *, by=None):  # type: ignore[no-untyped-def]
    return await world.services.work_results.validate_results(
        actor=world.actor(by or world.lead),
        request_id=world.request_id,
        work_item_id=result.work_item_id,
        result_ids=[result.id],
    )


async def admin_remove(world: World, result: PrWorkResult, *, by=None) -> PrWorkResult:  # type: ignore[no-untyped-def]
    return await world.services.work_maintenance.admin_remove_result(
        actor=world.actor(by or world.owner), request_id=world.request_id, result_id=result.id
    )


async def self_approved_content(world: World) -> tuple[uuid.UUID, PrWorkResult]:
    """The writer holds the head gate and takes it: the result is ``PENDING``."""
    await grant(world, world.member, PrCapability.PR_HEAD_REVIEW)
    content_id = await approved_content(world, writer=world.member, head=world.member)
    await project(world, content_id)
    result = await source_result(world, content_id, KIND)
    assert result is not None and result.status is PrWorkCountStatus.PENDING
    return content_id, result


async def fresh(world: World, result: PrWorkResult) -> PrWorkResult:
    await world.session.refresh(result)
    return result


async def actual(world: World, result: PrWorkResult) -> Decimal:
    return Decimal((await container_of(world, result)).quantity or 0)


async def history(
    world: World, result: PrWorkResult, event: PrWorkEventType
) -> list[PrWorkHistory]:
    return list(
        (
            await world.session.execute(
                select(PrWorkHistory)
                .where(
                    PrWorkHistory.work_item_id == result.work_item_id,
                    PrWorkHistory.event_type == event,
                )
                .order_by(PrWorkHistory.created_at.asc())
            )
        ).scalars()
    )


async def worker_runs(world: World, content_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """A content event queues the piece; the worker settles it as the system actor."""
    await request_content_work_projection(world.session, content_id)
    await world.session.flush()
    return await worker_settles(world, content_id)


def rejected(result: PrWorkResult, *, by: uuid.UUID | None = None) -> None:
    assert result.status is PrWorkCountStatus.EXCLUDED
    assert result.exclusion_kind is PrWorkExclusionKind.VALIDATOR_REJECTED
    assert result.excluded_reason == REASON
    assert result.excluded_at is not None and result.counted_at is None
    if by is not None:
        assert result.excluded_by_user_id == by


# ===========================================================================
# 0: THE POLICY, AS A PURE FUNCTION
# ===========================================================================


def test_00_the_convergence_policy_is_one_function() -> None:
    assert source_may_restore(PrWorkExclusionKind.ADMIN_REMOVED)
    assert source_may_restore(PrWorkExclusionKind.SOURCE_REVERSED)
    assert not source_may_restore(PrWorkExclusionKind.VALIDATOR_REJECTED)
    assert not source_may_restore(None), "a legacy exclusion is not guessed at"
    assert may_reconsider(PrWorkExclusionKind.VALIDATOR_REJECTED)
    assert may_reconsider(None)
    assert not may_reconsider(PrWorkExclusionKind.ADMIN_REMOVED)
    assert not may_reconsider(PrWorkExclusionKind.SOURCE_REVERSED)


# ===========================================================================
# 1-8: VALIDATOR REJECT
# ===========================================================================


async def test_01_08_a_validator_rejects_a_pending_result(world: World) -> None:
    type_row = await customers(world)
    result = await pending_manual(world, type_row)

    row = await reject(world, result)
    rejected(row, by=world.lead.id)
    assert await actual(world, row) == Decimal("0.00"), "not counted"
    summary = await world.services.work_results.summary(await container_of(world, row))
    assert summary is not None
    assert summary.counted_quantity == Decimal("0.00")
    assert summary.excluded_quantity == Decimal("3.00")
    assert summary.pending_quantity == Decimal("0.00")

    events = await history(world, row, PrWorkEventType.RESULT_REJECTED)
    assert len(events) == 1 and events[0].note == REASON
    assert events[0].actor_user_id == world.lead.id
    trail = await audit_rows(world, AuditAction.PR_WORK_RESULT_REJECTED)
    assert len(trail) == 1
    after = trail[0].after_data or {}
    assert after["exclusion_kind"] == "VALIDATOR_REJECTED"
    assert after["result_id"] == str(row.id)
    assert after["work_item_id"] == str(row.work_item_id)
    assert after["work_type_id"] == str(type_row.id)
    assert after["subject_user_id"] == str(world.member.id)
    assert after["note"] == REASON
    assert (trail[0].before_data or {})["status"] == "PENDING"
    assert after["status"] == "EXCLUDED"


async def test_01b_a_counted_result_may_still_be_taken_back_by_a_validator(world: World) -> None:
    """The correction flow M1 always had at this grain is preserved, and it
    is a validator's decision like any other."""
    type_row = await customers(world)
    result = await pending_manual(world, type_row, 5)
    await validate(world, result)
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED
    assert await actual(world, result) == Decimal("5.00")
    rejected(await reject(world, result, by=world.head), by=world.head.id)
    assert await actual(world, result) == Decimal("0.00")


async def test_01c_a_rejection_needs_a_reason_that_is_not_whitespace(world: World) -> None:
    type_row = await customers(world)
    result = await pending_manual(world, type_row)
    for empty in ("", "   ", "\n\t"):
        with pytest.raises(PrValidationError):
            await reject(world, result, reason=empty)
    assert (await fresh(world, result)).status is PrWorkCountStatus.PENDING


async def test_01d_rejecting_an_already_excluded_result_changes_nothing(world: World) -> None:
    await mapped_month(world)
    _content_id, result = await counted_content(world, content_type=TYPE)
    await admin_remove(world, result)
    row = await reject(world, result)
    assert row.exclusion_kind is PrWorkExclusionKind.ADMIN_REMOVED, "not rewritten"
    assert await history(world, row, PrWorkEventType.RESULT_REJECTED) == []


# ===========================================================================
# 9-16: PROJECTION PRESERVES THE REJECTION
# ===========================================================================


async def test_09_16_no_projection_path_revives_a_rejected_content_result(
    world: World,
) -> None:
    period, _type_row = await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    assert await actual(world, result) == Decimal("1.00")
    rejected(await reject(world, result))
    assert await actual(world, result) == Decimal("0.00")

    # 10-11. manual single-content sync, over HTTP, as the owner.
    response = await manual_sync(world, content_id)
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "HELD_BY_VALIDATOR"
    rejected(await fresh(world, result))

    # 12-13. the automatic worker, as the system actor.
    report = await worker_runs(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.HELD_BY_VALIDATOR
    rejected(await fresh(world, result))

    # 14-15. batch "Đồng bộ thiếu" and "Xây dựng lại từ Nội dung".
    run = await sync(world, period)
    assert run.content_items == 0, "not missing: the ledger already says what it should"
    rejected(await fresh(world, result))
    run = await rebuild(world, period)
    assert run.results_removed == 0
    assert run.counts.get("HELD_BY_VALIDATOR") == 1
    rejected(await fresh(world, result))

    # The projector's plain call, a dry run, and the preview all agree.
    report = await project(world, content_id, dry_run=True)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.HELD_BY_VALIDATOR
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.HELD_BY_VALIDATOR
    preview = await world.services.work_maintenance.preview(
        actor=world.actor(world.owner), scope=MaintenanceScope(period_id=period.id)
    )
    assert preview.missing_result_count == 0 and preview.correct_result_count == 1

    # 16. nothing restored, and one row for ever.
    rejected(await fresh(world, result))
    assert await actual(world, result) == Decimal("0.00")
    rows = list(
        (
            await world.session.execute(
                select(PrWorkResult).where(PrWorkResult.source_key == result.source_key)
            )
        ).scalars()
    )
    assert len(rows) == 1


async def test_09b_a_rejected_result_is_not_refiled_by_a_mapping_change(world: World) -> None:
    """A held row is left where it is - not moved to a corrected type's
    stream, not restored there. The validator's decision is about *this*
    result, wherever the mapping points now."""
    _period, old_type = await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    rejected(await reject(world, result))
    new_type = await work_type(world, code="SHORT_SCRIPT_V2", name="Kịch bản ngắn v2")
    await rule(world, kind=KIND, type_row=new_type, content_type=TYPE)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.HELD_BY_VALIDATOR
    row = await fresh(world, result)
    rejected(row)
    assert (await container_of(world, row)).work_type_id == old_type.id


# ===========================================================================
# 17-22: RECONSIDER
# ===========================================================================


async def test_17_22_reconsider_releases_to_pending_and_keeps_the_trail(world: World) -> None:
    type_row = await customers(world)
    result = await pending_manual(world, type_row)
    rejected(await reject(world, result, by=world.lead))

    # 18-19. another validator - not the one who rejected - reconsiders.
    row = await reconsider(world, result, by=world.head, note="Có thêm minh chứng")
    assert row.status is PrWorkCountStatus.PENDING
    assert row.exclusion_kind is None
    assert row.excluded_at is None and row.excluded_by_user_id is None
    assert row.excluded_reason is None
    assert await actual(world, row) == Decimal("0.00"), "nothing counted by a release"

    # 20. the rejection is still in the story, and the release is beside it.
    rejections = await history(world, row, PrWorkEventType.RESULT_REJECTED)
    releases = await history(world, row, PrWorkEventType.RESULT_RECONSIDERED)
    assert len(rejections) == 1 and rejections[0].note == REASON
    assert len(releases) == 1 and releases[0].actor_user_id == world.head.id
    assert releases[0].note == "Có thêm minh chứng"
    assert (releases[0].event_metadata or {})["released_reason"] == REASON
    trail = await audit_rows(world, AuditAction.PR_WORK_RESULT_RECONSIDERED)
    assert len(trail) == 1
    assert (trail[0].before_data or {})["exclusion_kind"] == "VALIDATOR_REJECTED"
    assert (trail[0].after_data or {})["status"] == "PENDING"
    assert len(await audit_rows(world, AuditAction.PR_WORK_RESULT_REJECTED)) == 1

    # 21-22. a third validator confirms; it counts.
    await validate(world, row, by=world.owner)
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.COUNTED
    assert row.counted_by_user_id == world.owner.id
    assert await actual(world, row) == Decimal("3.00")
    assert len(await history(world, row, PrWorkEventType.RESULT_COUNTED)) == 1

    # And it can be rejected again, which is a second decision, not a rewrite.
    rejected(await reject(world, row, by=world.lead))
    assert len(await history(world, row, PrWorkEventType.RESULT_REJECTED)) == 2


async def test_17b_reconsider_refuses_what_is_not_a_validators_decision(world: World) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)

    # A pending or counted result has nothing to release.
    with pytest.raises(PrConflictError) as caught:
        await reconsider(world, result)
    assert caught.value.details["reason"] == "work_result_not_reconsiderable"
    assert caught.value.details["status"] == "COUNTED"

    # An administrator's removal is the projector's to re-evaluate.
    await admin_remove(world, result)
    with pytest.raises(PrConflictError) as caught:
        await reconsider(world, result)
    assert caught.value.details["reason"] == "work_result_not_reconsiderable"
    assert caught.value.details["exclusion_kind"] == "ADMIN_REMOVED"

    # And so is a source reversal.
    await project(world, content_id)
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    await project(world, content_id)
    row = await fresh(world, result)
    assert row.exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED
    with pytest.raises(PrConflictError) as caught:
        await reconsider(world, result)
    assert caught.value.details["exclusion_kind"] == "SOURCE_REVERSED"


async def test_17c_reconsidering_a_content_result_does_not_force_the_source(
    world: World,
) -> None:
    """Part I. Released to pending; the source's next word settles it, and a
    release never counts anything by itself."""
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    rejected(await reject(world, result))
    row = await reconsider(world, result)
    assert row.status is PrWorkCountStatus.PENDING
    assert await actual(world, row) == Decimal("0.00")
    # The source is still independently validated: the next projection counts it.
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED
    assert await actual(world, result) == Decimal("1.00")


# ===========================================================================
# 23-28: ADMIN REMOVE
# ===========================================================================


async def test_23_28_admin_removal_is_resyncable_and_syncs_nothing_itself(world: World) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    queued_before = await queue_row(world, content_id)
    queue_state = (
        (queued_before.status, queued_before.updated_at) if queued_before is not None else None
    )

    row = await admin_remove(world, result, by=world.head)
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.ADMIN_REMOVED
    assert row.excluded_by_user_id == world.head.id
    assert await actual(world, row) == Decimal("0.00"), "26. the actual drops"

    # 27. the removal projected nothing, queued nothing, touched no content.
    queued = await queue_row(world, content_id)
    assert ((queued.status, queued.updated_at) if queued is not None else None) == queue_state
    content = await world.reload(content_id)
    assert content.workflow_stage.value == "APPROVED"
    assert len(await audit_rows(world, AuditAction.PR_CONTENT_WORK_RECONCILED)) == 0
    events = await history(world, row, PrWorkEventType.RESULT_ADMIN_REMOVED)
    assert len(events) == 1
    trail = await audit_rows(world, AuditAction.PR_WORK_RESULT_ADMIN_REMOVED)
    assert (trail[0].after_data or {})["exclusion_kind"] == "ADMIN_REMOVED"

    # 28. the next manual sync restores it according to current truth.
    response = await manual_sync(world, content_id)
    assert response.json()["outcome"] == "PROJECTED"
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.COUNTED and row.exclusion_kind is None
    assert await actual(world, row) == Decimal("1.00")


async def test_23b_an_admin_removal_is_also_restored_by_the_worker_and_the_batch(
    world: World,
) -> None:
    period, _type_row = await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    await admin_remove(world, result)
    report = await worker_runs(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED
    await admin_remove(world, result)
    run = await rebuild(world, period)
    assert run.counts.get("PROJECTED") == 1
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED


# ===========================================================================
# 29-32: ADMIN REMOVE OF A SELF-APPROVED SOURCE
# ===========================================================================


async def test_29_32_a_removed_self_approved_result_returns_pending_not_counted(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await self_approved_content(world)
    row = await admin_remove(world, result)
    assert row.exclusion_kind is PrWorkExclusionKind.ADMIN_REMOVED
    response = await manual_sync(world, content_id)
    assert response.json()["outcome"] == "PENDING_VALIDATION"
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.PENDING and row.exclusion_kind is None
    assert await actual(world, row) == Decimal("0.00")
    restored = [
        one
        for one in await history(world, row, PrWorkEventType.RESULT_REPORTED)
        if (one.event_metadata or {}).get("restored")
    ]
    assert len(restored) == 1
    assert (restored[0].event_metadata or {})["restored_from"] == "ADMIN_REMOVED"


# ===========================================================================
# 33-36: VALIDATOR REJECT VS ADMIN REMOVE
# ===========================================================================


async def test_33_36_an_administrator_cannot_delete_around_a_rejection(world: World) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    rejected(await reject(world, result))

    with pytest.raises(PrConflictError) as caught:
        await admin_remove(world, result)
    assert caught.value.details["reason"] == "work_result_validator_rejected"
    assert "Xem xét lại" in str(caught.value)
    rejected(await fresh(world, result))

    # 35. over HTTP, the structured refusal.
    world.act_as(world.owner)
    response = world.client.post(ADMIN_REMOVE.format(result.id), json={"note": "gỡ"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "work_result_validator_rejected"

    # And a sync afterwards still holds it - the bypass does not exist.
    response = await manual_sync(world, content_id)
    assert response.json()["outcome"] == "HELD_BY_VALIDATOR"
    rejected(await fresh(world, result))

    # 36. the validator reconsiders first; then the administrator may act.
    await reconsider(world, result)
    row = await admin_remove(world, result)
    assert row.exclusion_kind is PrWorkExclusionKind.ADMIN_REMOVED


# ===========================================================================
# 37-40: SOURCE INVALID
# ===========================================================================


async def test_37_40_a_source_reversal_is_its_own_kind_and_follows_the_source(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED, "39. not ADMIN_REMOVED"
    assert await actual(world, row) == Decimal("0.00")
    trail = await audit_rows(world, AuditAction.PR_WORK_RESULT_EXCLUDED)
    assert len(trail) == 1 and (trail[0].after_data or {})["exclusion_kind"] == "SOURCE_REVERSED"

    # While the source says no, no sync brings it back - and no rejection is
    # invented for it either.
    for _ in range(2):
        assert (await manual_sync(world, content_id)).json()["outcome"] != "PROJECTED"
    assert (await fresh(world, result)).exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED

    # 40. approved again: the canonical restoration rule counts it on the new instant.
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.COUNTED and row.exclusion_kind is None
    assert await actual(world, row) == Decimal("1.00")


async def test_37b_a_source_reversal_does_not_overwrite_a_rejection(world: World) -> None:
    """Source invalid *and* validator rejected: the row is out either way, and
    the validator's decision is the one that stays on it."""
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    rejected(await reject(world, result))
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    report = await project(world, content_id)
    # No live milestone, and the orphan sweep reads counted rows only: the
    # projector has nothing to say about this piece, and says so.
    assert outcome_for(report, KIND) is None
    assert report.worst is PrContentWorkOutcome.NOT_QUALIFIED
    rejected(await fresh(world, result))
    # Approved again: still held. The source coming back is not a release.
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.HELD_BY_VALIDATOR
    rejected(await fresh(world, result))


# ===========================================================================
# 41-46: AUTHORIZATION AND INDEPENDENT VALIDATION
# ===========================================================================


async def test_41_45_the_two_capabilities_are_separate(world: World) -> None:
    """TEAM_LEAD holds ``PR_WORK_VALIDATE`` and not ``PR_WORK_CONFIGURE``;
    EMPLOYEE holds neither; ADMIN and OWNER hold both. Capabilities, not
    role names, are what every service checks."""
    type_row = await customers(world)

    # 41. VALIDATE-only (the lead) may reject, reconsider and confirm.
    result = await pending_manual(world, type_row)
    rejected(await reject(world, result, by=world.lead))
    assert (await reconsider(world, result, by=world.lead)).status is PrWorkCountStatus.PENDING
    await validate(world, result, by=world.lead)
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED

    # 43 / 42. VALIDATE-only cannot admin-remove; the maintenance route is
    # CONFIGURE's and refuses the lead whatever they may validate.
    with pytest.raises(PrPermissionDeniedError):
        await admin_remove(world, result, by=world.lead)
    world.act_as(world.lead)
    assert world.client.post(ADMIN_REMOVE.format(result.id), json={}).status_code == 403

    # 45. EMPLOYEE without the capability can do none of it.
    other_result = await pending_manual(world, type_row)
    for call in (
        reject(world, other_result, by=world.other),
        reconsider(world, other_result, by=world.other),
        validate(world, other_result, by=world.other),
        admin_remove(world, other_result, by=world.other),
    ):
        with pytest.raises(PrPermissionDeniedError):
            await call
    world.act_as(world.other)
    assert (
        world.client.post(REJECT.format(other_result.id), json={"reason": "x"}).status_code == 403
    )
    assert world.client.post(RECONSIDER.format(other_result.id), json={}).status_code == 403
    assert (await fresh(world, other_result)).status is PrWorkCountStatus.PENDING

    # 44. ADMIN and OWNER validate through VALIDATE, remove through CONFIGURE.
    rejected(await reject(world, other_result, by=world.head))
    assert (await reconsider(world, other_result, by=world.owner)).status is (
        PrWorkCountStatus.PENDING
    )
    assert (await admin_remove(world, other_result, by=world.head)).exclusion_kind is (
        PrWorkExclusionKind.ADMIN_REMOVED
    )


async def test_42_configure_alone_is_not_validation_authority(world: World, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No role today holds ``PR_WORK_CONFIGURE`` without ``PR_WORK_VALIDATE``,
    so the case is pinned at the seam every write checks: a capability
    service that answers *configure only* is refused by every validation
    write and admitted by the removal."""
    type_row = await customers(world)
    result = await pending_manual(world, type_row)
    capabilities = world.services.capabilities
    assert world.services.work_results._capabilities is capabilities
    assert world.services.work_maintenance._capabilities is capabilities
    real_require = capabilities.require

    async def configure_only(actor: Actor, capability: PrCapability, *a: Any, **k: Any) -> Any:
        if capability is not PrCapability.PR_WORK_CONFIGURE:
            raise PrPermissionDeniedError(
                "configure only", details={"reason": "missing_capability"}
            )
        return await real_require(actor, capability, *a, **k)

    monkeypatch.setattr(capabilities, "require", configure_only)
    for call in (
        reject(world, result, by=world.owner),
        validate(world, result, by=world.owner),
        reconsider(world, result, by=world.owner),
    ):
        with pytest.raises(PrPermissionDeniedError):
            await call
    assert (await admin_remove(world, result, by=world.owner)).exclusion_kind is (
        PrWorkExclusionKind.ADMIN_REMOVED
    )


async def test_46_the_subject_cannot_reject_reconsider_or_confirm_their_own(world: World) -> None:
    """Independent validation, on all three acts. The lead holds
    ``PR_WORK_VALIDATE`` and reports into their own stream: the capability is
    not the question."""
    type_row = await customers(world)
    own = await world.services.work_results.report_result(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        quantity=Decimal(2),
        work_type_id=type_row.id,
    )
    for call in (reject(world, own, by=world.lead), validate(world, own, by=world.lead)):
        with pytest.raises(PrPermissionDeniedError) as caught:
            await call
        assert caught.value.details["reason"] == "self_validation"
    rejected(await reject(world, own, by=world.head), by=world.head.id)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await reconsider(world, own, by=world.lead)
    assert caught.value.details["reason"] == "self_validation"
    rejected(await fresh(world, own))
    assert (await reconsider(world, own, by=world.owner)).status is PrWorkCountStatus.PENDING


async def test_46b_a_shut_month_refuses_the_three_acts(world: World) -> None:
    from meobot.domain.pr.reporting import PrPeriodStatus

    period = await open_month(world, utcnow())
    type_row = await customers(world)
    result = await pending_manual(world, type_row)
    rejected(await reject(world, result))
    period.status = PrPeriodStatus.CLOSED
    await world.session.flush()
    with pytest.raises(PrConflictError):
        await reconsider(world, result)
    period.status = PrPeriodStatus.OPEN
    await world.session.flush()
    assert (await reconsider(world, result)).status is PrWorkCountStatus.PENDING


# ===========================================================================
# THE READ MODEL, THE STORY, THE LEGACY ROW, M2
# ===========================================================================


async def test_r1_the_detail_says_why_a_row_is_out_and_what_this_person_may_do(
    world: World,
) -> None:
    await mapped_month(world)
    type_row = await customers(world)
    manual = await pending_manual(world, type_row)
    world.act_as(world.head)
    rows = world.client.get(f"/api/pr/work/{manual.work_item_id}").json()["results"]
    assert rows[0]["status_label"] == "Chờ xác nhận"
    assert rows[0]["can_validate"] and rows[0]["can_reject"] and not rows[0]["can_reconsider"]
    assert rows[0]["exclusion_kind"] is None and not rows[0]["held_by_validator"]

    response = world.client.post(REJECT.format(manual.id), json={"reason": REASON})
    assert response.status_code == 200, response.text
    row = response.json()["results"][0]
    assert row["status"] == "EXCLUDED" and row["exclusion_kind"] == "VALIDATOR_REJECTED"
    assert row["status_label"] == "Đã từ chối"
    assert row["exclusion_kind_label"] == "Đã từ chối"
    assert row["excluded_reason"] == REASON
    assert row["excluded_by_user_id"] == str(world.head.id)
    assert row["excluded_by_name"] == world.head.full_name
    assert row["excluded_at"] is not None
    assert row["held_by_validator"] is True
    assert row["can_reconsider"] is True
    assert row["can_validate"] is False and row["can_reject"] is False

    # The subject sees the same facts and none of the controls.
    world.act_as(world.member)
    row = world.client.get(f"/api/pr/work/{manual.work_item_id}").json()["results"][0]
    assert row["status_label"] == "Đã từ chối" and row["held_by_validator"]
    assert not (row["can_validate"] or row["can_reject"] or row["can_reconsider"])

    # Reconsider over HTTP, then the labels of the other two kinds.
    world.act_as(world.owner)
    response = world.client.post(RECONSIDER.format(manual.id), json={"note": None})
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status_label"] == "Chờ xác nhận"

    content_id, content = await counted_content(world, content_type=TYPE)
    await admin_remove(world, content)
    row = world.client.get(f"/api/pr/work/{content.work_item_id}").json()["results"][0]
    assert row["status_label"] == "Đã xóa khỏi ghi nhận"
    assert row["exclusion_kind"] == "ADMIN_REMOVED" and not row["held_by_validator"]
    assert row["can_reconsider"] is False
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    await project(world, content_id)  # restores, then reverses on the withdrawn approval
    await project(world, content_id)
    row = world.client.get(f"/api/pr/work/{content.work_item_id}").json()["results"][0]
    assert row["exclusion_kind"] in {"ADMIN_REMOVED", "SOURCE_REVERSED"}
    if row["exclusion_kind"] == "SOURCE_REVERSED":
        assert row["status_label"] == "Không còn đủ điều kiện"


async def test_r2_the_timeline_reads_as_a_decision_trail(world: World) -> None:
    type_row = await customers(world)
    result = await pending_manual(world, type_row)
    await reject(world, result, by=world.lead)
    await reconsider(world, result, by=world.head)
    await validate(world, result, by=world.owner)
    world.act_as(world.owner)
    lines = world.client.get(f"/api/pr/work/{result.work_item_id}/history").json()
    story = [(one["event_label"], one["actor_name"], one["note"]) for one in lines]
    assert ("Báo cáo kết quả", world.member.full_name, "Khách tuần 2") in story
    assert ("Từ chối kết quả", world.lead.full_name, REASON) in story
    assert ("Mở lại để xem xét", world.head.full_name, None) in story
    assert ("Xác nhận kết quả", world.owner.full_name, None) in story
    labels = [one[0] for one in story]
    assert (
        labels.index("Từ chối kết quả")
        < labels.index("Mở lại để xem xét")
        < labels.index("Xác nhận kết quả")
    )


async def test_r3_a_legacy_exclusion_is_held_and_released_like_a_rejection(world: World) -> None:
    """A row excluded before ``0041`` has no kind. The projector does not guess
    who excluded it - it holds - and a validator's *Xem xét lại* releases it."""
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    result.status = PrWorkCountStatus.EXCLUDED
    result.counted_at = None
    result.excluded_at = utcnow()
    result.excluded_reason = "Quản trị viên gỡ kết quả: ghi nhầm"
    result.exclusion_kind = None
    await world.session.flush()
    await world.services.work_results.sync_container(
        await container_of(world, result), now=utcnow()
    )

    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.HELD_BY_VALIDATOR
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.EXCLUDED and row.exclusion_kind is None
    world.act_as(world.owner)
    api_row = world.client.get(f"/api/pr/work/{row.work_item_id}").json()["results"][0]
    assert api_row["status_label"] == "Đã loại bỏ" and api_row["held_by_validator"]
    assert api_row["can_reconsider"] is True
    with pytest.raises(PrConflictError) as caught:
        await admin_remove(world, row)
    assert caught.value.details["reason"] == "work_result_not_admin_removable"

    assert (await reconsider(world, row)).status is PrWorkCountStatus.PENDING
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED


async def test_r4_m2_and_the_stored_performance_figure_follow_a_rejection(world: World) -> None:
    period, type_row = await mapped_month(world)
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
    before = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert before.counted_contributions == 1
    rejected(await reject(world, result))
    after = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert after.counted_contributions == 0
    await reconsider(world, result)
    await validate(world, result, by=world.owner)
    again = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert again.counted_contributions == 1


# ===========================================================================
# S: SOURCE TRUTH WINS OVER A STALE PENDING ROW
# ===========================================================================


async def withdraw(world: World, content_id: uuid.UUID, *, by=None) -> None:  # type: ignore[no-untyped-def]
    """The head approval is undone: the milestone simply is not one any more."""
    await world.services.undo.undo_last(
        actor=world.actor(by or world.member), request_id=world.request_id, content_id=content_id
    )


async def test_s1_the_projector_sweeps_a_pending_row_whose_source_was_withdrawn(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await self_approved_content(world)
    await withdraw(world, content_id)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED
    assert await actual(world, row) == Decimal("0.00")
    # Nothing pending: a validator asking for "every pending result" counts nothing.
    batch = await world.services.work_results.validate_results(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=row.work_item_id
    )
    assert batch.counted == () and batch.reversed == ()
    assert (await fresh(world, result)).status is PrWorkCountStatus.EXCLUDED
    # The worker's path sweeps it the same way.
    content_2, result_2 = await self_approved_content_titled(world, "Bài thứ hai")
    await withdraw(world, content_2)
    report = await worker_runs(world, content_2)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED
    assert (await fresh(world, result_2)).exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED


async def self_approved_content_titled(world: World, title: str) -> tuple[uuid.UUID, PrWorkResult]:
    content_id = await approved_content(world, writer=world.member, head=world.member, title=title)
    await project(world, content_id)
    result = await source_result(world, content_id, KIND)
    assert result is not None and result.status is PrWorkCountStatus.PENDING
    return content_id, result


async def test_s2_a_validator_cannot_count_a_pending_row_the_source_withdrew(
    world: World,
) -> None:
    """The projector has not run. The validator's own path asks the source."""
    await mapped_month(world)
    content_id, result = await self_approved_content(world)
    await withdraw(world, content_id)
    assert (await fresh(world, result)).status is PrWorkCountStatus.PENDING, "stale, on purpose"

    # Named: refused, nothing written.
    with pytest.raises(PrConflictError) as caught:
        await validate(world, result, by=world.head)
    assert caught.value.details["reason"] == "work_result_source_not_eligible"
    assert caught.value.details["result_id"] == str(result.id)
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.PENDING and row.counted_at is None
    assert await actual(world, row) == Decimal("0.00")

    # Over HTTP: a business refusal, never a 500.
    world.act_as(world.head)
    response = world.client.post(
        f"/api/pr/work/{result.work_item_id}/results/validate",
        json={"result_ids": [str(result.id)]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "work_result_source_not_eligible"

    # "Every pending result": the stale row is converged, not counted, and a
    # manual result beside it is counted as usual - manual work never
    # depends on Content.
    manual = await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=Decimal(2),
        work_item_id=result.work_item_id,
    )
    batch = await world.services.work_results.validate_results(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=result.work_item_id
    )
    assert batch.reversed == (result.id,)
    assert batch.counted == (manual.id,)
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED
    assert (await fresh(world, manual)).status is PrWorkCountStatus.COUNTED
    assert await actual(world, row) == Decimal("2.00")
    assert len(await audit_rows(world, AuditAction.PR_WORK_RESULT_EXCLUDED)) == 1
    # And the projector, arriving later, finds nothing more to do.
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is None
    assert (await fresh(world, result)).exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED


async def test_s3_a_rejection_is_not_released_while_the_source_is_withdrawn(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    rejected(await reject(world, result))
    await withdraw(world, content_id, by=world.head)
    with pytest.raises(PrConflictError) as caught:
        await reconsider(world, result)
    assert caught.value.details["reason"] == "work_result_source_not_eligible"
    rejected(await fresh(world, result))
    assert len(await history(world, result, PrWorkEventType.RESULT_REJECTED)) == 1
    assert await history(world, result, PrWorkEventType.RESULT_RECONSIDERED) == []
    # The projector leaves a rejected row alone whatever the source does.
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is None
    rejected(await fresh(world, result))
    # The source returns: the release works, and the source's word counts it.
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    assert (await reconsider(world, result)).status is PrWorkCountStatus.PENDING
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED


async def test_s4_an_admin_removal_is_not_restored_while_the_source_is_withdrawn(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    await admin_remove(world, result)
    await withdraw(world, content_id, by=world.head)
    for _ in range(2):
        response = await manual_sync(world, content_id)
        assert response.status_code == 200 and response.json()["outcome"] != "PROJECTED"
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.EXCLUDED
    assert row.exclusion_kind is PrWorkExclusionKind.ADMIN_REMOVED, "not relabelled either"
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    assert (await manual_sync(world, content_id)).json()["outcome"] == "PROJECTED"
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED


async def test_s5_a_reversed_self_approved_row_returns_to_pending_when_the_source_does(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await self_approved_content(world)
    await withdraw(world, content_id)
    await project(world, content_id)
    assert (await fresh(world, result)).exclusion_kind is PrWorkExclusionKind.SOURCE_REVERSED
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.member)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PENDING_VALIDATION
    row = await fresh(world, result)
    assert row.status is PrWorkCountStatus.PENDING and row.exclusion_kind is None
    # And now a validator may count it: the source backs it again.
    await validate(world, row, by=world.head)
    assert (await fresh(world, result)).status is PrWorkCountStatus.COUNTED


async def test_s6_the_read_model_withholds_the_controls_on_a_stale_pending_row(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await self_approved_content(world)
    world.act_as(world.head)
    row = world.client.get(f"/api/pr/work/{result.work_item_id}").json()["results"][0]
    assert row["source_eligible"] is True and row["can_validate"] and row["can_reject"]

    await withdraw(world, content_id)
    row = world.client.get(f"/api/pr/work/{result.work_item_id}").json()["results"][0]
    assert row["status"] == "PENDING" and row["source_eligible"] is False
    assert row["can_validate"] is False and row["can_reject"] is False
    await project(world, content_id)
    row = world.client.get(f"/api/pr/work/{result.work_item_id}").json()["results"][0]
    assert row["status_label"] == "Không còn đủ điều kiện"
    assert row["source_eligible"] is None, "not a pending row any more"

    # A manual result is never asked about the source.
    manual = await world.services.work_results.report_result(
        actor=world.actor(world.member),
        request_id=world.request_id,
        quantity=Decimal(1),
        work_item_id=result.work_item_id,
    )
    rows = {
        one["id"]: one
        for one in world.client.get(f"/api/pr/work/{result.work_item_id}").json()["results"]
    }
    assert rows[str(manual.id)]["source_eligible"] is None and rows[str(manual.id)]["can_validate"]
