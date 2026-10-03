"""The PR transition matrices, as tables rather than as behaviour.

These run offline against no database at all: everything here is a pure
function of two enum values. Keeping them separate from
``tests/unit/test_pr_application_services.py`` is deliberate - when a matrix
test fails it is a policy decision that changed, and when a service test fails
it is the wiring. Mixing them makes both harder to read.
"""

from __future__ import annotations

import pytest

from meobot.domain.pr.errors import PrWorkflowTransitionError
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrTaskStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.workflow import (
    CANCELLABLE_STAGES,
    CONTENT_TRANSITIONS,
    DIRECT_TEAM_LEAD_SUBMISSION,
    EDITABLE_STAGES,
    GATING_AI_REVIEW_TYPE,
    RETIRED_STAGES,
    SCRIPT_SUBMISSION_TARGETS,
    STAGE_APPROVAL_GATES,
    TASK_TRANSITIONS,
    TERMINAL_STAGES,
    TERMINAL_TASK_STATUSES,
    PrTransitionTrigger,
    ai_review_target,
    allowed_content_targets,
    approval_target,
    assert_content_transition,
    assert_task_transition,
    can_transition_content,
    can_transition_task,
    is_direct_team_lead_submission,
    is_gating_review,
)

#: The happy path, as the approved specification writes it. Each pair is one
#: edge; who is entitled to drive it is asserted separately below.
CANONICAL_PATH: tuple[tuple[PrWorkflowStage, PrWorkflowStage], ...] = (
    (PrWorkflowStage.IDEA, PrWorkflowStage.BRIEFING),
    (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING),
    (PrWorkflowStage.SCRIPTING, PrWorkflowStage.AI_REVIEW),
    (PrWorkflowStage.AI_REVIEW, PrWorkflowStage.TEAM_LEAD_REVIEW),
    (PrWorkflowStage.TEAM_LEAD_REVIEW, PrWorkflowStage.HEAD_REVIEW),
    (PrWorkflowStage.HEAD_REVIEW, PrWorkflowStage.APPROVED),
    (PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION),
    (PrWorkflowStage.PRODUCTION, PrWorkflowStage.INTERNAL_REVIEW),
    (PrWorkflowStage.INTERNAL_REVIEW, PrWorkflowStage.READY_TO_PUBLISH),
    (PrWorkflowStage.READY_TO_PUBLISH, PrWorkflowStage.PUBLISHED),
    # Step 1F.2.3f.5. ``MEASURED`` is retired: publication is the terminal
    # normal state, and archiving is one deliberate step from it rather than
    # something only reachable through a measurement claim.
    (PrWorkflowStage.PUBLISHED, PrWorkflowStage.ARCHIVED),
)

#: Exactly the edges a person may drive without recording a review.
#:
#: ``READY_TO_PUBLISH -> PUBLISHED`` was here until Step 1F.2.3f.1 and is
#: deliberately gone: it now needs ``PrTransitionTrigger.PUBLICATION``, so the
#: only way to reach ``PUBLISHED`` is to record the publication that says where
#: the content actually went. See ``EXPECTED_PUBLICATION_EDGES`` below.
EXPECTED_MANUAL_EDGES: frozenset[tuple[PrWorkflowStage, PrWorkflowStage]] = frozenset(
    {
        (PrWorkflowStage.IDEA, PrWorkflowStage.BRIEFING),
        (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING),
        (PrWorkflowStage.SCRIPTING, PrWorkflowStage.AI_REVIEW),
        # Step 1F.2.10. The direct submission: a finished script handed to the
        # Team Lead with the AI review skipped. It enters the first human gate;
        # it decides nothing there, and every later gate is unchanged.
        (PrWorkflowStage.SCRIPTING, PrWorkflowStage.TEAM_LEAD_REVIEW),
        (PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION),
        (PrWorkflowStage.PRODUCTION, PrWorkflowStage.INTERNAL_REVIEW),
        (PrWorkflowStage.PUBLISHED, PrWorkflowStage.ARCHIVED),
    }
    | {(stage, PrWorkflowStage.CANCELLED) for stage in CANCELLABLE_STAGES}
)

#: Step 1F.2.3f.1. Exactly the edges a *publication record* drives, and there is
#: one. Enumerated for the same reason the manual set is: this is the trigger
#: that must never grow, because anything reachable through it is reachable
#: without a review and without a person naming a stage.
EXPECTED_PUBLICATION_EDGES: frozenset[tuple[PrWorkflowStage, PrWorkflowStage]] = frozenset(
    {(PrWorkflowStage.READY_TO_PUBLISH, PrWorkflowStage.PUBLISHED)}
)


# --- The canonical path exists, end to end ---------------------------------


@pytest.mark.parametrize(("source", "target"), CANONICAL_PATH)
def test_every_step_of_the_canonical_workflow_is_a_legal_edge(
    source: PrWorkflowStage, target: PrWorkflowStage
) -> None:
    assert can_transition_content(source, target)


def test_the_canonical_path_visits_every_stage_except_cancelled() -> None:
    """Nothing in the sequence is unreachable, and nothing is skipped.

    ``CANCELLED`` is the exception by design: it is a terminal alternative
    reachable from ten stages, not a position in the sequence.
    """
    visited = {CANONICAL_PATH[0][0]} | {target for _, target in CANONICAL_PATH}
    # ``CANCELLED`` is off the canonical path by construction, and ``MEASURED``
    # is off it because Step 1F.2.3f.5 retired the stage - it is in no edge of
    # the matrix, which is what retiring it means.
    assert visited == set(PrWorkflowStage) - {
        PrWorkflowStage.CANCELLED,
        *RETIRED_STAGES,
    }


# --- Who may drive what ----------------------------------------------------


def test_the_manual_edges_are_exactly_the_ones_specified() -> None:
    """The full set a person may drive, enumerated rather than sampled.

    This is the test that fails if somebody widens
    ``PrContentWorkflowService.request_transition`` by adding an edge to the
    matrix with the wrong trigger - which is the one mistake that would let
    content reach ``APPROVED`` with no approval event behind it.
    """
    manual = {
        (source, target)
        for source, targets in CONTENT_TRANSITIONS.items()
        for target, triggers in targets.items()
        if PrTransitionTrigger.MANUAL in triggers
    }
    assert manual == EXPECTED_MANUAL_EDGES


def test_publishing_is_not_a_manual_edge_any_more() -> None:
    """Step 1F.2.3f.1. ``READY_TO_PUBLISH -> PUBLISHED`` needs a publication.

    The bypass this closes was real and reachable: the edge was ``MANUAL``, so
    ``POST /contents/{id}/transition {"target_stage": "PUBLISHED"}`` moved a piece
    to published **with no record of where it went** - and the panel drew a
    standalone "Đánh dấu đã đăng" button beside the publication form that did
    exactly that. The result was content sitting at ``PUBLISHED`` with an empty
    publication history, which is the state Step 1F.2.3f's output reference exists
    to make impossible.

    Two halves, and both matter: the edge is gone from ``MANUAL``, and it exists
    under a trigger only ``register_publication`` passes.
    """
    publication_driven = {
        (source, target)
        for source, targets in CONTENT_TRANSITIONS.items()
        for target, triggers in targets.items()
        if PrTransitionTrigger.PUBLICATION in triggers
    }
    assert publication_driven == EXPECTED_PUBLICATION_EDGES
    assert not can_transition_content(
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
        trigger=PrTransitionTrigger.MANUAL,
    )
    assert can_transition_content(
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
        trigger=PrTransitionTrigger.PUBLICATION,
    )


def test_un_publishing_is_an_undo_edge_and_nothing_else() -> None:
    """Step 1F.2.3f.1. ``PUBLISHED -> READY_TO_PUBLISH`` exists, under ``UNDO``.

    Reachable only through
    ``PrPublicationService.reverse_publication``, which decides whether it is
    safe. ``request_transition`` refuses the ``UNDO`` trigger exactly as it
    refuses ``HUMAN_APPROVAL``, so nobody un-publishes anything by naming a
    stage - and the generic undo classifies only ``HUMAN_APPROVAL`` edges, so it
    has never offered to and still does not.
    """
    assert can_transition_content(
        PrWorkflowStage.PUBLISHED,
        PrWorkflowStage.READY_TO_PUBLISH,
        trigger=PrTransitionTrigger.UNDO,
    )
    for refused in (PrTransitionTrigger.MANUAL, PrTransitionTrigger.PUBLICATION):
        assert not can_transition_content(
            PrWorkflowStage.PUBLISHED, PrWorkflowStage.READY_TO_PUBLISH, trigger=refused
        ), refused


def test_no_manual_edge_reaches_a_review_outcome() -> None:
    """A person cannot hand-wave content through a gate.

    Every stage that is the *outcome* of a human decision - ``HEAD_REVIEW``,
    ``APPROVED``, ``READY_TO_PUBLISH`` - is unreachable by
    :attr:`PrTransitionTrigger.MANUAL`. ``TEAM_LEAD_REVIEW`` is not among them
    since Step 1F.2.10: *entering* the first gate is a submission, not a
    decision, and the next test pins down that the only hand-driven way in is
    from ``SCRIPTING``.
    """
    guarded = {
        PrWorkflowStage.HEAD_REVIEW,
        PrWorkflowStage.APPROVED,
        PrWorkflowStage.READY_TO_PUBLISH,
    }
    for source in PrWorkflowStage:
        manual_targets = allowed_content_targets(source, trigger=PrTransitionTrigger.MANUAL)
        assert not (manual_targets & guarded), (source, manual_targets & guarded)


def test_the_direct_team_lead_submission_is_the_only_manual_way_into_a_gate() -> None:
    """Step 1F.2.10. One edge, from one stage, and it skips nothing but the AI.

    ``SCRIPTING -> TEAM_LEAD_REVIEW`` is manual; no other source reaches the
    gate by hand, and ``AI_REVIEW -> TEAM_LEAD_REVIEW`` in particular stays an
    AI-only edge - so a draft already under machine review cannot be pulled out
    of it by naming the gate.
    """
    sources = {
        source
        for source in PrWorkflowStage
        if PrWorkflowStage.TEAM_LEAD_REVIEW
        in allowed_content_targets(source, trigger=PrTransitionTrigger.MANUAL)
    }
    assert sources == {PrWorkflowStage.SCRIPTING}
    assert DIRECT_TEAM_LEAD_SUBMISSION == (
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.TEAM_LEAD_REVIEW,
    )
    assert is_direct_team_lead_submission(*DIRECT_TEAM_LEAD_SUBMISSION)
    assert not is_direct_team_lead_submission(
        PrWorkflowStage.AI_REVIEW, PrWorkflowStage.TEAM_LEAD_REVIEW
    )
    assert not can_transition_content(
        PrWorkflowStage.AI_REVIEW,
        PrWorkflowStage.TEAM_LEAD_REVIEW,
        trigger=PrTransitionTrigger.MANUAL,
    )
    assert (
        allowed_content_targets(PrWorkflowStage.SCRIPTING, trigger=PrTransitionTrigger.MANUAL)
        - {PrWorkflowStage.CANCELLED}
        == SCRIPT_SUBMISSION_TARGETS
    )


def test_ai_review_may_only_drive_the_two_edges_out_of_ai_review() -> None:
    ai_edges = {
        (source, target)
        for source, targets in CONTENT_TRANSITIONS.items()
        for target, triggers in targets.items()
        if PrTransitionTrigger.AI_REVIEW in triggers
    }
    assert ai_edges == {
        (PrWorkflowStage.AI_REVIEW, PrWorkflowStage.TEAM_LEAD_REVIEW),
        (PrWorkflowStage.AI_REVIEW, PrWorkflowStage.SCRIPTING),
    }


def test_ai_review_can_never_reach_approved_by_any_trigger() -> None:
    """The structural guarantee, not merely the service's good behaviour."""
    for trigger in (None, *PrTransitionTrigger):
        assert not can_transition_content(
            PrWorkflowStage.AI_REVIEW, PrWorkflowStage.APPROVED, trigger=trigger
        )


def test_requesting_a_review_edge_manually_is_refused() -> None:
    """The pair is legal; the trigger is not, and the error says so."""
    assert can_transition_content(PrWorkflowStage.AI_REVIEW, PrWorkflowStage.TEAM_LEAD_REVIEW)
    with pytest.raises(PrWorkflowTransitionError) as raised:
        assert_content_transition(
            PrWorkflowStage.AI_REVIEW,
            PrWorkflowStage.TEAM_LEAD_REVIEW,
            trigger=PrTransitionTrigger.MANUAL,
        )
    assert raised.value.details["trigger"] == PrTransitionTrigger.MANUAL.value
    assert PrWorkflowStage.TEAM_LEAD_REVIEW.value not in raised.value.details["allowed"]


# --- Cancellation ----------------------------------------------------------


@pytest.mark.parametrize("stage", sorted(CANCELLABLE_STAGES, key=lambda s: s.value))
def test_work_may_be_abandoned_before_it_is_published(stage: PrWorkflowStage) -> None:
    assert can_transition_content(
        stage, PrWorkflowStage.CANCELLED, trigger=PrTransitionTrigger.MANUAL
    )


@pytest.mark.parametrize(
    "stage",
    [
        PrWorkflowStage.PUBLISHED,
        PrWorkflowStage.MEASURED,
        PrWorkflowStage.ARCHIVED,
        PrWorkflowStage.CANCELLED,
    ],
)
def test_published_and_later_content_cannot_be_cancelled(stage: PrWorkflowStage) -> None:
    """Once something is public, "cancelled" is not a true description of it."""
    assert not can_transition_content(stage, PrWorkflowStage.CANCELLED)
    with pytest.raises(PrWorkflowTransitionError):
        assert_content_transition(stage, PrWorkflowStage.CANCELLED)


def test_the_cancellable_set_is_exactly_everything_before_publication() -> None:
    assert (
        set(PrWorkflowStage)
        - {
            PrWorkflowStage.PUBLISHED,
            PrWorkflowStage.MEASURED,
            PrWorkflowStage.ARCHIVED,
            PrWorkflowStage.CANCELLED,
        }
        == CANCELLABLE_STAGES
    )


@pytest.mark.parametrize("stage", sorted(TERMINAL_STAGES, key=lambda s: s.value))
def test_nothing_moves_out_of_a_terminal_stage(stage: PrWorkflowStage) -> None:
    assert CONTENT_TRANSITIONS[stage] == {}


@pytest.mark.parametrize(
    "stage",
    [
        PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrWorkflowStage.HEAD_REVIEW,
        PrWorkflowStage.INTERNAL_REVIEW,
    ],
)
def test_a_gate_reaches_cancelled_both_by_rejection_and_by_abandonment(
    stage: PrWorkflowStage,
) -> None:
    """One edge, two entitled drivers - which is why triggers are a set.

    A reviewer rejecting the work gets to ``CANCELLED``, and so does somebody
    abandoning it without filing a decision. Recording only one of those would
    either forbid rejection or forbid abandonment.
    """
    triggers = CONTENT_TRANSITIONS[stage][PrWorkflowStage.CANCELLED]
    assert triggers == {PrTransitionTrigger.MANUAL, PrTransitionTrigger.HUMAN_APPROVAL}


# --- Review outcome tables -------------------------------------------------


def test_both_passing_ai_results_hand_the_work_to_a_human() -> None:
    """Same destination, different meaning - and the difference is preserved.

    ``PASS`` and ``PASS_WITH_WARNINGS`` both reach ``TEAM_LEAD_REVIEW``. What
    keeps them distinguishable is the stored ``result``, which
    ``PrQueryService.get_content_review_context`` exposes as an enum rather
    than a boolean.
    """
    assert ai_review_target(PrAiReviewResult.PASS) is PrWorkflowStage.TEAM_LEAD_REVIEW
    assert ai_review_target(PrAiReviewResult.PASS_WITH_WARNINGS) is PrWorkflowStage.TEAM_LEAD_REVIEW
    assert PrAiReviewResult.PASS is not PrAiReviewResult.PASS_WITH_WARNINGS


def test_a_revision_request_from_the_ai_returns_the_work_to_scripting() -> None:
    assert ai_review_target(PrAiReviewResult.REVISION_REQUIRED) is PrWorkflowStage.SCRIPTING


def test_full_review_is_the_only_gating_ai_review_type() -> None:
    assert GATING_AI_REVIEW_TYPE is PrAiReviewType.FULL_REVIEW
    gating = [review_type for review_type in PrAiReviewType if is_gating_review(review_type)]
    assert gating == [PrAiReviewType.FULL_REVIEW]


@pytest.mark.parametrize(
    ("stage", "decision", "expected"),
    [
        (
            PrApprovalStage.TEAM_LEAD_REVIEW,
            PrApprovalDecision.APPROVED,
            PrWorkflowStage.HEAD_REVIEW,
        ),
        (
            PrApprovalStage.TEAM_LEAD_REVIEW,
            PrApprovalDecision.REVISION_REQUIRED,
            PrWorkflowStage.SCRIPTING,
        ),
        (
            PrApprovalStage.TEAM_LEAD_REVIEW,
            PrApprovalDecision.REJECTED,
            PrWorkflowStage.CANCELLED,
        ),
        (PrApprovalStage.HEAD_REVIEW, PrApprovalDecision.APPROVED, PrWorkflowStage.APPROVED),
        (
            PrApprovalStage.HEAD_REVIEW,
            PrApprovalDecision.REVISION_REQUIRED,
            PrWorkflowStage.SCRIPTING,
        ),
        (PrApprovalStage.HEAD_REVIEW, PrApprovalDecision.REJECTED, PrWorkflowStage.CANCELLED),
        (
            PrApprovalStage.INTERNAL_REVIEW,
            PrApprovalDecision.APPROVED,
            PrWorkflowStage.READY_TO_PUBLISH,
        ),
        (
            PrApprovalStage.INTERNAL_REVIEW,
            PrApprovalDecision.REVISION_REQUIRED,
            PrWorkflowStage.PRODUCTION,
        ),
        (
            PrApprovalStage.INTERNAL_REVIEW,
            PrApprovalDecision.REJECTED,
            PrWorkflowStage.CANCELLED,
        ),
    ],
)
def test_the_human_decision_table_is_the_one_specified(
    stage: PrApprovalStage, decision: PrApprovalDecision, expected: PrWorkflowStage
) -> None:
    assert approval_target(stage, decision) is expected


def test_every_approval_gate_maps_to_exactly_one_workflow_stage() -> None:
    """No gate is unreachable, and no stage takes two different gates."""
    assert set(STAGE_APPROVAL_GATES.values()) == set(PrApprovalStage)
    assert len(set(STAGE_APPROVAL_GATES.values())) == len(STAGE_APPROVAL_GATES)


# --- Editability -----------------------------------------------------------


def test_content_is_editable_only_before_anybody_starts_judging_it() -> None:
    assert {
        PrWorkflowStage.IDEA,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
    } == EDITABLE_STAGES
    assert PrWorkflowStage.AI_REVIEW not in EDITABLE_STAGES
    assert PrWorkflowStage.TEAM_LEAD_REVIEW not in EDITABLE_STAGES


# --- Tasks -----------------------------------------------------------------

ALLOWED_TASK_EDGES: frozenset[tuple[PrTaskStatus, PrTaskStatus]] = frozenset(
    {
        (PrTaskStatus.TODO, PrTaskStatus.IN_PROGRESS),
        (PrTaskStatus.TODO, PrTaskStatus.CANCELLED),
        (PrTaskStatus.IN_PROGRESS, PrTaskStatus.BLOCKED),
        (PrTaskStatus.IN_PROGRESS, PrTaskStatus.IN_REVIEW),
        (PrTaskStatus.IN_PROGRESS, PrTaskStatus.DONE),
        (PrTaskStatus.IN_PROGRESS, PrTaskStatus.CANCELLED),
        (PrTaskStatus.BLOCKED, PrTaskStatus.IN_PROGRESS),
        (PrTaskStatus.BLOCKED, PrTaskStatus.CANCELLED),
        (PrTaskStatus.IN_REVIEW, PrTaskStatus.REVISION_REQUIRED),
        (PrTaskStatus.IN_REVIEW, PrTaskStatus.DONE),
        (PrTaskStatus.IN_REVIEW, PrTaskStatus.CANCELLED),
        (PrTaskStatus.REVISION_REQUIRED, PrTaskStatus.IN_PROGRESS),
        (PrTaskStatus.REVISION_REQUIRED, PrTaskStatus.CANCELLED),
    }
)


@pytest.mark.parametrize(
    ("source", "target"),
    sorted(ALLOWED_TASK_EDGES, key=lambda pair: (pair[0].value, pair[1].value)),
)
def test_every_specified_task_transition_is_allowed(
    source: PrTaskStatus, target: PrTaskStatus
) -> None:
    assert can_transition_task(source, target)


def test_the_task_matrix_contains_nothing_else() -> None:
    """Exhaustive: every one of the 49 ordered pairs is decided.

    The interesting refusals fall out of this rather than being listed - and
    they include the two a caller most often expects to work, ``TODO -> DONE``
    and ``DONE -> IN_PROGRESS``.
    """
    actual = {
        (source, target) for source, targets in TASK_TRANSITIONS.items() for target in targets
    }
    assert actual == ALLOWED_TASK_EDGES

    refused = [
        (source, target)
        for source in PrTaskStatus
        for target in PrTaskStatus
        if (source, target) not in ALLOWED_TASK_EDGES
    ]
    assert len(refused) == len(PrTaskStatus) ** 2 - len(ALLOWED_TASK_EDGES)
    for source, target in refused:
        assert not can_transition_task(source, target), (source, target)
        with pytest.raises(PrWorkflowTransitionError):
            assert_task_transition(source, target)


@pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATUSES, key=lambda s: s.value))
def test_done_and_cancelled_tasks_are_terminal(status: PrTaskStatus) -> None:
    assert TASK_TRANSITIONS[status] == frozenset()


def test_a_task_never_moves_to_its_own_status() -> None:
    """No self-edges: "set it to what it already is" is not a transition."""
    for status in PrTaskStatus:
        assert not can_transition_task(status, status)


# ===========================================================================
# THE STRUCTURAL GUARANTEE
# ===========================================================================


def test_only_the_workflow_service_writes_the_stage_column() -> None:
    """No PR service assigns ``workflow_stage`` except the one that owns it.

    A grep rather than a behavioural test on purpose: the failure mode is a
    *new* service being written that sets the column directly, and no
    behavioural test of today's services would notice that.
    """
    import re
    from pathlib import Path

    # ``.workflow_stage =`` but not ``.workflow_stage ==``: the first is a
    # write, the second is a filter in a query and is entirely fine.
    assignment = re.compile(r"\.workflow_stage\s*=(?!=)")
    root = Path(__file__).resolve().parents[2] / "src" / "meobot" / "application"
    offenders = []
    for path in sorted(root.glob("pr_*.py")):
        if path.name == "pr_workflow_service.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if assignment.search(stripped):
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], offenders

    # And the sanity check that the pattern finds anything at all - a regex
    # that matched nothing would make this test pass forever.
    owner = (root / "pr_workflow_service.py").read_text(encoding="utf-8")
    assert assignment.search(owner) is not None


def test_no_pr_service_imports_an_llm_or_telegram_client() -> None:
    """Step 1C is services, not integrations."""
    import re
    from pathlib import Path

    forbidden = re.compile(
        r"\b(?:openai|anthropic|litellm|langchain|aiogram|telegram|apscheduler|celery"
        r"|gspread|openpyxl|httpx|requests)\b"
    )
    root = Path(__file__).resolve().parents[2] / "src" / "meobot"
    for path in [
        *sorted((root / "application").glob("pr_*.py")),
        *sorted((root / "domain" / "pr").glob("*.py")),
    ]:
        imports = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        )
        assert not forbidden.search(imports), path.name


def test_the_retired_stage_is_in_no_edge_of_the_matrix() -> None:
    """Step 1F.2.3f.5. **What retiring a stage means, asserted.**

    ``MEASURED`` is still a member of the enum - a transition-history row can
    name it, and a stale request must parse far enough to be refused by name -
    so "retired" cannot mean "deleted". It means the matrix has no edge into it
    and no edge out of it, under any trigger. That is the property that makes it
    unreachable, and it is the one worth testing.
    """
    for stage in RETIRED_STAGES:
        assert CONTENT_TRANSITIONS.get(stage, {}) == {}, stage
        into = {source for source, targets in CONTENT_TRANSITIONS.items() if stage in targets}
        assert into == set(), (stage, into)


def test_a_stale_request_for_the_retired_stage_is_refused_by_name() -> None:
    """Part Z. A specific reason, and never a silent remap to ``PUBLISHED``.

    A browser that rendered *Đã đo hiệu quả* before this shipped will still ask
    for it. It is told the stage is retired rather than being sent somewhere it
    did not choose - guessing at the intention is how a retired stage turns into
    a lifecycle event nobody asked for.
    """
    with pytest.raises(PrWorkflowTransitionError) as caught:
        assert_content_transition(
            PrWorkflowStage.PUBLISHED,
            PrWorkflowStage.MEASURED,
            trigger=PrTransitionTrigger.MANUAL,
        )
    assert caught.value.details["reason"] == "measured_stage_retired"
    assert caught.value.details["target"] == "MEASURED"
    # And the refusal still names where the content *can* go.
    assert "ARCHIVED" in caught.value.details["allowed"]
