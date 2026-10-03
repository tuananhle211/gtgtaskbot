"""User-initiated deletion of one legacy (item-grain) content work item.

The pre-``0039`` projector wrote one work item per content milestone; the
current one writes one result into the contributor's monthly container. The
old rows are still valid history, and an administrator who opens one and
decides it should go needs a way to remove *that row* - and nothing else.

What this suite pins, numbered against the task:

* **16-21 identification** is provenance, never text: ``source_type =
  CONTENT`` on a row that is not a period container. A manual job, a
  recurring job, a container and a modern result all classify ``False``,
  and a legacy row titled anything at all classifies ``True``;
* **9-15 authorization** is ``PR_WORK_CONFIGURE`` on the service, proved
  over HTTP for the two roles that lack it - and a refused call writes
  nothing;
* **22-35 the delete** removes the row and only what it owns; the content,
  its lifecycle and its approvals are byte-for-byte what they were, a modern
  result for the same source key is untouched, and so is every other kind of
  work in the month;
* **29-30, 41** nothing is put back: no result, no container, no queue row,
  no compensation - the actual drops and stays down until somebody runs the
  sync themselves;
* **36-38** M2 and the stored performance figure follow the drop without any
  formula changing;
* **42-45** an OPEN month accepts; CLOSED, LOCKED and a finalised figure
  refuse with a structured 4xx before anything is touched.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_maintenance_service import (
    PERIOD_NOT_OPEN_FOR_CLEANUP,
    PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE,
    WORK_ITEM_NOT_FOUND,
    WORK_ITEM_NOT_LEGACY_CONTENT,
)
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_content_work import PrContentWorkProjection
from meobot.db.models.pr_performance import PrPerformanceResult, PrWorkScoreAllocation
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
    PrWorkType,
)
from meobot.db.models.pr_work_quota import PrWorkQuotaAllocation
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.audit.models import AuditAction
from meobot.domain.pr.content_work import (
    PrContentWorkKind,
    content_work_source_key,
    is_legacy_content_work_item,
)
from meobot.domain.pr.errors import PrConflictError, PrNotFoundError
from meobot.domain.pr.models import PrContentType
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkCountStatus, PrWorkSourceType, PrWorkStatus
from meobot.domain.pr.work_quota import PrWorkQuotaStatus
from meobot.domain.pr.work_results import PrWorkResultSource
from tests.unit.test_pr_content_work_projection import (
    approved_content,
    contributions_of,
    open_month,
    rule,
    source_result,
    work_type,
)
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_maintenance import (
    audit_rows,
    count,
    counted_content,
    manual_result,
    recurring_items,
)

pytestmark = pytest.mark.asyncio

KIND = PrContentWorkKind.CONTENT_CREATION
TYPE = PrContentType.SHORT_VIDEO_SCRIPT
MAINT = "/api/pr/work/maintenance"


# ===========================================================================
# Helpers
# ===========================================================================


async def month(world: World):  # type: ignore[no-untyped-def]
    return await open_month(world, utcnow())


async def legacy_item(
    world: World,
    *,
    content_id: uuid.UUID,
    type_row: PrWorkType,
    kind: PrContentWorkKind = KIND,
    counted: bool = True,
    title: str = "Nội dung: Một kiểu trưởng thành rất buồn",
    at: datetime | None = None,
) -> PrWorkItem:
    """The row the pre-``0039`` projector wrote, through the service it used.

    Indistinguishable from a real one: ``source_type = CONTENT``, the semantic
    source key, one ``PRIMARY`` contribution, ``COMPLETED`` - and, when
    ``counted``, ``APPROVED`` with the contribution ``COUNTED`` on the head's
    instant, exactly as ``count_source_work`` writes it.
    """
    when = at or utcnow()
    item = await world.services.work.create_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        source_key=content_work_source_key(kind, content_id),
        work_type_id=type_row.id,
        title=title,
        contributor_user_id=world.member.id,
        content_id=content_id,
        occurred_at=when,
    )
    if counted:
        await world.services.work.count_source_work(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            work_item_id=item.id,
            validated_by_user_id=world.head.id,
            effective_validation_at=when,
        )
    await world.session.refresh(item)
    return item


async def delete_legacy(world: World, actor_user, item_id: uuid.UUID, note=None):  # type: ignore[no-untyped-def]
    return await world.services.work_maintenance.admin_delete_legacy_work_item(
        actor=world.actor(actor_user), request_id=world.request_id, work_item_id=item_id, note=note
    )


async def legacy_setup(world: World, *, counted: bool = True):  # type: ignore[no-untyped-def]
    """An open month, a mapped type, an approved piece and its legacy row."""
    period = await month(world)
    type_row = await work_type(world, code="TINY_SCRIPT", name="Kịch bản siêu ngắn")
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    content_id = await approved_content(world, content_type=TYPE)
    item = await legacy_item(world, content_id=content_id, type_row=type_row, counted=counted)
    return period, type_row, content_id, item


async def content_fingerprint(world: World, content_id: uuid.UUID) -> dict[str, object]:
    """Everything about the content the delete must leave alone, as one value."""
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    await world.session.refresh(content)
    approvals = await count(
        world,
        select(func.count())
        .select_from(PrApprovalEvent)
        .where(PrApprovalEvent.content_id == content_id),
    )
    transitions = await count(
        world,
        select(func.count())
        .select_from(PrContentTransitionEvent)
        .where(PrContentTransitionEvent.content_id == content_id),
    )
    return {
        "title": content.title,
        "stage": content.workflow_stage,
        "status": getattr(content, "status", None),
        "content_type": content.content_type,
        "updated_at": content.updated_at,
        "approvals": approvals,
        "transitions": transitions,
    }


async def projection_rows(world: World, content_id: uuid.UUID) -> list[PrContentWorkProjection]:
    return list(
        (
            await world.session.execute(
                select(PrContentWorkProjection).where(
                    PrContentWorkProjection.content_id == content_id
                )
            )
        ).scalars()
    )


async def rows_of(world: World, model, column, value) -> int:  # type: ignore[no-untyped-def]
    return await count(world, select(func.count()).select_from(model).where(column == value))


async def plan_with_quota(world: World, period, type_row: PrWorkType) -> None:  # type: ignore[no-untyped-def]
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


# ===========================================================================
# 16-21: IDENTIFICATION IS PROVENANCE
# ===========================================================================


async def test_16_a_real_legacy_content_work_item_classifies_true(world: World) -> None:
    _period, _type_row, _content_id, item = await legacy_setup(world)
    assert item.source_type is PrWorkSourceType.CONTENT
    assert item.reporting_period_id is None
    assert is_legacy_content_work_item(item) is True


async def test_17_a_manual_work_item_classifies_false(world: World) -> None:
    from meobot.application.pr_work_service import CreateWorkCommand

    type_row = await work_type(world, code="SHOOT", name="Quay")
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            title="Nội dung: trông như việc từ Nội dung nhưng do người nhập",
            work_type_id=type_row.id,
            contributor_user_ids=(world.member.id,),
        ),
    )
    assert item.source_type is PrWorkSourceType.MANUAL
    assert is_legacy_content_work_item(item) is False


async def test_18_recurring_work_classifies_false(world: World) -> None:
    items = await recurring_items(world)
    assert items, "the routine generated a job"
    for item in items:
        assert item.recurring_occurrence_id is not None
        assert is_legacy_content_work_item(item) is False


async def test_19_20_a_modern_container_and_its_content_result_are_not_legacy(
    world: World,
) -> None:
    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    await month(world)
    _content_id, result = await counted_content(world, content_type=TYPE)
    container = await world.session.get(PrWorkItem, result.work_item_id)
    assert container is not None
    assert container.is_period_container
    # The container is opened as MANUAL and keyed on its month, and the
    # content result is a row in another table entirely.
    assert is_legacy_content_work_item(container) is False
    assert result.source_type is PrWorkResultSource.CONTENT
    assert not isinstance(result, PrWorkItem)


async def test_21_no_title_code_or_type_name_heuristic_is_used(world: World) -> None:
    """A legacy row titled nothing like the projector's pattern, under a type
    named nothing like a content type, is still legacy; and a manual row
    dressed up as one is still manual."""
    type_row = await work_type(world, code="MISC", name="Việc khác")
    content_id = await approved_content(world, content_type=TYPE)
    disguised = await legacy_item(
        world, content_id=content_id, type_row=type_row, title="x", counted=False
    )
    assert is_legacy_content_work_item(disguised) is True
    # The predicate reads two columns and nothing else. Flip either and it
    # answers the other way; nothing about the title, code or content id
    # enters into it.
    disguised.reporting_period_id = uuid.uuid4()
    assert is_legacy_content_work_item(disguised) is False
    disguised.reporting_period_id = None
    disguised.source_type = PrWorkSourceType.MANUAL
    assert is_legacy_content_work_item(disguised) is False
    await world.session.rollback()


# ===========================================================================
# 9-15: AUTHORIZATION
# ===========================================================================


@pytest.mark.parametrize("role", ["lead", "member", "other"])
async def test_13_14_15_team_lead_and_employee_are_refused_and_nothing_is_written(
    world: World, role: str
) -> None:
    _period, _type_row, content_id, item = await legacy_setup(world)
    audits_before = await count(world, select(func.count()).select_from(AuditLog))
    world.act_as(getattr(world, role))
    response = world.client.delete(f"{MAINT}/items/{item.id}")
    assert response.status_code == 403, (role, response.text)
    assert await world.session.get(PrWorkItem, item.id) is not None
    rows = await contributions_of(world, item.id)
    assert len(rows) == 1 and rows[0].count_status is PrWorkCountStatus.COUNTED
    assert await count(world, select(func.count()).select_from(AuditLog)) == audits_before
    assert await source_result(world, content_id, KIND) is None, "nothing projected either"


@pytest.mark.parametrize("role", ["owner", "head"])
async def test_09_10_22_23_owner_and_admin_delete_over_http(world: World, role: str) -> None:
    _period, _type_row, _content_id, item = await legacy_setup(world)
    world.act_as(getattr(world, role))
    detail = world.client.get(f"/api/pr/work/{item.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["item"]["is_legacy_content_work"] is True
    assert detail.json()["can_delete_legacy"] is True

    response = world.client.delete(f"{MAINT}/items/{item.id}", params={"note": "dữ liệu cũ"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["work_item_id"] == str(item.id)
    assert body["code"] == item.code
    assert body["results_created"] == 0
    assert body["projection_requested"] is False
    assert body["removed"]["contributions"] == 1
    assert await world.session.get(PrWorkItem, item.id) is None
    # A second delete of the same id is a deterministic not-found.
    again = world.client.delete(f"{MAINT}/items/{item.id}")
    assert again.status_code == 404
    assert again.json()["error"]["details"]["reason"] == WORK_ITEM_NOT_FOUND


@pytest.mark.parametrize(("role", "counted"), [("lead", False), ("member", True)])
async def test_11_12_the_detail_offers_no_delete_to_the_lower_roles(
    world: World, role: str, counted: bool
) -> None:
    # A validator may open finished work waiting on them; the member may open
    # their own. Either way the detail names the row as legacy and offers
    # nobody below ADMIN the delete.
    _period, _type_row, _content_id, item = await legacy_setup(world, counted=counted)
    world.act_as(getattr(world, role))
    detail = world.client.get(f"/api/pr/work/{item.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["item"]["is_legacy_content_work"] is True
    assert detail.json()["can_delete_legacy"] is False


# ===========================================================================
# 22-35: THE DELETE, AND WHAT IT LEAVES ALONE
# ===========================================================================


async def test_24_25_the_row_and_exactly_its_own_children_are_removed(world: World) -> None:
    period, type_row, content_id, item = await legacy_setup(world)
    await plan_with_quota(world, period, type_row)
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    await world.services.work.add_evidence_text(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        text="Link: https://example.com/a",
    )
    contribution = (await contributions_of(world, item.id))[0]
    assert (
        await rows_of(
            world,
            PrWorkQuotaAllocation,
            PrWorkQuotaAllocation.work_contribution_id,
            contribution.id,
        )
        == 1
    )
    history_before = await rows_of(world, PrWorkHistory, PrWorkHistory.work_item_id, item.id)
    assert history_before >= 2

    deletion = await delete_legacy(world, world.head, item.id, note="dọn dữ liệu cũ")

    assert deletion.removed == {
        "contributions": 1,
        "counted_contributions": 1,
        "quota_allocations": 1,
        "score_allocations": 0,
        "evidence": 1,
        "history": history_before,
    }
    assert deletion.period_code == period.code
    assert deletion.responsible_user_id == world.member.id
    assert deletion.content_id == content_id
    assert await world.session.get(PrWorkItem, item.id) is None
    for model, column in (
        (PrWorkContribution, PrWorkContribution.work_item_id),
        (PrWorkEvidence, PrWorkEvidence.work_item_id),
        (PrWorkHistory, PrWorkHistory.work_item_id),
        (PrWorkResult, PrWorkResult.work_item_id),
    ):
        assert await rows_of(world, model, column, item.id) == 0, model.__name__
    assert (
        await rows_of(
            world,
            PrWorkQuotaAllocation,
            PrWorkQuotaAllocation.work_contribution_id,
            contribution.id,
        )
        == 0
    )
    assert (
        await rows_of(
            world,
            PrWorkScoreAllocation,
            PrWorkScoreAllocation.work_contribution_id,
            contribution.id,
        )
        == 0
    )


async def test_26_27_28_the_content_its_lifecycle_and_its_approvals_are_untouched(
    world: World,
) -> None:
    _period, _type_row, content_id, item = await legacy_setup(world)
    before = await content_fingerprint(world, content_id)
    await delete_legacy(world, world.owner, item.id)
    assert await content_fingerprint(world, content_id) == before
    assert before["approvals"] >= 2, "the head approval is still recorded"


async def test_29_30_no_replacement_result_container_or_projection_request(
    world: World,
) -> None:
    """The load-bearing promise: the delete puts nothing back."""
    _period, _type_row, content_id, item = await legacy_setup(world)
    queue_before = [
        (row.status, row.requested_at) for row in await projection_rows(world, content_id)
    ]
    results_before = await count(world, select(func.count()).select_from(PrWorkResult))
    containers_before = await count(
        world,
        select(func.count())
        .select_from(PrWorkItem)
        .where(PrWorkItem.reporting_period_id.is_not(None)),
    )

    deletion = await delete_legacy(world, world.owner, item.id)

    assert deletion.results_created == 0 and deletion.projection_requested is False
    assert await count(world, select(func.count()).select_from(PrWorkResult)) == results_before
    assert (
        await count(
            world,
            select(func.count())
            .select_from(PrWorkItem)
            .where(PrWorkItem.reporting_period_id.is_not(None)),
        )
        == containers_before
    )
    assert await source_result(world, content_id, KIND) is None
    queue_after = [
        (row.status, row.requested_at) for row in await projection_rows(world, content_id)
    ]
    assert queue_after == queue_before, "no request queued, none touched"
    projected = await audit_rows(world, AuditAction.PR_CONTENT_WORK_PROJECTED)
    assert all(row.entity_type != "pr_work_result" for row in projected), "no result audited"


async def test_30b_a_projection_already_queued_by_the_content_is_left_as_it_was(
    world: World,
) -> None:
    """Part J. A request another content event queued is not this
    operation's to cancel, and not this operation's to add to either."""
    _period, _type_row, content_id, item = await legacy_setup(world)
    await world.services.content_work.request(content_id)
    rows = await projection_rows(world, content_id)
    assert len(rows) == 1
    stamp = (rows[0].status, rows[0].requested_at, rows[0].attempts)

    await delete_legacy(world, world.owner, item.id)

    rows = await projection_rows(world, content_id)
    assert len(rows) == 1 and (rows[0].status, rows[0].requested_at, rows[0].attempts) == stamp


async def test_31_a_modern_result_for_the_same_source_key_survives(world: World) -> None:
    period, type_row, content_id, item = await legacy_setup(world)
    # The same milestone, already recorded at result grain too - the state a
    # half-migrated month is in. Same source key, different table.
    modern = await world.services.work_results.record_source_result(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        source_type=PrWorkResultSource.CONTENT,
        source_key=item.source_key or "",
        work_type_id=type_row.id,
        subject_user_id=world.member.id,
        occurred_at=utcnow(),
        validated_by_user_id=world.head.id,
        validated_at=utcnow(),
        label="CNT · hiện đại",
    )
    assert modern.status is PrWorkCountStatus.COUNTED
    container = await world.session.get(PrWorkItem, modern.work_item_id)
    assert container is not None and container.reporting_period_id == period.id

    await delete_legacy(world, world.head, item.id)

    await world.session.refresh(modern)
    assert modern.status is PrWorkCountStatus.COUNTED
    assert (await source_result(world, content_id, KIND)) is not None
    container = await world.session.get(PrWorkItem, modern.work_item_id)
    assert container is not None and container.quantity == Decimal("1.00")
    rows = await contributions_of(world, container.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED


async def test_32_33_34_35_every_other_kind_of_work_in_the_month_is_unchanged(
    world: World,
) -> None:
    period, type_row, _content_id, item = await legacy_setup(world)
    # A second legacy row, a modern content result, a manual result in a
    # container, a manual one-off job and a recurring job - the whole zoo.
    other_content = await approved_content(world, title="Bài khác", content_type=TYPE)
    sibling = await legacy_item(world, content_id=other_content, type_row=type_row)
    _modern_content, modern = await counted_content(
        world, title="Hiện đại", content_type=PrContentType.PRESS_ARTICLE
    )
    manual = await manual_result(world, type_row)
    from meobot.application.pr_work_service import CreateWorkCommand

    one_off = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            title="Việc tay",
            work_type_id=type_row.id,
            contributor_user_ids=(world.other.id,),
        ),
    )
    routine = await recurring_items(world)

    async def snapshot() -> dict[str, object]:
        out: dict[str, object] = {}
        for label, row in (
            ("sibling", sibling),
            ("manual", manual),
            ("modern", modern),
            ("one_off", one_off),
        ):
            await world.session.refresh(row)
            out[label] = (row.status, getattr(row, "quantity", None), row.updated_at)
        for row in routine:
            await world.session.refresh(row)
            out[f"routine:{row.id}"] = (row.status, row.updated_at)
        out["items"] = await count(world, select(func.count()).select_from(PrWorkItem))
        out["results"] = await count(world, select(func.count()).select_from(PrWorkResult))
        out["contributions"] = await count(
            world, select(func.count()).select_from(PrWorkContribution)
        )
        return out

    before = await snapshot()
    await delete_legacy(world, world.owner, item.id)
    after = await snapshot()
    before["items"] = int(before["items"]) - 1  # type: ignore[call-overload]
    before["contributions"] = int(before["contributions"]) - 1  # type: ignore[call-overload]
    assert after == before
    assert period.status is PrPeriodStatus.OPEN


# ===========================================================================
# 36-41: ACCOUNTING
# ===========================================================================


async def test_36_41_the_actual_drops_and_is_not_compensated(world: World) -> None:
    period, type_row, content_id, item = await legacy_setup(world)
    await plan_with_quota(world, period, type_row)
    # The one legacy row is this person's whole month.
    before = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert before.counted_contributions == 1

    await delete_legacy(world, world.owner, item.id)

    after = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert after.counted_contributions == 0, "actual = 0, and nothing put it back"
    assert await source_result(world, content_id, KIND) is None
    # The user, later and separately, runs the sync - and only then does the
    # modern architecture record the still-accepted piece.
    from meobot.application.pr_work_maintenance_service import MaintenanceScope

    run = await world.services.work_maintenance.sync_missing(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        scope=MaintenanceScope(period_id=period.id),
    )
    assert run.counts.get("PROJECTED") == 1
    restored = await source_result(world, content_id, KIND)
    assert restored is not None and restored.status is PrWorkCountStatus.COUNTED
    later = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert later.counted_contributions == 1


async def test_37_38_39_40_m2_and_the_stored_figure_follow_and_nothing_else_moves(
    world: World,
) -> None:
    period, type_row, _content_id, item = await legacy_setup(world)
    await plan_with_quota(world, period, type_row)
    await world.services.work_eligibility.reconcile_period(
        actor=world.actor(world.owner), request_id=world.request_id, period_id=period.id
    )
    contribution = (await contributions_of(world, item.id))[0]
    allocation = (
        await world.session.execute(
            select(PrWorkQuotaAllocation).where(
                PrWorkQuotaAllocation.work_contribution_id == contribution.id
            )
        )
    ).scalar_one()
    assert allocation.quota_status is PrWorkQuotaStatus.ELIGIBLE
    quota_target = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
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
    stamp_before = stored.updated_at
    formula_before = (stored.policy_id, stored.finalized_at)

    deletion = await delete_legacy(world, world.head, item.id)

    assert deletion.performance_refreshed == 1
    assert (
        await world.session.execute(
            select(PrWorkQuotaAllocation).where(
                PrWorkQuotaAllocation.work_contribution_id == contribution.id
            )
        )
    ).scalar_one_or_none() is None, "the allocation went with its contribution"
    after = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert after.counted_contributions == 0
    # The KPI target is a plan fact and did not move; only the actual did.
    assert [one.target_value for one in after.types] == [
        one.target_value for one in quota_target.types
    ]
    assert after.plan_id == quota_target.plan_id
    await world.session.refresh(stored)
    assert stored.finalized_at is None
    assert stored.updated_at >= stamp_before
    assert (stored.policy_id, stored.finalized_at) == formula_before


# ===========================================================================
# 42-45: PERIOD GUARDS
# ===========================================================================


async def test_42_an_open_month_accepts(world: World) -> None:
    period, _type_row, _content_id, item = await legacy_setup(world)
    assert period.status is PrPeriodStatus.OPEN
    deletion = await delete_legacy(world, world.owner, item.id)
    assert deletion.period_code == period.code


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_43_44_a_shut_month_refuses_with_a_structured_409(
    world: World, state: PrPeriodStatus
) -> None:
    period, _type_row, _content_id, item = await legacy_setup(world)
    period.status = state
    await world.session.flush()
    with pytest.raises(PrConflictError) as caught:
        await delete_legacy(world, world.owner, item.id)
    assert caught.value.details["reason"] == PERIOD_NOT_OPEN_FOR_CLEANUP
    assert str(caught.value) == PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE
    assert await world.session.get(PrWorkItem, item.id) is not None
    assert period.status is state, "not silently reopened"
    world.act_as(world.owner)
    response = world.client.delete(f"{MAINT}/items/{item.id}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == PERIOD_NOT_OPEN_FOR_CLEANUP
    assert response.json()["error"]["details"]["operation"] == "legacy_delete"
    assert response.json()["error"]["message"] == PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE


async def test_45_a_finalised_performance_figure_refuses(world: World) -> None:
    from datetime import date as _date

    period, _type_row, _content_id, item = await legacy_setup(world)
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
    stored.policy_id = policy.id
    stored.final_performance_index = Decimal("80")
    stored.finalized_at = utcnow()
    stored.finalized_by_user_id = world.owner.id
    await world.session.flush()

    with pytest.raises(PrConflictError) as caught:
        await delete_legacy(world, world.owner, item.id)
    assert caught.value.details["reason"] == PERIOD_NOT_OPEN_FOR_CLEANUP
    assert caught.value.details["cause"] == "performance_finalized"
    assert await world.session.get(PrWorkItem, item.id) is not None


# ===========================================================================
# THE ROUTE IS NOT A GENERIC HARD DELETE
# ===========================================================================


async def test_r_manual_recurring_and_container_rows_are_refused(world: World) -> None:
    from meobot.application.pr_work_service import CreateWorkCommand

    type_row = await work_type(world)
    await rule(world, kind=KIND, type_row=type_row, content_type=TYPE)
    await month(world)
    manual = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            title="Việc tay", work_type_id=type_row.id, contributor_user_ids=(world.member.id,)
        ),
    )
    routine = (await recurring_items(world))[0]
    _content_id, result = await counted_content(world, content_type=TYPE)
    container = await world.session.get(PrWorkItem, result.work_item_id)
    assert container is not None
    stream = await manual_result(world, type_row)
    stream_container = await world.session.get(PrWorkItem, stream.work_item_id)
    assert stream_container is not None

    expected = {
        manual.id: "manual",
        routine.id: "recurring",
        container.id: "period_container",
        stream_container.id: "period_container",
    }
    for item_id, cause in expected.items():
        with pytest.raises(PrConflictError) as caught:
            await delete_legacy(world, world.owner, item_id)
        assert caught.value.details["reason"] == WORK_ITEM_NOT_LEGACY_CONTENT
        assert caught.value.details["cause"] == cause
        assert await world.session.get(PrWorkItem, item_id) is not None
    world.act_as(world.head)
    response = world.client.delete(f"{MAINT}/items/{manual.id}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == WORK_ITEM_NOT_LEGACY_CONTENT
    # And the modern result's *own* id is not a work item at all.
    with pytest.raises(PrNotFoundError):
        await delete_legacy(world, world.owner, result.id)


async def test_n_the_audit_row_outlives_the_item(world: World) -> None:
    period, type_row, content_id, item = await legacy_setup(world)
    await delete_legacy(world, world.owner, item.id, note="dọn")
    trail = await audit_rows(world, AuditAction.PR_WORK_ITEM_ADMIN_DELETED)
    assert len(trail) == 1
    row = trail[0]
    assert row.entity_type == "pr_work_item" and row.entity_id == str(item.id)
    before = row.before_data or {}
    assert before["code"] == item.code
    assert before["title"] == item.title
    assert before["source_type"] == "CONTENT"
    assert before["source_key"] == item.source_key
    assert before["content_id"] == str(content_id)
    assert before["work_type_id"] == str(type_row.id)
    assert before["responsible_user_id"] == str(world.member.id)
    assert before["period"] == period.code
    assert before["note"] == "dọn"
    assert before["removed"]["contributions"] == 1
    assert "script" not in str(before).lower() and "Nội dung." not in str(before)
    assert (row.after_data or {}) == {
        "deleted": True,
        "results_created": 0,
        "projection_requested": False,
    }


async def test_u_an_uncounted_legacy_row_deletes_without_touching_m2(world: World) -> None:
    """A self-approved legacy row - COMPLETED, contribution PENDING - has no
    accounting anywhere; deleting it recomputes nothing and refreshes nothing."""
    _period, _type_row, _content_id, item = await legacy_setup(world, counted=False)
    assert item.status is PrWorkStatus.COMPLETED
    deletion = await delete_legacy(world, world.owner, item.id)
    assert deletion.removed["counted_contributions"] == 0
    assert deletion.performance_refreshed == 0
    assert await world.session.get(PrWorkItem, item.id) is None


async def test_u2_a_row_with_no_opened_month_still_deletes(world: World) -> None:
    """Nobody opened a period for the row's month: nothing to refuse, nothing
    to recompute, and the row goes."""
    type_row = await work_type(world)
    content_id = await approved_content(world, content_type=TYPE)
    item = await legacy_item(
        world,
        content_id=content_id,
        type_row=type_row,
        counted=True,
        at=datetime(2024, 3, 5, 9, 0, tzinfo=UTC),
    )
    deletion = await delete_legacy(world, world.owner, item.id)
    assert deletion.period_code is None
    assert await world.session.get(PrWorkItem, item.id) is None
