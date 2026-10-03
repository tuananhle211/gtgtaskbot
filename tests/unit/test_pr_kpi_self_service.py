"""KPI self-service: the employee proposes, the manager decides, the approved plan counts.

Migration ``0038``. The M2 lifecycle - ``DRAFT → APPROVED``, revision by
cloning, one draft in flight, immutability of the plan in force - is kept
byte for byte; this file is the contract of what was added around it:

* an employee starts and edits **their own** draft, and submits it;
* from submission the draft is the manager's: locked for its author,
  editable by the manager, approved or returned as the **same version**;
* nobody approves their own plan, whatever else they hold;
* nothing a draft says reaches M2 before approval.

Helpers come from the M2 suite so the two cannot drift.
"""

# ruff: noqa: F811 - ``world`` is a fixture reused across the work suites.

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_work_quota import PrWorkPlan
from meobot.db.models.user import User
from meobot.db.models.user_notification import UserNotification
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Role
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.pr.errors import (
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
    PrWorkPlanStateError,
)
from meobot.domain.pr.performance import PrWorkScoringMode
from meobot.domain.pr.work import PrWorkCategory, PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkPlanStatus, PrWorkQuotaBasis
from tests.unit.test_pr_performance import policy, rule, schedule
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401
from tests.unit.test_pr_work_quota import (
    SEPTEMBER,
    allocations_for,
    approved_plan,
    counted,
    month,
    seeding_type,
    work_type,
)

pytestmark = pytest.mark.asyncio


# ===========================================================================
# Helpers
# ===========================================================================


async def self_draft(world: World, period, *, user=None):  # type: ignore[no-untyped-def]
    person = user or world.member
    return await world.services.work_plans.self_create_plan(
        actor=world.actor(person), request_id=world.request_id, period_id=period.id
    )


async def add(world: World, plan_id: uuid.UUID, type_row, target: str, *, by=None):  # type: ignore[no-untyped-def]
    return await world.services.work_plans.add_quota(
        actor=world.actor(by or world.member),
        request_id=world.request_id,
        plan_id=plan_id,
        work_type_id=type_row.id,
        target_value=Decimal(target),
        eligibility_cap=Decimal(target),
    )


async def submit(world: World, plan_id: uuid.UUID, *, by=None):  # type: ignore[no-untyped-def]
    return await world.services.work_plans.submit(
        actor=world.actor(by or world.member), request_id=world.request_id, plan_id=plan_id
    )


async def approve(world: World, plan_id: uuid.UUID, *, by=None):  # type: ignore[no-untyped-def]
    return await world.services.work_plans.approve(
        actor=world.actor(by or world.head), request_id=world.request_id, plan_id=plan_id
    )


async def send_back(world: World, plan_id: uuid.UUID, *, note=None, by=None):  # type: ignore[no-untyped-def]
    return await world.services.work_plans.return_for_revision(
        actor=world.actor(by or world.head), request_id=world.request_id, plan_id=plan_id, note=note
    )


async def submitted_draft(world: World, period, type_row, target: str = "5"):  # type: ignore[no-untyped-def]
    detail = await self_draft(world, period)
    await add(world, detail.plan.id, type_row, target)
    return await submit(world, detail.plan.id)


async def reload(world: World, plan_id: uuid.UUID) -> PrWorkPlan:
    row = await world.session.get(PrWorkPlan, plan_id)
    assert row is not None
    await world.session.refresh(row)
    return row


async def actions_on(world: World, plan_id: uuid.UUID) -> list[str]:
    rows = await world.session.execute(
        select(AuditLog.action).where(AuditLog.entity_id == str(plan_id))
    )
    return list(rows.scalars().all())


def mine(world: World, period_id: uuid.UUID, *, as_user=None) -> dict:  # type: ignore[type-arg,no-untyped-def]
    world.act_as(as_user or world.member)
    response = world.client.get(
        "/api/pr/work/plans/mine/summary", params={"period_id": str(period_id)}
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# ===========================================================================
# 1-7: DOMAIN INVARIANTS
# ===========================================================================


async def test_01_an_employee_creates_their_own_first_draft(world: World) -> None:
    period = await month(world)
    detail = await self_draft(world, period)
    assert detail.plan.user_id == world.member.id
    assert detail.plan.created_by_user_id == world.member.id
    assert detail.plan.status is PrWorkPlanStatus.DRAFT and detail.plan.version_no == 1
    assert detail.is_subject and detail.can_edit and detail.can_discard
    assert not detail.can_approve
    # Not ready yet, and told why in a code rather than a sentence.
    assert detail.can_submit is False and detail.readiness_blockers == ("plan_has_no_quotas",)


async def test_02_the_subject_is_the_session_not_the_request(world: World) -> None:
    """There is no ``user_id`` on the self path; the manager path still needs the right."""
    period = await month(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_plans.create_plan(
            actor=world.actor(world.member),
            request_id=world.request_id,
            user_id=world.other.id,
            period_id=period.id,
        )
    world.act_as(world.member)
    # The body has no place for a subject at all: a smuggled one is a 422.
    smuggled = world.client.post(
        "/api/pr/work/plans/mine",
        json={"period_id": str(period.id), "user_id": str(world.other.id)},
    )
    assert smuggled.status_code == 422, smuggled.text
    response = world.client.post("/api/pr/work/plans/mine", json={"period_id": str(period.id)})
    assert response.status_code == 201, response.text
    assert response.json()["plan"]["user_id"] == str(world.member.id)


async def test_03_04_an_approved_plan_is_revised_not_recreated_and_quotas_come_across(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("7")),)
    )
    with pytest.raises(PrWorkPlanStateError) as refused:
        await self_draft(world, period)
    assert refused.value.details["reason"] == "approved_plan_requires_revision"
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=plan.id
    )
    assert draft.plan.version_no == plan.version_no + 1
    assert draft.plan.supersedes_plan_id == plan.id
    assert draft.plan.created_by_user_id == world.member.id
    assert [(q.work_type_id, q.target_value, q.eligibility_cap) for q in draft.quotas] == [
        (type_row.id, Decimal("5"), Decimal("7"))
    ]
    assert (await reload(world, plan.id)).status is PrWorkPlanStatus.APPROVED


async def test_05_06_one_draft_and_the_existing_one_is_named(world: World) -> None:
    period = await month(world)
    first = await self_draft(world, period)
    with pytest.raises(PrWorkPlanStateError) as refused:
        await self_draft(world, period)
    assert refused.value.details["reason"] == "draft_already_exists"
    assert refused.value.details["plan_id"] == str(first.plan.id)
    type_row = await work_type(world)
    plan = await approved_plan(
        world, period=period, user=world.other, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    await world.services.work_plans.revise(
        actor=world.actor(world.other), request_id=world.request_id, plan_id=plan.id
    )
    with pytest.raises(PrWorkPlanStateError) as again:
        await world.services.work_plans.revise(
            actor=world.actor(world.other), request_id=world.request_id, plan_id=plan.id
        )
    assert again.value.details["reason"] == "draft_already_exists"
    versions = (
        (
            await world.session.execute(
                select(PrWorkPlan.version_no).where(PrWorkPlan.user_id == world.other.id)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(versions) == [1, 2]


async def test_07_an_approved_plan_is_never_edited_in_place(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    plan = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    quota = (
        await world.services.work_plans.detail(actor=world.actor(world.owner), plan_id=plan.id)
    ).quotas[0]
    for actor in (world.member, world.owner):
        with pytest.raises(PrWorkPlanStateError) as refused:
            await world.services.work_plans.update_quota(
                actor=world.actor(actor),
                request_id=world.request_id,
                plan_id=plan.id,
                quota_id=quota.id,
                target_value=Decimal("9"),
            )
        assert refused.value.details["reason"] == "plan_not_draft"


# ===========================================================================
# 8-13: EMPLOYEE EDIT
# ===========================================================================


async def test_08_09_an_employee_edits_their_own_draft_and_nobody_elses(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await self_draft(world, period)
    edited = await add(world, detail.plan.id, type_row, "12")
    assert edited.quotas[0].target_value == Decimal("12")
    with pytest.raises(PrNotFoundError):
        await add(world, detail.plan.id, type_row, "1", by=world.other)
    world.act_as(world.other)
    assert world.client.get(f"/api/pr/work/plans/{detail.plan.id}").status_code == 404


async def test_10_11_a_submitted_draft_is_locked_for_its_author(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await submitted_draft(world, period, type_row)
    quota = detail.quotas[0]
    with pytest.raises(PrWorkPlanStateError) as refused:
        await world.services.work_plans.update_quota(
            actor=world.actor(world.member),
            request_id=world.request_id,
            plan_id=detail.plan.id,
            quota_id=quota.id,
            target_value=Decimal("99"),
        )
    assert refused.value.details["reason"] == "draft_submitted_locked"
    # The stale tab, over HTTP: the same refusal, as a 409 with the code.
    world.act_as(world.member)
    response = world.client.patch(
        f"/api/pr/work/plans/{detail.plan.id}/quotas/{quota.id}", json={"target_value": "99"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "draft_submitted_locked"
    assert (await reload(world, detail.plan.id)).is_submitted
    fresh = await world.services.work_plans.detail(
        actor=world.actor(world.member), plan_id=detail.plan.id
    )
    assert fresh.quotas[0].target_value == Decimal("5")
    assert fresh.can_edit is False


async def test_12_13_the_catalogue_stays_the_owners_and_validation_still_applies(
    world: World,
) -> None:
    period = await month(world)
    detail = await self_draft(world, period)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.create_work_type(
            actor=world.actor(world.member),
            request_id=world.request_id,
            code="MY_OWN_TYPE",
            name="Tự đặt",
            category=PrWorkCategory.CONTENT,
            default_unit=PrWorkUnit.ITEM,
            default_quota_basis=PrWorkQuotaBasis.ITEM_COUNT,
        )
    seeding = await seeding_type(world)
    with pytest.raises(PrValidationError):
        # A quantity-measured type refuses an item-count quota, for the employee
        # exactly as for the manager.
        await world.services.work_plans.add_quota(
            actor=world.actor(world.member),
            request_id=world.request_id,
            plan_id=detail.plan.id,
            work_type_id=seeding.id,
            target_value=Decimal("100"),
            eligibility_cap=Decimal("100"),
            basis=PrWorkQuotaBasis.ITEM_COUNT,
        )
    with pytest.raises(PrValidationError):
        await world.services.work_plans.add_quota(
            actor=world.actor(world.member),
            request_id=world.request_id,
            plan_id=detail.plan.id,
            work_type_id=seeding.id,
            target_value=Decimal("100"),
            eligibility_cap=Decimal("50"),
        )


# ===========================================================================
# 14-20: SUBMISSION
# ===========================================================================


async def test_14_15_only_the_subject_submits_and_only_a_ready_draft(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await self_draft(world, period)
    with pytest.raises(PrValidationError) as unready:
        await submit(world, detail.plan.id)
    assert unready.value.details["reason"] == "plan_not_ready"
    assert unready.value.details["blockers"] == ["plan_has_no_quotas"]
    await add(world, detail.plan.id, type_row, "5")
    for someone in (world.other, world.head, world.owner):
        with pytest.raises(PrPermissionDeniedError) as refused:
            await submit(world, detail.plan.id, by=someone)
        assert refused.value.details["reason"] == "not_plan_subject"
    sent = await submit(world, detail.plan.id)
    assert sent.plan.is_submitted and sent.plan.submitted_by_user_id == world.member.id
    assert sent.can_edit is False and sent.can_submit is False and sent.is_subject


async def test_16_readiness_is_shared_between_submit_and_approve(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await submitted_draft(world, period, type_row)
    # The type is retired between submission and approval: the same rule that
    # would have refused the submission now refuses the approval.
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=type_row.id,
        is_active=False,
    )
    fresh = await world.services.work_plans.detail(
        actor=world.actor(world.head), plan_id=detail.plan.id
    )
    assert "work_type_inactive" in fresh.readiness_blockers and fresh.can_approve is False
    with pytest.raises(PrValidationError) as refused:
        await approve(world, detail.plan.id)
    assert refused.value.details["reason"] == "work_type_inactive"


async def test_17_19_submission_approves_nothing_supersedes_nothing_recomputes_nothing(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    current = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    before = [
        (one.quota_status, one.work_plan_id)
        for one in await allocations_for(world, user=world.member, period=period)
    ]
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=current.id
    )
    await world.services.work_plans.update_quota(
        actor=world.actor(world.member),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        quota_id=draft.quotas[0].id,
        target_value=Decimal("1"),
        eligibility_cap=Decimal("1"),
    )
    sent = await submit(world, draft.plan.id)
    assert sent.plan.status is PrWorkPlanStatus.DRAFT
    assert (await reload(world, current.id)).status is PrWorkPlanStatus.APPROVED
    after = [
        (one.quota_status, one.work_plan_id)
        for one in await allocations_for(world, user=world.member, period=period)
    ]
    assert after == before and all(plan_id == current.id for _, plan_id in after)
    in_force = await world.services.work_plans.plan_in_force(
        actor=world.actor(world.member), user_id=None, period_id=period.id
    )
    assert in_force is not None and in_force.plan.id == current.id


async def test_20_submitting_twice_is_a_structured_conflict(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await submitted_draft(world, period, type_row)
    with pytest.raises(PrWorkPlanStateError) as refused:
        await submit(world, detail.plan.id)
    assert refused.value.details["reason"] == "draft_already_submitted"
    assert (await actions_on(world, detail.plan.id)).count(
        AuditAction.PR_WORK_PLAN_SUBMITTED.value
    ) == 1
    world.act_as(world.member)
    assert world.client.post(f"/api/pr/work/plans/{detail.plan.id}/submit").status_code == 409


# ===========================================================================
# 21-26: MANAGER REVIEW
# ===========================================================================


async def test_21_22_the_queue_is_submitted_drafts_only(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await submitted_draft(world, period, type_row)
    unsubmitted = await self_draft(world, period, user=world.other)
    await add(world, unsubmitted.plan.id, type_row, "3", by=world.other)
    world.act_as(world.head)
    body = world.client.get(
        "/api/pr/work/plans/summary", params={"period_id": str(period.id)}
    ).json()
    by_user = {row["user_id"]: row for row in body["items"]}
    assert by_user[str(world.member.id)]["draft_review_state"] == "SUBMITTED"
    assert by_user[str(world.member.id)]["draft_review_state_label"] == "Chờ duyệt"
    assert by_user[str(world.member.id)]["draft_is_submitted"] is True
    assert by_user[str(world.other.id)]["draft_review_state"] == "EDITING"
    assert by_user[str(world.other.id)]["draft_is_submitted"] is False
    assert body["counts"]["pending_review"] == 1
    assert body["counts"]["drafting"] == 1


async def test_23_26_the_manager_edits_the_submitted_draft_in_place_and_the_employee_stays_locked(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await submitted_draft(world, period, type_row, "30")
    opened = await world.services.work_plans.detail(
        actor=world.actor(world.head), plan_id=detail.plan.id
    )
    assert opened.can_edit and opened.can_return and opened.can_approve
    corrected = await world.services.work_plans.update_quota(
        actor=world.actor(world.head),
        request_id=world.request_id,
        plan_id=detail.plan.id,
        quota_id=detail.quotas[0].id,
        target_value=Decimal("35"),
        eligibility_cap=Decimal("35"),
    )
    assert corrected.plan.id == detail.plan.id and corrected.plan.version_no == 1
    assert corrected.quotas[0].target_value == Decimal("35")
    assert corrected.plan.is_submitted
    versions = (
        (
            await world.session.execute(
                select(PrWorkPlan).where(PrWorkPlan.user_id == world.member.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(versions) == 1
    with pytest.raises(PrWorkPlanStateError) as refused:
        await world.services.work_plans.update_quota(
            actor=world.actor(world.member),
            request_id=world.request_id,
            plan_id=detail.plan.id,
            quota_id=detail.quotas[0].id,
            target_value=Decimal("30"),
        )
    assert refused.value.details["reason"] == "draft_submitted_locked"
    assert AuditAction.PR_WORK_PLAN_QUOTA_UPDATED.value in await actions_on(
        world, detail.quotas[0].id
    )


# ===========================================================================
# 27-35: APPROVAL
# ===========================================================================


async def test_27_32_approval_goes_through_the_canonical_path(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    old = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=old.id
    )
    await submit(world, draft.plan.id)
    decided = await approve(world, draft.plan.id)
    assert decided.plan.status is PrWorkPlanStatus.APPROVED
    assert (
        decided.plan.approved_by_user_id == world.head.id and decided.plan.approved_at is not None
    )
    assert (await reload(world, old.id)).status is PrWorkPlanStatus.SUPERSEDED
    allocations = await allocations_for(world, user=world.member, period=period)
    assert allocations and all(one.work_plan_id == draft.plan.id for one in allocations)
    in_force = await world.services.work_plans.plan_in_force(
        actor=world.actor(world.member), user_id=None, period_id=period.id
    )
    assert in_force is not None and in_force.plan.id == draft.plan.id
    assert AuditAction.PR_WORK_PLAN_APPROVED.value in await actions_on(world, draft.plan.id)


async def test_33_34_self_approval_is_refused_and_an_employee_cannot_approve_at_all(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    # The owner writes their own plan and holds every right there is.
    own = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.owner.id,
        period_id=period.id,
    )
    await add(world, own.plan.id, type_row, "5", by=world.owner)
    with pytest.raises(PrPermissionDeniedError) as refused:
        await approve(world, own.plan.id, by=world.owner)
    assert refused.value.details["reason"] == "self_approval_forbidden"
    assert (await reload(world, own.plan.id)).status is PrWorkPlanStatus.DRAFT
    detail = await world.services.work_plans.detail(
        actor=world.actor(world.owner), plan_id=own.plan.id
    )
    assert detail.can_approve is False and detail.can_edit is True
    # Somebody else with the right may.
    await approve(world, own.plan.id, by=world.head)
    # And an employee holds no approval right over any plan.
    other = await submitted_draft(world, period, type_row)
    with pytest.raises(PrPermissionDeniedError):
        await approve(world, other.plan.id, by=world.member)
    world.act_as(world.member)
    assert (
        world.client.post(f"/api/pr/work/plans/{other.plan.id}/approve", json={}).status_code == 403
    )


async def test_35_double_approval_is_safe(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await submitted_draft(world, period, type_row)
    await approve(world, detail.plan.id)
    with pytest.raises(PrWorkPlanStateError) as refused:
        await approve(world, detail.plan.id, by=world.owner)
    assert refused.value.details["reason"] == "plan_not_draft"
    approved_rows = (
        (
            await world.session.execute(
                select(PrWorkPlan).where(
                    PrWorkPlan.user_id == world.member.id,
                    PrWorkPlan.status == PrWorkPlanStatus.APPROVED,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(approved_rows) == 1


# ===========================================================================
# 36-42: RETURN
# ===========================================================================


async def test_36_42_return_reopens_the_same_version_with_the_note(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    old = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=old.id
    )
    await submit(world, draft.plan.id)
    returned = await send_back(world, draft.plan.id, note="Mục tiêu video cần điều chỉnh.")
    row = returned.plan
    assert row.id == draft.plan.id and row.version_no == draft.plan.version_no
    assert row.status is PrWorkPlanStatus.DRAFT and row.is_returned and not row.is_submitted
    assert row.return_note == "Mục tiêu video cần điều chỉnh."
    assert row.returned_by_user_id == world.head.id
    assert returned.returned_by_name == world.head.full_name
    assert (await reload(world, old.id)).status is PrWorkPlanStatus.APPROVED
    fresh = await world.services.work_plans.detail(
        actor=world.actor(world.member), plan_id=draft.plan.id
    )
    assert fresh.can_edit and fresh.can_submit
    # Edit, resubmit, approve - still the same version.
    await world.services.work_plans.update_quota(
        actor=world.actor(world.member),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        quota_id=draft.quotas[0].id,
        target_value=Decimal("8"),
        eligibility_cap=Decimal("8"),
    )
    resent = await submit(world, draft.plan.id)
    assert (
        resent.plan.is_submitted
        and resent.plan.returned_at is None
        and resent.plan.return_note is None
    )
    decided = await approve(world, draft.plan.id)
    assert decided.plan.id == draft.plan.id and decided.plan.status is PrWorkPlanStatus.APPROVED
    assert decided.quotas[0].target_value == Decimal("8")
    versions = sorted(
        (
            await world.session.execute(
                select(PrWorkPlan.version_no).where(PrWorkPlan.user_id == world.member.id)
            )
        )
        .scalars()
        .all()
    )
    assert versions == [1, 2]
    actions = await actions_on(world, draft.plan.id)
    assert actions.count(AuditAction.PR_WORK_PLAN_SUBMITTED.value) == 2
    assert actions.count(AuditAction.PR_WORK_PLAN_RETURNED.value) == 1


async def test_36b_only_a_submitted_draft_can_be_returned(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await self_draft(world, period)
    await add(world, detail.plan.id, type_row, "5")
    with pytest.raises(PrWorkPlanStateError) as refused:
        await send_back(world, detail.plan.id)
    assert refused.value.details["reason"] == "draft_not_submitted"
    with pytest.raises(PrPermissionDeniedError):
        await send_back(world, detail.plan.id, by=world.member)


# ===========================================================================
# 43-45: INITIAL PLAN
# ===========================================================================


async def test_43_45_a_submitted_first_version_is_not_in_force(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    detail = await submitted_draft(world, period, type_row)
    assert (
        await world.services.work_plans.plan_in_force(
            actor=world.actor(world.member), user_id=None, period_id=period.id
        )
        is None
    )
    summary = await world.services.work_eligibility.summary(
        actor=world.actor(world.member), user_id=None, period_id=period.id
    )
    assert summary.plan_id is None
    row = mine(world, period.id)
    assert row["current_status"] == "DRAFT" and row["draft_is_submitted"] is True
    assert row["approved_at"] is None
    await approve(world, detail.plan.id)
    in_force = await world.services.work_plans.plan_in_force(
        actor=world.actor(world.member), user_id=None, period_id=period.id
    )
    assert in_force is not None and in_force.plan.id == detail.plan.id
    assert (
        await world.services.work_eligibility.summary(
            actor=world.actor(world.member), user_id=None, period_id=period.id
        )
    ).plan_id == detail.plan.id


# ===========================================================================
# 46-50: READ MODEL
# ===========================================================================


async def test_46_50_the_read_model_keeps_the_approved_plan_and_the_draft_apart(
    world: World,
) -> None:
    period = await month(world)
    type_row = await work_type(world)
    old = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("5"), Decimal("5")),)
    )
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=old.id
    )
    await submit(world, draft.plan.id)
    row = mine(world, period.id)
    assert row["current_plan_id"] == str(old.id) and row["current_status"] == "APPROVED"
    assert row["latest_draft_id"] == str(draft.plan.id) and row["draft_version_no"] == 2
    assert row["draft_review_state"] == "SUBMITTED" and row["draft_submitted_at"] is not None
    assert row["draft_submitted_by_user_id"] == str(world.member.id)
    assert row["history_count"] == 2 and row["terminal_count"] == 0
    world.act_as(world.member)
    history = world.client.get(
        "/api/pr/work/plans/history",
        params={"user_id": str(world.member.id), "period_id": str(period.id)},
    ).json()
    flags = {
        entry["version_no"]: (entry["is_current"], entry["is_active_draft"])
        for entry in history["items"]
    }
    assert flags == {1: (True, False), 2: (False, True)}
    # And somebody else's row is not theirs to read.
    world.act_as(world.other)
    assert (
        world.client.get(
            "/api/pr/work/plans/mine/summary",
            params={"period_id": str(period.id), "user_id": str(world.member.id)},
        ).status_code
        == 200
    )
    # (user_id is not a parameter of the self route; the caller only ever gets themselves)
    assert world.client.get(
        "/api/pr/work/plans/mine/summary", params={"period_id": str(period.id)}
    ).json()["user_id"] == str(world.other.id)


# ===========================================================================
# 51-54: MANAGER CREATE ON BEHALF
# ===========================================================================


async def test_51_54_the_manager_flow_still_works_and_needs_no_submission(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    created = await world.services.work_plans.create_plan(
        actor=world.actor(world.head),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period.id,
    )
    assert created.plan.created_by_user_id == world.head.id and created.is_subject is False
    await add(world, created.plan.id, type_row, "5", by=world.head)
    # No submission needed: the manager's own draft is approved directly, by
    # somebody who is not its subject.
    decided = await approve(world, created.plan.id, by=world.owner)
    assert decided.plan.status is PrWorkPlanStatus.APPROVED and decided.plan.submitted_at is None
    revised = await world.services.work_plans.revise(
        actor=world.actor(world.head), request_id=world.request_id, plan_id=created.plan.id
    )
    assert revised.plan.version_no == 2 and revised.quotas[0].target_value == Decimal("5")
    with pytest.raises(PrWorkPlanStateError) as refused:
        await world.services.work_plans.create_plan(
            actor=world.actor(world.head),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    assert refused.value.details["reason"] == "draft_already_exists"
    await world.services.work_plans.discard(
        actor=world.actor(world.head), request_id=world.request_id, plan_id=revised.plan.id
    )
    with pytest.raises(PrWorkPlanStateError) as again:
        await world.services.work_plans.create_plan(
            actor=world.actor(world.head),
            request_id=world.request_id,
            user_id=world.member.id,
            period_id=period.id,
        )
    assert again.value.details["reason"] == "approved_plan_requires_revision"


# ===========================================================================
# 55-58: WORKLOAD PREVIEW
# ===========================================================================


async def test_55_58_the_preview_prices_the_plan_with_m6s_own_facts(world: World) -> None:
    await schedule(world)
    await policy(world)
    period = await month(world)
    scripting = await work_type(world)
    excluded = await work_type(world, code="MISC", name="Việc khác")
    unpriced = await work_type(world, code="NEW_TYPE", name="Loại mới")
    await rule(world, scripting, minutes=Decimal("90"))
    await rule(world, excluded, minutes=None, mode=PrWorkScoringMode.EXCLUDED_FROM_PERFORMANCE)
    detail = await self_draft(world, period)
    await add(world, detail.plan.id, scripting, "50")
    await add(world, detail.plan.id, excluded, "10")
    priced = await add(world, detail.plan.id, unpriced, "4")
    workload = priced.workload
    assert workload is not None
    assert workload.projected_minutes == Decimal("4500.00")
    assert workload.unscored_work_type_ids == (unpriced.id,)
    # September 2026 has 22 weekdays at 300 minutes: 6 600. But one quota is
    # unpriced, so the plan is incomplete and the percentage is withheld - the
    # workload visibility patch: 4 500 is a floor, not 68.2% of the month.
    assert workload.target_minutes == Decimal("6600.00")
    assert workload.percent is None
    assert workload.is_complete is False and workload.unpriced_quota_count == 1
    row = mine(world, period.id)
    assert row["draft_workload"]["percent"] is None
    assert row["draft_workload"]["is_complete"] is False
    # Price the last type and the percentage appears: 4 500 / 6 600 = 68.2%.
    await rule(world, unpriced, minutes=Decimal("0"))
    row = mine(world, period.id)
    assert row["draft_workload"]["percent"] == "68.2"
    assert row["draft_workload"]["is_complete"] is True
    # An employee cannot touch the rate that priced it.
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work_scoring_rules.create_draft(
            actor=world.actor(world.member),
            request_id=world.request_id,
            work_type_id=scripting.id,
            mode=PrWorkScoringMode.STANDARD_MINUTES,
            standard_minutes_per_unit=Decimal("900"),
            effective_from=date(2026, 1, 1),
        )


# ===========================================================================
# 59-64: M2 / M6 / WORK ARE UNTOUCHED BY DRAFTS
# ===========================================================================


async def test_59_62_drafts_and_manager_edits_do_not_move_allocations(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    await counted(world, type_row=type_row, at=SEPTEMBER)
    current = await approved_plan(
        world, period=period, quotas=((type_row, Decimal("1"), Decimal("1")),)
    )

    def snapshot(rows):  # type: ignore[no-untyped-def]
        return [(one.quota_status, one.work_plan_id, one.eligible_amount) for one in rows]

    before = snapshot(await allocations_for(world, user=world.member, period=period))
    draft = await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=current.id
    )
    await world.services.work_plans.update_quota(
        actor=world.actor(world.member),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        quota_id=draft.quotas[0].id,
        target_value=Decimal("50"),
        eligibility_cap=Decimal("50"),
    )
    assert snapshot(await allocations_for(world, user=world.member, period=period)) == before
    await submit(world, draft.plan.id)
    assert snapshot(await allocations_for(world, user=world.member, period=period)) == before
    await world.services.work_plans.update_quota(
        actor=world.actor(world.head),
        request_id=world.request_id,
        plan_id=draft.plan.id,
        quota_id=draft.quotas[0].id,
        target_value=Decimal("60"),
        eligibility_cap=Decimal("60"),
    )
    assert snapshot(await allocations_for(world, user=world.member, period=period)) == before
    await approve(world, draft.plan.id)
    after = await allocations_for(world, user=world.member, period=period)
    assert all(one.work_plan_id == draft.plan.id for one in after)


# ===========================================================================
# 65-69: AUTHORIZATION
# ===========================================================================


async def test_65_69_the_permission_surface(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    theirs = await submitted_draft(world, period, type_row)
    world.act_as(world.other)
    assert world.client.get(f"/api/pr/work/plans/{theirs.plan.id}").status_code == 404
    assert world.client.post(f"/api/pr/work/plans/{theirs.plan.id}/submit").status_code == 403
    assert (
        world.client.post(f"/api/pr/work/plans/{theirs.plan.id}/return", json={}).status_code == 403
    )
    assert (
        world.client.post(f"/api/pr/work/plans/{theirs.plan.id}/approve", json={}).status_code
        == 403
    )
    assert (
        world.client.get(
            "/api/pr/work/plans/summary", params={"period_id": str(period.id)}
        ).status_code
        == 403
    )
    # A lead holds PR_WORK_MANAGE and still none of this.
    world.act_as(world.lead)
    assert (
        world.client.post(f"/api/pr/work/plans/{theirs.plan.id}/approve", json={}).status_code
        == 403
    )
    assert (
        world.client.post(f"/api/pr/work/plans/{theirs.plan.id}/return", json={}).status_code == 403
    )


# ===========================================================================
# 70-74: CONCURRENCY, AS SEQUENCES OF STALE ACTIONS
# ===========================================================================


async def test_70_74_the_stale_action_loses(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await submitted_draft(world, period, type_row)
    # 70. submit won; the employee's save is refused.
    with pytest.raises(PrWorkPlanStateError):
        await world.services.work_plans.update_quota(
            actor=world.actor(world.member),
            request_id=world.request_id,
            plan_id=detail.plan.id,
            quota_id=detail.quotas[0].id,
            target_value=Decimal("9"),
        )
    # 71. approve won; the return is refused, and vice versa.
    await approve(world, detail.plan.id)
    with pytest.raises(PrWorkPlanStateError) as refused:
        await send_back(world, detail.plan.id)
    assert refused.value.details["reason"] == "plan_not_draft"
    # 72. approve won; the manager's edit is refused.
    with pytest.raises(PrWorkPlanStateError):
        await world.services.work_plans.update_quota(
            actor=world.actor(world.head),
            request_id=world.request_id,
            plan_id=detail.plan.id,
            quota_id=detail.quotas[0].id,
            target_value=Decimal("9"),
        )
    # 73. two revisions: one draft.
    await world.services.work_plans.revise(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=detail.plan.id
    )
    with pytest.raises(PrWorkPlanStateError):
        await world.services.work_plans.revise(
            actor=world.actor(world.head), request_id=world.request_id, plan_id=detail.plan.id
        )
    # 74. two approvals: one plan in force (test 35), asserted by the index.
    drafts = (
        (
            await world.session.execute(
                select(PrWorkPlan).where(
                    PrWorkPlan.user_id == world.member.id,
                    PrWorkPlan.status == PrWorkPlanStatus.DRAFT,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(drafts) == 1


# ===========================================================================
# DISCARD, NOTIFICATIONS, AUDIT
# ===========================================================================


async def test_discard_rules(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await self_draft(world, period)
    await add(world, detail.plan.id, type_row, "5")
    with pytest.raises(PrNotFoundError):
        await world.services.work_plans.discard(
            actor=world.actor(world.other), request_id=world.request_id, plan_id=detail.plan.id
        )
    await submit(world, detail.plan.id)
    with pytest.raises(PrWorkPlanStateError) as refused:
        await world.services.work_plans.discard(
            actor=world.actor(world.member), request_id=world.request_id, plan_id=detail.plan.id
        )
    assert refused.value.details["reason"] == "draft_submitted_locked"
    await send_back(world, detail.plan.id)
    dropped = await world.services.work_plans.discard(
        actor=world.actor(world.member), request_id=world.request_id, plan_id=detail.plan.id
    )
    assert dropped.plan.status is PrWorkPlanStatus.DISCARDED
    # The slot is free again, and the next version does not reuse the number.
    again = await self_draft(world, period)
    assert again.plan.version_no == 2


async def test_notifications_and_audit(world: World) -> None:
    period = await month(world)
    type_row = await work_type(world)
    detail = await submitted_draft(world, period, type_row)

    async def inbox(user: User, event: NotificationEvent) -> int:
        rows = await world.session.execute(
            select(UserNotification).where(
                UserNotification.recipient_user_id == user.id,
                UserNotification.event_type == event.value,
            )
        )
        return len(rows.scalars().all())

    # Every configurer hears about the submission; the submitter does not.
    assert await inbox(world.head, NotificationEvent.PR_KPI_PLAN_SUBMITTED) == 1
    assert await inbox(world.owner, NotificationEvent.PR_KPI_PLAN_SUBMITTED) == 1
    assert await inbox(world.member, NotificationEvent.PR_KPI_PLAN_SUBMITTED) == 0
    await send_back(world, detail.plan.id, note="Thêm chỉ tiêu video.")
    assert await inbox(world.member, NotificationEvent.PR_KPI_PLAN_RETURNED) == 1
    await submit(world, detail.plan.id)
    assert await inbox(world.head, NotificationEvent.PR_KPI_PLAN_SUBMITTED) == 2
    await approve(world, detail.plan.id)
    assert await inbox(world.member, NotificationEvent.PR_KPI_PLAN_APPROVED) == 1
    actions = await actions_on(world, detail.plan.id)
    for action in (
        AuditAction.PR_WORK_PLAN_CREATED,
        AuditAction.PR_WORK_PLAN_SUBMITTED,
        AuditAction.PR_WORK_PLAN_RETURNED,
        AuditAction.PR_WORK_PLAN_APPROVED,
    ):
        assert action.value in actions
    assert world.member.role is Role.EMPLOYEE


# ===========================================================================
# HOTFIX: THE WRITE PATHS OVER HTTP
# ===========================================================================


async def test_hotfix_discard_and_submit_over_http_do_not_500(world: World) -> None:
    """*Bỏ bản nháp* and *Gửi duyệt* returned 500 in production.

    Both flush an UPDATE to the plan row, which expires the database-maintained
    ``updated_at``; the response serializer then read it in synchronous code
    and SQLAlchemy raised ``MissingGreenlet``. The service-level tests above
    never saw it because they never serialized. This test does what the panel
    does, over the router, and checks the row afterwards.
    """
    period = await month(world)
    type_row = await work_type(world)
    world.act_as(world.member)

    # CASE A - discard an empty own draft.
    created = world.client.post("/api/pr/work/plans/mine", json={"period_id": str(period.id)})
    assert created.status_code == 201, created.text
    first = created.json()["plan"]["id"]
    dropped = world.client.post(f"/api/pr/work/plans/{first}/discard", json={"note": None})
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["plan"]["status"] == "DISCARDED"
    assert (await reload(world, uuid.UUID(first))).status is PrWorkPlanStatus.DISCARDED

    # CASE B - submit an empty draft: a structured refusal, still editable.
    created = world.client.post("/api/pr/work/plans/mine", json={"period_id": str(period.id)})
    plan_id = created.json()["plan"]["id"]
    empty = world.client.post(f"/api/pr/work/plans/{plan_id}/submit")
    assert empty.status_code == 422, empty.text
    assert empty.json()["error"]["details"]["reason"] == "plan_not_ready"
    assert empty.json()["error"]["details"]["blockers"] == ["plan_has_no_quotas"]
    assert (await reload(world, uuid.UUID(plan_id))).submitted_at is None

    # CASE C - submit a ready draft, then return, resubmit, approve.
    added = world.client.post(
        f"/api/pr/work/plans/{plan_id}/quotas",
        json={"work_type_id": str(type_row.id), "target_value": "5", "eligibility_cap": "5"},
    )
    assert added.status_code == 201, added.text
    sent = world.client.post(f"/api/pr/work/plans/{plan_id}/submit")
    assert sent.status_code == 200, sent.text
    assert sent.json()["plan"]["review_state"] == "SUBMITTED"
    assert sent.json()["can_edit"] is False
    row = await reload(world, uuid.UUID(plan_id))
    assert row.submitted_at is not None and row.submitted_by_user_id == world.member.id
    assert row.status is PrWorkPlanStatus.DRAFT
    world.act_as(world.head)
    returned = world.client.post(f"/api/pr/work/plans/{plan_id}/return", json={"note": "Sửa."})
    assert returned.status_code == 200, returned.text
    assert returned.json()["plan"]["review_state"] == "RETURNED"
    world.act_as(world.member)
    resent = world.client.post(f"/api/pr/work/plans/{plan_id}/submit")
    assert resent.status_code == 200, resent.text
    world.act_as(world.head)
    decided = world.client.post(f"/api/pr/work/plans/{plan_id}/approve", json={})
    assert decided.status_code == 200, decided.text
    assert decided.json()["plan"]["status"] == "APPROVED"
    # Every plan-shaped response carries the freshly maintained timestamp.
    assert decided.json()["plan"]["updated_at"] is not None
