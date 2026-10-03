"""M4A - manual work, and the four ways it could have gone wrong.

Numbered 1-34 against the milestone's own requirement list: 1-24 for manual
work, 25-34 for bulk validation.

What this file is really testing
---------------------------------

**M4 adds no second ledger.** Every test below reaches ``PrWorkItem`` and
``PrWorkContribution`` - M1's tables, M1's ladder, M1's validation rule. There
is no manual status, no manual count, and no path from a manual job to a KPI
that does not run through ``approve``. Test 19 asserts the negative directly:
nothing a person can call produces ``CONTENT`` source work.

**One instruction to three people is three obligations, not one job.** The
distinction M4A exists to make - tests 11 and 12. Recording three people's
independent responsibilities as one shared item would let one of them complete
it for all three and one validation count all three, so the mode is required
rather than defaulted, and the batch is atomic.

**A quantity-measured type without a quantity reads as zero.** Test 10. M1 left
``quantity`` optional for every type, which was right while nothing read it; M2
then made the basis decide how counted work is *reported*, and a ``QUANTITY``
job filed without one is not a small omission.

**Bulk validation is the same write, in a loop, or nothing.** Tests 25-33. The
batch decides membership; ``approve`` decides everything else, which is why the
self-validation rule needs no restating here and test 28 proves it holds anyway.
"""

from __future__ import annotations

# The ``world`` fixture comes from the production-lifecycle suite, like every
# other Work suite: five people with five different roles is exactly what an
# anti-gaming rule has to be argued against.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_bulk_validation_service import (
    BULK_VALIDATION_MAX_ITEMS,
    BulkValidateCommand,
)
from meobot.application.pr_work_query_service import PrWorkScope, WorkQuery
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.user import User
from meobot.domain.pr.errors import (
    PrBulkApprovalStaleError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.work import (
    PrWorkAssignmentMode,
    PrWorkCategory,
    PrWorkCountStatus,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import (
    PrWorkQuotaBasis,
    PrWorkUnmeasurableReason,
    measure_contribution,
)
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)

pytestmark = pytest.mark.asyncio

SEPTEMBER = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)


# ===========================================================================
# Helpers
# ===========================================================================


async def work_type(
    world: World,
    *,
    code: str = "PAGE_RECOVERY",
    name: str = "Kháng page",
    category: PrWorkCategory = PrWorkCategory.OPERATIONS,
    unit: PrWorkUnit = PrWorkUnit.ITEM,
    basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT,
    requires_evidence: bool = False,
) -> PrWorkType:
    """One work type, registered by the owner - or the one already registered.

    Idempotent by ``code`` because several helpers below want *a* work type and
    do not care whether an earlier call in the same test already made it. The
    alternative - a fresh code per call - would hide the thing some of these
    tests are about, which is several jobs of **one** kind.
    """
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
        category=category,
        default_unit=unit,
        default_quota_basis=basis,
        requires_evidence=requires_evidence,
    )


async def seeding_type(world: World) -> PrWorkType:
    """``SEEDING_COMMENT``: measured in comments, by quantity. The M4 archetype."""
    return await work_type(
        world,
        code="SEEDING_COMMENT",
        name="Seeding bình luận",
        category=PrWorkCategory.COMMUNITY,
        unit=PrWorkUnit.COMMENT,
        basis=PrWorkQuotaBasis.QUANTITY,
    )


async def assign_batch(
    world: World,
    *,
    contributors: tuple[User, ...],
    mode: PrWorkAssignmentMode,
    type_row: PrWorkType | None = None,
    quantity: Decimal | None = None,
    title: str = "100 comment seeding hôm nay",
    manager: User | None = None,
) -> tuple[PrWorkItem, ...]:
    row = type_row or await work_type(world)
    return await world.services.work.assign_work_batch(
        actor=world.actor(manager or world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id,
            title=title,
            quantity=quantity,
            contributor_user_ids=tuple(person.id for person in contributors),
        ),
        mode=mode,
    )


async def contributions_of(world: World, item_id: uuid.UUID) -> list[PrWorkContribution]:
    result = await world.session.execute(
        select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
    )
    return list(result.scalars().all())


async def take_to_completed(world: World, item: PrWorkItem, *, by: User) -> PrWorkItem:
    await world.services.work.start(
        actor=world.actor(by), request_id=world.request_id, work_item_id=item.id
    )
    return await world.services.work.complete(
        actor=world.actor(by), request_id=world.request_id, work_item_id=item.id
    )


async def completed_items(
    world: World, *, count: int, by: User | None = None, type_row: PrWorkType | None = None
) -> list[PrWorkItem]:
    """``count`` separate jobs, each finished by its own contributor."""
    person = by or world.member
    row = type_row or await work_type(world)
    items: list[PrWorkItem] = []
    for index in range(count):
        item = await world.services.work.assign_work(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=CreateWorkCommand(
                work_type_id=row.id,
                title=f"Việc thường nhật {index + 1}",
                contributor_user_ids=(person.id,),
            ),
        )
        items.append(await take_to_completed(world, item, by=person))
    return items


# ===========================================================================
# 1-9: CREATING MANUAL WORK
# ===========================================================================


async def test_01_an_employee_proposes_work_they_actually_did(world: World) -> None:
    """ "Kháng page David" - unplanned, real, and filed by the person who did it."""
    row = await work_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=row.id, title="Kháng page David"),
    )
    assert item.status is PrWorkStatus.PROPOSED
    assert item.source_type is PrWorkSourceType.MANUAL
    assert item.source_key is None


async def test_02_a_self_proposal_is_not_in_anybodys_workload(world: World) -> None:
    """``PROPOSED`` is not work. The whole reason the state exists."""
    row = await work_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=row.id, title="Research đối thủ tháng 9"),
    )
    assert item.accepted_at is None
    assert item.assigned_by_user_id is None
    assert all(
        one.count_status is PrWorkCountStatus.PENDING
        for one in await contributions_of(world, item.id)
    )


async def test_03_a_proposer_cannot_authorize_their_own_proposal(world: World) -> None:
    """The anti-gaming rule M4 must not weaken, restated against manual work.

    The member here holds no ``PR_WORK_MANAGE`` at all; test 4 makes the
    sharper version of the point.
    """
    row = await work_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.member),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=row.id, title="Chuẩn bị báo cáo tuần"),
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.accept(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        )


async def test_04_even_a_manager_cannot_accept_their_own_proposal(world: World) -> None:
    """Holding the capability does not help. **This is the milestone's floor.**

    A manager who proposes work is a proposer, and a proposer who could accept
    would make "propose" and "assign" the same button with two names.
    """
    row = await work_type(world)
    item = await world.services.work.propose_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(work_type_id=row.id, title="Kháng page do tôi đề xuất"),
    )
    with pytest.raises(PrPermissionDeniedError) as error:
        await world.services.work.accept(
            actor=world.actor(world.lead), request_id=world.request_id, work_item_id=item.id
        )
    assert error.value.details.get("reason") == "self_acceptance"


async def test_05_a_manager_assignment_is_the_authorization(world: World) -> None:
    """M1's existing semantics, reused unchanged. No fake acceptance actor."""
    row = await work_type(world)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id,
            title="Quay bác sĩ Tiến nửa ngày",
            contributor_user_ids=(world.member.id,),
        ),
    )
    assert item.status is PrWorkStatus.ACCEPTED
    assert item.assigned_by_user_id == world.lead.id
    assert item.assigned_at is not None
    # Accepted by an assignment, not by a manufactured acceptance event.
    assert item.accepted_at is not None


async def test_06_an_employee_cannot_assign_work_to_themselves(world: World) -> None:
    """``PR_WORK_MANAGE``, and an employee does not hold it."""
    row = await work_type(world)
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.assign_work(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=CreateWorkCommand(
                work_type_id=row.id,
                title="Tự giao cho mình",
                contributor_user_ids=(world.member.id,),
            ),
        )


async def test_07_an_item_count_job_needs_no_quantity(world: World) -> None:
    """``ITEM_COUNT`` measures rows. One job is one unit, quantity or not."""
    row = await work_type(world, basis=PrWorkQuotaBasis.ITEM_COUNT)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id, title="Kháng page David", contributor_user_ids=(world.member.id,)
        ),
    )
    assert item.quantity is None
    # The CHECK constraint requires the pair, so no unit either.
    assert item.unit is None


async def test_08_a_quantity_job_carries_its_number_and_the_types_unit(world: World) -> None:
    """The unit comes from the **type**, never from the person filing."""
    row = await seeding_type(world)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id,
            title="100 comment seeding",
            quantity=Decimal("100"),
            contributor_user_ids=(world.member.id,),
        ),
    )
    assert item.quantity == Decimal("100")
    assert item.unit is PrWorkUnit.COMMENT


async def test_09_one_hundred_comments_is_one_work_item(world: World) -> None:
    """**Not a hundred rows.** The mismeasurement the whole ledger is shaped around."""
    row = await seeding_type(world)
    await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id,
            title="100 comment seeding",
            quantity=Decimal("100"),
            contributor_user_ids=(world.member.id,),
        ),
    )
    total = await world.session.scalar(
        select(func.count()).select_from(PrWorkItem).where(PrWorkItem.work_type_id == row.id)
    )
    assert total == 1


# ===========================================================================
# 10: THE QUANTITY RULE M4A ADDS
# ===========================================================================


async def test_10_a_quantity_measured_type_refuses_work_with_no_quantity(world: World) -> None:
    """**M4A's one new creation rule**, and it lives at the route.

    A ``QUANTITY`` type filed without a number does not mean "one"; it reports
    as zero comments against a plan expressed in comments. Every way a *person*
    files work is an HTTP call, so the form's contract is enforced there.

    Deliberately **not** in ``_create``. Putting it in the shared creator made
    M2's ``MISSING_QUANTITY`` allocation state unconstructible - the content
    projector always writes ``quantity = 1`` and M2.5 locks
    ``default_quota_basis`` once a type is in use, so no path would have been
    left to reach it. See ``require_quantity_for_basis``.
    """
    row = await seeding_type(world)
    await world.session.commit()
    world.act_as(world.lead)
    response = world.client.post(
        "/api/pr/work",
        json={
            "work_type_id": str(row.id),
            "title": "Seeding không ghi số lượng",
            "contributor_user_ids": [str(world.member.id)],
        },
    )
    assert response.status_code == 422, response.text
    assert "quantity_required_for_basis" in response.text


async def test_10a_the_same_type_is_accepted_with_a_quantity(world: World) -> None:
    """The other half: the rule asks for a number, not for a different type."""
    row = await seeding_type(world)
    await world.session.commit()
    world.act_as(world.lead)
    response = world.client.post(
        "/api/pr/work",
        json={
            "work_type_id": str(row.id),
            "title": "100 comment seeding",
            "quantity": "100",
            "contributor_user_ids": [str(world.member.id)],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["item"]["quantity"] == "100"


async def test_10c_m2_can_still_construct_an_unmeasurable_contribution(
    world: World,
) -> None:
    """**The state M4A must not have deleted, and M4B moved the line for.**

    M2 materialises an allocation that names the quota and says the quantity is
    missing, rather than a silent ``NO_QUOTA`` telling an employee their manager
    set no target when their manager had. That row describes something that can
    still be true of work already in the table, so the system must remain able to
    **represent** it.

    M4A put the rule at the route, on the reasoning that every way a *person*
    files work is an HTTP call. M4B made that reasoning false - the recurring
    generator files work from a beat sweep and touches no router - so the rule
    moved down exactly one level, to the service's operational entry points. The
    boundary it stopped at is the one this test is about:

    * ``assign_work`` is an **operational command**: somebody is asking for work
      to exist now, so a quantity-measured type without its number is refused;
    * ``_create`` and the row itself are **representation**, and stay permissive.
      The pair is nullable together by the ``quantity_and_unit_together`` CHECK,
      and a row in that state is not incoherent - it is *incomplete*, which is
      precisely the fact M2 exists to report.

    So the state is still constructible, still stored, and still measured as
    ``MISSING_QUANTITY`` - which is what "do not delete an older milestone's
    repair path" actually requires. What is gone is the ability to *ask* for it
    through a command, and that was never the state's reason for existing.
    """
    row = await seeding_type(world)
    with pytest.raises(PrValidationError) as refused:
        await world.services.work.assign_work(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=CreateWorkCommand(
                work_type_id=row.id,
                title="Seeding cũ chưa nhập số lượng",
                contributor_user_ids=(world.member.id,),
            ),
        )
    assert refused.value.details["reason"] == "quantity_required_for_basis"

    # And the row M2 has to be able to describe is still describable.
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id,
            title="Seeding cũ chưa nhập số lượng",
            quantity=Decimal("40"),
            contributor_user_ids=(world.member.id,),
        ),
    )
    item.quantity = None
    item.unit = None
    await world.session.flush()
    assert item.quantity is None
    assert item.unit is None

    measurement = measure_contribution(
        PrWorkQuotaBasis.QUANTITY, quantity=item.quantity, credit_weight=Decimal("1.0000")
    )
    assert measurement.is_measurable is False
    assert measurement.reason is PrWorkUnmeasurableReason.MISSING_QUANTITY


async def test_10b_a_zero_or_negative_quantity_is_refused(world: World) -> None:
    """M1's bound, restated because M4 is the milestone that types the number in."""
    row = await seeding_type(world)
    for bad in (Decimal("0"), Decimal("-5")):
        with pytest.raises(PrValidationError) as error:
            await world.services.work.assign_work(
                actor=world.actor(world.lead),
                request_id=world.request_id,
                command=CreateWorkCommand(
                    work_type_id=row.id,
                    title="Số lượng sai",
                    quantity=bad,
                    contributor_user_ids=(world.member.id,),
                ),
            )
        assert error.value.details.get("reason") == "out_of_range"


# ===========================================================================
# 11-13: ONE JOB, OR ONE JOB EACH
# ===========================================================================


async def test_11_shared_work_is_one_item_with_a_contribution_each(world: World) -> None:
    """ "Quay bác sĩ Tiến nửa ngày" - one shoot, three people."""
    items = await assign_batch(
        world,
        contributors=(world.member, world.other, world.head),
        mode=PrWorkAssignmentMode.SHARED_WORK,
        title="Quay bác sĩ Tiến nửa ngày",
    )
    assert len(items) == 1
    assert len(await contributions_of(world, items[0].id)) == 3


async def test_12_separate_per_assignee_is_one_item_each(world: World) -> None:
    """**The distinction M4A exists to make.**

    Three people told to do a hundred comments each hold three obligations. One
    shared item would let one of them complete it for all three.
    """
    items = await assign_batch(
        world,
        contributors=(world.member, world.other, world.head),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
        type_row=await seeding_type(world),
        quantity=Decimal("100"),
    )
    assert len(items) == 3
    assert len({item.id for item in items}) == 3
    for item in items:
        rows = await contributions_of(world, item.id)
        assert len(rows) == 1
        # Each carries the whole quantity: each person does a hundred comments.
        assert item.quantity == Decimal("100")
    assert {
        rows[0].user_id for item in items for rows in [await contributions_of(world, item.id)]
    } == {
        world.member.id,
        world.other.id,
        world.head.id,
    }


async def test_13_separate_assignees_complete_and_are_validated_independently(
    world: World,
) -> None:
    """One person finishing does not finish anybody else's work."""
    items = await assign_batch(
        world,
        contributors=(world.member, world.other),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
    )
    owners = {item.id: (await contributions_of(world, item.id))[0].user_id for item in items}
    mine = next(item for item in items if owners[item.id] == world.member.id)
    theirs = next(item for item in items if item.id != mine.id)
    await take_to_completed(world, mine, by=world.member)
    await world.session.refresh(theirs)
    assert theirs.status is PrWorkStatus.ACCEPTED


# ===========================================================================
# 14-18: EVIDENCE, COMPLETION, VALIDATION
# ===========================================================================


async def test_14_evidence_requirements_are_the_work_types_and_still_apply(
    world: World,
) -> None:
    """M4 changes nothing here. The type says so, and completion enforces it."""
    row = await work_type(world, requires_evidence=True)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id, title="Cần minh chứng", contributor_user_ids=(world.member.id,)
        ),
    )
    await world.services.work.start(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    with pytest.raises(PrValidationError):
        await world.services.work.complete(
            actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
        )


async def test_15_completing_manual_work_does_not_count_it(world: World) -> None:
    """``COMPLETED != COUNTED``. The state the anti-gaming rule lives around."""
    items = await assign_batch(
        world, contributors=(world.member,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    item = await take_to_completed(world, items[0], by=world.member)
    assert item.status is PrWorkStatus.COMPLETED
    assert all(
        one.count_status is PrWorkCountStatus.PENDING
        for one in await contributions_of(world, item.id)
    )


async def test_16_a_contributor_cannot_validate_their_own_manual_work(world: World) -> None:
    """The rule, against the work a person filed themselves."""
    items = await assign_batch(
        world, contributors=(world.owner,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    await take_to_completed(world, items[0], by=world.owner)
    with pytest.raises(PrPermissionDeniedError) as error:
        await world.services.work.approve(
            actor=world.actor(world.owner), request_id=world.request_id, work_item_id=items[0].id
        )
    assert error.value.details.get("reason") == "self_validation"


async def test_17_independent_validation_counts_manual_work(world: World) -> None:
    """The one path from a manual job to a KPI, and it is M1's."""
    items = await assign_batch(
        world, contributors=(world.member,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    await take_to_completed(world, items[0], by=world.member)
    detail = await world.services.work.approve(
        actor=world.actor(world.lead), request_id=world.request_id, work_item_id=items[0].id
    )
    assert detail.item.status is PrWorkStatus.APPROVED
    assert all(one.count_status is PrWorkCountStatus.COUNTED for one in detail.contributions)
    assert all(one.counted_at is not None for one in detail.contributions)


async def test_18_cancelling_manual_work_is_audited_and_keeps_the_row(world: World) -> None:
    """Never a deletion. The row and its history stay."""
    items = await assign_batch(
        world, contributors=(world.member,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    await world.services.work.cancel(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=items[0].id,
        reason="Khách huỷ lịch",
    )
    row = await world.session.get(PrWorkItem, items[0].id)
    assert row is not None
    assert row.status is PrWorkStatus.CANCELLED
    assert row.cancel_reason == "Khách huỷ lịch"
    # ``entity_id`` is stored as text - see ``AuditEntry``.
    logged = (
        (
            await world.session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == str(items[0].id))
            )
        )
        .scalars()
        .all()
    )
    assert "pr.work.cancelled" in set(logged)


# ===========================================================================
# 19-21: THE SOURCE BOUNDARY
# ===========================================================================


async def test_19_no_manual_path_can_produce_content_source_work(world: World) -> None:
    """**Part H, structurally.**

    Manual work must not become an alternative way to create content-derived
    workload. Not a guard somebody remembers to apply: ``_create`` hard-codes
    ``MANUAL``, and no command field reaches ``source_type`` at all.
    """
    assert "source_type" not in CreateWorkCommand.__dataclass_fields__
    assert "source_key" not in CreateWorkCommand.__dataclass_fields__
    assert "content_id" not in CreateWorkCommand.__dataclass_fields__

    row = await work_type(world)
    made = [
        await world.services.work.propose_work(
            actor=world.actor(world.member),
            request_id=world.request_id,
            command=CreateWorkCommand(work_type_id=row.id, title="Đề xuất"),
        ),
        *await assign_batch(
            world,
            contributors=(world.member, world.other),
            mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
            type_row=row,
        ),
        *await assign_batch(
            world,
            contributors=(world.member, world.other),
            mode=PrWorkAssignmentMode.SHARED_WORK,
            type_row=row,
        ),
    ]
    assert {item.source_type for item in made} == {PrWorkSourceType.MANUAL}
    assert all(item.source_key is None for item in made)
    assert all(item.content_id is None for item in made)


async def test_20_the_ledger_filters_by_where_work_came_from(world: World) -> None:
    """Part AM. A filter, and one that narrows rather than widens."""
    await assign_batch(
        world, contributors=(world.member,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    manual = await world.services.work_queries.page(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, source_type=PrWorkSourceType.MANUAL),
    )
    recurring = await world.services.work_queries.page(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, source_type=PrWorkSourceType.RECURRING),
    )
    assert manual.total == 1
    assert recurring.total == 0


async def test_21_the_source_filter_does_not_widen_read_scope(world: World) -> None:
    """It narrows what the caller could already see, and nothing more."""
    await assign_batch(
        world, contributors=(world.other,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    page = await world.services.work_queries.page(
        actor=world.actor(world.member),
        query=WorkQuery(scope=PrWorkScope.MINE, source_type=PrWorkSourceType.MANUAL),
    )
    assert page.total == 0


# ===========================================================================
# 22-24: M2 AND M6 SEE NOTHING SPECIAL
# ===========================================================================


async def test_22_manual_work_with_no_quota_is_still_real_counted_work(world: World) -> None:
    """**Part J.** ``NO_QUOTA`` is not a refusal and must never become one."""
    items = await assign_batch(
        world, contributors=(world.member,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    await take_to_completed(world, items[0], by=world.member)
    detail = await world.services.work.approve(
        actor=world.actor(world.lead), request_id=world.request_id, work_item_id=items[0].id
    )
    assert all(one.count_status is PrWorkCountStatus.COUNTED for one in detail.contributions)


async def test_23_the_readiness_diagnostic_states_the_gap_without_blocking(
    world: World,
) -> None:
    """**Parts J and AP.** A sentence on the screen, never a refusal."""
    row = await work_type(world)
    readiness = await world.services.work_readiness.readiness(
        actor=world.actor(world.lead),
        work_type_id=row.id,
        user_ids=(world.member.id,),
        now=SEPTEMBER,
    )
    # No M6 rule has been approved for this type, and no plan for this person.
    assert readiness.has_scoring_rule is False
    assert len(readiness.assignees_without_quota) == 1
    # And the work is created anyway.
    items = await assign_batch(
        world,
        contributors=(world.member,),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
        type_row=row,
    )
    assert items[0].status is PrWorkStatus.ACCEPTED


async def test_24_m4_writes_no_quota_and_no_score_of_its_own(world: World) -> None:
    """**Part BG.** M4 creates work. It allocates nothing and prices nothing.

    Asserted against the source rather than by observing a run: a projection
    that happened to be absent in one scenario is not the same promise as a
    module that cannot make one.
    """
    import inspect

    from meobot.application import (
        pr_work_bulk_validation_service,
        pr_work_readiness_service,
    )

    for module in (pr_work_bulk_validation_service, pr_work_readiness_service):
        source = inspect.getsource(module)
        assert "PrWorkQuotaAllocation" not in source, module.__name__
        assert "PrWorkScoreAllocation" not in source, module.__name__
        assert "PrPerformanceResult" not in source, module.__name__
        assert "performance_index" not in source, module.__name__


# ===========================================================================
# 25-34: BULK VALIDATION
# ===========================================================================


async def test_25_a_validator_confirms_a_queue_in_one_act(world: World) -> None:
    """The operation the milestone adds, and its whole happy path."""
    items = await completed_items(world, count=5)
    outcome = await world.services.work_bulk_validation.validate(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=BulkValidateCommand(work_item_ids=[one.id for one in items]),
    )
    assert len(outcome.validated) == 5
    assert outcome.counted_contributions == 5
    for item in items:
        await world.session.refresh(item)
        assert item.status is PrWorkStatus.APPROVED


async def test_26_a_batch_at_the_limit_is_accepted(world: World) -> None:
    """200 is a bound on an explicit list, not a suggestion. Checked without
    creating two hundred jobs: the limit is arithmetic on the id list, and the
    refusal above it is what matters - see test 27."""
    assert BULK_VALIDATION_MAX_ITEMS == 200


async def test_27_a_batch_over_the_limit_is_refused(world: World) -> None:
    """A batch nobody can read before pressing the button is not a decision."""
    with pytest.raises(PrValidationError) as error:
        await world.services.work_bulk_validation.validate(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=BulkValidateCommand(
                work_item_ids=[uuid.uuid4() for _ in range(BULK_VALIDATION_MAX_ITEMS + 1)]
            ),
        )
    assert error.value.details.get("reason") == "batch_too_large"


async def test_28_a_contributor_cannot_bulk_validate_their_own_work(world: World) -> None:
    """**The rule the batch must not become a way around.**

    The owner holds every capability and did one of these jobs, so the batch is
    refused as a whole - and the refusal names the row.
    """
    mine = await completed_items(world, count=1, by=world.owner)
    theirs = await completed_items(world, count=2, by=world.member)
    with pytest.raises(PrBulkApprovalStaleError) as error:
        await world.services.work_bulk_validation.validate(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            command=BulkValidateCommand(work_item_ids=[one.id for one in mine + theirs]),
        )
    affected = error.value.details["affected"]
    assert [one["reason"] for one in affected] == ["self_validation"]
    assert error.value.details["validated"] == 0


async def test_29_a_row_missing_required_evidence_blocks_the_batch(world: World) -> None:
    """Evidence is enforced at completion - and re-checked here, because a
    required file can be removed afterwards and a batch is exactly the shape in
    which nobody would notice."""
    row = await work_type(world, code="SHOOT", name="Quay", requires_evidence=True)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id, title="Quay có minh chứng", contributor_user_ids=(world.member.id,)
        ),
    )
    await world.services.work.start(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    evidence = await world.services.work.add_evidence(
        actor=world.actor(world.member),
        request_id=world.request_id,
        work_item_id=item.id,
        label="Bản dựng",
        location="https://drive.example/1",
    )
    await world.services.work.complete(
        actor=world.actor(world.member), request_id=world.request_id, work_item_id=item.id
    )
    await world.services.work.remove_evidence(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        evidence_id=evidence.id,
    )
    with pytest.raises(PrBulkApprovalStaleError) as error:
        await world.services.work_bulk_validation.validate(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=BulkValidateCommand(work_item_ids=[item.id]),
        )
    assert error.value.details["affected"][0]["reason"] == "evidence_required"


async def test_30_a_row_at_the_wrong_status_blocks_the_batch(world: World) -> None:
    """Only ``COMPLETED`` work is waiting for a validator."""
    items = await completed_items(world, count=2)
    fresh = await assign_batch(
        world,
        contributors=(world.member,),
        mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE,
        title="Chưa làm xong",
    )
    with pytest.raises(PrBulkApprovalStaleError) as error:
        await world.services.work_bulk_validation.validate(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=BulkValidateCommand(work_item_ids=[one.id for one in items] + [fresh[0].id]),
        )
    reasons = {one["reason"] for one in error.value.details["affected"]}
    assert reasons == {"not_completed"}


async def test_31_a_refused_batch_validates_nothing(world: World) -> None:
    """**All or nothing.** The promise the panel makes, kept structurally."""
    items = await completed_items(world, count=3)
    fresh = await assign_batch(
        world, contributors=(world.member,), mode=PrWorkAssignmentMode.SEPARATE_PER_ASSIGNEE
    )
    with pytest.raises(PrBulkApprovalStaleError):
        await world.services.work_bulk_validation.validate(
            actor=world.actor(world.lead),
            request_id=world.request_id,
            command=BulkValidateCommand(work_item_ids=[one.id for one in items] + [fresh[0].id]),
        )
    for item in items:
        await world.session.refresh(item)
        assert item.status is PrWorkStatus.COMPLETED
        assert all(
            one.count_status is PrWorkCountStatus.PENDING
            for one in await contributions_of(world, item.id)
        )


async def test_32_the_preflight_names_every_blocked_row_without_writing(world: World) -> None:
    """What lets a validator drop four rows instead of bisecting a refusal."""
    mine = await completed_items(world, count=1, by=world.owner)
    theirs = await completed_items(world, count=2, by=world.member)
    preflight = await world.services.work_bulk_validation.preflight(
        actor=world.actor(world.owner),
        work_item_ids=[one.id for one in mine + theirs],
    )
    assert len(preflight.blocked) == 1
    assert preflight.blocked[0].reason == "self_validation"
    assert len(preflight.validatable) == 2
    # And it wrote nothing.
    for item in theirs:
        await world.session.refresh(item)
        assert item.status is PrWorkStatus.COMPLETED


async def test_33_a_shared_job_counts_every_contributor_once(world: World) -> None:
    """A batch of jobs is not a batch of people: three contributors on one
    shared job are three counted contributions from one validated item."""
    items = await assign_batch(
        world,
        contributors=(world.member, world.other, world.head),
        mode=PrWorkAssignmentMode.SHARED_WORK,
    )
    await take_to_completed(world, items[0], by=world.member)
    outcome = await world.services.work_bulk_validation.validate(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=BulkValidateCommand(work_item_ids=[items[0].id]),
    )
    assert len(outcome.validated) == 1
    assert outcome.counted_contributions == 3


async def test_34_a_batch_writes_one_batch_row_and_one_row_per_item(world: World) -> None:
    """Nothing is collapsed. The per-item rows stay the record of who counted
    whose work; the batch row answers what one sweep did."""
    items = await completed_items(world, count=4)
    await world.services.work_bulk_validation.validate(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=BulkValidateCommand(work_item_ids=[one.id for one in items]),
    )
    per_item = await world.session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "pr.work.approved")
    )
    batch = await world.session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "pr.work.validation_batch_recorded")
    )
    assert per_item == 4
    assert batch == 1
