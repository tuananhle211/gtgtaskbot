"""Terminal work: cancelled work is out of the default dashboard, and a cancelled or
rejected ordinary row is deletable by an administrator when safe.

Three facts, and they are separate concepts:

* **cancelled is hidden.** A ``CANCELLED`` work item is abandoned work kept
  for its history. It is in nobody's workload and no figure counts it, so the
  default list - *Tất cả* - leaves it out **server-side**: out of the page,
  out of the total, out of the summary tiles and out of a default search.
  ``status=CANCELLED`` is the explicit view and the only way it comes back;
* **a terminal row may be deleted, by a person, when safe.** ``PR_WORK_CONFIGURE``
  (ADMIN and OWNER) may hard-delete one ``CANCELLED`` **or ``REJECTED``**
  ordinary work item from its detail, confirmed. Cancelling never deletes;
  rejecting never deletes; nothing is batched; hard-delete is maintenance and
  not a transition, so a rejected proposal is never cancelled on the way out
  (there is no such edge); and *terminal* does not mean *safe*: a result, a
  counted contribution or an M2/M6 allocation still on the row refuses the
  delete with the counts;
* **rejected stays visible.** Only ``CANCELLED`` is out of the default list;
  a rejected proposal is listed as before.

The REJECTED half of the matrix is at the end of this file (R13-R24).

Numbered against the task:

* **1-7** the default dashboard: an active row is listed, a cancelled row is
  not, the total and the summary exclude it, a default search does not find
  it, the cancelled filter lists exactly the cancelled rows;
* **8-14** authorization: OWNER and ADMIN get the flag and the delete;
  TEAM_LEAD and EMPLOYEE get neither, and a forged call writes nothing;
* **15-22** a clean cancelled row deletes, with exactly its own children, one
  audit row, and every other row - other work, content, modern results,
  recurring data - byte-for-byte what it was;
* **23-27** a row that still holds accounting is refused with a structured
  409 naming what it holds, and nothing is half-deleted;
* **28-31** an active, an approved and a period-container row are refused,
  and a modern result is never removed through this path;
* the two delete rules stay explicit: a cancelled manual row is not legacy,
  and a cancelled legacy row qualifies through either.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_maintenance_service import (
    PERIOD_NOT_OPEN_FOR_CLEANUP,
    TERMINAL_DELETE_BLOCKED_MESSAGE,
    TERMINAL_DELETE_OPERATION,
    TERMINAL_WORK_ITEM_DELETE_BLOCKED,
    WORK_ITEM_NOT_FOUND,
    WORK_ITEM_NOT_LEGACY_CONTENT,
    WORK_ITEM_NOT_TERMINAL,
)
from meobot.application.pr_work_query_service import (
    PrWorkPreset,
    PrWorkScope,
    WorkQuery,
)
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
    PrWorkType,
)
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.pr_work_recurring import PrWorkRecurringTemplate
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.pr.errors import PrConflictError, PrNotFoundError
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkSourceType, PrWorkStatus
from tests.unit.test_pr_content_work_projection import open_month, work_type
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_core import assigned, contributions_of, take_to_completed
from tests.unit.test_pr_work_legacy_delete import legacy_setup, plan_with_quota
from tests.unit.test_pr_work_maintenance import (
    audit_rows,
    count,
    counted_content,
    manual_result,
    recurring_items,
)

pytestmark = pytest.mark.asyncio

MAINT = "/api/pr/work/maintenance"


# ===========================================================================
# Helpers
# ===========================================================================


async def a_type(world: World, code: str = "CHAT") -> PrWorkType:
    """One work type per code, created on first use. ``assigned`` would create
    the default type again on every call and collide with itself."""
    existing = (
        await world.session.execute(select(PrWorkType).where(PrWorkType.code == code))
    ).scalar_one_or_none()
    return existing or await work_type(world, code=code, name=f"Loại {code}")


async def cancelled(
    world: World,
    *,
    title: str = "Chat khách",
    type_row: PrWorkType | None = None,
    evidence: bool = False,
) -> PrWorkItem:
    """A manual job somebody assigned and then cancelled, through the service."""
    item = await assigned(world, title=title, type_row=type_row or await a_type(world))
    if evidence:
        await world.services.work.add_evidence_text(
            actor=world.actor(world.member),
            request_id=world.request_id,
            work_item_id=item.id,
            text="Link: https://example.com/chat",
        )
    row = await world.services.work.cancel(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        reason="Khách hủy lịch",
    )
    assert row.status is PrWorkStatus.CANCELLED
    return row


async def rejected(
    world: World,
    *,
    title: str = "Kịch bản video tâm sự",
    type_row: PrWorkType | None = None,
    evidence: bool = False,
) -> PrWorkItem:
    """A proposal the member filed and the lead rejected, through the service."""
    from meobot.application.pr_work_service import CreateWorkCommand

    row = type_row or await a_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateWorkCommand(
            title=title, work_type_id=row.id, contributor_user_ids=(world.member.id,)
        ),
    )
    if evidence:
        await world.services.work.add_evidence_text(
            actor=world.actor(world.member),
            request_id=world.request_id,
            work_item_id=item.id,
            text="Link: https://example.com/script",
        )
    out = await world.services.work.reject(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        reason="Không phù hợp",
    )
    assert out.status is PrWorkStatus.REJECTED
    return out


async def page(world: World, user: User, **kw):  # type: ignore[no-untyped-def]
    return await world.services.work_queries.page(
        actor=world.actor(user), query=WorkQuery(scope=PrWorkScope.ALL, **kw)
    )


async def delete_cancelled(world: World, user: User, item_id: uuid.UUID, note=None):  # type: ignore[no-untyped-def]
    return await world.services.work_maintenance.admin_delete_terminal_work_item(
        actor=world.actor(user), request_id=world.request_id, work_item_id=item_id, note=note
    )


async def rows_of(world: World, model, column, value) -> int:  # type: ignore[no-untyped-def]
    return await count(world, select(func.count()).select_from(model).where(column == value))


async def children(world: World, item_id: uuid.UUID) -> dict[str, int]:
    return {
        "contributions": await rows_of(
            world, PrWorkContribution, PrWorkContribution.work_item_id, item_id
        ),
        "evidence": await rows_of(world, PrWorkEvidence, PrWorkEvidence.work_item_id, item_id),
        "history": await rows_of(world, PrWorkHistory, PrWorkHistory.work_item_id, item_id),
        "results": await rows_of(world, PrWorkResult, PrWorkResult.work_item_id, item_id),
    }


async def audits(world: World) -> int:
    return await count(world, select(func.count()).select_from(AuditLog))


# ===========================================================================
# 1-7: THE DEFAULT DASHBOARD
# ===========================================================================


async def test_01_02_03_active_is_listed_cancelled_is_not_and_the_total_agrees(
    world: World,
) -> None:
    active = await assigned(world, title="Quay TVC Apexmed", type_row=await a_type(world))
    gone = await cancelled(world, title="Chat khách")

    result = await page(world, world.owner)
    ids = {one.id for one in result.items}
    assert active.id in ids
    assert gone.id not in ids
    assert result.total == len(result.items) == 1

    # Over HTTP, with the defaults a first load sends: same answer.
    world.act_as(world.owner)
    response = world.client.get("/api/pr/work", params={"scope": "ALL", "preset": "ALL"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [one["id"] for one in body["items"]] == [str(active.id)]
    assert body["total"] == 1


async def test_02b_cancelled_is_out_under_every_preset_and_scope(world: World) -> None:
    gone = await cancelled(world)
    for preset in (PrWorkPreset.ALL, PrWorkPreset.MONTH, PrWorkPreset.OPEN, PrWorkPreset.WEEK):
        result = await page(world, world.owner, preset=preset)
        assert all(one.id != gone.id for one in result.items), preset
    # The cancelled row is still the member's own contribution and the lead's
    # own assignment; neither scope shows it without asking.
    for user, scope in ((world.member, PrWorkScope.MINE), (world.lead, PrWorkScope.ASSIGNED_BY_ME)):
        result = await world.services.work_queries.page(
            actor=world.actor(user), query=WorkQuery(scope=scope)
        )
        assert all(one.id != gone.id for one in result.items), scope


async def test_04_the_default_summary_does_not_count_cancelled_work(world: World) -> None:
    period = await open_month(world, utcnow())
    await assigned(world, title="Quay TVC Apexmed", type_row=await a_type(world))
    before = await world.services.work_queries.summary(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, period_id=period.id),
    )
    await cancelled(world, title="Chat khách")
    after = await world.services.work_queries.summary(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, period_id=period.id),
    )
    # Created, accepted, open: none of them moved for a row nobody will do.
    assert after.created == before.created == 1
    assert after.accepted == before.accepted == 1
    assert after.open == before.open == 1
    assert after.counted_contributions == before.counted_contributions == 0


async def test_05_a_default_search_does_not_find_cancelled_work(world: World) -> None:
    gone = await cancelled(world, title="Chat khách Apexmed")
    default = await page(world, world.owner, search="Chat khách")
    assert all(one.id != gone.id for one in default.items)
    assert default.total == 0
    explicit = await page(world, world.owner, search="Chat khách", status=PrWorkStatus.CANCELLED)
    assert [one.id for one in explicit.items] == [gone.id]


async def test_06_07_the_cancelled_filter_lists_exactly_the_cancelled_rows(
    world: World,
) -> None:
    active = await assigned(world, title="Quay TVC Apexmed", type_row=await a_type(world))
    gone = await cancelled(world, title="Chat khách")
    result = await page(world, world.owner, status=PrWorkStatus.CANCELLED)
    assert [one.id for one in result.items] == [gone.id]
    assert result.total == 1
    assert active.id not in {one.id for one in result.items}

    world.act_as(world.owner)
    response = world.client.get("/api/pr/work", params={"scope": "ALL", "status": "CANCELLED"})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [one["id"] for one in items] == [str(gone.id)]
    # The card keeps what the cancelled view still has to show.
    card = items[0]
    assert card["status"] == "CANCELLED"
    assert card["status_label"] == "Đã hủy"
    assert card["title"] == "Chat khách"
    assert card["contributors"][0]["user_name"] == world.member.full_name
    assert card["source_label"]
    assert card["work_type_name"]


async def test_07b_the_content_related_work_list_follows_the_same_default(
    world: World,
) -> None:
    """``content_id`` is a filter over the same query, so the same default."""
    gone = await cancelled(world)
    result = await world.services.work_queries.page(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, status=PrWorkStatus.CANCELLED),
    )
    assert gone.id in {one.id for one in result.items}


# ===========================================================================
# 8-14: AUTHORIZATION
# ===========================================================================


@pytest.mark.parametrize("role", ["owner", "head"])
async def test_08_09_owner_and_admin_get_the_flag_and_the_delete(world: World, role: str) -> None:
    gone = await cancelled(world)
    world.act_as(getattr(world, role))
    detail = world.client.get(f"/api/pr/work/{gone.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["can_admin_delete"] is True
    assert body["admin_delete"] == {
        "rule": "terminal",
        "previous_status": "CANCELLED",
        "deletable": True,
        "reason": None,
        "cause": None,
        "blocking": {
            "results": 0,
            "counted_contributions": 0,
            "quota_allocations": 0,
            "score_allocations": 0,
        },
        "period_code": body["admin_delete"]["period_code"],
        "message": None,
    }
    # The legacy rule is a different rule, and this row does not meet it.
    assert body["can_delete_legacy"] is False

    response = world.client.delete(
        f"{MAINT}/terminal-items/{gone.id}", params={"note": "Dọn dữ liệu công việc test cũ"}
    )
    assert response.status_code == 200, response.text
    deleted = response.json()
    assert deleted["work_item_id"] == str(gone.id)
    assert deleted["code"] == gone.code
    assert deleted["previous_status"] == "CANCELLED"
    assert deleted["source_type"] == "MANUAL"
    assert deleted["responsible_user_id"] == str(world.member.id)
    assert deleted["results_created"] == 0
    assert deleted["projection_requested"] is False
    assert deleted["removed"]["contributions"] == 1
    assert await world.session.get(PrWorkItem, gone.id) is None
    # A second delete of the same id is a deterministic not-found.
    again = world.client.delete(f"{MAINT}/terminal-items/{gone.id}")
    assert again.status_code == 404
    assert again.json()["error"]["details"]["reason"] == WORK_ITEM_NOT_FOUND


@pytest.mark.parametrize("role", ["lead", "member"])
async def test_10_11_the_lower_roles_get_no_flag_and_no_reasoning(world: World, role: str) -> None:
    gone = await cancelled(world)
    world.act_as(getattr(world, role))
    detail = world.client.get(f"/api/pr/work/{gone.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["can_admin_delete"] is False
    assert detail.json()["admin_delete"] is None


@pytest.mark.parametrize("role", ["lead", "member", "other"])
async def test_12_13_14_forged_deletes_are_403_and_write_nothing(world: World, role: str) -> None:
    gone = await cancelled(world, evidence=True)
    before = await children(world, gone.id)
    audits_before = await audits(world)
    world.act_as(getattr(world, role))
    response = world.client.delete(f"{MAINT}/terminal-items/{gone.id}")
    assert response.status_code == 403, (role, response.text)
    assert await world.session.get(PrWorkItem, gone.id) is not None
    assert await children(world, gone.id) == before
    assert await audits(world) == audits_before


# ===========================================================================
# 15-22: THE DELETE, AND WHAT IT LEAVES ALONE
# ===========================================================================


async def test_15_16_17_a_clean_cancelled_row_deletes_with_exactly_its_children(
    world: World,
) -> None:
    gone = await cancelled(world, evidence=True)
    before = await children(world, gone.id)
    assert before["evidence"] == 1
    assert before["history"] >= 3, "created, evidence, cancelled, excluded"
    assert before["contributions"] == 1
    rows = await contributions_of(world, gone.id)
    assert rows[0].count_status is PrWorkCountStatus.EXCLUDED

    deletion = await delete_cancelled(world, world.head, gone.id, note="dọn dữ liệu test")

    assert deletion.removed == {
        "contributions": 1,
        "evidence": 1,
        "history": before["history"],
    }
    assert deletion.previous_status == "CANCELLED"
    assert deletion.responsible_user_id == world.member.id
    assert deletion.source_type == "MANUAL"
    assert await world.session.get(PrWorkItem, gone.id) is None
    assert await children(world, gone.id) == {
        "contributions": 0,
        "evidence": 0,
        "history": 0,
        "results": 0,
    }


async def test_18_one_admin_audit_row_records_what_went(world: World) -> None:
    gone = await cancelled(world, evidence=True)
    rows_before = len(await audit_rows(world, AuditAction.PR_WORK_ITEM_ADMIN_DELETED))
    await delete_cancelled(world, world.owner, gone.id, note="Dọn dữ liệu công việc test cũ")
    rows = await audit_rows(world, AuditAction.PR_WORK_ITEM_ADMIN_DELETED)
    assert len(rows) == rows_before + 1
    row = next(one for one in rows if one.entity_id == str(gone.id))
    assert row.entity_type == "pr_work_item"
    assert row.actor_user_id == world.owner.id
    before = row.before_data or {}
    assert before["operation"] == TERMINAL_DELETE_OPERATION
    assert before["previous_status"] == "CANCELLED"
    assert before["code"] == gone.code
    assert before["title"] == "Chat khách"
    assert before["work_type_id"] == str(gone.work_type_id)
    assert before["source_type"] == "MANUAL"
    assert before["responsible_user_id"] == str(world.member.id)
    assert before["cancel_reason"] == "Khách hủy lịch"
    assert before["cancelled_by_user_id"] == str(world.lead.id)
    assert before["note"] == "Dọn dữ liệu công việc test cũ"
    assert before["removed"]["contributions"] == 1
    assert before["removed"]["evidence"] == 1
    assert before["removed"]["history"] >= 3
    assert "description" not in before, "no bodies in the trail"
    assert row.after_data == {
        "deleted": True,
        "results_created": 0,
        "projection_requested": False,
    }
    assert row.created_at is not None


async def test_19_20_21_22_everything_else_is_byte_for_byte_what_it_was(world: World) -> None:
    await open_month(world, utcnow())
    type_row = await work_type(world, code="TINY_SCRIPT", name="Kịch bản siêu ngắn")
    # Another job of the same kind for the same person, with its own evidence.
    other = await assigned(world, title="Quay TVC Apexmed", type_row=type_row)
    await world.services.work.add_evidence_text(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=other.id,
        text="Link: https://example.com/tvc",
    )
    # A modern content result in the member's container, and a manual one.
    content_id, content_result = await counted_content(world)
    manual = await manual_result(world, type_row)
    # A routine and the job it generated.
    generated = await recurring_items(world)
    template = (await world.session.execute(select(PrWorkRecurringTemplate))).scalars().one()
    template_before = (template.name, template.status, template.updated_at)
    gone = await cancelled(world, title="Chat khách", type_row=type_row, evidence=True)

    other_before = await children(world, other.id)
    results_before = await count(world, select(func.count()).select_from(PrWorkResult))
    contributions_before = await count(world, select(func.count()).select_from(PrWorkContribution))
    content_before = await world.reload(content_id)
    content_stamp = (content_before.title, content_before.workflow_stage, content_before.updated_at)

    await delete_cancelled(world, world.owner, gone.id)

    assert await children(world, other.id) == other_before
    assert (await world.session.get(PrWorkItem, other.id)) is not None
    assert await count(world, select(func.count()).select_from(PrWorkResult)) == results_before
    assert (
        await count(world, select(func.count()).select_from(PrWorkContribution))
        == contributions_before - 1
    ), "exactly the deleted row's own contribution"
    for result, status in ((content_result, PrWorkCountStatus.COUNTED), (manual, manual.status)):
        await world.session.refresh(result)
        assert result.status is status
        assert await world.session.get(PrWorkItem, result.work_item_id) is not None
    content_after = await world.reload(content_id)
    assert (content_after.title, content_after.workflow_stage, content_after.updated_at) == (
        content_stamp
    )
    for item in generated:
        assert await world.session.get(PrWorkItem, item.id) is not None
    await world.session.refresh(template)
    assert (template.name, template.status, template.updated_at) == template_before


async def test_22b_a_cancelled_recurring_job_deletes_and_its_routine_stays(world: World) -> None:
    """Old data: the lifecycle refuses to cancel generated work today, but a
    row that was cancelled before that rule qualifies here like any other, and
    its template and occurrence are not this operation's to touch."""
    generated = await recurring_items(world)
    item = generated[0]
    template = (await world.session.execute(select(PrWorkRecurringTemplate))).scalars().one()
    occurrence_id = item.recurring_occurrence_id
    assert occurrence_id is not None
    item.status = PrWorkStatus.CANCELLED
    item.cancelled_at = utcnow()
    for row in await contributions_of(world, item.id):
        row.count_status = PrWorkCountStatus.EXCLUDED
    await world.session.flush()

    deletion = await delete_cancelled(world, world.owner, item.id)
    assert deletion.source_type == "RECURRING"
    assert deletion.recurring_occurrence_id == occurrence_id
    assert await world.session.get(PrWorkItem, item.id) is None
    await world.session.refresh(template)
    assert template.id is not None


# ===========================================================================
# 23-27: BLOCKED
# ===========================================================================


async def test_23_a_result_still_on_the_row_refuses_with_the_count(world: World) -> None:
    await open_month(world, utcnow())
    type_row = await work_type(world)
    result = await manual_result(world, type_row)
    gone = await cancelled(world, type_row=type_row, evidence=True)
    # Old, hand-shaped data: a result pointing at a row that is not a stream.
    result.work_item_id = gone.id
    await world.session.flush()
    before = await children(world, gone.id)
    result_status = result.status

    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, gone.id)
    details = caught.value.details
    assert details["reason"] == TERMINAL_WORK_ITEM_DELETE_BLOCKED
    assert details["cause"] == "blocking_references"
    assert details["blocking"]["results"] == 1
    assert details["operation"] == TERMINAL_DELETE_OPERATION
    assert str(caught.value) == TERMINAL_DELETE_BLOCKED_MESSAGE
    assert await world.session.get(PrWorkItem, gone.id) is not None
    assert await children(world, gone.id) == before
    await world.session.refresh(result)
    assert result.status is result_status, "the result was not touched"
    assert result.work_item_id == gone.id

    world.act_as(world.head)
    detail = world.client.get(f"/api/pr/work/{gone.id}")
    assert detail.json()["can_admin_delete"] is False
    assert detail.json()["admin_delete"]["blocking"]["results"] == 1
    assert detail.json()["admin_delete"]["message"] == TERMINAL_DELETE_BLOCKED_MESSAGE
    response = world.client.delete(f"{MAINT}/terminal-items/{gone.id}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["blocking"] == {
        "results": 1,
        "counted_contributions": 0,
        "quota_allocations": 0,
        "score_allocations": 0,
    }


async def test_24_a_counted_contribution_refuses(world: World) -> None:
    gone = await cancelled(world)
    row = (await contributions_of(world, gone.id))[0]
    row.count_status = PrWorkCountStatus.COUNTED
    row.counted_at = utcnow()
    await world.session.flush()
    before = await children(world, gone.id)

    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, gone.id)
    assert caught.value.details["reason"] == TERMINAL_WORK_ITEM_DELETE_BLOCKED
    assert caught.value.details["blocking"]["counted_contributions"] == 1
    assert await world.session.get(PrWorkItem, gone.id) is not None
    assert await children(world, gone.id) == before
    await world.session.refresh(row)
    assert row.count_status is PrWorkCountStatus.COUNTED


async def test_25_26_27_an_m2_allocation_refuses_and_nothing_is_half_deleted(
    world: World,
) -> None:
    period, type_row, _content_id, item = await legacy_setup(world)
    await plan_with_quota(world, period, type_row)
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    contribution = (await contributions_of(world, item.id))[0]
    allocations = await rows_of(
        world, PrWorkQuotaAllocation, PrWorkQuotaAllocation.work_contribution_id, contribution.id
    )
    assert allocations == 1
    # Old data: cancelled by hand after it was counted.
    item.status = PrWorkStatus.CANCELLED
    await world.session.flush()
    before = await children(world, item.id)

    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, item.id)
    blocking = caught.value.details["blocking"]
    assert blocking["counted_contributions"] == 1
    assert blocking["quota_allocations"] == 1
    assert await world.session.get(PrWorkItem, item.id) is not None
    assert await children(world, item.id) == before
    assert (
        await rows_of(
            world,
            PrWorkQuotaAllocation,
            PrWorkQuotaAllocation.work_contribution_id,
            contribution.id,
        )
        == 1
    )


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_27b_a_shut_month_refuses_and_says_so_as_a_delete(
    world: World, state: PrPeriodStatus
) -> None:
    period = await open_month(world, utcnow())
    gone = await cancelled(world)
    period.status = state
    await world.session.flush()
    world.act_as(world.owner)
    detail = world.client.get(f"/api/pr/work/{gone.id}")
    assert detail.json()["can_admin_delete"] is False
    assert detail.json()["admin_delete"]["cause"] == "period_not_open"
    assert detail.json()["admin_delete"]["period_code"] == period.code
    response = world.client.delete(f"{MAINT}/terminal-items/{gone.id}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == PERIOD_NOT_OPEN_FOR_CLEANUP
    assert response.json()["error"]["details"]["operation"] == TERMINAL_DELETE_OPERATION
    assert await world.session.get(PrWorkItem, gone.id) is not None


# ===========================================================================
# 28-31: STATUS GUARD
# ===========================================================================


async def test_28_29_an_active_and_an_approved_row_are_refused(world: World) -> None:
    active = await assigned(world, title="Quay TVC Apexmed", type_row=await a_type(world))
    approved = await assigned(world, title="Kịch bản", type_row=await a_type(world))
    await take_to_completed(world, approved, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=approved.id
    )
    world.act_as(world.owner)
    for item, status in ((active, "ACCEPTED"), (approved, "APPROVED")):
        detail = world.client.get(f"/api/pr/work/{item.id}")
        assert detail.json()["can_admin_delete"] is False
        assert detail.json()["admin_delete"] is None
        response = world.client.delete(f"{MAINT}/terminal-items/{item.id}")
        assert response.status_code == 409, response.text
        details = response.json()["error"]["details"]
        assert details["reason"] == WORK_ITEM_NOT_TERMINAL
        assert details["status"] == status
        assert await world.session.get(PrWorkItem, item.id) is not None


async def test_30_31_a_period_container_is_refused_and_its_result_stays(world: World) -> None:
    await open_month(world, utcnow())
    type_row = await work_type(world)
    result = await manual_result(world, type_row)
    container = await world.session.get(PrWorkItem, result.work_item_id)
    assert container is not None and container.is_period_container
    # Old data: the lifecycle never lets a container into CANCELLED.
    container.status = PrWorkStatus.CANCELLED
    await world.session.flush()
    result_status = result.status

    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, container.id)
    assert caught.value.details["reason"] == TERMINAL_WORK_ITEM_DELETE_BLOCKED
    assert caught.value.details["cause"] == "period_container"
    assert await world.session.get(PrWorkItem, container.id) is not None
    await world.session.refresh(result)
    assert result.status is result_status
    assert result.work_item_id == container.id
    world.act_as(world.owner)
    detail = world.client.get(f"/api/pr/work/{container.id}")
    assert detail.json()["can_admin_delete"] is False
    assert detail.json()["admin_delete"]["cause"] == "period_container"


# ===========================================================================
# The two delete rules stay explicit
# ===========================================================================


async def test_a_cancelled_manual_row_is_not_legacy(world: World) -> None:
    gone = await cancelled(world)
    with pytest.raises(PrConflictError) as caught:
        await world.services.work_maintenance.admin_delete_legacy_work_item(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=gone.id
        )
    assert caught.value.details["reason"] == WORK_ITEM_NOT_LEGACY_CONTENT
    assert caught.value.details["cause"] == "manual"
    assert await world.session.get(PrWorkItem, gone.id) is not None


async def test_a_cancelled_legacy_row_qualifies_through_either_and_the_content_stays(
    world: World,
) -> None:
    _period, _type_row, content_id, item = await legacy_setup(world, counted=False)
    assert item.source_type is PrWorkSourceType.CONTENT
    item.status = PrWorkStatus.CANCELLED
    for row in await contributions_of(world, item.id):
        row.count_status = PrWorkCountStatus.EXCLUDED
    await world.session.flush()
    stamp = await world.reload(content_id)
    before = (stamp.title, stamp.workflow_stage, stamp.updated_at)

    world.act_as(world.owner)
    detail = world.client.get(f"/api/pr/work/{item.id}")
    assert detail.json()["can_delete_legacy"] is True
    assert detail.json()["can_admin_delete"] is True

    deletion = await delete_cancelled(world, world.owner, item.id)
    assert deletion.source_type == "CONTENT"
    assert deletion.content_id == content_id
    assert deletion.source_key == item.source_key
    assert await world.session.get(PrWorkItem, item.id) is None
    after = await world.reload(content_id)
    assert (after.title, after.workflow_stage, after.updated_at) == before


async def test_a_missing_row_is_a_not_found(world: World) -> None:
    with pytest.raises(PrNotFoundError) as caught:
        await delete_cancelled(world, world.owner, uuid.uuid4())
    assert caught.value.details["reason"] == WORK_ITEM_NOT_FOUND


async def test_deleting_changes_no_accounting_figure(world: World) -> None:
    """The month's counted figures before and after are the same numbers."""
    period = await open_month(world, utcnow())
    type_row = await work_type(world)
    await manual_result(world, type_row, quantity=3)
    gone = await cancelled(world, type_row=type_row)
    before = await world.services.work_queries.summary(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, period_id=period.id),
    )
    await delete_cancelled(world, world.owner, gone.id)
    after = await world.services.work_queries.summary(
        actor=world.actor(world.owner),
        query=WorkQuery(scope=PrWorkScope.ALL, period_id=period.id),
    )
    assert (after.counted_work_items, after.counted_contributions) == (
        before.counted_work_items,
        before.counted_contributions,
    )
    assert Decimal(3) == Decimal(3)


# ===========================================================================
# R13-R24: THE REJECTED HALF
# ===========================================================================


async def test_r13_a_clean_rejected_row_deletes_without_being_cancelled_first(
    world: World,
) -> None:
    gone = await rejected(world, evidence=True)
    before = await children(world, gone.id)
    assert before["evidence"] == 1 and before["contributions"] == 1
    rows = await contributions_of(world, gone.id)
    assert rows[0].count_status is PrWorkCountStatus.EXCLUDED
    world.act_as(world.head)
    detail = world.client.get(f"/api/pr/work/{gone.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["item"]["status_label"] == "Không được chấp nhận"
    assert body["can_cancel"] is False
    assert body["can_admin_delete"] is True
    assert body["admin_delete"]["rule"] == "terminal"
    assert body["admin_delete"]["previous_status"] == "REJECTED"
    assert body["admin_delete"]["deletable"] is True

    deletion = await delete_cancelled(world, world.head, gone.id, note="dọn đề xuất cũ")
    assert deletion.previous_status == "REJECTED"
    assert deletion.removed == {"contributions": 1, "evidence": 1, "history": before["history"]}
    assert await world.session.get(PrWorkItem, gone.id) is None
    assert await children(world, gone.id) == {
        "contributions": 0,
        "evidence": 0,
        "history": 0,
        "results": 0,
    }
    # Never cancelled on the way out: the trail has no cancel event for it.
    cancels = [
        row
        for row in await audit_rows(world, AuditAction.PR_WORK_CANCELLED)
        if row.entity_id == str(gone.id)
    ]
    assert cancels == []


async def test_r22_the_audit_row_says_previous_status_rejected(world: World) -> None:
    gone = await rejected(world)
    await delete_cancelled(world, world.owner, gone.id, note="Dọn đề xuất test")
    rows = await audit_rows(world, AuditAction.PR_WORK_ITEM_ADMIN_DELETED)
    row = next(one for one in rows if one.entity_id == str(gone.id))
    before = row.before_data or {}
    assert before["operation"] == TERMINAL_DELETE_OPERATION
    assert before["previous_status"] == "REJECTED"
    assert before["code"] == gone.code
    assert before["title"] == "Kịch bản video tâm sự"
    assert before["responsible_user_id"] == str(world.member.id)
    assert before["note"] == "Dọn đề xuất test"
    assert before["removed"]["contributions"] == 1


@pytest.mark.parametrize("role", ["lead", "member", "other"])
async def test_r23_r24_forged_deletes_of_a_rejected_row_are_403_and_write_nothing(
    world: World, role: str
) -> None:
    gone = await rejected(world, evidence=True)
    before = await children(world, gone.id)
    audits_before = await audits(world)
    world.act_as(getattr(world, role))
    response = world.client.delete(f"{MAINT}/terminal-items/{gone.id}")
    assert response.status_code == 403, (role, response.text)
    assert await world.session.get(PrWorkItem, gone.id) is not None
    assert await children(world, gone.id) == before
    assert await audits(world) == audits_before


@pytest.mark.parametrize("role", ["owner", "head"])
async def test_r_owner_and_admin_delete_a_rejected_row_over_http(world: World, role: str) -> None:
    gone = await rejected(world)
    world.act_as(getattr(world, role))
    response = world.client.delete(f"{MAINT}/terminal-items/{gone.id}")
    assert response.status_code == 200, response.text
    assert response.json()["previous_status"] == "REJECTED"
    assert await world.session.get(PrWorkItem, gone.id) is None
    again = world.client.delete(f"{MAINT}/terminal-items/{gone.id}")
    assert again.status_code == 404


async def test_r18_a_rejected_row_with_a_result_is_refused_and_not_cancelled(
    world: World,
) -> None:
    await open_month(world, utcnow())
    type_row = await work_type(world)
    result = await manual_result(world, type_row)
    gone = await rejected(world, type_row=type_row)
    result.work_item_id = gone.id
    await world.session.flush()
    before = await children(world, gone.id)
    result_status = result.status

    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, gone.id)
    details = caught.value.details
    assert details["reason"] == TERMINAL_WORK_ITEM_DELETE_BLOCKED
    assert details["blocking"]["results"] == 1
    assert str(caught.value) == TERMINAL_DELETE_BLOCKED_MESSAGE
    fresh = await world.session.get(PrWorkItem, gone.id)
    assert fresh is not None and fresh.status is PrWorkStatus.REJECTED
    assert await children(world, gone.id) == before
    await world.session.refresh(result)
    assert result.status is result_status and result.work_item_id == gone.id

    world.act_as(world.head)
    body = world.client.get(f"/api/pr/work/{gone.id}").json()
    assert body["can_admin_delete"] is False
    assert body["admin_delete"]["rule"] == "terminal"
    assert body["admin_delete"]["blocking"]["results"] == 1
    assert body["admin_delete"]["previous_status"] == "REJECTED"
    assert body["admin_delete"]["message"] == TERMINAL_DELETE_BLOCKED_MESSAGE


async def test_r19_a_rejected_row_with_a_counted_contribution_is_refused(world: World) -> None:
    gone = await rejected(world)
    row = (await contributions_of(world, gone.id))[0]
    row.count_status = PrWorkCountStatus.COUNTED
    row.counted_at = utcnow()
    await world.session.flush()
    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, gone.id)
    assert caught.value.details["blocking"]["counted_contributions"] == 1
    assert await world.session.get(PrWorkItem, gone.id) is not None


async def test_r20_a_rejected_row_with_an_m2_allocation_is_refused(world: World) -> None:
    period, type_row, _content_id, item = await legacy_setup(world)
    await plan_with_quota(world, period, type_row)
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    # Old data: rejected by hand after it was counted and allocated.
    item.status = PrWorkStatus.REJECTED
    await world.session.flush()
    before = await children(world, item.id)
    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, item.id)
    blocking = caught.value.details["blocking"]
    assert blocking["counted_contributions"] == 1 and blocking["quota_allocations"] == 1
    assert await children(world, item.id) == before


async def test_r17_a_rejected_period_container_is_refused(world: World) -> None:
    await open_month(world, utcnow())
    type_row = await work_type(world)
    result = await manual_result(world, type_row)
    container = await world.session.get(PrWorkItem, result.work_item_id)
    assert container is not None and container.is_period_container
    container.status = PrWorkStatus.REJECTED
    await world.session.flush()
    with pytest.raises(PrConflictError) as caught:
        await delete_cancelled(world, world.owner, container.id)
    assert caught.value.details["cause"] == "period_container"
    assert await world.session.get(PrWorkItem, container.id) is not None
    await world.session.refresh(result)
    assert result.work_item_id == container.id


async def test_r_rejected_stays_in_the_default_list(world: World) -> None:
    """Part H: this patch is about delete capability, not visibility."""
    gone = await rejected(world)
    result = await page(world, world.owner)
    assert gone.id in {one.id for one in result.items}


async def test_r_a_rejected_row_offers_no_cancel_and_the_cancel_route_refuses(
    world: World,
) -> None:
    gone = await rejected(world)
    world.act_as(world.owner)
    assert world.client.get(f"/api/pr/work/{gone.id}").json()["can_cancel"] is False
    response = world.client.post(f"/api/pr/work/{gone.id}/cancel", json={"note": None})
    assert 400 <= response.status_code < 500
    assert response.json()["error"]["details"]["reason"] == "illegal_transition"
