"""Final Content → Work semantics: four separate operations, one projector.

* **Normal automatic projection** - a content transition queues a request,
  the worker settles it, a modern result appears. Nobody clicks anything.
* **Manual single-content sync** - *Đồng bộ lại từ Nội dung*: the same
  projector, run now, for one piece, by ADMIN/OWNER.
* **Legacy work item delete** - removes the old item-grain row and calls no
  projector.
* **Modern result admin remove** - an exclusion that calls no projector, and
  **does not poison the source**: the next projection, manual or automatic,
  re-evaluates the content and restores the result if it still qualifies.

Numbered against the task's matrix. Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_content_work_projector import request_content_work_projection
from meobot.core.time import utcnow
from meobot.db.models.pr_content_work import PrContentWorkProjection
from meobot.db.models.pr_work import PrWorkItem
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.content_work import (
    PrContentWorkKind,
    PrContentWorkOutcome,
    PrContentWorkProjectionStatus,
    content_work_source_entity,
    content_work_source_key,
)
from meobot.domain.pr.models import PrContentType
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkSourceType
from meobot.domain.pr.work_results import PrWorkResultSource
from tests.unit.test_pr_content_work_projection import (
    approved_content,
    content_results,
    contributions_of,
    grant,
    open_month,
    outcome_for,
    project,
    rule,
    source_result,
    work_type,
)
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_legacy_delete import delete_legacy, legacy_item
from tests.unit.test_pr_work_maintenance import container_of, count, counted_content

pytestmark = pytest.mark.asyncio

KIND = PrContentWorkKind.CONTENT_CREATION
TYPE = PrContentType.SHORT_VIDEO_SCRIPT
PROJECT = "/api/pr/work/content/{}/project"


# ===========================================================================
# Helpers
# ===========================================================================


def worker_actor() -> Actor:
    """What the beat task runs as - see ``TaskContext.system_actor``. No user id."""
    return Actor(
        user_id=None,
        telegram_user_id=None,
        telegram_username=None,
        full_name="meobot-worker",
        role=Role.OWNER,
        active=True,
        is_bootstrap_owner=True,
    )


async def queue_row(world: World, content_id: uuid.UUID) -> PrContentWorkProjection | None:
    return (
        (
            await world.session.execute(
                select(PrContentWorkProjection).where(
                    PrContentWorkProjection.content_id == content_id
                )
            )
        )
        .scalars()
        .one_or_none()
    )


async def worker_settles(world: World, content_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """The worker's path: claim the queued row, project as the system actor, settle."""
    row = await queue_row(world, content_id)
    assert row is not None and row.status is PrContentWorkProjectionStatus.PENDING
    row.status = PrContentWorkProjectionStatus.RUNNING
    await world.session.flush()
    return await world.services.content_work.settle(
        actor=worker_actor(), request_id=world.request_id, content_id=content_id
    )


async def admin_remove(world: World, result: PrWorkResult) -> PrWorkResult:
    return await world.services.work_maintenance.admin_remove_result(
        actor=world.actor(world.head), request_id=world.request_id, result_id=result.id
    )


async def manual_sync(world: World, content_id: uuid.UUID, user=None):  # type: ignore[no-untyped-def]
    """*Đồng bộ lại từ Nội dung* over HTTP, as the person given (owner by default)."""
    world.act_as(user or world.owner)
    return world.client.post(PROJECT.format(content_id))


async def results_named(world: World, content_id: uuid.UUID) -> list[PrWorkResult]:
    return list(
        (
            await world.session.execute(
                select(PrWorkResult).where(
                    PrWorkResult.source_type == PrWorkResultSource.CONTENT,
                    PrWorkResult.source_key == content_work_source_key(KIND, content_id),
                )
            )
        ).scalars()
    )


async def mapped_month(world: World, *, code: str = "SHORT_SCRIPT"):  # type: ignore[no-untyped-def]
    period = await open_month(world, utcnow())
    type_row = await work_type(world, code=code, name=f"Kịch bản {code}")
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    return period, type_row


# ===========================================================================
# 1-4: NORMAL AUTOMATIC PROJECTION IS STILL THE NORMAL PATH
# ===========================================================================


async def test_01_04_a_content_transition_queues_a_projection_the_worker_settles(
    world: World,
) -> None:
    await mapped_month(world)
    content_id = await approved_content(world, content_type=TYPE)
    # 1. the transition queued it - nobody called reconcile or project.
    row = await queue_row(world, content_id)
    assert row is not None and row.status is PrContentWorkProjectionStatus.PENDING
    assert await source_result(world, content_id, KIND) is None
    # 2-4. the worker, as the system actor with no user id and no capability
    # check, creates the modern result.
    report = await worker_settles(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    result = await source_result(world, content_id, KIND)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED
    container = await world.session.get(PrWorkItem, result.work_item_id)
    assert container is not None and container.is_period_container
    assert container.source_type is PrWorkSourceType.MANUAL, "a container is not legacy"
    row = await queue_row(world, content_id)
    assert row is not None and row.status is PrContentWorkProjectionStatus.SETTLED
    assert row.last_outcome is PrContentWorkOutcome.PROJECTED


# ===========================================================================
# 14-20: LEGACY DELETE, THEN MANUAL SYNC
# ===========================================================================


async def test_14_20_after_a_legacy_delete_the_manual_sync_records_a_modern_result(
    world: World,
) -> None:
    period, old_type = await mapped_month(world, code="OLD_TYPE")
    content_id = await approved_content(world, content_type=TYPE)
    legacy = await legacy_item(world, content_id=content_id, type_row=old_type)
    legacy_id, legacy_key = legacy.id, legacy.source_key
    # The mapping has since been corrected to another type.
    new_type = await work_type(world, code="NEW_TYPE", name="Kịch bản mới")
    await rule(world, kind=KIND, type_row=new_type, content_type=TYPE)

    await delete_legacy(world, world.head, legacy_id)
    assert await source_result(world, content_id, KIND) is None, "the delete synced nothing"

    response = await manual_sync(world, content_id)
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "PROJECTED"

    result = await source_result(world, content_id, KIND)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED
    container = await world.session.get(PrWorkItem, result.work_item_id)
    assert container is not None
    assert container.work_type_id == new_type.id, "current mapping, not the historical type"
    assert container.reporting_period_id == period.id
    assert container.quantity == Decimal("1.00")
    # No legacy row came back: nothing keyed on the old source key in the items table.
    assert (
        await count(
            world,
            select(func.count())
            .select_from(PrWorkItem)
            .where(PrWorkItem.source_type == PrWorkSourceType.CONTENT),
        )
        == 0
    )
    assert legacy_key == result.source_key, "same milestone, new grain"


# ===========================================================================
# 21-32: MODERN REMOVE, THEN MANUAL RESYNC
# ===========================================================================


async def test_21_32_an_admin_removed_result_returns_on_manual_sync(world: World) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    container = await container_of(world, result)
    assert container.quantity == Decimal("1.00")
    queue_before = (await queue_row(world, content_id)).status  # type: ignore[union-attr]

    removed = await admin_remove(world, result)
    assert removed.status is PrWorkCountStatus.EXCLUDED
    container = await container_of(world, result)
    assert container.quantity == Decimal("0.00"), "actual dropped"
    # 24. the removal itself synced nothing and queued nothing.
    assert (await queue_row(world, content_id)).status is queue_before  # type: ignore[union-attr]
    assert (await source_result(world, content_id, KIND)).status is PrWorkCountStatus.EXCLUDED  # type: ignore[union-attr]

    response = await manual_sync(world, content_id, world.head)
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "PROJECTED"
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.COUNTED
    assert result.excluded_at is None and result.excluded_reason is None
    container = await container_of(world, result)
    assert container.quantity == Decimal("1.00"), "actual back"
    assert len(await results_named(world, content_id)) == 1, "one logical result"
    rows = await contributions_of(world, container.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED


async def test_25_the_content_is_untouched_by_the_removal(world: World) -> None:
    from tests.unit.test_pr_work_legacy_delete import content_fingerprint

    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    before = await content_fingerprint(world, content_id)
    await admin_remove(world, result)
    assert await content_fingerprint(world, content_id) == before


# ===========================================================================
# 33-37: MODERN REMOVE, THEN THE NORMAL WORKER BRINGS IT BACK
# ===========================================================================


async def test_33_37_a_later_content_event_lets_the_worker_restore_the_result(
    world: World,
) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    await admin_remove(world, result)
    assert (await container_of(world, result)).quantity == Decimal("0.00")

    # A legitimate content event - what a transition does inside its own
    # transaction. No person, no capability, no maintenance screen.
    await request_content_work_projection(world.session, content_id)
    report = await worker_settles(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.COUNTED
    assert (await container_of(world, result)).quantity == Decimal("1.00")
    assert len(await results_named(world, content_id)) == 1


# ===========================================================================
# 38-41: A SOURCE THAT NO LONGER QUALIFIES DOES NOT COME BACK
# ===========================================================================


async def test_38_41_manual_sync_respects_a_withdrawn_approval(world: World) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    await admin_remove(world, result)
    # The head undoes the approval: the piece no longer qualifies.
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )

    response = await manual_sync(world, content_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] != "PROJECTED"
    assert body["outcome"] in {"NOT_QUALIFIED", "REVERSED"}
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.EXCLUDED, "not restored"
    assert (await container_of(world, result)).quantity == Decimal("0.00")
    assert len(await results_named(world, content_id)) == 1


# ===========================================================================
# 42-47: THE CURRENT MAPPING WINS
# ===========================================================================


async def test_42_47_resync_after_a_mapping_change_files_under_the_new_type(
    world: World,
) -> None:
    _period, type_a = await mapped_month(world, code="TYPE_A")
    content_id, result = await counted_content(world, content_type=TYPE)
    container_a = await container_of(world, result)
    assert container_a.work_type_id == type_a.id
    type_b = await work_type(world, code="TYPE_B", name="Loại B")
    await rule(world, kind=KIND, type_row=type_b, content_type=TYPE)
    await admin_remove(world, result)

    response = await manual_sync(world, content_id)
    assert response.status_code == 200 and response.json()["outcome"] == "PROJECTED"
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.COUNTED
    container_b = await container_of(world, result)
    assert container_b.work_type_id == type_b.id, "current result represents B"
    assert container_b.quantity == Decimal("1.00")
    await world.session.refresh(container_a)
    assert container_a.quantity == Decimal("0.00"), "A holds nothing and was not recreated"
    assert len(await results_named(world, content_id)) == 1


# ===========================================================================
# 48-51: IDEMPOTENCY
# ===========================================================================


async def test_48_51_five_manual_syncs_converge_on_one_result(world: World) -> None:
    await mapped_month(world)
    content_id = await approved_content(world, content_type=TYPE)
    outcomes = []
    for _ in range(5):
        response = await manual_sync(world, content_id)
        assert response.status_code == 200, response.text
        outcomes.append(response.json()["outcome"])
    assert outcomes[0] == "PROJECTED" and set(outcomes[1:]) == {"UNCHANGED"}
    rows = await results_named(world, content_id)
    assert len(rows) == 1 and rows[0].quantity == Decimal("1.00")
    assert (await container_of(world, rows[0])).quantity == Decimal("1.00")
    assert len(await content_results(world, content_id)) == 1


# ===========================================================================
# 52-56: AUTHORIZATION
# ===========================================================================


@pytest.mark.parametrize("role", ["lead", "member", "other"])
async def test_54_55_team_lead_and_employee_cannot_run_the_manual_sync(
    world: World, role: str
) -> None:
    await mapped_month(world)
    content_id = await approved_content(world, content_type=TYPE)
    response = await manual_sync(world, content_id, getattr(world, role))
    assert response.status_code == 403, (role, response.text)
    assert await source_result(world, content_id, KIND) is None, "nothing projected"


@pytest.mark.parametrize("role", ["owner", "head"])
async def test_52_53_owner_and_admin_can_run_the_manual_sync(world: World, role: str) -> None:
    await mapped_month(world)
    content_id = await approved_content(world, content_type=TYPE)
    response = await manual_sync(world, content_id, getattr(world, role))
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "PROJECTED"
    assert (await source_result(world, content_id, KIND)).status is PrWorkCountStatus.COUNTED  # type: ignore[union-attr]


async def test_56_the_worker_projects_with_no_user_and_no_capability(world: World) -> None:
    await mapped_month(world)
    content_id = await approved_content(world, content_type=TYPE)
    report = await worker_settles(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED


# ===========================================================================
# EXCLUDED BUT PENDING: THE TRUTH IS "PENDING", NOT "GONE"
# ===========================================================================


async def test_x1_a_removed_self_approved_result_returns_to_pending_for_a_validator(
    world: World,
) -> None:
    """The head who approved the script is also its writer, so the result is
    pending an independent validator. An administrator removes it. The next
    projection puts it back to PENDING - not COUNTED, and not stuck EXCLUDED -
    so a validator can count it exactly as before."""
    from meobot.domain.pr.policy import PrCapability

    await mapped_month(world)
    await grant(world, world.member, PrCapability.PR_HEAD_REVIEW)
    content_id = await approved_content(world, writer=world.member, head=world.member)
    await project(world, content_id)
    result = await source_result(world, content_id, KIND)
    assert result is not None and result.status is PrWorkCountStatus.PENDING
    await admin_remove(world, result)
    assert result.status is PrWorkCountStatus.EXCLUDED

    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PENDING_VALIDATION
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.PENDING
    assert result.excluded_at is None and result.excluded_reason is None
    assert (await container_of(world, result)).quantity == Decimal("0.00"), "nothing counted"
    # And a validator who is not the subject can now count it.
    await world.services.work_results.validate_results(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=result.work_item_id
    )
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.COUNTED
    assert (await container_of(world, result)).quantity == Decimal("1.00")


async def test_x2_a_result_the_source_withdrew_stays_out_until_the_source_returns(
    world: World,
) -> None:
    """Part M. Not every exclusion returns: one the *source* caused stays out
    while the source says so, and comes back only when the source does."""
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.EXCLUDED
    for _ in range(3):
        response = await manual_sync(world, content_id)
        assert response.status_code == 200 and response.json()["outcome"] != "PROJECTED"
    await world.session.refresh(result)
    assert result.status is PrWorkCountStatus.EXCLUDED


async def test_x3_the_result_read_model_names_the_content_it_came_from(world: World) -> None:
    await mapped_month(world)
    content_id, result = await counted_content(world, content_type=TYPE)
    assert content_work_source_entity(result.source_key) == content_id
    assert content_work_source_entity("manual") is None
    assert content_work_source_entity(None) is None
    world.act_as(world.owner)
    detail = world.client.get(f"/api/pr/work/{result.work_item_id}")
    assert detail.status_code == 200, detail.text
    rows = detail.json()["results"]
    assert len(rows) == 1 and rows[0]["content_id"] == str(content_id)
