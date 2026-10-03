"""Content → Work auto-provisioning. **No preconfiguration required.**

The rule under test, stated once::

    A new content type must never lose its first accepted deliverable because
    nobody had configured the Work module for it yet.

So the projector, on meeting a content type with no binding, creates the work
type, binds the content type to it and records the first result - all in one
run - and every later piece of that type reuses the same type. What must
**not** move while that happens is everything M3 already protects: who earns
the work, who validates it, the self-approval rule, the closed-period rule, and
the meaning of a mapping an administrator wrote or turned off.

Numbered against the task's own scenarios:

* 1-4: first, second, another employee, another month;
* 5-6: self-approval, and a closed period;
* 7-10: precedence - explicit exact, explicit default, a deactivated rule, and
  content with no type;
* 11-13: one content type is two kinds of work; a dry run writes nothing; a
  stale resolver and a batch converge on one type;
* 14-17: the reserved namespace, retiring a mapped type, remapping, and the
  manual/auto boundary;
* 18-21: reversal, the worker's own actor, audit, and the unpriced actual.

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811 - `world` is a fixture imported from the production suite
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_content_work_service import ContentWorkResolver
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrApprovalEvent
from meobot.db.models.pr_content_work import PrContentWorkRule
from meobot.db.models.pr_performance import PrWorkScoringRule
from meobot.db.models.pr_work import PrWorkItem, PrWorkType
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.content_work import (
    AUTO_WORK_TYPE_CODE_PREFIX,
    PrContentWorkKind,
    PrContentWorkOutcome,
    auto_work_type_code,
    auto_work_type_name,
    is_auto_work_type_code,
)
from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.models import PrApprovalStage, PrContentType
from meobot.domain.pr.performance import PrContributionScoreStatus
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkCategory, PrWorkCountStatus, PrWorkUnit
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_results import PrWorkResultSource
from tests.unit.test_pr_content_work_projection import (
    _hand_to_producer,
    _to_head_review,
    approve_at,
    approved_content,
    contributions_of,
    grant,
    open_month,
    outcome_for,
    project,
    rule,
    source_result,
    submit,
    work_type,
    write_content,
)
from tests.unit.test_pr_production_lifecycle import World, world  # noqa: F401

pytestmark = pytest.mark.asyncio

KIND = PrContentWorkKind.CONTENT_CREATION
TYPE = PrContentType.SHORT_VIDEO_SCRIPT
AUTO_CODE = auto_work_type_code(KIND, TYPE)


# ===========================================================================
# Helpers
# ===========================================================================


async def work_types(world: World) -> list[PrWorkType]:
    return list(
        (await world.session.execute(select(PrWorkType).order_by(PrWorkType.code))).scalars()
    )


async def rules(world: World) -> list[PrContentWorkRule]:
    return list((await world.session.execute(select(PrContentWorkRule))).scalars())


async def auto_type(world: World, code: str = AUTO_CODE) -> PrWorkType | None:
    return (
        (await world.session.execute(select(PrWorkType).where(PrWorkType.code == code)))
        .scalars()
        .one_or_none()
    )


async def container_of(world: World, content_id: uuid.UUID, kind: PrContentWorkKind = KIND):
    result = await source_result(world, content_id, kind)
    assert result is not None
    item = await world.session.get(PrWorkItem, result.work_item_id)
    assert item is not None
    await world.session.refresh(item)
    return result, item


async def scoring_rule_count(world: World) -> int:
    return (
        await world.session.execute(select(func.count()).select_from(PrWorkScoringRule))
    ).scalar_one()


async def audit_rows(world: World, action: AuditAction) -> list[AuditLog]:
    return list(
        (await world.session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
    )


def system_actor() -> Actor:
    """The worker's actor: no user row, owner role - what ``TaskContext`` builds."""
    return Actor(user_id=None, full_name="meobot-worker", role=Role.OWNER, is_bootstrap_owner=True)


# ===========================================================================
# 1-4: FIRST, SECOND, ANOTHER EMPLOYEE, ANOTHER MONTH
# ===========================================================================


async def test_01_the_first_content_of_an_unmapped_type_provisions_and_counts(
    world: World,
) -> None:
    """**The milestone.** No work type, no rule - and the first approved piece
    still counts as one product, with nobody having configured anything."""
    period = await open_month(world, utcnow())
    assert await work_types(world) == []
    assert await rules(world) == []

    content_id = await approved_content(world, content_type=TYPE)
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED

    # 1-3. Exactly one work type, ITEM_COUNT, the canonical unit, active, in the
    # content category, named after the content type's label.
    types = await work_types(world)
    assert [row.code for row in types] == [AUTO_CODE]
    created = types[0]
    assert created.default_quota_basis is PrWorkQuotaBasis.ITEM_COUNT
    assert created.default_unit is PrWorkUnit.ITEM
    assert created.category is PrWorkCategory.CONTENT
    assert created.is_active is True
    assert created.requires_evidence is False
    assert created.name == auto_work_type_name(KIND, TYPE) == "Kịch bản video ngắn"
    assert is_auto_work_type_code(created.code)

    # 4. Exactly one binding, exact on (kind, content type), and nobody's.
    bound = await rules(world)
    assert len(bound) == 1
    assert bound[0].contribution_kind is KIND
    assert bound[0].content_type is TYPE
    assert bound[0].work_type_id == created.id
    assert bound[0].is_active is True
    assert bound[0].created_by_user_id is None
    assert bound[0].auto_provisioned is True

    # 5-10. One result, quantity 1, COUNTED because the head is not the writer,
    # inside the writer's container for the month, whose actual is 1.
    result, item = await container_of(world, content_id)
    assert result.source_type is PrWorkResultSource.CONTENT
    assert result.quantity == Decimal("1.00")
    assert result.status is PrWorkCountStatus.COUNTED
    assert result.user_id == world.member.id
    assert result.counted_by_user_id == world.head.id
    assert item.is_period_container
    assert item.work_type_id == created.id
    assert item.subject_user_id == world.member.id
    assert item.reporting_period_id == period.id
    assert item.quantity == Decimal("1.00")
    rows = await contributions_of(world, item.id)
    assert [row.count_status for row in rows] == [PrWorkCountStatus.COUNTED]

    # 11. No workload rule was invented.
    assert await scoring_rule_count(world) == 0

    # 12. A retry changes nothing.
    again = await project(world, content_id)
    assert outcome_for(again, KIND) is PrContentWorkOutcome.UNCHANGED
    assert len(await work_types(world)) == 1
    assert len(await rules(world)) == 1
    _, item = await container_of(world, content_id)
    assert item.quantity == Decimal("1.00")


async def test_02_the_second_content_of_the_type_reuses_the_type(world: World) -> None:
    first = await approved_content(world, content_type=TYPE, title="Một")
    await project(world, first)
    second = await approved_content(world, content_type=TYPE, title="Hai")
    report = await project(world, second)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED

    assert len(await work_types(world)) == 1, "no second work type"
    assert len(await rules(world)) == 1, "no second mapping"
    result_one, item_one = await container_of(world, first)
    result_two, item_two = await container_of(world, second)
    assert result_one.id != result_two.id
    assert item_one.id == item_two.id, "same employee, same month, same stream"
    assert item_one.quantity == Decimal("2.00")


async def test_03_another_employee_gets_their_own_container(world: World) -> None:
    first = await approved_content(world, writer=world.member, content_type=TYPE)
    await project(world, first)
    second = await approved_content(world, writer=world.other, content_type=TYPE, title="Của A")
    await project(world, second)

    assert len(await work_types(world)) == 1
    assert len(await rules(world)) == 1
    _, item_member = await container_of(world, first)
    _, item_other = await container_of(world, second)
    assert item_member.id != item_other.id
    assert item_member.work_type_id == item_other.work_type_id
    assert item_other.subject_user_id == world.other.id
    assert item_other.quantity == Decimal("1.00")
    assert item_member.quantity == Decimal("1.00")


async def test_04_the_next_month_opens_a_new_container(world: World) -> None:
    first = await approved_content(world, content_type=TYPE)
    await project(world, first)
    _, item_first = await container_of(world, first)

    second = await approved_content(world, content_type=TYPE, title="Tháng sau")
    # The head's decision is the instant the work is counted under. Move it a
    # month on, the way a late-decided piece would land in the next period.
    approval = (
        (
            await world.session.execute(
                select(PrApprovalEvent).where(
                    PrApprovalEvent.content_id == second,
                    PrApprovalEvent.approval_stage == PrApprovalStage.HEAD_REVIEW,
                )
            )
        )
        .scalars()
        .one()
    )
    approval.decided_at = approval.decided_at + timedelta(days=35)
    await world.session.flush()
    await project(world, second)

    assert len(await work_types(world)) == 1
    assert len(await rules(world)) == 1
    _, item_second = await container_of(world, second)
    assert item_second.id != item_first.id
    assert item_second.reporting_period_id != item_first.reporting_period_id
    assert item_second.subject_user_id == item_first.subject_user_id
    assert item_second.quantity == Decimal("1.00")
    await world.session.refresh(item_first)
    assert item_first.quantity == Decimal("1.00")


# ===========================================================================
# 5-6: SELF-APPROVAL AND A CLOSED PERIOD
# ===========================================================================


async def test_05_a_self_approved_piece_is_provisioned_but_not_counted(world: World) -> None:
    """Provisioning is not validation. The head writes and approves their own
    script: the type and the binding appear, the result exists, and it is
    **PENDING** exactly as it would be under a configured mapping."""
    content_id = await approved_content(
        world, writer=world.head, head=world.head, content_type=TYPE
    )
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PENDING_VALIDATION

    assert await auto_type(world) is not None
    assert len(await rules(world)) == 1
    result, item = await container_of(world, content_id)
    assert result.status is PrWorkCountStatus.PENDING
    assert result.counted_by_user_id is None
    assert item.quantity == Decimal("0.00")
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.PENDING


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_06_a_shut_period_is_not_written_into(world: World, state: PrPeriodStatus) -> None:
    """Existing behaviour, preserved: a first result cannot open a stream in a
    month that is no longer ``OPEN``, and the refusal is the period service's
    own. Provisioning does not get past it - the whole run is one transaction,
    so a refused first result leaves no half-provisioned configuration behind
    once the worker rolls back."""
    period = await open_month(world, utcnow())
    content_id = await approved_content(world, content_type=TYPE)
    period.status = state
    await world.session.flush()

    with pytest.raises(PrValidationError) as caught:
        await project(world, content_id)
    assert caught.value.details["reason"] == "period_not_open"
    assert await source_result(world, content_id, KIND) is None


# ===========================================================================
# 7-10: PRECEDENCE
# ===========================================================================


async def test_07_an_explicit_exact_mapping_always_wins(world: World) -> None:
    manual = await work_type(world, code="SHORT_SCRIPT", name="Kịch bản ngắn")
    await rule(world, kind=KIND, type_row=manual, content_type=TYPE)
    content_id = await approved_content(world, content_type=TYPE)
    await project(world, content_id)

    _, item = await container_of(world, content_id)
    assert item.work_type_id == manual.id
    assert await auto_type(world) is None, "nothing provisioned beside an explicit mapping"
    assert len(await rules(world)) == 1


async def test_08_a_configured_default_mapping_still_handles_the_kind(world: World) -> None:
    """*"Anything approved is a script"* keeps meaning that. A new content type
    falls under the sentence rather than growing a type of its own."""
    default = await work_type(world, code="ANY_SCRIPT", name="Kịch bản")
    await rule(world, kind=KIND, type_row=default)
    content_id = await approved_content(world, content_type=PrContentType.PRESS_ARTICLE)
    await project(world, content_id)

    _, item = await container_of(world, content_id)
    assert item.work_type_id == default.id
    assert [row.code for row in await work_types(world)] == ["ANY_SCRIPT"]
    assert len(await rules(world)) == 1


async def test_09_a_deactivated_exact_mapping_is_a_decision_not_a_gap(world: World) -> None:
    manual = await work_type(world)
    await rule(world, kind=KIND, type_row=manual, content_type=TYPE, is_active=False)
    content_id = await approved_content(world, content_type=TYPE)
    report = await project(world, content_id)

    assert outcome_for(report, KIND) is PrContentWorkOutcome.NO_MAPPING
    assert await auto_type(world) is None
    assert await source_result(world, content_id, KIND) is None
    assert len(await rules(world)) == 1


async def test_10_content_with_no_type_has_nothing_to_bind(world: World) -> None:
    content_id = await approved_content(world, content_type=None)
    report = await project(world, content_id)

    assert outcome_for(report, KIND) is PrContentWorkOutcome.NO_MAPPING
    assert await work_types(world) == []
    assert await rules(world) == []


# ===========================================================================
# 11-13: TWO KINDS, DRY RUN, STALE RESOLVER AND BATCH
# ===========================================================================


async def test_11_one_content_type_is_two_kinds_of_work(world: World) -> None:
    """The binding identity includes the kind: the script and the cut of one
    piece are two jobs, two types, two rules and two results."""
    await grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=world.member, content_type=TYPE)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await _hand_to_producer(world, content_id, producer=world.other)
    await submit(world, content_id, actor=world.other)
    await approve_at(world, content_id, stage=PrApprovalStage.INTERNAL_REVIEW, reviewer=world.head)

    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.PROJECTED

    codes = [row.code for row in await work_types(world)]
    assert codes == sorted([AUTO_CODE, auto_work_type_code(PrContentWorkKind.PRODUCTION, TYPE)])
    production = await auto_type(world, auto_work_type_code(PrContentWorkKind.PRODUCTION, TYPE))
    assert production is not None
    assert production.category is PrWorkCategory.PRODUCTION
    assert production.name == "Sản xuất: Kịch bản video ngắn"
    assert {(row.contribution_kind, row.content_type) for row in await rules(world)} == {
        (KIND, TYPE),
        (PrContentWorkKind.PRODUCTION, TYPE),
    }
    _, script = await container_of(world, content_id, KIND)
    _, cut = await container_of(world, content_id, PrContentWorkKind.PRODUCTION)
    assert script.subject_user_id == world.member.id
    assert cut.subject_user_id == world.other.id
    assert cut.work_type_id == production.id


async def test_12_a_dry_run_provisions_nothing(world: World) -> None:
    content_id = await approved_content(world, content_type=TYPE)
    report = await project(world, content_id, dry_run=True)
    outcome = next(one for one in report.results if one.kind is KIND)
    assert outcome.outcome is PrContentWorkOutcome.PROJECTED
    assert outcome.detail is not None and "would provision" in outcome.detail

    assert await work_types(world) == []
    assert await rules(world) == []
    assert await source_result(world, content_id, KIND) is None


async def test_13_a_stale_resolver_and_a_batch_converge_on_one_type(world: World) -> None:
    """A worker that loaded its mapping before another worker provisioned the
    type finds the binding on its miss and reuses it - the same path a retry
    after a lost race takes. And a reconcile over three pieces of one new type
    provisions once."""
    stale = ContentWorkResolver(exact={}, default={})
    first = await approved_content(world, content_type=TYPE, title="Một")
    await project(world, first)  # provisions with a fresh resolver
    assert len(await work_types(world)) == 1

    second = await approved_content(world, content_type=TYPE, title="Hai")
    report = await world.services.content_work.project_content(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        content_id=second,
        resolver=stale,
    )
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    assert len(await work_types(world)) == 1, "the stale worker reused the binding"
    assert len(await rules(world)) == 1
    assert stale.resolve(KIND, TYPE) is not None, "and learned it for the rest of its run"

    others = [
        await approved_content(world, content_type=PrContentType.FACEBOOK_POST, title=f"FB {n}")
        for n in range(3)
    ]
    outcome = await world.services.content_work.reconcile(
        actor=world.actor(world.owner), request_id=world.request_id, content_ids=others
    )
    assert outcome.counts["PROJECTED"] == 3
    facebook = auto_work_type_code(KIND, PrContentType.FACEBOOK_POST)
    assert [row.code for row in await work_types(world)] == sorted([AUTO_CODE, facebook])
    assert len(await rules(world)) == 2
    _, item = await container_of(world, others[0])
    assert item.quantity == Decimal("3.00")


# ===========================================================================
# 14-17: THE NAMESPACE, RETIRING, REMAPPING, THE MANUAL BOUNDARY
# ===========================================================================


async def test_14_the_reserved_namespace_is_refused_to_people(world: World) -> None:
    """Auto-provisioning is a system operation. Nobody creates a type in its
    namespace by hand, and the projector's own door refuses codes outside it -
    so the prefix is a statement of provenance, not a naming convention."""
    with pytest.raises(PrValidationError) as caught:
        await work_type(world, code=f"{AUTO_WORK_TYPE_CODE_PREFIX}ANYTHING")
    assert caught.value.details["reason"] == "reserved_code"

    # Nor renamed into it: an unused hand-made type could otherwise be moved
    # into the namespace and claim a provenance it never had.
    manual = await work_type(world, code="HAND_MADE", name="Tự tay")
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.update_work_type(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            work_type_id=manual.id,
            code=f"{AUTO_WORK_TYPE_CODE_PREFIX}HAND_MADE",
        )
    assert caught.value.details["reason"] == "reserved_code"

    with pytest.raises(PrValidationError) as caught:
        await world.services.work.ensure_source_work_type(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            code="HAND_MADE",
            name="Tự tay",
            category=PrWorkCategory.CONTENT,
            default_unit=PrWorkUnit.ITEM,
            provenance={},
        )
    assert caught.value.details["reason"] == "not_reserved_code"


async def test_15_a_type_an_active_mapping_files_into_cannot_be_retired(world: World) -> None:
    """Retire the mapping first, then the type - never the type underneath the
    mapping. And once the mapping is off, the decision holds: the next piece is
    ``NO_MAPPING``, not a second provisioned type."""
    content_id = await approved_content(world, content_type=TYPE)
    await project(world, content_id)
    created = await auto_type(world)
    assert created is not None

    with pytest.raises(PrValidationError) as caught:
        await world.services.work.set_work_type_active(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            work_type_id=created.id,
            is_active=False,
        )
    assert caught.value.details["reason"] == "work_type_mapped_from_content"
    await world.session.refresh(created)
    assert created.is_active is True

    await rule(world, kind=KIND, type_row=created, content_type=TYPE, is_active=False)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=created.id,
        is_active=False,
    )
    assert created.is_active is False

    later = await approved_content(world, content_type=TYPE, title="Sau khi tắt")
    report = await project(world, later)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.NO_MAPPING
    assert len(await work_types(world)) == 1, "no replacement type"
    # History is intact: the counted result and its stream still stand.
    result, item = await container_of(world, content_id)
    assert result.status is PrWorkCountStatus.COUNTED and item.quantity == Decimal("1.00")


async def test_16_an_administrator_may_remap_a_provisioned_binding(world: World) -> None:
    """The auto-created rule is an ordinary rule. Remapping it moves nothing
    that is counted, moves an uncounted result, and files the next piece under
    the chosen type - with no second provisioned type."""
    counted = await approved_content(world, content_type=TYPE, title="Đã tính")
    await project(world, counted)
    pending = await approved_content(
        world, writer=world.head, head=world.head, content_type=TYPE, title="Chờ"
    )
    await project(world, pending)
    created = await auto_type(world)
    assert created is not None

    chosen = await work_type(world, code="SHORT_SCRIPT", name="Kịch bản ngắn")
    remapped = await rule(world, kind=KIND, type_row=chosen, content_type=TYPE)
    assert remapped.work_type_id == chosen.id
    assert remapped.created_by_user_id is None, "how the rule began is not rewritten"
    assert len(await rules(world)) == 1

    await project(world, counted)
    await project(world, pending)
    _, item_counted = await container_of(world, counted)
    _, item_pending = await container_of(world, pending)
    assert item_counted.work_type_id == created.id, "counted history stays where it was"
    assert item_pending.work_type_id == chosen.id, "an uncounted result follows the mapping"

    later = await approved_content(world, content_type=TYPE, title="Sau")
    await project(world, later)
    _, item_later = await container_of(world, later)
    assert item_later.work_type_id == chosen.id
    assert [row.code for row in await work_types(world)] == sorted([AUTO_CODE, "SHORT_SCRIPT"])


async def test_17_a_manual_type_with_the_same_name_is_not_the_same_type(world: World) -> None:
    """No fuzzy matching. A manual "Kịch bản video ngắn" and the content type
    "Kịch bản video ngắn" share a label and nothing else until somebody binds
    them explicitly."""
    manual = await work_type(world, code="MANUAL_SCRIPT", name="Kịch bản video ngắn")
    content_id = await approved_content(world, content_type=TYPE)
    await project(world, content_id)

    created = await auto_type(world)
    assert created is not None and created.id != manual.id
    assert created.name == manual.name
    _, item = await container_of(world, content_id)
    assert item.work_type_id == created.id
    bound = await rules(world)
    assert len(bound) == 1 and bound[0].work_type_id == created.id
    # The manual type is untouched and unbound.
    await world.session.refresh(manual)
    assert manual.is_active is True


# ===========================================================================
# 18-21: REVERSAL, THE WORKER'S ACTOR, AUDIT, THE UNPRICED ACTUAL
# ===========================================================================


async def test_18_a_reversal_takes_the_result_back_and_keeps_the_type(world: World) -> None:
    content_id = await approved_content(world, content_type=TYPE)
    await project(world, content_id)
    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    report = await project(world, content_id)
    assert outcome_for(report, KIND) is PrContentWorkOutcome.REVERSED

    result, item = await container_of(world, content_id)
    assert result.status is PrWorkCountStatus.EXCLUDED
    assert item.quantity == Decimal("0.00")
    assert await auto_type(world) is not None, "configuration is not undone with a result"
    assert len(await rules(world)) == 1


async def test_19_the_worker_provisions_as_the_system_actor(world: World) -> None:
    """The worker holds no user row. The binding it writes names nobody, the
    audit row names the worker, and nothing asks the worker for a capability
    it does not hold."""
    content_id = await approved_content(world, content_type=TYPE)
    report = await world.services.content_work.project_content(
        actor=system_actor(), request_id=world.request_id, content_id=content_id
    )
    assert outcome_for(report, KIND) is PrContentWorkOutcome.PROJECTED
    bound = await rules(world)
    assert len(bound) == 1 and bound[0].created_by_user_id is None
    result, _ = await container_of(world, content_id)
    assert result.status is PrWorkCountStatus.COUNTED
    assert result.counted_by_user_id == world.head.id, "the validator is the human at source"


async def test_20_provisioning_is_audited_once_with_its_reason(world: World) -> None:
    content_id = await approved_content(world, content_type=TYPE)
    await project(world, content_id)
    await project(world, content_id)

    created = await audit_rows(world, AuditAction.PR_WORK_TYPE_CREATED)
    assert len(created) == 1
    after = created[0].after_data or {}
    assert after["reason"] == "content_auto_provision"
    assert after["code"] == AUTO_CODE
    assert after["contribution_kind"] == KIND.value
    assert after["content_type"] == TYPE.value

    bound = await audit_rows(world, AuditAction.PR_CONTENT_WORK_RULE_CREATED)
    assert len(bound) == 1
    assert (bound[0].after_data or {})["reason"] == "content_auto_provision"
    assert (bound[0].after_data or {})["work_type_code"] == AUTO_CODE

    projected = await audit_rows(world, AuditAction.PR_CONTENT_WORK_PROJECTED)
    assert len(projected) == 1
    assert (projected[0].after_data or {})["auto_provisioned"] is True


async def test_21_the_counted_actual_is_real_and_unpriced(world: World) -> None:
    """One product, and no minutes: the stream's actual is 1 and its standard
    minutes are ``None`` under ``NO_SCORING_RULE`` - not zero."""
    content_id = await approved_content(world, content_type=TYPE)
    await project(world, content_id)
    _, item = await container_of(world, content_id)

    summary = await world.services.work_results.summary(item)
    assert summary is not None
    assert summary.counted_quantity == Decimal("1.00")
    assert summary.result_count == 1
    assert summary.scoring_status is PrContributionScoreStatus.NO_SCORING_RULE
    assert summary.standard_minutes is None
    assert summary.standard_minutes_per_unit is None
    assert await scoring_rule_count(world) == 0
