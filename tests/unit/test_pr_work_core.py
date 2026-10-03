"""M1 - the Work Ledger, and the boundary that makes it worth having.

Numbered 1-40 against the milestone's own requirement list.

What this file is really testing
---------------------------------

**CREATED != ACCEPTED != COMPLETED != APPROVED != COUNTED.** Five facts that a
KPI depends on being different, and every one of the tests below exists because
collapsing two of them would let somebody write their own performance record:

* an employee filing work does not put it in their workload - tests 5-8;
* a proposer cannot accept their own proposal - test 9;
* finishing work does not count it - test 17;
* **a contributor cannot validate their own work**, whatever capability they
  hold, including an OWNER - tests 18-20. This is the milestone;
* two validators racing cannot count anything twice - test 27.

**One job is not one person's workload.** A shoot with three people is one
approved work item and three counted contributions, and reporting must be able
to say both without dividing or multiplying either - tests 21-23.

**A period filter must never hide outstanding work.** Selecting "this month"
answers a question about achievement; overdue answers a question about now, and
no combination of parameters may let the first suppress the second - tests
30-34.

Nothing here contacts a network, and nothing here scores anything: ``COUNTED``
is as far as M1 goes and test 40 asserts that no score field exists anywhere in
the module.
"""

from __future__ import annotations

# The ``world`` fixture comes from the production-lifecycle suite rather than
# being rebuilt: five people with five different roles is exactly what a
# self-approval rule has to be argued against, and a lookalike fixture would let
# the two drift.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_query_service import (
    PrWorkDateField,
    PrWorkPreset,
    PrWorkScope,
    WorkQuery,
)
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrContentItem, PrTask
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
    PrWorkType,
)
from meobot.db.models.user import User
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.work import (
    OVERDUE_STATUSES,
    TERMINAL_WORK_STATUSES,
    PrWorkCategory,
    PrWorkContributionRole,
    PrWorkCountStatus,
    PrWorkEventType,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
    assert_source_key,
    is_overdue,
    work_source_key,
)
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)


# ===========================================================================
# Helpers
# ===========================================================================


async def work_type(
    world: World,
    *,
    code: str = "SHORT_SCRIPT",
    name: str = "Kịch bản ngắn",
    category: PrWorkCategory = PrWorkCategory.CONTENT,
    unit: PrWorkUnit = PrWorkUnit.ITEM,
    requires_evidence: bool = False,
) -> PrWorkType:
    """One work type, registered by the owner. ``PR_WORK_CONFIGURE``."""
    return await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code=code,
        name=name,
        category=category,
        default_unit=unit,
        requires_evidence=requires_evidence,
    )


async def assigned(
    world: World,
    *,
    manager: User | None = None,
    contributors: tuple[User, ...] | None = None,
    due_at: datetime | None = None,
    quantity: Decimal | None = None,
    type_row: PrWorkType | None = None,
    title: str = "Quay TVC Apexmed",
) -> PrWorkItem:
    """Work a manager assigned. Lands ``ACCEPTED``: the assignment authorises it."""
    row = type_row or await work_type(world)
    people = contributors or (world.member,)
    return await world.services.work.assign_work(
        actor=world.actor(manager or world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id,
            title=title,
            due_at=due_at,
            quantity=quantity,
            contributor_user_ids=tuple(person.id for person in people),
        ),
    )


async def proposed(
    world: World, *, proposer: User | None = None, title: str = "Seeding nhóm kín"
) -> PrWorkItem:
    """Work an employee suggested. Lands ``PROPOSED``: in nobody's workload."""
    row = await work_type(world, code="SEEDING", name="Seeding", category=PrWorkCategory.COMMUNITY)
    return await world.services.work.propose_work(
        actor=world.actor(proposer or world.member),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=row.id, title=title),
    )


async def contributions_of(world: World, item_id: uuid.UUID) -> list[PrWorkContribution]:
    result = await world.session.execute(
        select(PrWorkContribution)
        .where(PrWorkContribution.work_item_id == item_id)
        .order_by(PrWorkContribution.assigned_at.asc())
    )
    return list(result.scalars().all())


async def take_to_completed(world: World, item: PrWorkItem, *, by: User) -> PrWorkItem:
    """Accepted -> in progress -> completed, by a contributor."""
    await world.services.work.start(
        actor=world.actor(by), request_id=world.request_id, work_item_id=item.id
    )
    return await world.services.work.complete(
        actor=world.actor(by), request_id=world.request_id, work_item_id=item.id
    )


# ===========================================================================
# 1-4: THE TAXONOMY
# ===========================================================================


async def test_01_a_work_type_is_a_row_with_an_enum_category(world: World) -> None:
    """The hybrid taxonomy: a configurable sub-type under a controlled category."""
    row = await work_type(world, code="half day shoot", name="Quay nửa buổi")
    assert row.code == "HALF_DAY_SHOOT"  # normalised, and the stable identifier
    assert row.category is PrWorkCategory.CONTENT
    assert row.is_active is True
    assert row.default_unit is PrWorkUnit.ITEM


async def test_02_configuring_the_taxonomy_is_an_admin_capability(world: World) -> None:
    """``PR_WORK_CONFIGURE`` is ``settings.write`` - ``ADMIN`` and above."""
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.create_work_type(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            code="X",
            name="X",
            category=PrWorkCategory.OTHER,
        )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.create_work_type(
            actor=world.actor(world.member),
            request_id=world.request_id,
            code="Y",
            name="Y",
            category=PrWorkCategory.OTHER,
        )


async def test_03_a_duplicate_work_type_code_is_refused(world: World) -> None:
    """The code is a key. Two "SHORT_SCRIPT"s would make history ambiguous."""
    await work_type(world)
    with pytest.raises(PrConflictError):
        await work_type(world)


async def test_04_a_deactivated_type_cannot_receive_new_work(world: World) -> None:
    """Deactivating keeps the history and stops the type being offered."""
    row = await work_type(world)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    with pytest.raises(PrValidationError) as caught:
        await assigned(world, type_row=row)
    assert caught.value.details["reason"] == "work_type_inactive"


# ===========================================================================
# 5-12: CREATED, AND WHY IT IS NOT ACCEPTED
# ===========================================================================


async def test_05_an_employee_can_propose_work(world: World) -> None:
    """Requirement 1. Filing what you did is ordinary contributor work."""
    item = await proposed(world)
    assert item.status is PrWorkStatus.PROPOSED
    assert item.created_by_user_id == world.member.id
    assert item.accepted_at is None
    assert item.assigned_by_user_id is None


async def test_06_a_proposal_is_not_counted(world: World) -> None:
    """Requirement 2. **The point of the milestone.**

    A proposal is in nobody's workload: its contribution is ``PENDING`` and
    ``counted_at`` is null, so no period figure includes it however the query is
    phrased.
    """
    item = await proposed(world)
    rows = await contributions_of(world, item.id)
    assert [row.count_status for row in rows] == [PrWorkCountStatus.PENDING]
    assert rows[0].counted_at is None

    summary = await world.services.work_queries.summary(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, preset=PrWorkPreset.MONTH),
    )
    assert summary.counted_contributions == 0
    assert summary.counted_work_items == 0
    assert summary.proposed == 1


async def test_07_a_proposer_is_always_a_contributor_on_their_own_proposal(
    world: World,
) -> None:
    """Proposing work you had no part in is a management act, not a proposal."""
    row = await work_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id, title="Việc chung", contributor_user_ids=(world.other.id,)
        ),
    )
    assert {one.user_id for one in await contributions_of(world, item.id)} == {
        world.member.id,
        world.other.id,
    }


async def test_08_manager_assigned_work_is_accepted_immediately(world: World) -> None:
    """Requirement 5. The assignment **is** the authorization.

    The asymmetry with a proposal is the anti-gaming design: an employee cannot
    put work into their own workload and a manager can, because that is what a
    manager is for. Asking the manager to accept their own assignment afterwards
    would be a click that decides nothing.
    """
    item = await assigned(world)
    assert item.status is PrWorkStatus.ACCEPTED
    assert item.accepted_at is not None
    assert item.assigned_by_user_id == world.lead.id


async def test_09_a_proposer_cannot_accept_their_own_proposal(world: World) -> None:
    """Requirement 3. **The first anti-gaming boundary.**

    Asserted against a manager who proposed - holding ``PR_WORK_MANAGE`` does
    not help, because the rule is about *who did the work*, not about rank. If
    it did help, "propose" and "assign" would be one button with two names.
    """
    row = await work_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=row.id, title="Việc của tôi"),
    )
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.accept(
            actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "self_acceptance"
    assert "người quản lý khác" in str(caught.value)


async def test_10_a_different_manager_can_accept_the_proposal(world: World) -> None:
    """Requirement 4. Independent acceptance is what makes it work."""
    item = await proposed(world)
    accepted = await world.services.work.accept(
        actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
    )
    assert accepted.status is PrWorkStatus.ACCEPTED
    assert accepted.assigned_by_user_id == world.lead.id
    # Still not counted. Acceptance puts work in a workload; it does not finish it.
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.PENDING


async def test_11_an_employee_cannot_accept_anything(world: World) -> None:
    """Accepting is ``PR_WORK_MANAGE`` - ``video.approve``, ``TEAM_LEAD`` and up."""
    item = await proposed(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.accept(
            actor=world.actor(world.other), request_id=world.request_id, work_item_id=item.id
        )


async def test_12_a_rejected_proposal_is_kept_and_excluded(world: World) -> None:
    """Nothing is deleted. Its pending credit is closed out rather than orphaned."""
    item = await proposed(world)
    rejected = await world.services.work.reject(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        reason="Trùng với việc đã giao",
    )
    assert rejected.status is PrWorkStatus.REJECTED
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.EXCLUDED
    assert rows[0].excluded_reason is not None


# ===========================================================================
# 13-17: EXECUTION, AND WHY IT IS NOT COUNTED
# ===========================================================================


async def test_13_a_contributor_can_start_their_work(world: World) -> None:
    """Requirement 6."""
    item = await assigned(world)
    started = await world.services.work.start(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    assert started.status is PrWorkStatus.IN_PROGRESS
    assert started.started_at is not None


async def test_14_somebody_not_on_the_job_cannot_move_it(world: World) -> None:
    """Requirement 19, at the service. Not a hidden button - a refusal."""
    item = await assigned(world)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.start(
            actor=world.actor(world.other), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "not_a_contributor"


async def test_15_a_contributor_can_report_the_work_finished(world: World) -> None:
    """Requirement 7."""
    item = await assigned(world)
    done = await take_to_completed(world, item, by=world.member)
    assert done.status is PrWorkStatus.COMPLETED
    assert done.completed_at is not None
    assert done.completed_by_user_id == world.member.id


async def test_16_evidence_is_required_when_the_work_type_says_so(world: World) -> None:
    """Enforced at completion - the moment "is this finished" is being asked."""
    row = await work_type(world, code="EDIT_VIDEO", name="Dựng video", requires_evidence=True)
    item = await assigned(world, type_row=row)
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.complete(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "evidence_required"

    await world.services.work.add_evidence(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        label="Bản dựng",
        location="https://drive.google.com/file/d/abc/view",
    )
    assert (
        await world.services.work.complete(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        )
    ).status is PrWorkStatus.COMPLETED


async def test_17_completed_work_is_still_not_counted(world: World) -> None:
    """Requirement 8. **The sentence the whole milestone is built around.**

    A contributor saying they finished is a claim, not a validation. Every
    contribution stays ``PENDING`` and every period figure still reads zero.
    """
    item = await assigned(world)
    await take_to_completed(world, item, by=world.member)

    rows = await contributions_of(world, item.id)
    assert [row.count_status for row in rows] == [PrWorkCountStatus.PENDING]
    assert rows[0].counted_at is None

    summary = await world.services.work_queries.summary(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, preset=PrWorkPreset.MONTH),
    )
    assert summary.completed == 1
    assert summary.counted_contributions == 0
    assert summary.awaiting_validation == 1


# ===========================================================================
# 18-20: THE COUNTING BOUNDARY
# ===========================================================================


async def test_18_a_contributor_cannot_validate_their_own_work(world: World) -> None:
    """Requirement 9. **The milestone.**

    Argued against a ``TEAM_LEAD`` who holds ``PR_WORK_VALIDATE`` and did the
    work: the capability is not the question. If holding it were enough, the
    last gate before a number lands on somebody's performance record would be a
    gate they stand on both sides of.
    """
    item = await assigned(world, contributors=(world.lead,))
    await take_to_completed(world, item, by=world.lead)

    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.approve(
            actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "self_validation"
    # And nothing moved.
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.PENDING


async def test_19_not_even_an_owner_may_validate_their_own_work(world: World) -> None:
    """The rule is about who did the work, never about rank.

    An OWNER holds every permission in the system. They still cannot confirm
    their own work, because there is nobody the confirmation would be
    independent of.
    """
    item = await assigned(world, manager=world.head, contributors=(world.owner,))
    await take_to_completed(world, item, by=world.owner)
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.approve(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "self_validation"


async def test_20_an_independent_validator_approves_and_it_counts(world: World) -> None:
    """Requirements 10, 11, 12. The one act that makes anything countable.

    Atomic: the item's ``approved_at`` and the contribution's ``counted_at`` are
    written in one transaction and carry the **same instant**, which is what
    makes period attribution unambiguous.
    """
    item = await assigned(world)
    await take_to_completed(world, item, by=world.member)

    detail = await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    assert detail.item.status is PrWorkStatus.APPROVED
    assert detail.item.approved_by_user_id == world.head.id
    assert detail.item.approved_at is not None

    rows = await contributions_of(world, item.id)
    assert [row.count_status for row in rows] == [PrWorkCountStatus.COUNTED]
    assert rows[0].counted_at == detail.item.approved_at


# ===========================================================================
# 21-24: ONE JOB, MANY PEOPLE
# ===========================================================================


async def test_21_three_contributors_produce_one_item_and_three_credits(
    world: World,
) -> None:
    """Requirement 13. **The two-count rule.**

    The department did one shoot. Three people each did a day's work. Both are
    true, both are reported, and neither is derived from the other by dividing
    or multiplying.
    """
    item = await assigned(
        world, contributors=(world.member, world.other, world.lead), title="Quay TVC"
    )
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )

    rows = await contributions_of(world, item.id)
    assert len(rows) == 3
    assert all(row.count_status is PrWorkCountStatus.COUNTED for row in rows)
    # Nobody's share was divided into a third.
    assert {row.credit_weight for row in rows} == {Decimal("1.0000")}

    approved_items = await world.session.scalar(
        select(func.count()).select_from(PrWorkItem).where(PrWorkItem.status == "APPROVED")
    )
    counted = await world.session.scalar(
        select(func.count())
        .select_from(PrWorkContribution)
        .where(PrWorkContribution.count_status == "COUNTED")
    )
    assert (approved_items, counted) == (1, 3)


async def test_22_each_contributor_sees_exactly_one_counted_contribution(
    world: World,
) -> None:
    """The per-person figure a future quota will be measured against."""
    item = await assigned(world, contributors=(world.member, world.other))
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    for person in (world.member, world.other):
        summary = await world.services.work_queries.summary(
            actor=world.actor(person),
            query=WorkQuery(scope=PrWorkScope.MINE, preset=PrWorkPreset.MONTH),
        )
        assert summary.counted_contributions == 1
        assert summary.counted_work_items == 1


async def test_23_a_contributor_can_be_added_and_the_last_one_cannot_be_removed(
    world: World,
) -> None:
    """Work belongs to somebody. Removing the last person would orphan it."""
    item = await assigned(world)
    await world.services.work.add_contributor(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        user_id=world.other.id,
    )
    rows = await contributions_of(world, item.id)
    assert len(rows) == 2

    await world.services.work.remove_contributor(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        contribution_id=rows[1].id,
    )
    remaining = await contributions_of(world, item.id)
    assert len(remaining) == 1
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.remove_contributor(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            work_item_id=item.id,
            contribution_id=remaining[0].id,
        )
    assert caught.value.details["reason"] == "last_contributor"


async def test_24_a_counted_contribution_cannot_be_removed(world: World) -> None:
    """It is a thing that happened, and a period may already have counted it."""
    item = await assigned(world, contributors=(world.member, world.other))
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    rows = await contributions_of(world, item.id)
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.remove_contributor(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            work_item_id=item.id,
            contribution_id=rows[0].id,
        )
    assert caught.value.details["reason"] == "contribution_counted"


# ===========================================================================
# 25-26: QUANTITY
# ===========================================================================


async def test_25_a_hundred_comments_is_one_work_item(world: World) -> None:
    """Requirement 14. The shape is the **type's** decision, not the filer's.

    One row with ``quantity = 100``, and the unit copied from the work type - so
    nobody can choose to file a hundred rows because a hundred rows would look
    better on a count.
    """
    row = await work_type(
        world,
        code="SEEDING_COMMENT",
        name="Bình luận seeding",
        category=PrWorkCategory.COMMUNITY,
        unit=PrWorkUnit.COMMENT,
    )
    item = await assigned(world, type_row=row, quantity=Decimal("100"))
    assert item.quantity == Decimal("100.00")
    assert item.unit is PrWorkUnit.COMMENT

    total = await world.session.scalar(select(func.count()).select_from(PrWorkItem))
    assert total == 1


async def test_26_an_implausible_quantity_is_refused(world: World) -> None:
    """``quantity`` scales what an item claims, so it is bounded."""
    row = await work_type(world, code="SEED", name="Seed", unit=PrWorkUnit.COMMENT)
    for bad in (Decimal("0"), Decimal("-5"), Decimal("999999999")):
        with pytest.raises(PrValidationError) as caught:
            await assigned(world, type_row=row, quantity=bad)
        assert caught.value.details["field"] == "quantity"


# ===========================================================================
# 27-29: CONCURRENCY AND FINALITY
# ===========================================================================


async def test_27_a_second_approval_does_not_count_anything_twice(world: World) -> None:
    """Requirement 21. The lock plus the transition matrix.

    The second validator waits on the row lock, re-reads ``APPROVED``, and is
    refused by the edge - so ``counted_at`` is written once and only once.
    """
    item = await assigned(world)
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    first = (await contributions_of(world, item.id))[0].counted_at

    with pytest.raises(PrValidationError) as caught:
        await world.services.work.approve(
            actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "illegal_transition"

    rows = await contributions_of(world, item.id)
    assert len(rows) == 1
    assert rows[0].counted_at == first
    counted_events = await world.session.scalar(
        select(func.count())
        .select_from(PrWorkHistory)
        .where(PrWorkHistory.work_item_id == item.id, PrWorkHistory.event_type == "COUNTED")
    )
    assert counted_events == 1


async def test_28_approved_work_cannot_be_cancelled(world: World) -> None:
    """An **M1 product decision**, documented rather than a gap.

    Approved work has written ``counted_at`` onto its contributions, and taking
    that back is a correction against a reporting period that may since have
    been closed. Correction semantics belong to the milestone with a quota
    engine to stay consistent with.
    """
    item = await assigned(world)
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.cancel(
            actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "approved_is_final"
    assert PrWorkStatus.APPROVED in TERMINAL_WORK_STATUSES


async def test_29_cancelling_open_work_keeps_it_and_excludes_the_credit(
    world: World,
) -> None:
    """Nothing is deleted, and no contribution is left ``PENDING`` for ever."""
    item = await assigned(world)
    cancelled = await world.services.work.cancel(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        reason="Khách hủy lịch",
    )
    assert cancelled.status is PrWorkStatus.CANCELLED
    assert cancelled.cancel_reason == "Khách hủy lịch"
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.EXCLUDED
    assert await world.session.get(PrWorkItem, item.id) is not None


# ===========================================================================
# 30-34: PERIODS AND CARRY-OVER
# ===========================================================================


async def test_30_old_overdue_work_appears_in_the_overdue_query(world: World) -> None:
    """Requirement 15."""
    long_ago = datetime.now(UTC) - timedelta(days=70)
    item = await assigned(world, due_at=long_ago)
    page = await world.services.work_queries.page(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, preset=PrWorkPreset.OVERDUE),
    )
    assert [row.id for row in page.items] == [item.id]


async def test_31_a_month_filter_never_hides_carried_over_work(world: World) -> None:
    """Requirement 16. **The carry-over guarantee.**

    Work assigned in June and still unfinished in September must stay in the
    overdue view while the performance period says September - so the summary is
    asked for the month and the overdue figure is asserted to be unaffected.
    """
    june = datetime.now(UTC) - timedelta(days=90)
    await assigned(world, due_at=june)

    summary = await world.services.work_queries.summary(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, preset=PrWorkPreset.MONTH),
    )
    # Nothing was achieved this month...
    assert summary.counted_contributions == 0
    # ...and the debt is still visible, because it is not period-filtered.
    assert summary.overdue == 1

    overdue = await world.services.work_queries.page(
        actor=world.actor(world.member),
        query=WorkQuery(
            scope=PrWorkScope.MINE,
            preset=PrWorkPreset.OVERDUE,
            # Deliberately passing a narrow window a status preset must ignore.
            date_from=datetime.now(UTC).date(),
            date_to=datetime.now(UTC).date(),
        ),
    )
    assert overdue.total == 1


async def test_32_work_counts_in_the_period_it_was_validated_in(world: World) -> None:
    """Requirement 17. Assigned 31 August, validated 2 September -> September.

    ``counted_at`` is the attribution instant, and it is written at validation.
    Asserted by counting with a window that starts *after* the item was assigned
    and completed, and still finds it.
    """
    item = await assigned(world, due_at=datetime.now(UTC) - timedelta(days=1))
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )
    row = (await contributions_of(world, item.id))[0]

    today = datetime.now(UTC).date()
    summary = await world.services.work_queries.summary(
        actor=world.actor(world.member),
        query=WorkQuery(
            scope=PrWorkScope.MINE,
            preset=PrWorkPreset.CUSTOM,
            date_from=today,
            date_to=today,
        ),
    )
    assert summary.counted_contributions == 1
    assert row.counted_at is not None
    assert item.approved_at is not None
    # One instant for the item and its contribution: a three-person job cannot
    # land in two months because two writes straddled midnight. Compared
    # naively because the offline fixture is SQLite, which drops tzinfo on
    # re-read - the stored instants are identical either way.
    assert row.counted_at.replace(tzinfo=None) == item.approved_at.replace(tzinfo=None)


async def test_33_the_five_figures_are_never_collapsed_into_one(world: World) -> None:
    """Requirement: created, accepted, completed, approved and counted are five.

    Two items: one taken all the way through, one only assigned. A single "task
    count" could not tell the resulting five figures apart, which is the whole
    reason the summary has five fields.
    """
    finished = await assigned(world, title="Xong")
    await take_to_completed(world, finished, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=finished.id
    )
    await assigned(world, title="Chưa xong", type_row=await work_type(world, code="B", name="B"))

    summary = await world.services.work_queries.summary(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, preset=PrWorkPreset.MONTH),
    )
    assert (summary.created, summary.accepted, summary.completed, summary.approved) == (2, 2, 1, 1)
    assert summary.counted_contributions == 1
    assert summary.open == 1


async def test_34_overdue_excludes_finished_and_unaccepted_work(world: World) -> None:
    """A proposal is not late - nobody agreed to it. Nor is completed work.

    Completed work is waiting on a *validator*, and putting that in an
    employee's "Nợ việc" would show them a manager's queue as their own debt.
    """
    past = datetime.now(UTC) - timedelta(days=5)
    assert is_overdue(PrWorkStatus.ACCEPTED, past, now=datetime.now(UTC)) is True
    assert is_overdue(PrWorkStatus.PROPOSED, past, now=datetime.now(UTC)) is False
    assert is_overdue(PrWorkStatus.COMPLETED, past, now=datetime.now(UTC)) is False
    assert is_overdue(PrWorkStatus.APPROVED, past, now=datetime.now(UTC)) is False
    assert is_overdue(PrWorkStatus.ACCEPTED, None, now=datetime.now(UTC)) is False
    assert frozenset({PrWorkStatus.ACCEPTED, PrWorkStatus.IN_PROGRESS}) == OVERDUE_STATUSES


# ===========================================================================
# 35-37: SOURCE KEYS AND IDEMPOTENCY
# ===========================================================================


async def test_35_the_source_key_names_the_event_not_the_record(world: World) -> None:
    """Requirement 18, first half.

    One content item legitimately produces several pieces of work, so the key
    carries the **milestone**. A key of ``content:{id}`` alone would let the
    writer's credit exist and silently swallow the editor's.
    """
    content_id = uuid.uuid4()
    script = work_source_key(PrWorkSourceType.CONTENT, content_id, "SCRIPT_APPROVED")
    production = work_source_key(PrWorkSourceType.CONTENT, content_id, "PRODUCTION_APPROVED")
    assert script != production
    assert script == f"content:{content_id}:SCRIPT_APPROVED"
    assert_source_key(script)

    with pytest.raises(PrValidationError):
        work_source_key(PrWorkSourceType.MANUAL, content_id, "X")
    for bad in ("content:not-a-uuid:X", f"content:{content_id}:lowercase", "nonsense"):
        with pytest.raises(PrValidationError):
            assert_source_key(bad)


async def test_36_manual_work_carries_no_source_key(world: World) -> None:
    """M1's whole source story: every row it writes is ``MANUAL`` and unkeyed.

    No request body reaches a field that could claim derived provenance, so
    nobody can file work as though the content workflow had produced it.
    """
    item = await assigned(world)
    assert item.source_type is PrWorkSourceType.MANUAL
    assert item.source_key is None


async def test_37_two_work_items_cannot_share_a_source_key(world: World) -> None:
    """Requirement 18, second half. The partial unique index.

    Asserted at the model level because M1 has no projector to drive it: what is
    being proved is that the guarantee **exists before** anything relies on it.
    """
    row = await work_type(world)
    key = work_source_key(PrWorkSourceType.CONTENT, uuid.uuid4(), "SCRIPT_APPROVED")
    for index in range(2):
        world.session.add(
            PrWorkItem(
                code=f"WRK-2026-90000{index}",
                title="Derived",
                work_type_id=row.id,
                source_type=PrWorkSourceType.CONTENT,
                source_key=key,
                status=PrWorkStatus.ACCEPTED,
                created_by_user_id=world.owner.id,
                assigned_at=datetime.now(UTC),
            )
        )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await world.session.flush()
    await world.session.rollback()


# ===========================================================================
# 38-40: AUDIT, ISOLATION, AND THE ABSENCE OF SCORING
# ===========================================================================


async def test_38_every_step_writes_both_histories(world: World) -> None:
    """Requirement 20. Two records, two shapes, one transaction.

    ``pr_work_history`` is the story somebody reads; ``audit_logs`` is the
    security record with the request id and the structured before/after. They
    are asserted together and their **content is deliberately different**.
    """
    item = await assigned(world)
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )

    rows, names = await world.services.work.history(
        actor=world.actor(world.head), work_item_id=item.id
    )
    events = [row.event_type for row in rows]
    assert events == [
        PrWorkEventType.ASSIGNED,
        PrWorkEventType.STARTED,
        PrWorkEventType.COMPLETED,
        PrWorkEventType.APPROVED,
        PrWorkEventType.COUNTED,
    ]
    assert names[world.head.id] == world.head.full_name

    audit = (
        (await world.session.execute(select(AuditLog).where(AuditLog.entity_id == str(item.id))))
        .scalars()
        .all()
    )
    actions = {row.action for row in audit}
    assert {"pr.work.created", "pr.work.approved"} <= actions
    approved = next(row for row in audit if row.action == "pr.work.approved")
    # The audit row carries what the timeline does not: who was counted, in a
    # structured payload an investigator can read without joining anything.
    assert approved.after_data is not None
    assert str(world.member.id) in approved.after_data["counted_user_ids"]


async def test_39_the_work_ledger_touches_no_task_or_content_row(world: World) -> None:
    """Requirement 22. M1 is additive, and this is the proof.

    A full lifecycle runs and the two tables the milestone was forbidden to
    disturb are counted before and after.
    """
    before_tasks = await world.session.scalar(select(func.count()).select_from(PrTask))
    before_content = await world.session.scalar(select(func.count()).select_from(PrContentItem))

    item = await assigned(world)
    await take_to_completed(world, item, by=world.member)
    await world.services.work.approve(
        actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
    )

    assert await world.session.scalar(select(func.count()).select_from(PrTask)) == before_tasks
    assert (
        await world.session.scalar(select(func.count()).select_from(PrContentItem))
        == before_content
    )
    # And the ledger never points at either from a row M1 wrote.
    assert item.task_id is None
    assert item.content_id is None


async def test_40_nothing_in_the_work_module_scores_anything(world: World) -> None:
    """M1 records that work is valid. **Neither milestone says what it is worth.**

    A structural assertion rather than a behavioural one: the columns, the
    enums and the API models are searched for every scoring word, so adding one
    without deciding to is a red test rather than a quiet feature.

    **Re-aimed by M2, deliberately, and narrowed rather than weakened.** The
    original list forbade ``quota`` and ``OVER_QUOTA`` along with the scoring
    words, because M1 had no quota engine and a column called ``quota_status``
    appearing in it would have been somebody starting M2 by accident. M2 *is*
    the quota engine, so the word is now part of the module's vocabulary - and
    the invariant that has to survive is the one Part Y of the M2 brief states:
    **no points**. So ``quota`` comes off the list, the point words go on it,
    and the sweep is widened to cover M2's own four modules as well as M1's
    four. Nothing that was forbidden for a *scoring* reason has been allowed.

    The companion assertion lives in ``test_pr_work_quota.py``, which checks the
    same words against the same M2 files from the other side.
    """
    import pathlib

    forbidden = (
        "score",
        "base_score",
        "awarded_score",
        "score_total",
        "score_cap",
        "points",
        "multiplier",
        "quality_multiplier",
        "quality_grade",
        "bonus",
        "awarded",
    )
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    files = [
        root / "domain" / "pr" / "work.py",
        root / "domain" / "pr" / "work_labels.py",
        root / "db" / "models" / "pr_work.py",
        root / "api" / "schemas" / "pr_work.py",
        # M2's four, held to exactly the same rule.
        root / "domain" / "pr" / "work_quota.py",
        root / "domain" / "pr" / "work_quota_labels.py",
        root / "db" / "models" / "pr_work_quota.py",
        root / "api" / "schemas" / "pr_work_quota.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        # Strip comments and docstrings crudely: the words are allowed to be
        # *discussed* - "no score here" is the point - and forbidden to be
        # *declared*.
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith(("#", "*", '"'))
        )
        for word in forbidden:
            assert f"{word} =" not in code, (path.name, word)
            assert f"{word}:" not in code, (path.name, word)

    # And no column anywhere in the ledger or the quota engine is named after
    # one. ``quota`` is expected in M2's tables and is no longer swept for;
    # ``score``, ``point`` and ``bonus`` are forbidden in both.
    from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota, PrWorkQuotaAllocation

    for table in (
        PrWorkItem,
        PrWorkContribution,
        PrWorkType,
        PrWorkEvidence,
        PrWorkHistory,
        PrWorkPlan,
        PrWorkQuota,
        PrWorkQuotaAllocation,
    ):
        for column in table.__table__.columns:
            assert "score" not in column.name, (table.__tablename__, column.name)
            assert "point" not in column.name, (table.__tablename__, column.name)
            assert "bonus" not in column.name, (table.__tablename__, column.name)


async def test_41_an_employee_cannot_widen_the_scope_or_ask_about_others(
    world: World,
) -> None:
    """Requirement 19, on the read path.

    A refusal rather than a silent narrowing: reducing a department-wide request
    to one person's own work would put a figure on screen labelled as something
    it is not.
    """
    await assigned(world)
    for scope in (PrWorkScope.ALL, PrWorkScope.ASSIGNED_BY_ME, PrWorkScope.NEEDS_MY_DECISION):
        with pytest.raises(PrPermissionDeniedError):
            await world.services.work_queries.page(
                actor=world.actor(world.member), query=WorkQuery(scope=scope)
            )
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work_queries.page(
            actor=world.actor(world.member),
            query=WorkQuery(scope=PrWorkScope.MINE, user_id=world.other.id),
        )
    assert caught.value.details["reason"] == "user_filter_not_permitted"


async def test_42_the_decision_queue_omits_what_the_actor_may_not_decide(
    world: World,
) -> None:
    """A queue of refusals would be worse than no queue.

    The lead's own proposal and the work the lead contributed to are both
    absent, because the lead is refused on both.
    """
    own_proposal = await world.services.work.propose_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=(await work_type(world)).id, title="Của tôi"),
    )
    mine = await assigned(
        world,
        manager=world.head,
        contributors=(world.lead,),
        type_row=await work_type(world, code="B", name="B"),
    )
    await take_to_completed(world, mine, by=world.lead)
    theirs = await assigned(
        world,
        manager=world.head,
        contributors=(world.member,),
        type_row=await work_type(world, code="C", name="C"),
    )
    await take_to_completed(world, theirs, by=world.member)

    page = await world.services.work_queries.page(
        actor=world.actor(world.lead), query=WorkQuery(scope=PrWorkScope.NEEDS_MY_DECISION)
    )
    ids = {row.id for row in page.items}
    assert theirs.id in ids
    assert own_proposal.id not in ids
    assert mine.id not in ids


async def test_43_visibility_is_a_relationship_never_a_team(world: World) -> None:
    """M1 invents no hierarchy. Four explicit relationships decide who may look."""
    item = await assigned(world)
    # Contributor, assigner and a manager may all read it.
    for person in (world.member, world.lead, world.head):
        assert (
            await world.services.work.detail(actor=world.actor(person), work_item_id=item.id)
        ).item.id == item.id
    # An unrelated employee may not.
    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work.detail(actor=world.actor(world.other), work_item_id=item.id)
    assert caught.value.details["reason"] == "not_involved"


async def test_44_evidence_belongs_to_the_item_and_is_audited(world: World) -> None:
    """Requirement: evidence, and no new binary storage."""
    item = await assigned(world)
    row = await world.services.work.add_evidence(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        label="Link sản phẩm",
        location="https://drive.google.com/file/d/xyz/view",
        note="Bản cuối",
    )
    assert row.work_item_id == item.id
    assert row.added_by_user_id == world.member.id
    stored = await world.session.scalar(
        select(func.count())
        .select_from(PrWorkEvidence)
        .where(PrWorkEvidence.work_item_id == item.id)
    )
    assert stored == 1

    rows, _ = await world.services.work.history(
        actor=world.actor(world.member), work_item_id=item.id
    )
    assert PrWorkEventType.EVIDENCE_ADDED in [one.event_type for one in rows]


async def test_45_a_deadline_change_records_both_dates(world: World) -> None:
    """ "Why is this not overdue any more" is answerable from the timeline."""
    was = datetime.now(UTC) - timedelta(days=2)
    item = await assigned(world, due_at=was)
    now_due = datetime.now(UTC) + timedelta(days=3)
    await world.services.work.change_deadline(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        due_at=now_due,
        reason="Khách dời lịch",
    )
    rows, _ = await world.services.work.history(actor=world.actor(world.lead), work_item_id=item.id)
    moved = next(one for one in rows if one.event_type is PrWorkEventType.DEADLINE_CHANGED)
    assert moved.event_metadata is not None
    assert moved.event_metadata["from"] is not None
    assert moved.event_metadata["to"] is not None
    assert moved.note == "Khách dời lịch"


async def test_46_a_credit_weight_above_one_is_refused(world: World) -> None:
    """A share may be reduced and never inflated. Nobody is worth two people."""
    item = await assigned(world)
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.add_contributor(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            work_item_id=item.id,
            user_id=world.other.id,
            credit_weight=Decimal("2.0"),
        )
    assert caught.value.details["field"] == "credit_weight"


async def test_47_work_cannot_be_assigned_to_a_suspended_account(world: World) -> None:
    """Putting work on somebody who cannot log in is a silent way to lose it."""
    world.other.active = False
    await world.session.flush()
    with pytest.raises(PrValidationError) as caught:
        await assigned(world, contributors=(world.other,))
    assert caught.value.details["reason"] == "user_inactive"


async def test_48_a_missing_work_item_is_a_not_found(world: World) -> None:
    """And never a leak: the same refusal whether or not the row exists."""
    with pytest.raises(PrNotFoundError):
        await world.services.work.detail(actor=world.actor(world.owner), work_item_id=uuid.uuid4())


async def test_49_a_validator_can_send_finished_work_back(world: World) -> None:
    """The counterpart of approving, and it counts nothing.

    Without it a validator looking at work that is not done has only two
    options: approve it anyway, or cancel somebody's afternoon.
    """
    item = await assigned(world)
    await take_to_completed(world, item, by=world.member)
    reopened = await world.services.work.reopen(
        actor=world.actor(world.head),
        request_id=world.request_id,
        work_item_id=item.id,
        reason="Thiếu cảnh cuối",
    )
    assert reopened.status is PrWorkStatus.IN_PROGRESS
    assert reopened.completed_at is None
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.PENDING
    assert rows[0].counted_at is None


async def test_50_the_contribution_role_is_unique_per_person_per_job(
    world: World,
) -> None:
    """Crediting somebody twice for one job in one capacity is unrepresentable."""
    item = await assigned(world)
    with pytest.raises(PrConflictError) as caught:
        await world.services.work.add_contributor(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            work_item_id=item.id,
            user_id=world.member.id,
            contribution_role=PrWorkContributionRole.PRIMARY,
        )
    assert caught.value.details["reason"] == "duplicate_contributor"


async def test_51_the_list_page_carries_its_contributors_without_an_n_plus_one(
    world: World,
) -> None:
    """Four queries however many rows come back. A card never asks for itself."""
    await assigned(world, contributors=(world.member, world.other))
    await assigned(
        world,
        contributors=(world.member,),
        type_row=await work_type(world, code="B", name="B"),
        title="Việc hai",
    )
    page = await world.services.work_queries.page(
        actor=world.actor(world.member), query=WorkQuery(scope=PrWorkScope.MINE)
    )
    assert page.total == 2
    assert len(page.contributions) == 3
    assert len(page.work_types) == 2
    assert page.contributor_names[world.member.id] == world.member.full_name


async def test_52_the_date_field_chooses_which_question_is_asked(world: World) -> None:
    """ "Due this week" and "credited this week" are different questions."""
    item = await assigned(world, due_at=datetime.now(UTC) + timedelta(days=1))
    today = datetime.now(UTC).date()

    by_due = await world.services.work_queries.page(
        actor=world.actor(world.member),
        query=WorkQuery(
            scope=PrWorkScope.MINE,
            preset=PrWorkPreset.CUSTOM,
            date_field=PrWorkDateField.CREATED_AT,
            date_from=today,
            date_to=today,
        ),
    )
    assert [row.id for row in by_due.items] == [item.id]

    # Nothing has been validated, so the counted view is empty even though the
    # item exists and was created today.
    by_counted = await world.services.work_queries.page(
        actor=world.actor(world.member),
        query=WorkQuery(
            scope=PrWorkScope.MINE,
            preset=PrWorkPreset.CUSTOM,
            date_field=PrWorkDateField.COUNTED_AT,
            date_from=today,
            date_to=today,
        ),
    )
    assert by_counted.total == 0
