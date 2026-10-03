"""Who owns the work taxonomy, and what a used work type refuses. M2.5.

M1 built ``pr_work_types`` and one route into it. M2 taught quotas to read it.
Neither gave the business a way to own it, and the table shipped **empty** - so
"Giao công việc" offered a dropdown with nothing in it and a KPI plan had
nothing to set a quota on. That is the bug this milestone exists for, and test
22-24 are the ones that close it.

The rule the rest of the file is about
---------------------------------------

``code``, ``default_quota_basis`` and ``default_unit`` are what historical rows
*mean*. A row filed under ``SEEDING_COMMENT`` measured by ``QUANTITY`` in
``COMMENT`` says ``120`` is a hundred and twenty comments; the same type flipped
to ``ITEM_COUNT`` would say it is one job, and every counted row in every
reported month would quietly change what it claims.

So the lock is on **use**, not on the field:

* unused - everything is editable, because a type created five minutes ago with
  the wrong basis is a typo (test 10);
* used - those three are refused, explicitly and by name (tests 11-13), while
  the label half stays editable (test 14).

And there is no delete. Test 28 asserts the absence of the route, because "we
just never added one" is not a guarantee.

Nothing here contacts a network.
"""

from __future__ import annotations

# The ``world`` fixture comes from the production-lifecycle suite and the work
# helpers from M1's own file, for the reason M2's suite gives: a lookalike
# fixture would let them drift, and a taxonomy test is meaningless without M1's
# ladder underneath it.
# ruff: noqa: F811
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_work_service import CreateWorkCommand
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.domain.audit.models import AuditAction
from meobot.domain.pr.errors import (
    PrConflictError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import PrWorkCategory, PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_types import BOOTSTRAP_WORK_TYPES, WORK_TYPE_STRUCTURE_LOCKED
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    world,
)
from tests.unit.test_pr_work_core import take_to_completed
from tests.unit.test_pr_work_quota import approved_plan, month

pytestmark = pytest.mark.asyncio


async def create(world: World, who=None, **kwargs):
    """Register a type as somebody. Defaults to the owner."""
    body = {
        "code": "SHORT_SCRIPT",
        "name": "Kịch bản ngắn",
        "category": PrWorkCategory.CONTENT,
        **kwargs,
    }
    return await world.services.work.create_work_type(
        actor=world.actor(who or world.owner), request_id=world.request_id, **body
    )


async def used(world: World, type_row: PrWorkType) -> PrWorkItem:
    """Put one real work item under a type, so it counts as used."""
    return await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id,
            title="Việc thật",
            quantity=Decimal("2"),
            contributor_user_ids=(world.member.id,),
        ),
    )


# ===========================================================================
# 1-6: WHO MAY OWN THE TAXONOMY
# ===========================================================================


async def test_01_an_admin_creates_a_work_type(world: World) -> None:
    """The whole point of M2.5: this needs no developer and no INSERT."""
    row = await create(world, code="video_edit", name="Dựng video")
    assert row.code == "VIDEO_EDIT", "the code is normalised to upper snake"
    assert row.name == "Dựng video"
    assert row.is_active is True


async def test_02_a_duplicate_code_is_refused(world: World) -> None:
    """The code is the identifier history is filed under. Two would be ambiguous."""
    await create(world)
    with pytest.raises(PrConflictError) as caught:
        await create(world)
    assert caught.value.details["reason"] == "duplicate_code"


async def test_03_a_malformed_code_is_refused(world: World) -> None:
    """A code is machine-readable. Accents and punctuation are the display name's job."""
    for bad in ("Kịch bản", "video-edit!", ""):
        with pytest.raises(PrValidationError) as caught:
            await create(world, code=bad)
        assert caught.value.details["reason"] == "malformed_code"


async def test_04_an_employee_cannot_create_a_work_type(world: World) -> None:
    """Requirement 1. A taxonomy anybody may extend while filing work is a free-text
    label with extra steps, and the report it feeds stops meaning anything."""
    with pytest.raises(PrPermissionDeniedError):
        await create(world, world.member)


async def test_05_managing_work_does_not_confer_configuring_it(world: World) -> None:
    """``PR_WORK_MANAGE`` assigns work. It does **not** decide what kinds exist.

    The separation M1 wrote down and M2.5 depends on: a Trưởng nhóm picks from
    the list and does not edit the list.
    """
    lead = world.actor(world.lead)
    assert await world.services.capabilities.allows(lead, PrCapability.PR_WORK_MANAGE)
    assert not await world.services.capabilities.allows(lead, PrCapability.PR_WORK_CONFIGURE)
    with pytest.raises(PrPermissionDeniedError):
        await create(world, world.lead)


async def test_06_pr_work_configure_may_create(world: World) -> None:
    """The owner holds it, and the capability is the one the service checks."""
    assert await world.services.capabilities.allows(
        world.actor(world.owner), PrCapability.PR_WORK_CONFIGURE
    )
    assert await create(world) is not None


# ===========================================================================
# 7-9: WHAT EACH PERSON SEES
# ===========================================================================


async def test_07_an_employee_reads_the_active_taxonomy(world: World) -> None:
    """Selecting is not configuring. A picker needs the list."""
    await create(world)
    rows = await world.services.work.list_work_types(actor=world.actor(world.member))
    assert [row.code for row in rows] == ["SHORT_SCRIPT"]


async def test_08_a_retired_type_leaves_the_default_list(world: World) -> None:
    """It stops being offered. That is the whole of what deactivation does."""
    row = await create(world)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    rows = await world.services.work.list_work_types(actor=world.actor(world.member))
    assert rows == []


async def test_09_only_a_configurer_may_list_the_retired_ones(world: World) -> None:
    """M1 let any ``PR_WORK_EXECUTE`` holder pass ``include_inactive`` - which put a
    retired type one query parameter away from a picker that asked for everything."""
    row = await create(world)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.list_work_types(
            actor=world.actor(world.member), include_inactive=True
        )
    rows = await world.services.work.list_work_types(
        actor=world.actor(world.owner), include_inactive=True
    )
    assert [one.code for one in rows] == ["SHORT_SCRIPT"]


# ===========================================================================
# 10-14: THE STRUCTURAL LOCK
# ===========================================================================


async def test_10_an_unused_type_is_fully_editable(world: World) -> None:
    """A type nobody has used yet is a draft. Forcing a second type to fix a typo
    would leave the mistake in the picker for ever."""
    row = await create(world)
    assert await world.services.work.is_work_type_in_use(row.id) is False
    updated = await world.services.work.update_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        code="SEEDING_COMMENT",
        name="Comment seeding",
        category=PrWorkCategory.COMMUNITY,
        default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        default_unit=PrWorkUnit.COMMENT,
    )
    assert updated.code == "SEEDING_COMMENT"
    assert updated.default_quota_basis is PrWorkQuotaBasis.QUANTITY
    assert updated.default_unit is PrWorkUnit.COMMENT
    assert updated.category is PrWorkCategory.COMMUNITY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "RENAMED_CODE"),
        ("default_quota_basis", PrWorkQuotaBasis.QUANTITY),
    ],
)
async def test_11_to_12_a_used_type_refuses_each_structural_field(
    world: World, field: str, value: object
) -> None:
    """Tests 11 and 12. Refused **by name**, never silently ignored.

    A structural edit that is quietly dropped is worse than one that is refused:
    the screen then shows the old value and the person believes they changed it.

    ``default_unit`` left this list with the period-container patch - see
    ``test_13_a_used_type_renames_its_unit_without_touching_history``.
    """
    row = await create(world)
    await used(world, row)
    assert await world.services.work.is_work_type_in_use(row.id) is True
    with pytest.raises(PrConflictError) as caught:
        await world.services.work.update_work_type(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            work_type_id=row.id,
            **{field: value},
        )
    assert caught.value.details["reason"] == WORK_TYPE_STRUCTURE_LOCKED
    assert caught.value.details["field"] == field
    await world.session.refresh(row)
    assert row.code == "SHORT_SCRIPT", "nothing was written before the refusal"


async def test_13_a_used_type_renames_its_unit_without_touching_history(world: World) -> None:
    """The unit is what a result is **called**. Period-container patch.

    "Sản phẩm" to "khách hàng" on a type in use is the correction the patch
    exists for, and it must not rewrite what was filed: the job filed under the
    old unit keeps it, because every amount stores its own copy.
    """
    row = await create(world)
    item = await used(world, row)
    before = item.unit
    updated = await world.services.work.update_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        default_unit=PrWorkUnit.CUSTOMER,
    )
    assert updated.default_unit is PrWorkUnit.CUSTOMER
    await world.session.refresh(item)
    assert item.unit is before, "a one-off job keeps the unit it was filed with"
    assert updated.code == "SHORT_SCRIPT" and updated.default_quota_basis is not None


async def test_14_a_used_type_still_renames(world: World) -> None:
    """Nothing authorises, counts or groups on the label, so renaming is safe -
    and a taxonomy nobody may retitle is one nobody keeps tidy."""
    row = await create(world)
    await used(world, row)
    updated = await world.services.work.update_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        name="Kịch bản video ngắn",
        description="Kịch bản dưới 60 giây",
        category=PrWorkCategory.PRODUCTION,
    )
    assert updated.name == "Kịch bản video ngắn"
    assert updated.category is PrWorkCategory.PRODUCTION
    assert updated.code == "SHORT_SCRIPT", "the identifier did not move"


async def test_10b_every_reference_counts_as_use(world: World) -> None:
    """A quota with no work filed yet still locks the type.

    The sharp case: a plan approved in March sets a target on a type nobody has
    filed against. Reading use from ``pr_work_items`` alone would call it unused
    and let the basis change under the quota that was approved on it.
    """
    row = await create(world)
    assert await world.services.work.is_work_type_in_use(row.id) is False
    await approved_plan(
        world, period=await month(world), quotas=((row, Decimal("5"), Decimal("5")),)
    )
    assert await world.services.work.is_work_type_in_use(row.id) is True


# ===========================================================================
# 15-19: RETIRING A TYPE KEEPS EVERYTHING
# ===========================================================================


async def test_15_deactivating_preserves_the_history(world: World) -> None:
    """**Not a delete.** The work filed under it is untouched and still resolves."""
    row = await create(world)
    await used(world, row)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    still_there = await world.session.get(PrWorkType, row.id)
    assert still_there is not None and still_there.is_active is False
    count = await world.session.scalar(
        select(func.count()).select_from(PrWorkType).where(PrWorkType.id == row.id)
    )
    assert count == 1, "deactivation deleted nothing"


async def test_16_a_retired_type_cannot_receive_new_work(world: World) -> None:
    """The refusal is the server's, not the dropdown's."""
    row = await create(world)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    with pytest.raises(PrValidationError) as caught:
        await used(world, row)
    assert caught.value.details["reason"] == "work_type_inactive"


async def test_17_existing_work_still_names_its_retired_type(world: World) -> None:
    """Otherwise a board would show last March with a hole in it."""
    row = await create(world)
    await used(world, row)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    resolved = await world.services.work.get_work_type(
        actor=world.actor(world.member), work_type_id=row.id
    )
    assert resolved.name == "Kịch bản ngắn", "readable when inactive, unlike the list"


async def test_18_a_retired_type_gets_no_new_quota(world: World) -> None:
    """M2's rule, and M2.5 leaves it exactly as it was."""
    row = await create(world)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    period_row = await month(world)
    draft = await world.services.work_plans.create_plan(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=world.member.id,
        period_id=period_row.id,
    )
    with pytest.raises(PrValidationError) as caught:
        await world.services.work_plans.add_quota(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            plan_id=draft.plan.id,
            work_type_id=row.id,
            target_value=Decimal("5"),
            eligibility_cap=Decimal("5"),
        )
    assert caught.value.details["reason"] == "work_type_inactive"


async def test_19_an_approved_plan_still_renders_a_retired_type(world: World) -> None:
    """Deactivating must not blank out a month somebody was assessed on."""
    from decimal import Decimal

    row = await create(world)
    period_row = await month(world)
    plan = await approved_plan(
        world, period=period_row, quotas=((row, Decimal("5"), Decimal("5")),)
    )
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=row.id,
        is_active=False,
    )
    detail = await world.services.work_plans.detail(actor=world.actor(world.owner), plan_id=plan.id)
    assert [one.name for one in detail.work_types] == ["Kịch bản ngắn"]
    assert len(detail.quotas) == 1, "the approved quota is still there"
    assert detail.work_types[0].is_active is False


# ===========================================================================
# 20-21: THE TWO BASES
# ===========================================================================


async def test_20_a_quantity_type_must_say_its_unit(world: World) -> None:
    """A seeding type silently inheriting "sản phẩm" is the ambiguity between
    "100 bình luận" and "100 việc" written into the taxonomy."""
    with pytest.raises(PrValidationError) as caught:
        await create(
            world,
            code="SEEDING_COMMENT",
            default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        )
    assert caught.value.details["reason"] == "unit_required_for_quantity"

    row = await create(
        world,
        code="SEEDING_COMMENT",
        default_quota_basis=PrWorkQuotaBasis.QUANTITY,
        default_unit=PrWorkUnit.COMMENT,
    )
    assert row.default_unit is PrWorkUnit.COMMENT


async def test_21_an_item_count_type_needs_no_unit(world: World) -> None:
    """Its amounts are counts of contributions, so there is nothing to decide -
    and requiring a meaningless unit would be a field nobody can answer."""
    row = await create(world)
    assert row.default_quota_basis is PrWorkQuotaBasis.ITEM_COUNT
    assert row.default_unit is PrWorkUnit.ITEM


# ===========================================================================
# 22-24: THE EMPTY DEPARTMENT, WHICH IS THE BUG
# ===========================================================================


async def test_22_bootstrap_creates_the_starting_taxonomy(world: World) -> None:
    """The department is usable from MeoChat with no developer involved."""
    created = await world.services.work.bootstrap_work_types(
        actor=world.actor(world.owner), request_id=world.request_id
    )
    assert len(created) == len(BOOTSTRAP_WORK_TYPES)
    codes = {row.code for row in created}
    assert {"SHORT_VIDEO_SCRIPT", "SEEDING_COMMENT", "ACCOUNT_CARE"} <= codes
    seeding = next(row for row in created if row.code == "SEEDING_COMMENT")
    assert seeding.default_quota_basis is PrWorkQuotaBasis.QUANTITY
    assert seeding.default_unit is PrWorkUnit.COMMENT


async def test_23_bootstrap_run_twice_creates_no_duplicates(world: World) -> None:
    """Idempotent on ``code``, the field a rename does not touch."""
    actor = world.actor(world.owner)
    await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    again = await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    assert again == ()
    total = await world.session.scalar(select(func.count()).select_from(PrWorkType))
    assert total == len(BOOTSTRAP_WORK_TYPES)


async def test_23b_bootstrap_does_not_resurrect_a_retired_type(world: World) -> None:
    """A run that reactivated what somebody retired would be a way to undo a
    decision rather than a way to seed a database."""
    actor = world.actor(world.owner)
    await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    row = await world.session.scalar(select(PrWorkType).where(PrWorkType.code == "SEEDING_COMMENT"))
    assert row is not None
    await world.services.work.set_work_type_active(
        actor=actor, request_id=world.request_id, work_type_id=row.id, is_active=False
    )
    await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    await world.session.refresh(row)
    assert row.is_active is False


async def test_23c_bootstrap_keeps_a_rename(world: World) -> None:
    """Matching is on the code, so the owner's label survives a second run."""
    actor = world.actor(world.owner)
    await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    row = await world.session.scalar(select(PrWorkType).where(PrWorkType.code == "VIDEO_EDIT"))
    assert row is not None
    await world.services.work.update_work_type(
        actor=actor, request_id=world.request_id, work_type_id=row.id, name="Dựng phim"
    )
    await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    await world.session.refresh(row)
    assert row.name == "Dựng phim"


async def test_24_the_work_picker_receives_the_bootstrapped_types(world: World) -> None:
    """The original symptom, asserted from the employee's side: an empty dropdown
    becomes a populated one, ordered the way somebody arranged it."""
    before = await world.services.work.list_work_types(actor=world.actor(world.member))
    assert before == [], "this is the production state M2.5 was opened for"
    await world.services.work.bootstrap_work_types(
        actor=world.actor(world.owner), request_id=world.request_id
    )
    after = await world.services.work.list_work_types(actor=world.actor(world.member))
    assert len(after) == len(BOOTSTRAP_WORK_TYPES)
    assert [row.category.value for row in after] == sorted(row.category.value for row in after), (
        "grouped by category, so a picker reads in order"
    )


async def test_24b_an_employee_may_not_bootstrap(world: World) -> None:
    """It writes the taxonomy, so it is a configuration act like any other."""
    with pytest.raises(PrPermissionDeniedError):
        await world.services.work.bootstrap_work_types(
            actor=world.actor(world.member), request_id=world.request_id
        )


# ===========================================================================
# 25-28: THE BOUNDARIES M2.5 MUST NOT MOVE
# ===========================================================================


async def test_25_m1_work_still_flows_under_a_managed_type(world: World) -> None:
    """A regression guard in this file as well as M1's: the ladder still runs
    end to end on a type created through the M2.5 route."""
    row = await create(world)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=row.id,
            title="Quay TVC",
            contributor_user_ids=(world.member.id,),
        ),
    )
    await take_to_completed(world, item, by=world.member)
    approved = await world.services.work.approve(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    assert approved.item.status.value == "APPROVED"


async def test_27_configuring_is_not_implied_by_anything_else(world: World) -> None:
    """The capability matrix M2.5 relies on, asserted rather than assumed."""
    member, lead, owner = (
        world.actor(world.member),
        world.actor(world.lead),
        world.actor(world.owner),
    )
    assert await world.services.capabilities.allows(member, PrCapability.PR_WORK_EXECUTE)
    assert not await world.services.capabilities.allows(member, PrCapability.PR_WORK_CONFIGURE)
    assert not await world.services.capabilities.allows(lead, PrCapability.PR_WORK_CONFIGURE)
    assert await world.services.capabilities.allows(owner, PrCapability.PR_WORK_CONFIGURE)


async def test_28_there_is_no_delete_route_for_a_work_type(world: World) -> None:
    """Asserted rather than assumed. "We never added one" is not a guarantee, and
    a ``RESTRICT`` foreign key turns an accidental one into a 500 rather than a
    refusal somebody understands."""
    routes = [
        (route.path, method)
        for route in world.client.app.routes  # type: ignore[attr-defined]
        for method in getattr(route, "methods", set())
    ]
    deletes = [path for path, method in routes if method == "DELETE" and "/types" in path]
    assert deletes == []


# ===========================================================================
# THE AUDIT TRAIL
# ===========================================================================


async def test_29_each_lifecycle_decision_writes_its_own_action(world: World) -> None:
    """Reading "who turned this off" out of a generic update's changed-field list
    is exactly the archaeology an audit trail exists to spare somebody."""
    row = await create(world)
    actor = world.actor(world.owner)
    await world.services.work.set_work_type_active(
        actor=actor, request_id=world.request_id, work_type_id=row.id, is_active=False
    )
    await world.services.work.set_work_type_active(
        actor=actor, request_id=world.request_id, work_type_id=row.id, is_active=True
    )
    actions = (
        (
            await world.session.execute(
                select(AuditLog.action).where(AuditLog.entity_type == "pr_work_type")
            )
        )
        .scalars()
        .all()
    )
    assert AuditAction.PR_WORK_TYPE_CREATED.value in actions
    assert AuditAction.PR_WORK_TYPE_DEACTIVATED.value in actions
    assert AuditAction.PR_WORK_TYPE_ACTIVATED.value in actions


async def test_30_an_idempotent_lifecycle_call_writes_nothing(world: World) -> None:
    """Nothing was decided, so there is nothing to record."""
    row = await create(world)
    actor = world.actor(world.owner)
    await world.services.work.set_work_type_active(
        actor=actor, request_id=world.request_id, work_type_id=row.id, is_active=True
    )
    count = await world.session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == AuditAction.PR_WORK_TYPE_ACTIVATED.value)
    )
    assert count == 0


async def test_31_bootstrap_records_only_what_it_created(world: World) -> None:
    """So "did somebody run this twice" is answerable from the trail."""
    actor = world.actor(world.owner)
    await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    await world.services.work.bootstrap_work_types(actor=actor, request_id=world.request_id)
    rows = (
        (
            await world.session.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.PR_WORK_TYPES_BOOTSTRAPPED.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, "the second run decided nothing"
    assert rows[0].after_data["created"] == len(BOOTSTRAP_WORK_TYPES)


# ===========================================================================
# OVER HTTP - the routes, and the one that could have been shadowed
# ===========================================================================


async def test_32_the_bootstrap_route_is_not_shadowed_by_the_detail_route(
    world: World,
) -> None:
    """``/types/bootstrap`` sits beside ``/types/{id}/…`` in the same router.

    Asserted over HTTP rather than by reading the decorators, because whether a
    literal segment wins against a parameterised sibling is FastAPI's answer and
    not one this file should be guessing.
    """
    world.act_as(world.owner)
    response = world.client.post("/api/pr/work/types/bootstrap")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["created"]) == len(BOOTSTRAP_WORK_TYPES)
    assert len(payload["work_types"]) == len(BOOTSTRAP_WORK_TYPES)

    # And it is idempotent over the wire too.
    again = world.client.post("/api/pr/work/types/bootstrap")
    assert again.status_code == 200
    assert again.json()["created"] == []


async def test_33_the_lifecycle_routes_round_trip(world: World) -> None:
    """Create, retire, revive - and the retired one leaves the default list."""
    world.act_as(world.owner)
    created = world.client.post(
        "/api/pr/work/types",
        json={"code": "VIDEO_EDIT", "name": "Dựng video", "category": "PRODUCTION"},
    )
    assert created.status_code == 201, created.text
    type_id = created.json()["id"]

    off = world.client.post(f"/api/pr/work/types/{type_id}/deactivate")
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert world.client.get("/api/pr/work/types").json() == []

    on = world.client.post(f"/api/pr/work/types/{type_id}/activate")
    assert on.status_code == 200 and on.json()["is_active"] is True
    assert [row["code"] for row in world.client.get("/api/pr/work/types").json()] == ["VIDEO_EDIT"]


async def test_34_an_employee_is_refused_the_retired_list_over_http(
    world: World,
) -> None:
    """The gate is the server's, so a client that just appends the parameter is
    refused rather than served."""
    world.act_as(world.member)
    assert world.client.get("/api/pr/work/types").status_code == 200
    assert world.client.get("/api/pr/work/types?include_inactive=true").status_code == 403


async def test_35_the_update_body_forbids_is_active(world: World) -> None:
    """``extra="forbid"``, so the field M2.5 removed cannot be smuggled back in
    through the generic edit route."""
    world.act_as(world.owner)
    created = world.client.post(
        "/api/pr/work/types",
        json={"code": "VIDEO_EDIT", "name": "Dựng video", "category": "PRODUCTION"},
    )
    type_id = created.json()["id"]
    response = world.client.patch(f"/api/pr/work/types/{type_id}", json={"is_active": False})
    assert response.status_code == 422, response.text


async def test_36_a_locked_structural_edit_is_a_409_over_http(world: World) -> None:
    """The refusal reaches the client as a conflict naming the field, which is
    what lets the screen say something better than "lỗi"."""
    row = await create(world)
    await used(world, row)
    world.act_as(world.owner)
    response = world.client.patch(
        f"/api/pr/work/types/{row.id}", json={"default_quota_basis": "QUANTITY"}
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["details"]["reason"] == WORK_TYPE_STRUCTURE_LOCKED
    assert error["details"]["field"] == "default_quota_basis"


async def test_37_the_detail_route_reports_the_lock(world: World) -> None:
    """``structure_locked`` is the server's answer, which is what the screen
    draws its disabled fields from."""
    row = await create(world)
    world.act_as(world.owner)
    before = world.client.get(f"/api/pr/work/types/{row.id}").json()
    assert before["is_in_use"] is False and before["structure_locked"] is False

    await used(world, row)
    after = world.client.get(f"/api/pr/work/types/{row.id}").json()
    assert after["is_in_use"] is True and after["structure_locked"] is True
