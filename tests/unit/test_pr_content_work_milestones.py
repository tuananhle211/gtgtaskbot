"""The two content milestones that create work, and the ones that must not. M3.1.

M3 asked *"which content facts are trustworthy enough to become work"* and
answered with three kinds. M3.1 asks the narrower product question - *"where does
the department actually want automatic workload"* - and answers with two:

    1. Duyệt trưởng phòng  -> the writer's job is done, and counted if the head
                              who approved it is not the writer;
    2. Gửi bản dựng        -> the editor's job is done. Workload recorded,
                              **not counted** until somebody accepts the cut.

The distinction the file exists to hold
----------------------------------------

M3 keyed production work on the *acceptance* of the cut. That made a finished
job invisible until a reviewer got to it: an editor who handed in on Friday had
nothing on their board until Monday, and a queue of unreviewed cuts looked like
a queue of people who had done nothing.

So M3.1 splits one milestone into two instants:

    submission  -> COMPLETED, workload visible      (tests 15-19)
    acceptance  -> COUNTED, on the acceptance's own instant  (tests 20-22)

and everything else follows from keeping those two apart. Four draft versions are
one job (tests 16-18) because the source key never mentions the submission.
Withdrawing the acceptance takes the count back out and **keeps the work item**
(test 30), because the editing happened whatever became of the cut.

What must not happen
---------------------

Publication, thumbnails, shoots, reviewing - none of them produce automatic work
in V1 (tests 31-33), and the publication work M3 already created is kept rather
than reconciled away (`test_pr_content_work_projection.py::test_21c`).

Nothing here contacts a network.
"""

from __future__ import annotations

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import select

from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.pr_work import PrWorkItem
from meobot.domain.pr.content_work import (
    AUTOMATIC_KINDS,
    PrContentWorkKind,
    PrContentWorkOutcome,
    content_work_source_key,
)
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrContentType,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import PrWorkCountStatus
from tests.unit.test_pr_content_work_projection import (
    _hand_to_producer,
    _to_head_review,
    approve_at,
    content_results,
    contributions_of,
    grant,
    open_month,
    outcome_for,
    project,
    rule,
    source_result,
    source_work,
    work_type,
    write_content,
)
from tests.unit.test_pr_production_lifecycle import (  # noqa: F401 - `world` is a fixture
    World,
    decide,
    submit,
    world,
)

pytestmark = pytest.mark.asyncio


async def creation_rule(world: World, *, content_type: PrContentType | None = None):  # type: ignore[no-untyped-def]
    """A mapping for the writer's milestone."""
    script = await work_type(
        world, code=f"SCRIPT_{uuid.uuid4().hex[:6].upper()}", name="Kịch bản video ngắn"
    )
    await rule(
        world,
        kind=PrContentWorkKind.CONTENT_CREATION,
        type_row=script,
        content_type=content_type,
    )
    return script


async def production_rule(world: World, *, content_type: PrContentType | None = None):  # type: ignore[no-untyped-def]
    """A mapping for the editor's milestone."""
    edit = await work_type(world, code=f"EDIT_{uuid.uuid4().hex[:6].upper()}", name="Dựng video")
    await rule(world, kind=PrContentWorkKind.PRODUCTION, type_row=edit, content_type=content_type)
    return edit


async def to_submitted(world: World, *, writer, producer, head=None):  # type: ignore[no-untyped-def]
    """Content approved by the head, handed over, and a cut handed in."""
    reviewer = head or world.head
    await grant(world, reviewer, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=writer)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=reviewer)
    await _hand_to_producer(world, content_id, producer=producer)
    await submit(world, content_id, actor=producer)
    return content_id


async def accept_cut(world: World, content_id: uuid.UUID, *, reviewer) -> None:  # type: ignore[no-untyped-def]
    """The canonical video approval - INTERNAL_REVIEW -> READY_TO_PUBLISH."""
    await approve_at(world, content_id, stage=PrApprovalStage.INTERNAL_REVIEW, reviewer=reviewer)


# ===========================================================================
# THE V1 RULE ITSELF
# ===========================================================================


async def test_00_only_two_kinds_are_projected_automatically() -> None:
    """The business decision, as one assertion.

    A regression here is somebody re-enabling a milestone by adding an enum
    member, which is exactly how the third one got in.
    """
    assert {
        PrContentWorkKind.CONTENT_CREATION,
        PrContentWorkKind.PRODUCTION,
    } == AUTOMATIC_KINDS
    assert PrContentWorkKind.PUBLICATION not in AUTOMATIC_KINDS


# ===========================================================================
# 14-19: GỬI BẢN DỰNG IS THE WORKLOAD MILESTONE
# ===========================================================================


async def test_14_assigning_a_producer_is_not_workload(world: World) -> None:
    """Handing somebody a job is not the job being done."""
    await creation_rule(world)
    await production_rule(world)
    await grant(world, world.head, PrCapability.PR_INTERNAL_REVIEW)
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await _hand_to_producer(world, content_id, producer=world.other)

    await project(world, content_id)
    assert await source_work(world, content_id, PrContentWorkKind.PRODUCTION) is None


async def test_15_the_first_submission_records_workload_uncounted(world: World) -> None:
    """**The milestone.** One work item, ``COMPLETED``, credited to the producer.

    Not counted, and the reason is M1's boundary rather than distrust of editors:
    the person who handed the file in is not a second person confirming it.
    """
    await creation_rule(world)
    edit = await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is (
        PrContentWorkOutcome.PENDING_VALIDATION
    )
    result = await source_result(world, content_id, PrContentWorkKind.PRODUCTION)
    assert result is not None
    assert result.status is PrWorkCountStatus.PENDING
    item = await world.session.get(PrWorkItem, result.work_item_id)
    assert item is not None and item.is_period_container
    assert item.work_type_id == edit.id
    rows = await contributions_of(world, item.id)
    assert [row.user_id for row in rows] == [world.other.id]
    assert rows[0].count_status is PrWorkCountStatus.PENDING
    assert rows[0].counted_at is None


@pytest.mark.parametrize("versions", [2, 3, 4])
async def test_16_17_many_draft_versions_are_one_job(world: World, versions: int) -> None:
    """Tests 16 and 17. V2, V3 and V4 are revisions, not new jobs.

    The guarantee is in the source key - ``content:{id}:PRODUCTION`` names no
    submission - so this holds however many times the editor re-cuts.
    """
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    for _ in range(versions - 1):
        # A re-cut is a real revision: the internal reviewer sends it back and
        # the editor hands in again. Submitting twice from ``INTERNAL_REVIEW``
        # is not a thing the product can do, so faking it would be testing a
        # path that does not exist.
        await decide(
            world,
            actor=world.head,
            content_id=content_id,
            stage=PrApprovalStage.INTERNAL_REVIEW,
            decision=PrApprovalDecision.REVISION_REQUIRED,
        )
        await submit(world, content_id, actor=world.other)

    await project(world, content_id)
    key = content_work_source_key(PrContentWorkKind.PRODUCTION, content_id)
    results = [row for row in await content_results(world, content_id) if row.source_key == key]
    assert len(results) == 1, f"{versions} submissions produced {len(results)} results"
    assert results[0].quantity == 1
    assert len(await contributions_of(world, results[0].work_item_id)) == 1


async def test_18_a_worker_retry_changes_nothing(world: World) -> None:
    """Idempotent on replay, which is what makes the sweeper safe to re-run."""
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)

    await project(world, content_id)
    first = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert first is not None
    for _ in range(4):
        await project(world, content_id)
    again = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert again is not None and again.id == first.id
    assert len(await contributions_of(world, again.id)) == 1


async def test_19_the_production_work_type_comes_from_the_mapping(world: World) -> None:
    """Owner configuration, per content type - not a constant in the projector.

    The whole point of the rule table: a department that decides long YouTube
    cuts are a different job from short ones configures it, and nobody deploys.
    """
    await creation_rule(world)
    generic = await production_rule(world)
    tvc = await work_type(world, code="TVC_EDIT", name="Dựng TVC")
    await rule(
        world,
        kind=PrContentWorkKind.PRODUCTION,
        type_row=tvc,
        content_type=PrContentType.SHORT_VIDEO_SCRIPT,
    )

    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert item is not None
    assert item.work_type_id == tvc.id, "the exact rule beats the default"
    assert item.work_type_id != generic.id


async def test_19b_production_with_no_mapping_is_reported_not_guessed(
    world: World,
) -> None:
    """``NO_MAPPING``, and no work. Guessing would file the cut under a heading
    nobody chose, and it looks identical to a correct row afterwards.

    Since ``0040`` a *typed* piece with no mapping is bound to a work type
    provisioned for its type - ``test_pr_content_work_auto_provision`` covers
    the production kind - so the case that is still refused is a mapping an
    administrator **deactivated**: a decision, not a gap to fill.
    """
    await creation_rule(world)
    retired = await work_type(world, code="OLD_EDIT", name="Dựng cũ")
    await rule(
        world,
        kind=PrContentWorkKind.PRODUCTION,
        type_row=retired,
        content_type=PrContentType.SHORT_VIDEO_SCRIPT,
        is_active=False,
    )
    content_id = await to_submitted(world, writer=world.member, producer=world.other)

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.NO_MAPPING
    assert await source_work(world, content_id, PrContentWorkKind.PRODUCTION) is None


# ===========================================================================
# 20-23: THE VIDEO APPROVAL COUNTS IT
# ===========================================================================


async def test_20_an_independent_acceptance_counts_the_same_contribution(
    world: World,
) -> None:
    """Same work item, same contribution - **no second job for the reviewing**."""
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await project(world, content_id)
    before = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert before is not None

    await accept_cut(world, content_id, reviewer=world.head)
    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.PROJECTED

    after = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert after is not None and after.id == before.id, "the same job, now accepted"
    rows = await contributions_of(world, after.id)
    assert len(rows) == 1
    assert rows[0].count_status is PrWorkCountStatus.COUNTED


async def test_21_counted_at_is_the_video_approval_instant(world: World) -> None:
    """Not the hand-in, and not the projector's clock.

    The workload happened when the cut was submitted; the **KPI** belongs to the
    month somebody accepted it, and a worker retry next month must not move it.
    """
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await project(world, content_id)
    await accept_cut(world, content_id, reviewer=world.head)
    await project(world, content_id)

    item = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert item is not None
    counted_at = (await contributions_of(world, item.id))[0].counted_at
    assert counted_at is not None

    approval_event = (
        (
            await world.session.execute(
                select(PrContentTransitionEvent).where(
                    PrContentTransitionEvent.content_id == content_id,
                    PrContentTransitionEvent.to_stage == PrWorkflowStage.READY_TO_PUBLISH,
                )
            )
        )
        .scalars()
        .one()
    )
    approval = await world.session.get(PrApprovalEvent, approval_event.approval_event_id)
    assert approval is not None
    assert counted_at == approval.decided_at

    # And a retry does not move it.
    await project(world, content_id)
    assert (await contributions_of(world, item.id))[0].counted_at == counted_at


async def test_22_the_editor_cannot_validate_their_own_production_work(
    world: World,
) -> None:
    """M1's anti-gaming rule reaches source-derived work unchanged.

    The **head** is the editor here, deliberately: an ADMIN who already holds
    ``PR_WORK_VALIDATE`` by role. Using an employee would make this pass because
    they lack the capability rather than because the self-validation rule
    refused them - the same assertion passing for the wrong reason.
    """
    from meobot.domain.pr.errors import PrPermissionDeniedError

    await creation_rule(world)
    await production_rule(world)
    await grant(world, world.lead, PrCapability.PR_HEAD_REVIEW)
    content_id = await to_submitted(
        world, writer=world.member, producer=world.head, head=world.lead
    )
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert item is not None

    with pytest.raises(PrPermissionDeniedError) as caught:
        await world.services.work_results.validate_results(
            actor=world.actor(world.head),
            request_id=world.request_id,
            work_item_id=item.id,
        )
    assert caught.value.details["reason"] == "self_validation"


async def test_23_the_video_reviewer_receives_no_work_of_their_own(
    world: World,
) -> None:
    """Reviewing is real effort and is **not** a work type in V1.

    The reviewer appears as the validator on the editor's contribution and gets
    no row of their own - which is also what keeps the two of them on opposite
    sides of the anti-gaming boundary.
    """
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await accept_cut(world, content_id, reviewer=world.head)
    await project(world, content_id)

    for result in await content_results(world, content_id):
        assert result.user_id != world.head.id, "the reviewer earned no workload"
        for row in await contributions_of(world, result.work_item_id):
            assert row.user_id != world.head.id


# ===========================================================================
# 30-33: REVERSAL, AND THE MILESTONES THAT MUST STAY SILENT
# ===========================================================================


async def test_30_withdrawing_the_acceptance_keeps_the_job_and_drops_the_count(
    world: World,
) -> None:
    """**The state M3 could not reach.**

    Under M3 the acceptance *was* the milestone, so losing it made the work an
    orphan and the whole item was reversed. Under M3.1 the submission is the
    milestone and it does not un-happen: the editing job stays on the board, and
    only the KPI credit goes away.
    """
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await accept_cut(world, content_id, reviewer=world.head)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert item is not None
    assert (await contributions_of(world, item.id))[0].count_status is PrWorkCountStatus.COUNTED

    # Withdraw the acceptance the way an undo does: the transition stops being
    # live, which is the one predicate the projector reads.
    event = (
        (
            await world.session.execute(
                select(PrContentTransitionEvent).where(
                    PrContentTransitionEvent.content_id == content_id,
                    PrContentTransitionEvent.to_stage == PrWorkflowStage.READY_TO_PUBLISH,
                )
            )
        )
        .scalars()
        .one()
    )
    event.reversed_by_event_id = event.id
    await world.session.flush()

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is PrContentWorkOutcome.REVERSED

    same = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert same is not None and same.id == item.id, "the stream is not deleted"
    result = await source_result(world, content_id, PrContentWorkKind.PRODUCTION)
    assert result is not None and result.status is PrWorkCountStatus.EXCLUDED, "kept, uncounted"
    rows = await contributions_of(world, same.id)
    assert rows[0].count_status is not PrWorkCountStatus.COUNTED


async def test_30b_a_closed_period_blocks_the_withdrawal(world: World) -> None:
    """Historical performance is not rewritten, and there is no force flag."""
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await accept_cut(world, content_id, reviewer=world.head)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.PRODUCTION)
    assert item is not None
    counted_at = (await contributions_of(world, item.id))[0].counted_at
    assert counted_at is not None

    period = await open_month(world, counted_at)
    period.status = PrPeriodStatus.CLOSED
    await world.session.flush()

    event = (
        (
            await world.session.execute(
                select(PrContentTransitionEvent).where(
                    PrContentTransitionEvent.content_id == content_id,
                    PrContentTransitionEvent.to_stage == PrWorkflowStage.READY_TO_PUBLISH,
                )
            )
        )
        .scalars()
        .one()
    )
    event.reversed_by_event_id = event.id
    await world.session.flush()

    report = await project(world, content_id)
    assert outcome_for(report, PrContentWorkKind.PRODUCTION) is (
        PrContentWorkOutcome.BLOCKED_BY_PERIOD
    )
    rows = await contributions_of(world, item.id)
    assert rows[0].count_status is PrWorkCountStatus.COUNTED, "the closed month is untouched"


async def test_31_32_33_no_other_content_step_creates_work(world: World) -> None:
    """Publication, thumbnails and shoots produce nothing. **V1 rule.**

    Asserted as one test over the whole ledger rather than three that each look
    for one absent row: what must hold is that a fully-published piece has
    *exactly* the two jobs, whatever else happened to it along the way.
    """
    await creation_rule(world)
    await production_rule(world)
    content_id = await to_submitted(world, writer=world.member, producer=world.other)
    await accept_cut(world, content_id, reviewer=world.head)
    await project(world, content_id)

    results = await content_results(world, content_id)
    kinds = sorted(row.source_key.rsplit(":", 1)[-1] for row in results if row.source_key)
    assert kinds == ["CONTENT_CREATION", "PRODUCTION"], kinds


# ===========================================================================
# 24-27: CORRECTING THE WORK TYPE, AND WHERE THE CORRECTION STOPS
# ===========================================================================


async def self_approved(world: World):  # type: ignore[no-untyped-def]
    """Content whose head approval was its own writer's, so the work is
    projected **uncounted** - the state a correction is still allowed in."""
    content_id = await write_content(world, writer=world.head)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    return content_id


async def test_24_correcting_the_content_type_refiles_uncounted_work(
    world: World,
) -> None:
    """Somebody classified a piece wrongly and fixed it. **M3.1.**

    M3 resolved the work type once, at creation, so a corrected content type
    left the work under the heading nobody meant - permanently, and invisibly.
    While nothing is counted there is no month to protect and no reason not to
    re-file it.
    """
    short = await work_type(world, code="SHORT_SCRIPT", name="Kịch bản video ngắn")
    ultra = await work_type(world, code="ULTRA_SHORT", name="Kịch bản video siêu ngắn")
    await rule(
        world,
        kind=PrContentWorkKind.CONTENT_CREATION,
        type_row=short,
        content_type=PrContentType.SHORT_VIDEO_SCRIPT,
    )
    await rule(
        world,
        kind=PrContentWorkKind.CONTENT_CREATION,
        type_row=ultra,
        content_type=PrContentType.ULTRA_SHORT_SCRIPT,
    )
    content_id = await self_approved(world)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None and item.work_type_id == short.id
    assert (await contributions_of(world, item.id))[0].count_status is not (
        PrWorkCountStatus.COUNTED
    )

    content = await world.session.get(PrContentItem, content_id)
    assert content is not None
    content.content_type = PrContentType.ULTRA_SHORT_SCRIPT
    await world.session.flush()

    await project(world, content_id)
    moved = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert moved is not None and moved.work_type_id == ultra.id, "re-filed under the corrected type"
    assert moved.id != item.id, "into the stream of the corrected type"
    await world.session.refresh(item)
    assert item.work_type_id == short.id, "the old stream keeps its type, and is now empty"


async def test_25_changing_the_mapping_refiles_uncounted_work(world: World) -> None:
    """The owner's correction, not a rewrite of anybody's record.

    The mirror of ``test_pr_content_work_projection.py::test_39``: that one pins
    that **counted** work keeps the type it was filed as, and this one pins that
    work nobody has counted yet does not get stranded under a mapping the
    department has replaced.
    """
    first = await work_type(world, code="OLD_TYPE", name="Cũ")
    second = await work_type(world, code="NEW_TYPE", name="Mới")
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=first)
    content_id = await self_approved(world)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None and item.work_type_id == first.id

    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=second)
    await project(world, content_id)
    moved = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert moved is not None and moved.work_type_id == second.id


async def test_26_27_a_counted_row_is_never_refiled(world: World) -> None:
    """Tests 26 and 27, and the boundary the correction path stops at.

    Once a contribution is ``COUNTED`` it is a figure in a reporting period
    somebody may already have been assessed on. Re-filing it under another
    heading would rewrite what that month claimed - so the mapping change is
    simply not applied, whatever the period's state, and the ledger keeps saying
    what it always said.
    """
    first = await work_type(world, code="OLD_TYPE", name="Cũ")
    second = await work_type(world, code="NEW_TYPE", name="Mới")
    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=first)
    # Independently approved, so it counts.
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)
    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None
    assert (await contributions_of(world, item.id))[0].count_status is PrWorkCountStatus.COUNTED

    await rule(world, kind=PrContentWorkKind.CONTENT_CREATION, type_row=second)
    await project(world, content_id)
    same = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert same is not None and same.id == item.id
    assert same.work_type_id == first.id, "a counted month is not re-typed"


# ===========================================================================
# 34-40: M2.5 AND M2 STAY WHERE THEY ARE
# ===========================================================================


async def test_34_a_work_type_created_today_is_mappable_today(world: World) -> None:
    """The M2.5 handoff: no code change to add a content work type."""
    fresh = await work_type(world, code="ULTRA_SHORT_SCRIPT", name="Kịch bản video siêu ngắn")
    await rule(
        world,
        kind=PrContentWorkKind.CONTENT_CREATION,
        type_row=fresh,
        content_type=PrContentType.SHORT_VIDEO_SCRIPT,
    )
    content_id = await write_content(world, writer=world.member)
    await _to_head_review(world, content_id)
    await approve_at(world, content_id, stage=PrApprovalStage.HEAD_REVIEW, reviewer=world.head)

    await project(world, content_id)
    item = await source_work(world, content_id, PrContentWorkKind.CONTENT_CREATION)
    assert item is not None and item.work_type_id == fresh.id


async def test_39_the_projector_writes_no_quota_allocation(world: World) -> None:
    """M3 never writes M2's vocabulary. The source scan, kept for M3.1's changes."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    source = (root / "application" / "pr_content_work_projector.py").read_text("utf-8")
    # Parsed rather than grepped: the module *discusses* M2's vocabulary at
    # length, and that prose is the point - a substring scan would either fail on
    # the docstrings or force them to stop explaining the boundary.
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {alias.name for alias in node.names}
    for forbidden in ("PrWorkQuotaAllocation", "PrWorkQuota", "quota_status", "eligibility_cap"):
        assert forbidden not in names, forbidden


async def test_40_no_score_vocabulary_entered_the_projector() -> None:
    """Points are M6's. Not here, not in M3.1."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "meobot"
    for name in (
        "application/pr_content_work_projector.py",
        "domain/pr/content_work.py",
    ):
        source = (root / name).read_text("utf-8")
        for forbidden in ("base_score", "awarded_score", "quality_multiplier", "score_total"):
            assert forbidden not in source, f"{name}: {forbidden}"
