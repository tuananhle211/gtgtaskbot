"""M3 - Content → Work, and the two boundaries it must not cross.

Numbered 1-48 against the milestone's own requirement list.

What this file is really testing
---------------------------------

**If MeoChat already knows somebody did the work, they should not type it in
again - and that must not cost the boundary M1 is built on.** Every test below
exists because one of those two halves could quietly be lost:

* an intermediate step is not an accepted deliverable - tests 1-2;
* **a self-approved script does not count itself** - tests 7-9. This is the
  milestone: the content workflow has no rule against approving your own piece,
  so the Work boundary is where KPI integrity comes from;
* a publisher recording their own publication is not independent evidence -
  test 21, and it is why ``PUBLICATION`` never validates itself;
* a retry, an undo and a redo are the same job - tests 10-13 and 22-25;
* **the projector never guesses whose work it was** - tests 29-31;
* a closed month is not rewritten - tests 26-28;
* source-derived work is not a manual item with a badge - tests 32-35.

And the architecture boundary, twice over: **M3 decides no eligibility**
(tests 4-6) and **writes no work column itself** (test 11b).

Nothing here contacts a network.
"""

from __future__ import annotations

# The ``world`` fixture comes from the production-lifecycle suite: five people
# with five roles and a real content workflow is exactly what a projection has
# to be argued against, and a lookalike fixture would let the two drift.
# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from meobot.application.pr_content_work_projector import (
    request_content_work_projection,
)
from meobot.application.pr_work_service import CreateWorkCommand
from meobot.core.time import utcnow
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_content_work import PrContentWorkProjection, PrContentWorkRule
from meobot.db.models.pr_reporting import PrPublication, PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.domain.pr.content_work import (
    PrContentWorkKind,
    PrContentWorkOutcome,
    PrContentWorkProjectionStatus,
    content_work_source_key,
)
from meobot.domain.pr.errors import (
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrContentType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus, PrPublicationStatus
from meobot.domain.pr.work import (
    PrWorkCategory,
    PrWorkCountStatus,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
)
from meobot.domain.pr.work_quota import PrWorkQuotaBasis
from meobot.domain.pr.work_results import PrWorkResultSource
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    decide,
    make_content,
    submit,
    to_approved,
    to_production,
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
    code: str = "SHORT_SCRIPT",
    name: str = "Kịch bản ngắn",
    unit: PrWorkUnit = PrWorkUnit.ITEM,
    basis: PrWorkQuotaBasis = PrWorkQuotaBasis.ITEM_COUNT,
) -> PrWorkType:
    return await world.services.work.create_work_type(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        code=code,
        name=name,
        category=PrWorkCategory.CONTENT,
        default_unit=unit,
        default_quota_basis=basis,
    )


async def rule(
    world: World,
    *,
    kind: PrContentWorkKind,
    type_row: PrWorkType,
    content_type: PrContentType | None = None,
    is_active: bool = True,
) -> PrContentWorkRule:
    """One Content → Work mapping, written the way an administrator would."""
    return await world.services.content_work_rules.upsert_rule(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        contribution_kind=kind,
        content_type=content_type,
        work_type_id=type_row.id,
        is_active=is_active,
    )


async def grant(world: World, user: User, capability) -> None:  # type: ignore[no-untyped-def]
    """Give somebody a grant-backed review capability.

    The ``world`` fixture grants only the team-lead and head gates, to the two
    people who normally hold them. M3's tests need the internal-review gate as
    well, and need the *writer* to hold the head gate for the self-approval
    case - which is the whole scenario the milestone turns on.
    """
    await world.services.capabilities.grant(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        user_id=user.id,
        capability=capability,
    )


async def write_content(
    world: World,
    *,
    writer: User,
    title: str = "Bí quyết ngủ ngon",
    content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
) -> uuid.UUID:
    """One content item, **written by the person who is meant to get credit**.

    The production suite's own helper always acts as the owner of the world, so
    every version it creates is authored by the same person - which is fine for
    what that suite tests and useless here, because the fact M3 reads is
    ``pr_content_versions.created_by_user_id`` and it is set from the **acting**
    user. Acting as the writer is what makes these tests about the writer.
    """
    from meobot.application.pr_content_service import (
        ContentTargetSpec,
        CreateContentCommand,
    )

    snapshot = await world.services.content.create_content(
        actor=world.actor(writer),
        request_id=world.request_id,
        command=CreateContentCommand(
            title=title,
            brand_id=world.brand_id,
            owner_user_id=writer.id,
            content_type=content_type,
            script_text="Nội dung.",
            targets=(ContentTargetSpec(channel_id=world.channel_id),),
        ),
    )
    return snapshot.content.id


async def approve_at(
    world: World,
    content_id: uuid.UUID,
    *,
    stage: PrApprovalStage,
    reviewer: User,
    decision: PrApprovalDecision = PrApprovalDecision.APPROVED,
    version: int = 1,
) -> None:
    """File one human decision at one gate, through the real approval service."""
    await decide(
        world,
        actor=reviewer,
        content_id=content_id,
        stage=stage,
        decision=decision,
        version=version,
    )


async def approved_content(
    world: World,
    *,
    writer: User | None = None,
    head: User | None = None,
    title: str = "Bí quyết ngủ ngon",
    content_type: PrContentType | None = PrContentType.SHORT_VIDEO_SCRIPT,
) -> uuid.UUID:
    """A content item carried to ``APPROVED`` through the real workflow.

    Every stage is the service the department uses - the brief, the script, the
    AI verdict and both human gates - because the whole question M3 answers is
    *"what does the content workflow actually record"*, and a fixture that wrote
    a stage directly would be testing the projector against a world that does
    not exist.

    ``head`` overrides who takes the final gate, which is how the self-approval
    tests put the writer on both sides of it.
    """
    author = writer or world.member
    content_id = await write_content(world, writer=author, title=title, content_type=content_type)
    await _to_head_review(world, content_id)
    await approve_at(
        world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=head or world.head
    )
    return content_id


async def _to_head_review(world: World, content_id: uuid.UUID) -> None:
    """Brief, script, AI verdict and the team-lead gate. Stops at ``HEAD_REVIEW``."""
    from meobot.application.pr_ai_review_service import RecordAiReviewCommand
    from meobot.domain.pr.models import PrAiReviewResult, PrAiReviewType

    for stage in (
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
    ):
        await world.services.workflow.request_transition(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_id=content_id,
            target=stage,
        )
    await world.services.ai_reviews.record_review(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        command=RecordAiReviewCommand(
            content_id=content_id,
            reviewed_version=1,
            review_type=PrAiReviewType.FULL_REVIEW,
            result=PrAiReviewResult.PASS,
            model_name="claude-opus-5",
            prompt_version="p@1",
            reviewed_at=utcnow(),
        ),
    )
    await approve_at(world, content_id, stage=PrApprovalStage.TEAM_LEAD_REVIEW, reviewer=world.lead)


async def project(world: World, content_id: uuid.UUID, *, dry_run: bool = False):  # type: ignore[no-untyped-def]
    """Run the projector once, as the worker does."""
    return await world.services.content_work.project_content(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        content_id=content_id,
        dry_run=dry_run,
    )


async def source_result(
    world: World, content_id: uuid.UUID, kind: PrContentWorkKind
) -> PrWorkResult | None:
    """The **result** one semantic milestone produced, by its source key.

    Period-container patch: a content milestone contributes one result to the
    contributor's monthly stream rather than one work item of its own. The
    idempotency contract moved with it - ``(CONTENT, source_key)`` is unique
    over ``pr_work_results``.
    """
    key = content_work_source_key(kind, content_id)
    return (
        (
            await world.session.execute(
                select(PrWorkResult).where(
                    PrWorkResult.source_type == PrWorkResultSource.CONTENT,
                    PrWorkResult.source_key == key,
                )
            )
        )
        .scalars()
        .one_or_none()
    )


async def source_work(
    world: World, content_id: uuid.UUID, kind: PrContentWorkKind
) -> PrWorkItem | None:
    """The **container** holding the milestone's result, or ``None``.

    Kept under its M3 name so the tests read as they did: the "work" a content
    milestone produces is now the stream it was recorded into.
    """
    result = await source_result(world, content_id, kind)
    if result is None:
        return None
    return await world.session.get(PrWorkItem, result.work_item_id)


async def content_results(world: World, content_id: uuid.UUID) -> list[PrWorkResult]:
    """Every result any milestone of this content produced."""
    keys = [content_work_source_key(kind, content_id) for kind in PrContentWorkKind]
    return list(
        (
            await world.session.execute(
                select(PrWorkResult).where(
                    PrWorkResult.source_type == PrWorkResultSource.CONTENT,
                    PrWorkResult.source_key.in_(keys),
                )
            )
        )
        .scalars()
        .all()
    )


async def containers_of(world: World, content_id: uuid.UUID) -> set[uuid.UUID]:
    return {row.work_item_id for row in await content_results(world, content_id)}


async def contributions_of(world: World, item_id: uuid.UUID) -> list[PrWorkContribution]:
    return list(
        (
            await world.session.execute(
                select(PrWorkContribution).where(PrWorkContribution.work_item_id == item_id)
            )
        )
        .scalars()
        .all()
    )


def outcome_for(report, kind: PrContentWorkKind) -> PrContentWorkOutcome | None:  # type: ignore[no-untyped-def]
    for result in report.results:
        if result.kind is kind:
            return result.outcome
    return None


async def open_month(world: World, at: datetime) -> PrReportingPeriod:
    """The reporting period an instant falls in, created and left ``OPEN``."""
    local = at.astimezone(world.settings.timezone)
    return await world.services.work_periods.ensure_month_period(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        year=local.year,
        month=local.month,
    )


# ===========================================================================
# 1-6: CONTENT CREATION, AND THE ARCHITECTURE BOUNDARY
# ===========================================================================


async def test_01_content_created_but_not_approved_produces_no_counted_work(
    world: World,
) -> None:
    """Creating a piece is not doing the job. Nothing is counted, and nothing is
    even projected - there is no accepted deliverable yet."""
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await make_content(world, owner=world.member)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is None
    assert report.worst is PrContentWorkOutcome.NOT_QUALIFIED
    assert await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION) is None


async def test_02_an_intermediate_approval_is_not_the_accepted_deliverable(
    world: World,
) -> None:
    """A team-lead approval is a step on the way, not acceptance.

    The milestone is the **final** content acceptance, and choosing the
    intermediate one would count work for a script the department has not agreed
    to yet - and count it twice when the head sends it back.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)

    content = await world.session.get(PrContentItem, content_id)
    assert content is not None and content.workflow_stage is PrWorkflowStage.HEAD_REVIEW
    await project(world, content_id)
    assert await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION) is None


async def test_03_the_final_approval_projects_counted_writer_work(world: World) -> None:
    """**The milestone.** One work item, the writer credited, and it counts.

    The head reviewer is not the writer, so the source supplied the independent
    second person M1 requires - and the work reaches ``COUNTED`` without anybody
    filing anything.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.PROJECTED
    )

    result = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert result is not None
    assert result.source_type is PrWorkResultSource.CONTENT
    assert result.status is PrWorkCountStatus.COUNTED
    assert result.quantity == Decimal("1.00")
    # And the head reviewer is recorded as the validator, not the worker.
    assert result.counted_by_user_id == world.head.id
    # Inside the writer's stream for the month - the writer from the version
    # that was approved, not today's owner - and that stream is counted.
    item = await world.session.get(PrWorkItem, result.work_item_id)
    assert item is not None and item.is_period_container
    assert item.subject_user_id == world.member.id
    assert item.quantity == Decimal("1.00")
    rows = await contributions_of(world, item.id)
    assert [row.user_id for row in rows] == [world.member.id]
    assert rows[0].count_status is PrWorkCountStatus.COUNTED


async def test_04_counted_source_work_reaches_the_m2_handoff(world: World) -> None:
    """M3 hands over and stops. M2 decides, exactly as it does for manual work."""
    period = await open_month(world, utcnow())
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)

    summary = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.member.id, period_id=period.id
    )
    assert summary.counted_contributions == 1
    # No approved plan exists, so M2's answer is NO_QUOTA - and that is M2's
    # answer, reached through M2's own evaluator.
    assert summary.contributions_by_status["NO_QUOTA"] == 1


async def test_05_no_quota_does_not_prevent_counting(world: World) -> None:
    """**The boundary.** Work validation never depends on a quota existing.

    A department that has configured no KPI plan at all still gets its content
    work recorded; whether it is eligible is a different question with a
    different owner.
    """
    await open_month(world, utcnow())
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)

    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED
    assert rows[0].counted_at is not None


async def test_06_the_projector_never_reads_an_eligibility_decision(world: World) -> None:
    """Structural. M3 must behave identically whatever M2 concluded.

    A projector that read a ``quota_status`` would be a second authority on the
    question M2 exists to be the only authority on - and a missing allocation
    means three different things, only one of which is ``NO_QUOTA``.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    for name in (
        "application/pr_content_work_projector.py",
        "application/pr_content_work_service.py",
        "domain/pr/content_work.py",
        "api/routers/pr_content_work.py",
        "api/schemas/pr_content_work.py",
        "tasks/pr_content_work.py",
    ):
        names = _identifiers(root / name)
        for forbidden in (
            "PrWorkQuotaAllocation",
            "PrWorkQuota",
            "PrWorkPlan",
            "quota_status",
            "eligible_amount",
            "PrWorkQuotaStatus",
        ):
            assert forbidden not in names, (name, forbidden)


# ===========================================================================
# 7-9: SELF-APPROVAL. THE MILESTONE.
# ===========================================================================


async def test_07_a_self_approved_script_is_projected_but_not_counted(world: World) -> None:
    """**The one that matters.**

    The content workflow has no rule against approving your own piece - there is
    no self-review bar in ``PrApprovalService``, deliberately, because that is an
    operational decision the department made. So the Work boundary is where KPI
    integrity comes from: the work exists, it is completed, and it does **not**
    count until somebody who did not do it says so.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    # The owner writes it and the owner approves it at the head gate.
    # The head, deliberately: an ADMIN who holds ``PR_WORK_VALIDATE`` *and* the
    # head review gate. Using an employee would make test 08 pass because they
    # lack the work capability rather than because the self-validation rule
    # refused them - the same rule passing for the wrong reason.
    content_id = await approved_content(world, writer=world.head, head=world.head)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.PENDING_VALIDATION
    )

    result = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert result is not None
    # The work is real and recorded - the employee does not have to file it.
    assert result.status is PrWorkCountStatus.PENDING
    item = await world.session.get(PrWorkItem, result.work_item_id)
    assert item is not None and item.subject_user_id == world.head.id
    rows = await contributions_of(world, item.id)
    assert rows[0].user_id == world.head.id
    # And it is not counted - the stream's actual is still zero.
    assert rows[0].count_status is PrWorkCountStatus.PENDING
    assert rows[0].counted_at is None
    assert item.quantity == Decimal("0.00")
    # The content itself is untouched: M3 did not force a second approval.
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None and content.workflow_stage is PrWorkflowStage.APPROVED


async def test_08_the_writer_cannot_validate_their_own_projected_work(world: World) -> None:
    """M1's rule, unchanged, and reached through the ordinary Work route.

    Whatever capability they hold - this actor is the OWNER.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    # The head, deliberately: an ADMIN who holds ``PR_WORK_VALIDATE`` *and* the
    # head review gate. Using an employee would make test 08 pass because they
    # lack the work capability rather than because the self-validation rule
    # refused them - the same rule passing for the wrong reason.
    content_id = await approved_content(world, writer=world.head, head=world.head)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None

    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work_results.validate_results(
            actor=world.actor(world.head), request_id=world.request_id, work_item_id=item.id
        )
    assert caught.value.details["reason"] == "self_validation"


async def test_09_an_independent_validator_counts_it_and_m2_follows(world: World) -> None:
    """The escape hatch, and the only manual action source-derived work offers.

    Somebody who did not do the work validates it through the ordinary Work
    module, and everything downstream is exactly as it would be for manual work.
    """
    period = await open_month(world, utcnow())
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    # The head, deliberately: an ADMIN who holds ``PR_WORK_VALIDATE`` *and* the
    # head review gate. Using an employee would make test 08 pass because they
    # lack the work capability rather than because the self-validation rule
    # refused them - the same rule passing for the wrong reason.
    content_id = await approved_content(world, writer=world.head, head=world.head)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None

    await world.services.work_results.validate_results(
        actor=world.actor(world.owner), request_id=world.request_id, work_item_id=item.id
    )
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED
    summary = await world.services.work_eligibility.summary(
        actor=world.actor(world.owner), user_id=world.head.id, period_id=period.id
    )
    assert summary.counted_contributions == 1


# ===========================================================================
# 10-13: IDEMPOTENCY
# ===========================================================================


async def test_10_projecting_twice_produces_one_work_item(world: World) -> None:
    """Convergent, not additive. The second run finds the ledger already right."""
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)

    first = await project(world, content_id)
    second = await project(world, content_id)
    assert outcome_for(first, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.PROJECTED
    )
    assert outcome_for(second, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.UNCHANGED
    )

    assert len(await content_results(world, content_id)) == 1
    assert len(await containers_of(world, content_id)) == 1


async def test_11_ten_runs_produce_one_contribution(world: World) -> None:
    """A worker retry is not a second job. Neither are nine of them."""
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    for _ in range(10):
        await project(world, content_id)

    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None
    rows = await contributions_of(world, item.id)
    assert len(rows) == 1
    assert sum(1 for row in rows if row.count_status is PrWorkCountStatus.COUNTED) == 1
    # Ten replays, one result, one unit of actual - never ten.
    assert len(await content_results(world, content_id)) == 1
    assert item.quantity == Decimal("1.00")


async def test_11b_the_projector_writes_no_work_column_itself(world: World) -> None:
    """Structural. Every state change goes through the Work service.

    A projector that stamped ``count_status`` or ``counted_at`` in its own SQL
    would be a second implementation of the order the ladder, the history, the
    audit row and the M2 handoff happen in - and the second one would drift.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    source = (root / "application" / "pr_content_work_projector.py").read_text("utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", "*", '"'))
    )
    # Narrowed to the **work** columns. The projector does assign
    # ``row.status`` - on its own queue row, which it owns - and forbidding that
    # would be forbidding it to have a queue.
    for forbidden in (
        "count_status =",
        "counted_at =",
        "approved_at =",
        "excluded_at =",
        "credit_weight =",
    ):
        assert forbidden not in code, forbidden


async def test_12_the_source_key_is_semantic_not_a_transition_attempt(world: World) -> None:
    """``content:{id}:CONTENT_CREATION`` - the *result*, not the attempt.

    Keying on the transition event id would make an undo-and-redo two pieces of
    work and double-count the writer, which is the whole reason the contract
    names the milestone instead.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)

    item = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None
    assert item.source_key == f"content:{content_id}:CONTENT_CREATION"

    # And no transition event id appears in it.
    from meobot.db.models.pr_transition import PrContentTransitionEvent

    events = (
        (
            await world.session.execute(
                select(PrContentTransitionEvent.id).where(
                    PrContentTransitionEvent.content_id == content_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert events
    for event_id in events:
        assert str(event_id) not in (item.source_key or "")


async def test_13_one_content_item_can_produce_several_work_items(world: World) -> None:
    """Writing it, cutting it and posting it are three different real jobs.

    Forcing one content item to one work item would credit whoever came first
    and silently lose the other two.
    """
    script = await work_type(world)
    edit = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=script)
    await rule(world, kind=PrContentWorkKind.PRODUCTION, type_row=edit)

    await grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await _hand_to_producer(world, content_id, producer=world.other)
    await submit(world, content_id, actor=world.other)
    await approve_at(world, content_id, stage=PrApprovalStage.INTERNAL_REVIEW, reviewer=world.head)
    await project(world, content_id)

    writing = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    cutting = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert writing is not None and cutting is not None
    # Two results, in two different people's streams for the month.
    assert len(await content_results(world, content_id)) == 2
    assert writing.id != cutting.id
    assert writing.subject_user_id == world.member.id
    assert cutting.subject_user_id == world.other.id


# ===========================================================================
# 14-18: PRODUCTION
# ===========================================================================


async def test_14_a_producer_being_assigned_is_not_work(world: World) -> None:
    """An assignment is a plan. Nothing is projected from it."""
    edit = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    await rule(world, kind=PrContentWorkKind.PRODUCTION, type_row=edit)
    await grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await _hand_to_producer(world, content_id, producer=world.other)

    await project(world, content_id)
    assert await source_work(world, content_id, PrContentWorkKind.PRODUCTION) is None


async def test_15_an_unreviewed_submission_is_workload_but_not_counted(
    world: World,
) -> None:
    """**M3.1 reversed this test's original rule, deliberately.**

    M3 asserted that a cut handed in is "a claim" and produced no work at all
    until a reviewer accepted it. That made an editor's finished job invisible
    until somebody got to it - a queue of unreviewed cuts looked like a queue of
    people who had done nothing.

    V1's rule instead: *Gửi bản dựng* **is** the workload milestone, and
    acceptance is the counting milestone. So the work exists, sits at
    ``COMPLETED``, and is **not** counted - the editor handing in their own file
    is not somebody else confirming it.
    """
    edit = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    await rule(world, kind=PrContentWorkKind.PRODUCTION, type_row=edit)
    await grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await _hand_to_producer(world, content_id, producer=world.other)
    await submit(world, content_id, actor=world.other)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is (
        PrContentWorkOutcome.PENDING_VALIDATION
    )
    result = await source_result(world, content_id, PrContentWorkKind.PRODUCTION)
    assert result is not None, "the editing job happened and is recorded"
    assert result.status is PrWorkCountStatus.PENDING, "nobody independent yet"
    item = await world.session.get(PrWorkItem, result.work_item_id)
    assert item is not None
    rows = await contributions_of(world, item.id)
    assert [row.user_id for row in rows] == [world.other.id], "the producer, not the submitter"
    assert rows[0].count_status is PrWorkCountStatus.PENDING
    assert rows[0].counted_at is None


async def test_16_17_the_accepted_cut_projects_the_canonical_producer(world: World) -> None:
    """The internal reviewer accepting the cut is the milestone, and the credited
    producer comes from the **submission** rather than from the content item.

    ``producer_user_id`` on the item changes when somebody is reassigned;
    ``producer_user_id`` on the submission is copied at hand-over precisely so a
    later reassignment does not rewrite who made the file.
    """
    edit = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    await rule(world, kind=PrContentWorkKind.PRODUCTION, type_row=edit)
    await grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await _hand_to_producer(world, content_id, producer=world.other)
    await submit(world, content_id, actor=world.other)
    await approve_at(world, content_id, stage=PrApprovalStage.INTERNAL_REVIEW, reviewer=world.head)

    # Somebody is reassigned afterwards. The credit must not follow.
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    content.producer_user_id = world.lead.id
    await world.session.flush()

    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert item is not None
    rows = await contributions_of(world, item.id)
    assert [row.user_id for row in rows] == [world.other.id], "the submission's producer"
    assert rows[0].count_status is PrWorkCountStatus.COUNTED


async def test_18_a_re_cut_is_one_job_that_took_two_attempts(world: World) -> None:
    """A cut sent back and re-done is not two production jobs.

    One semantic result, one source key, one work item - and the credit lands
    once. A projector keyed on submissions would pay for every revision.
    """
    edit = await work_type(world, code="VIDEO_EDIT", name="Dựng video")
    await rule(world, kind=PrContentWorkKind.PRODUCTION, type_row=edit)
    await grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await _hand_to_producer(world, content_id, producer=world.other)
    await submit(world, content_id, actor=world.other)
    # Sent back, re-cut, accepted.
    await approve_at(
        world,
        content_id,
        stage=PrApprovalStage.INTERNAL_REVIEW,
        reviewer=world.head,
        decision=PrApprovalDecision.REVISION_REQUIRED,
    )
    await submit(world, content_id, actor=world.other)
    await approve_at(world, content_id, stage=PrApprovalStage.INTERNAL_REVIEW, reviewer=world.head)
    await project(world, content_id)

    results = [
        row
        for row in await content_results(world, content_id)
        if row.source_key is not None and row.source_key.endswith("PRODUCTION")
    ]
    assert len(results) == 1
    assert results[0].status is PrWorkCountStatus.COUNTED
    assert len(await contributions_of(world, results[0].work_item_id)) == 1


# ===========================================================================
# 19-21: PUBLICATION - **turned off in V1. M3.1.**
#
# M3 projected one posting job per publication row. M3.1's business rule is that
# content contributes automatic workload at exactly two milestones, and posting
# is not one of them: it may come back later as manual or recurring work, or as
# a mapping somebody deliberately configures.
#
# These tests now pin the *absence*, which is the thing that could regress
# silently - and test 21c pins the other half, that switching the tap off did
# not drain the tank.
# ===========================================================================


async def test_19_unpublished_content_produces_no_publication_work(world: World) -> None:
    post = await work_type(world, code="PUBLISH", name="Đăng bài")
    await rule(world, kind=PrContentWorkKind.PUBLICATION, type_row=post)
    content_id = await approved_content(world)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PUBLICATION) is None


async def test_20_21_a_recorded_publication_no_longer_projects_work(world: World) -> None:
    """**V1 rule: publication creates no automatic workload.**

    The mapping row may even exist - a department that configured one before
    M3.1, or one that expects the kind to come back - and it still produces
    nothing. The rule is in :data:`AUTOMATIC_KINDS`, not in whether somebody
    happened to configure a work type.
    """
    post = await work_type(world, code="PUBLISH", name="Đăng bài")
    await rule(world, kind=PrContentWorkKind.PUBLICATION, type_row=post)
    content_id = await approved_content(world)
    publication = await _publish(world, content_id, publisher=world.member)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PUBLICATION) is None, (
        "the kind is not considered at all"
    )
    assert await source_work(world, publication.id, PrContentWorkKind.PUBLICATION) is None


async def test_21b_a_fan_out_produces_no_posting_work_either(world: World) -> None:
    """Three channels was three postings under M3. Under V1 it is none.

    Asserted on the fan-out specifically because that was the case M3's design
    notes singled out as the reason publication was keyed on the row rather than
    the content - so it is the case most likely to be re-introduced by somebody
    reading those notes without this one.
    """
    post = await work_type(world, code="PUBLISH", name="Đăng bài")
    await rule(world, kind=PrContentWorkKind.PUBLICATION, type_row=post)
    content_id = await approved_content(world)
    first = await _publish(world, content_id, publisher=world.member)
    second = await _publish(world, content_id, publisher=world.other, channel_no=2)

    await project(world, content_id)
    assert await source_work(world, first.id, PrContentWorkKind.PUBLICATION) is None
    assert await source_work(world, second.id, PrContentWorkKind.PUBLICATION) is None


async def test_21c_publication_work_projected_before_m31_is_left_alone(
    world: World,
) -> None:
    """**Turning the tap off is not draining the tank.**

    A department that ran M3 has counted publication work in months people have
    already been assessed on. The projector reconciles work whose milestone is no
    longer live - and publication milestones are *never* live now - so without an
    explicit exemption every one of those rows would be reversed on the next
    sweep. That would be this milestone silently deleting historical KPI credit.
    """
    post = await work_type(world, code="PUBLISH", name="Đăng bài")
    await rule(world, kind=PrContentWorkKind.PUBLICATION, type_row=post)
    content_id = await approved_content(world)
    publication = await _publish(world, content_id, publisher=world.member)

    # The row M3 would have written, created through the same service the
    # projector uses so that it is indistinguishable from a real one.
    legacy = await world.services.work.create_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        source_key=content_work_source_key(PrContentWorkKind.PUBLICATION, publication.id),
        work_type_id=post.id,
        title="Đăng bài (lịch sử)",
        contributor_user_id=world.member.id,
        content_id=content_id,
        occurred_at=utcnow(),
    )
    await world.services.work.count_source_work(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_item_id=legacy.id,
        validated_by_user_id=world.head.id,
        effective_validation_at=utcnow(),
        note="lịch sử",
    )

    await project(world, content_id)

    await world.session.refresh(legacy)
    assert legacy.status is PrWorkStatus.APPROVED, "not reversed"
    rows = await contributions_of(world, legacy.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED, "historical credit is kept"


async def test_22_23_undoing_the_approval_takes_the_work_back_out(world: World) -> None:
    """**The convergence claim.** Same work item, no deletion, no duplicate.

    An undo raises no *"un-count that"* event - the approval simply stops being
    a live milestone - so an event-driven projector could not notice it at all.
    """
    await open_month(world, utcnow())
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)

    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None
    assert (await contributions_of(world, item.id))[0].count_status is PrWorkCountStatus.COUNTED
    before_id = item.id

    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.REVERSED
    )

    await world.session.refresh(item)
    assert item.id == before_id, "the same stream, not a replacement"
    result = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert result is not None and result.work_item_id == before_id
    assert result.status is PrWorkCountStatus.EXCLUDED, "the result is kept, and taken out"
    assert result.counted_at is None
    assert result.excluded_reason
    assert len(await content_results(world, content_id)) == 1, "nothing duplicated"
    # The stream's actual drops to zero and its contribution stops counting.
    assert item.quantity == Decimal("0.00")
    rows = await contributions_of(world, item.id)
    assert len(rows) == 1
    assert rows[0].count_status is PrWorkCountStatus.PENDING
    assert rows[0].counted_at is None


async def test_24_25_a_redo_reuses_the_same_work_on_the_new_instant(world: World) -> None:
    """Undo then redo is the same job, counted under the **new** decision.

    The original milestone was genuinely reversed; the piece was accepted again
    at a new moment, and that is the business instant the work belongs to. Not
    the projector's clock, and not the withdrawn approval's.
    """
    await open_month(world, utcnow())
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None

    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    await project(world, content_id)

    # Approved again, by the head, at a new moment.
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.PROJECTED
    )

    assert len(await content_results(world, content_id)) == 1, "no second result"
    assert len(await containers_of(world, content_id)) == 1, "no second stream"
    rows = await contributions_of(world, item.id)
    assert len(rows) == 1, "no second contribution"
    assert rows[0].count_status is PrWorkCountStatus.COUNTED

    # And the instant is the approval's own, not the projector's.
    latest = (
        (
            await world.session.execute(
                select(PrApprovalEvent)
                .where(
                    PrApprovalEvent.content_id == content_id,
                    PrApprovalEvent.approval_stage == PrApprovalStage.HEAD_REVIEW,
                    PrApprovalEvent.decision == PrApprovalDecision.APPROVED,
                )
                .order_by(PrApprovalEvent.decided_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .one()
    )
    assert rows[0].counted_at == latest.decided_at


# ===========================================================================
# 26-28: CLOSED AND LOCKED
# ===========================================================================


@pytest.mark.parametrize("state", [PrPeriodStatus.CLOSED, PrPeriodStatus.LOCKED])
async def test_26_27_a_shut_period_is_never_rewritten(world: World, state: PrPeriodStatus) -> None:
    """Historical performance stands. The discrepancy is **reported**, not fixed.

    The numbers were agreed. A source reversal afterwards is a correction
    against a month somebody has already reported, and that belongs to a
    workflow with its own trail rather than to a projector running on a timer.
    """
    period = await open_month(world, utcnow())
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None

    await world.services.undo.undo_last(
        actor=world.actor(world.head), request_id=world.request_id, content_id=content_id
    )
    period.status = state
    await world.session.flush()

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.BLOCKED_BY_PERIOD
    )

    await world.session.refresh(item)
    result = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert result is not None and result.status is PrWorkCountStatus.COUNTED, "history stands"
    assert item.quantity == Decimal("1.00")
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED
    assert rows[0].counted_at is not None
    # And the refusal is on the record rather than silent.
    blocked = await world.session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "pr.content_work.blocked")
    )
    assert blocked and blocked >= 1


async def test_28_there_is_no_force_flag_anywhere(world: World) -> None:
    """Structural. Getting past a closed period is a correction workflow, not a
    boolean somebody can pass."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    for name in (
        "application/pr_content_work_projector.py",
        "api/schemas/pr_content_work.py",
        "api/routers/pr_content_work.py",
    ):
        names = _identifiers(root / name)
        assert "force" not in names, name


# ===========================================================================
# 29-31: CONTRIBUTOR INTEGRITY
# ===========================================================================


async def test_29_30_the_contributor_is_the_historical_author(world: World) -> None:
    """Never today's owner. The version that was approved names its own author,
    and that row is append-only.

    A catch-up that read ``owner_user_id`` would credit today's owner for a
    script somebody else wrote - a wrong row that afterwards looks exactly like
    a right one.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world, writer=world.member)

    # Handed over to somebody else before anybody projects it.
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    content.owner_user_id = world.other.id
    await world.session.flush()

    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None
    rows = await contributions_of(world, item.id)
    assert [row.user_id for row in rows] == [world.member.id], "the author, not the new owner"


async def test_31_an_unreconstructable_contributor_is_reported_not_guessed(
    world: World,
) -> None:
    """``UNRESOLVED_CONTRIBUTOR`` - a projection problem, and **not** a quota one.

    M3 exercised this through a publication with no publisher. V1 no longer
    projects publications, so the case moves to the path that still exists and is
    the one that actually matters: a head approval whose transition does not pin
    the version that was approved, which is the shape imported or pre-versioning
    content has.

    The point is unchanged and is the reason the outcome exists: the content item
    has an ``owner_user_id`` sitting right there, and reading it would produce a
    row crediting today's owner for somebody else's script - indistinguishable
    from a correct one afterwards. Crediting nobody is the safe failure.
    """
    from meobot.db.models.pr_transition import PrContentTransitionEvent

    script = await work_type(world, code="SHORT_SCRIPT", name="Kịch bản ngắn")
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=script)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)

    event = (
        (
            await world.session.execute(
                select(PrContentTransitionEvent).where(
                    PrContentTransitionEvent.content_id == content_id,
                    PrContentTransitionEvent.to_stage == PrWorkflowStage.APPROVED,
                )
            )
        )
        .scalars()
        .one()
    )
    event.content_version_id = None
    await world.session.flush()

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.UNRESOLVED_CONTRIBUTOR
    )
    assert await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION) is None


async def test_32_33_34_source_facts_cannot_be_hand_edited(world: World) -> None:
    """The rule is the same for an employee and for a manager.

    Not a permission level - *whose fact it is*. The projector rewrites these
    from the source on every run, so an edit would either be silently reverted
    or would make the ledger disagree with the workflow it is a view of.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None

    owner = world.actor(world.owner)
    result = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert result is not None
    # The stream is a container: it is not finished, validated, staffed or
    # cancelled like a job - the same refusal for every ordinary lifecycle act.
    for call, reason in (
        (
            world.services.work.cancel(
                actor=owner, request_id=world.request_id, work_item_id=item.id
            ),
            "period_container",
        ),
        (
            world.services.work.add_contributor(
                actor=owner,
                request_id=world.request_id,
                work_item_id=item.id,
                user_id=world.other.id,
            ),
            "period_container",
        ),
        (
            world.services.work.approve(
                actor=owner, request_id=world.request_id, work_item_id=item.id
            ),
            "period_container",
        ),
    ):
        with pytest.raises(PrValidationError) as caught:
            await call
        assert caught.value.details["reason"] == reason
    # And the source's own result is not something a person withdraws.
    with pytest.raises(PrValidationError) as refused:
        await world.services.work_results.withdraw_result(
            actor=world.actor(world.member), request_id=world.request_id, result_id=result.id
        )
    assert refused.value.details["reason"] == "source_result"


async def test_35_independent_validation_stays_available(world: World) -> None:
    """The one manual action that is **not** a source fact.

    The source has no opinion about whether somebody else has checked the work,
    which is exactly why this is the action that stays open.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    # The head, deliberately: an ADMIN who holds ``PR_WORK_VALIDATE`` *and* the
    # head review gate. Using an employee would make test 08 pass because they
    # lack the work capability rather than because the self-validation rule
    # refused them - the same rule passing for the wrong reason.
    content_id = await approved_content(world, writer=world.head, head=world.head)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None

    detail = await world.services.work.detail(actor=world.actor(world.owner), work_item_id=item.id)
    assert detail.can_validate is True
    assert detail.is_subject is False
    # The result names the piece it came from, so a card can still say
    # "CNT-2026-000042" rather than a UUID.
    result = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert result is not None and result.label
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None and content.code in result.label


async def test_35b_the_source_operations_reach_no_route(world: World) -> None:
    """Structural. ``count_source_work`` and ``reverse_source_work`` are internal.

    A public route for either would be a route through which a client could
    supply its own ``counted_at`` or un-count somebody's work - the two things
    the whole design keeps out of a request body.
    """
    import pathlib

    routers = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot" / "api" / "routers"
    for path in routers.rglob("*.py"):
        # Identifiers, parsed rather than grepped: this module's own docstring
        # explains at length that these methods are internal, and a text sweep
        # would forbid saying so.
        names = _identifiers(path)
        assert "count_source_work" not in names, path.name
        assert "reverse_source_work" not in names, path.name
        assert "effective_validation_at" not in names, path.name


# ===========================================================================
# 36-39: MAPPING
# ===========================================================================


async def test_36_no_mapping_produces_no_false_work_type(world: World) -> None:
    """Filing work under a heading nobody chose is the mistake the content-type
    backfill already refused to make.

    Since ``0040`` a *typed* piece with no mapping is bound to a work type
    provisioned for exactly its type - see
    ``test_pr_content_work_auto_provision`` - so the case that still has no
    answer is content with **no type**: there is nothing stable to bind, and
    guessing is still refused.
    """
    content_id = await approved_content(world, content_type=None)
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.NO_MAPPING
    )
    assert await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION) is None
    assert (await world.session.execute(select(PrWorkType))).scalars().all() == []


async def test_36b_an_inactive_mapping_maps_nothing(world: World) -> None:
    """A rule somebody turned off is a decision, and provisioning does not
    overrule it: the exact case stays ``NO_MAPPING`` and no type is created."""
    type_row = await work_type(world)
    await rule(
        world,
        kind=PrContentWorkKind.CONTENT_CREATION,
        type_row=type_row,
        content_type=PrContentType.SHORT_VIDEO_SCRIPT,
        is_active=False,
    )
    content_id = await approved_content(world, content_type=PrContentType.SHORT_VIDEO_SCRIPT)
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.NO_MAPPING
    )
    assert [row.code for row in (await world.session.execute(select(PrWorkType))).scalars()] == [
        type_row.code
    ]


async def test_37_an_exact_mapping_beats_the_default(world: World) -> None:
    """A department says one broad thing and refines it later, without having to
    enumerate everything else first."""
    default = await work_type(world, code="ANY_SCRIPT", name="Kịch bản")
    specific = await work_type(world, code="SHORT_VIDEO", name="Kịch bản video ngắn")
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=default)
    await rule(
        world,
        kind=PrContentWorkKind.CONTENT_CREATION,
        type_row=specific,
        content_type=PrContentType.SHORT_VIDEO_SCRIPT,
    )
    content_id = await approved_content(world, content_type=PrContentType.SHORT_VIDEO_SCRIPT)
    await project(world, content_id)

    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None and item.work_type_id == specific.id

    # And unclassified content falls to the default.
    other_id = await approved_content(world, content_type=None, title="Chưa phân loại")
    await project(world, other_id)
    fallback = await source_work(world, other_id, PrContentWorkKind.CONTENT_CREATION)
    assert fallback is not None and fallback.work_type_id == default.id


async def test_38_a_retired_work_type_cannot_be_mapped(world: World) -> None:
    """Refused at configuration rather than discovered later as work filed under
    a heading the department retired."""
    type_row = await work_type(world)
    await world.services.work.set_work_type_active(
        actor=world.actor(world.owner),
        request_id=world.request_id,
        work_type_id=type_row.id,
        is_active=False,
    )
    with pytest.raises(PrValidationError) as caught:
        await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    assert caught.value.details["reason"] == "work_type_inactive"


async def test_39_changing_a_mapping_rewrites_no_existing_work(world: World) -> None:
    """Every work item records the type it was filed as, so yesterday's counted
    work keeps saying what it always said."""
    first = await work_type(world, code="OLD_TYPE", name="Cũ")
    second = await work_type(world, code="NEW_TYPE", name="Mới")
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=first)
    content_id = await approved_content(world)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None and item.work_type_id == first.id

    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=second)
    await project(world, content_id)
    await world.session.refresh(item)
    assert item.work_type_id == first.id, "history keeps the type it was filed as"
    result = await source_result(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert result is not None and result.work_item_id == item.id, "the counted result stays put"


# ===========================================================================
# 40-43: THE TRIGGER AND THE CATCH-UP
# ===========================================================================


async def test_40_reconciling_one_content_item_is_idempotent(world: World) -> None:
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)

    for index in range(3):
        report = await world.services.content_work.reconcile(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            content_ids=[content_id],
        )
        assert report.content_items == 1
        expected = "PROJECTED" if index == 0 else "UNCHANGED"
        assert report.counts[expected] >= 1

    assert len(await content_results(world, content_id)) == 1


async def test_41_a_bounded_reconcile_needs_the_configure_capability(world: World) -> None:
    """A catch-up can move KPI figures. ``PR_WORK_MANAGE`` does not open it."""
    with pytest.raises(PrPermissionDeniedError):
        await world.services.content_work.reconcile(
            actor=world.actor(world.lead), request_id=world.request_id, limit=5
        )
    with pytest.raises(PrPermissionDeniedError):
        await world.services.content_work.reconcile(
            actor=world.actor(world.member), request_id=world.request_id, limit=5
        )


async def test_42_a_dry_run_writes_nothing(world: World) -> None:
    """So an operator can see what a catch-up would do before doing it."""
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)

    report = await project(world, content_id, dry_run=True)
    assert outcome_for(report, PrContentWorkKind.CONTENT_CREATION) is (
        PrContentWorkOutcome.PROJECTED
    )
    assert await content_results(world, content_id) == [], "a dry run is a read"


async def test_43_the_migration_backfilled_nothing_and_the_trigger_queues(
    world: World,
) -> None:
    """No historical backfill, and a content transition leaves a work list entry.

    The request row is written **inside** the content transaction, which is the
    whole delivery guarantee: if the transition committed, so did this.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)

    row = (
        (
            await world.session.execute(
                select(PrContentWorkProjection).where(
                    PrContentWorkProjection.content_id == content_id
                )
            )
        )
        .scalars()
        .one()
    )
    assert row.status is PrContentWorkProjectionStatus.PENDING
    # One row for the whole journey, however many stages it moved through.
    total = await world.session.scalar(select(func.count()).select_from(PrContentWorkProjection))
    assert total == 1

    # And the queue claims, settles and records what it concluded.
    claimed = await world.services.content_work.claim_batch()
    assert claimed == [content_id]
    await world.services.content_work.settle(
        actor=world.actor(world.owner), request_id=world.request_id, content_id=content_id
    )
    await world.session.refresh(row)
    assert row.status is PrContentWorkProjectionStatus.SETTLED
    assert row.last_outcome is PrContentWorkOutcome.PROJECTED
    assert row.attempts == 1


async def test_43b_a_request_while_running_re_queues_it(world: World) -> None:
    """The worker holding it is looking at facts that have since changed, and its
    settlement must not be the last word."""
    content_id = await make_content(world, owner=world.member)
    await world.services.content_work.claim_batch()
    await request_content_work_projection(world.session, content_id)

    row = (
        (
            await world.session.execute(
                select(PrContentWorkProjection).where(
                    PrContentWorkProjection.content_id == content_id
                )
            )
        )
        .scalars()
        .one()
    )
    assert row.status is PrContentWorkProjectionStatus.PENDING


# ===========================================================================
# 44-48: REGRESSION AND THE M4 SEAM
# ===========================================================================


async def test_44_manual_work_is_untouched(world: World) -> None:
    """M1's ladder, exactly as M1 shipped it. In particular ``APPROVED`` is still
    terminal for anything a person filed."""
    type_row = await work_type(world)
    item = await world.services.work.assign_work(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        command=CreateWorkCommand(
            work_type_id=type_row.id,
            title="Việc tay",
            contributor_user_ids=(world.member.id,),
        ),
    )
    assert item.source_type is PrWorkSourceType.MANUAL
    # A person's item may still be cancelled and edited.
    await world.services.work.change_priority(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        work_item_id=item.id,
        priority=__import__("meobot.domain.pr.models", fromlist=["PrPriority"]).PrPriority.HIGH,
    )
    # And the source-only edge is refused for it.
    with pytest.raises(PrValidationError) as caught:
        await world.services.work.reverse_source_work(
            actor=world.actor(world.owner),
            request_id=world.request_id,
            work_item_id=item.id,
            reason="test",
        )
    assert caught.value.details["reason"] == "not_source_derived"


async def test_45_approved_is_still_terminal_for_manual_work(world: World) -> None:
    """The domain rule, asserted directly rather than only through a service."""
    from meobot.domain.pr.work import (
        allowed_work_transitions,
        can_transition_work,
    )

    assert not can_transition_work(PrWorkStatus.APPROVED, PrWorkStatus.COMPLETED)
    assert can_transition_work(PrWorkStatus.APPROVED, PrWorkStatus.COMPLETED, source_derived=True)
    assert allowed_work_transitions(PrWorkStatus.APPROVED) == frozenset()


async def test_46_the_content_workflow_is_unchanged(world: World) -> None:
    """M3 adapts to content, never the other way round.

    The stage ladder, the approval gates and the undo semantics are the ones the
    department already runs on - the projector reads them and writes nowhere
    near them.
    """
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    content_id = await approved_content(world)
    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    stage_before = content.workflow_stage

    await project(world, content_id)
    await world.session.refresh(content)
    assert content.workflow_stage is stage_before
    assert content.owner_user_id == world.member.id
    # No second approval was demanded of anybody.
    approvals = await world.session.scalar(
        select(func.count())
        .select_from(PrApprovalEvent)
        .where(PrApprovalEvent.content_id == content_id)
    )
    assert approvals == 2


async def test_47_the_task_module_is_untouched(world: World) -> None:
    """No bridge, no changed semantics. M4's, if anybody's."""
    import pathlib

    from meobot.db.models.pr import PrTask

    before = await world.session.scalar(select(func.count()).select_from(PrTask))
    type_row = await work_type(world)
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=type_row)
    await project(world, await approved_content(world))
    assert await world.session.scalar(select(func.count()).select_from(PrTask)) == before

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    names = _identifiers(root / "application" / "pr_content_work_projector.py")
    assert "PrTask" not in names
    assert "PrTaskAssignment" not in names


async def test_48_no_scoring_word_appears_in_the_m3_modules(world: World) -> None:
    """M3 records that work happened. What it is worth is still M6's."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    for name in (
        "domain/pr/content_work.py",
        "db/models/pr_content_work.py",
        "application/pr_content_work_projector.py",
        "application/pr_content_work_service.py",
        "api/schemas/pr_content_work.py",
        "tasks/pr_content_work.py",
    ):
        code = "\n".join(
            line
            for line in (root / name).read_text("utf-8").splitlines()
            if not line.lstrip().startswith(("#", "*", '"'))
        )
        for word in ("base_score", "awarded_score", "points", "quality_multiplier", "bonus"):
            assert f"{word} =" not in code and f"{word}:" not in code, (name, word)


# ===========================================================================
# Helpers used above
# ===========================================================================


async def _hand_to_producer(world: World, content_id: uuid.UUID, *, producer: User) -> None:
    """Assign the edit and start it. The two deliberate acts after approval."""
    await world.services.production.assign_producer(
        actor=world.actor(world.lead),
        request_id=world.request_id,
        content_id=content_id,
        producer_user_id=producer.id,
    )
    await world.services.production.start_production(
        actor=world.actor(producer), request_id=world.request_id, content_id=content_id
    )


async def _publish(
    world: World, content_id: uuid.UUID, *, publisher: User, channel_no: int = 1
) -> PrPublication:
    """One publication row, written directly.

    Directly rather than through ``register_publication`` because that path
    requires the content to be at ``READY_TO_PUBLISH`` and would drag the whole
    production half into every publication test - and what M3 reads is the row,
    not the route that wrote it. The columns set here are exactly the ones the
    projector reads.
    """
    row = PrPublication(
        code=f"PUB-2026-{uuid.uuid4().hex[:6]}",
        content_id=content_id,
        channel_id=world.channel_id,
        published_at=utcnow() - timedelta(minutes=channel_no),
        publisher_user_id=publisher.id,
        status=PrPublicationStatus.PUBLISHED,
    )
    world.session.add(row)
    await world.session.flush()
    return row


def _identifiers(path) -> set[str]:  # type: ignore[no-untyped-def]
    """Every name a module actually *uses*, with prose excluded.

    Parsed with :mod:`ast` rather than grepped, so a docstring explaining why a
    table is not reached for does not read as reaching for it - and these
    modules explain that at length, because the boundary is the milestone.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.update(node.module.split("."))
        elif isinstance(node, ast.arg) or (isinstance(node, ast.keyword) and node.arg):
            found.add(node.arg)
    return found
