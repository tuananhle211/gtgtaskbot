"""The PR content and task transition matrices, and nothing that touches a row.

Pure policy, in the shape :mod:`meobot.domain.scripts.workflow` already
established: a mapping of legal edges, a predicate, and an ``assert_`` that
raises :class:`~meobot.core.errors.WorkflowStateError` with the allowed set in
``details``. No session, no I/O, no authorization - those belong to the
application services, and keeping them out is what makes the matrix testable as
a table.

Who may drive an edge
---------------------

The content matrix is not flat. Three of its edges out of ``AI_REVIEW`` and
nine out of the human gates exist *only* as the recorded consequence of a
review, and letting a caller take them directly would defeat the point of
recording one: content could reach ``APPROVED`` with no approval event behind
it, or leave ``AI_REVIEW`` with no verdict.

One human gate is nevertheless reachable by hand - ``SCRIPTING ->
TEAM_LEAD_REVIEW``, Step 1F.2.10 - because *entering* the first gate is a
submission, not a decision. See :data:`DIRECT_TEAM_LEAD_SUBMISSION`.

So each edge carries a :class:`PrTransitionTrigger`:

* :attr:`PrTransitionTrigger.MANUAL` - a person moving work along.
  :meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.request_transition`
  accepts these and only these.
* :attr:`PrTransitionTrigger.AI_REVIEW` - the consequence of a gating AI
  verdict. Reachable only through
  :class:`~meobot.application.pr_ai_review_service.PrAiReviewService`.
* :attr:`PrTransitionTrigger.HUMAN_APPROVAL` - the consequence of a human
  decision. Reachable only through
  :class:`~meobot.application.pr_approval_service.PrApprovalService`.

Every one of them goes through the same
:meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.apply`,
under the same row lock, validated against the same matrix. The trigger says
*who may ask*, never *what is checked*.

What is deliberately not here
-----------------------------

``PUBLISHED -> MEASURED`` is in the matrix, but its precondition - at least one
metric snapshot exists for at least one publication of this content - is not,
because answering it requires a query. It lives in
:class:`~meobot.application.pr_workflow_service.PrContentWorkflowService`,
which has a session. A matrix that could sometimes say "legal" and sometimes
"illegal" for the same pair would not be a matrix.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.pr.errors import PrWorkflowTransitionError
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrTaskStatus,
    PrWorkflowStage,
)


class PrTransitionTrigger(StrEnum):
    """What kind of thing is entitled to drive a content-stage edge."""

    MANUAL = "MANUAL"
    AI_REVIEW = "AI_REVIEW"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    #: Step 1F.2.3b. A person taking back the decision they just made. Reachable
    #: only through
    #: :class:`~meobot.application.pr_undo_service.PrWorkflowUndoService` and,
    #: since Step 1F.2.3f.1, through
    #: :meth:`~meobot.application.pr_publication_service.PrPublicationService.reverse_publication`
    #: - which is what makes an undo a compensating business action rather than a
    #: stage jump: :meth:`request_transition` refuses this trigger exactly as it
    #: refuses ``HUMAN_APPROVAL``, so no route and no tool can drive a backward
    #: edge by naming a stage.
    UNDO = "UNDO"
    #: Step 1F.2.3f.1. A publication record being written, or taken back. The
    #: **only** thing entitled to drive ``READY_TO_PUBLISH -> PUBLISHED``.
    #:
    #: Before this step that edge was ``MANUAL``, which meant the panel offered a
    #: standalone *"Đánh dấu đã đăng"* button beside the publication form and the
    #: API accepted a bare ``POST /transition {"target_stage": "PUBLISHED"}``.
    #: Both marked content as published **with no record of where it went**, and
    #: the piece then sat at ``PUBLISHED`` with an empty publication history -
    #: which is exactly the state Step 1F.2.3f built the output reference to make
    #: impossible. Giving the edge its own trigger closes that: ``MANUAL`` no
    #: longer names it, so no route, no tool and no script can reach it without
    #: writing the publication that justifies it.
    PUBLICATION = "PUBLICATION"


#: Stages a piece of work may still be abandoned from. Everything before
#: publication, and nothing after it: once something is public, "cancelled" is
#: not a true description of it - the honest records are a ``REMOVED``
#: publication and, eventually, ``ARCHIVED``.
CANCELLABLE_STAGES: frozenset[PrWorkflowStage] = frozenset(
    {
        PrWorkflowStage.IDEA,
        PrWorkflowStage.BRIEFING,
        PrWorkflowStage.SCRIPTING,
        PrWorkflowStage.AI_REVIEW,
        PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrWorkflowStage.HEAD_REVIEW,
        PrWorkflowStage.APPROVED,
        PrWorkflowStage.PRODUCTION,
        PrWorkflowStage.INTERNAL_REVIEW,
        PrWorkflowStage.READY_TO_PUBLISH,
    }
)

#: **Stages the workflow no longer produces.** Step 1F.2.3f.5.
#:
#: ``MEASURED`` was ``PUBLISHED -> MEASURED -> ARCHIVED``: a post-publication
#: measurement milestone, gated on a metric snapshot existing. It is retired
#: because it made archiving reachable only *through* a measurement claim, while
#: measuring is a reporting activity that no longer needs a lifecycle stage of
#: its own - the snapshots and the analytics that read them are untouched.
#:
#: The member stays on :class:`~meobot.domain.pr.models.PrWorkflowStage` on
#: purpose, and is not deleted: ``pr_content_transition_events`` stores stages by
#: value, so a history row naming ``MEASURED`` must still deserialise, and a
#: stale client's request must parse far enough to be refused *by name* - see
#: :func:`assert_content_transition`. What retires it is being in no edge of the
#: matrix, no group, no lane and no filter.
RETIRED_STAGES: frozenset[PrWorkflowStage] = frozenset({PrWorkflowStage.MEASURED})

#: Stages from which nothing may move. ``ARCHIVED`` is the end of a life;
#: ``CANCELLED`` is the end of an abandoned one.
TERMINAL_STAGES: frozenset[PrWorkflowStage] = frozenset(
    {PrWorkflowStage.ARCHIVED, PrWorkflowStage.CANCELLED}
)

#: Stages a publication may be recorded from. A domain rule and not a service
#: detail: two services ask it - one to enforce it on the way in, one to decide
#: whether to offer the control - and a copy in either would be the second
#: authority this module exists to prevent.
#:
#: ``READY_TO_PUBLISH`` is the first, and the one that moves the item.
#: ``PUBLISHED`` is here so the second and third channels of a fan-out can still
#: be recorded - including one in October for a piece first posted in August,
#: which is the reuse case Step 1F.2.3f was built around. That case is unchanged
#: by Step 1F.2.3f.5 retiring ``MEASURED``: it was ``PUBLISHED`` that made it
#: work, and publication is now simply where such a piece stays.
#:
#: ``ARCHIVED`` is deliberately absent, and that is unchanged rather than newly
#: decided. Archiving **is** the end of a life - it is a
#: :data:`TERMINAL_STAGES` member and nothing transitions out of it - and a new
#: posting is new activity rather than a correction to the record. Something
#: being published from an archived piece means it should not have been archived.
PUBLISHABLE_STAGES: frozenset[PrWorkflowStage] = frozenset(
    {
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
    }
)

#: Step 1F.2.10. **The one manual edge into a human gate**, and what it means.
#:
#: ``SCRIPTING -> TEAM_LEAD_REVIEW`` is a finished script submitted straight to
#: the Team Lead, with the AI review deliberately not asked. It lands on exactly
#: the stage the AI path lands on when a gating verdict passes, so everything
#: downstream - reviewer permissions, decisions, notifications, board lanes,
#: history - is the same code with no second implementation.
#:
#: It is an **edge**, not a status and not a flag: the history row that records
#: it (``from_stage = SCRIPTING``, ``to_stage = TEAM_LEAD_REVIEW``, ``trigger =
#: MANUAL``) is the only way content ever makes that move, which is what lets a
#: client say *"bỏ qua AI review, gửi duyệt Trưởng nhóm"* from the row alone
#: and lets the approval gate accept the draft without a verdict. No AI review
#: row, run, score or "passed" mark is written by taking it - see
#: :meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.request_transition`.
DIRECT_TEAM_LEAD_SUBMISSION: tuple[PrWorkflowStage, PrWorkflowStage] = (
    PrWorkflowStage.SCRIPTING,
    PrWorkflowStage.TEAM_LEAD_REVIEW,
)

#: The two places a finished script may be sent from ``SCRIPTING``. Both are
#: *submissions* of the same draft and carry the same readiness requirements;
#: they differ only in whether a machine reads it first.
SCRIPT_SUBMISSION_TARGETS: frozenset[PrWorkflowStage] = frozenset(
    {PrWorkflowStage.AI_REVIEW, PrWorkflowStage.TEAM_LEAD_REVIEW}
)


def is_direct_team_lead_submission(source: PrWorkflowStage, target: PrWorkflowStage) -> bool:
    """True for the one edge that skips the AI review on purpose."""
    return (source, target) == DIRECT_TEAM_LEAD_SUBMISSION


#: Stages during which the draft may still be rewritten. Narrow on purpose:
#: the moment content enters ``AI_REVIEW`` somebody - or something - is
#: forming a judgement about a specific draft, and a draft that changes under
#: a reviewer makes their verdict meaningless.
EDITABLE_STAGES: frozenset[PrWorkflowStage] = frozenset(
    {PrWorkflowStage.IDEA, PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING}
)

#: Stages at which a human decision is expected, mapped to the approval stage
#: that decision must be filed under. One gate, one vocabulary term: filing a
#: head-review decision while the content sits at team-lead review is refused.
STAGE_APPROVAL_GATES: Mapping[PrWorkflowStage, PrApprovalStage] = MappingProxyType(
    {
        PrWorkflowStage.TEAM_LEAD_REVIEW: PrApprovalStage.TEAM_LEAD_REVIEW,
        PrWorkflowStage.HEAD_REVIEW: PrApprovalStage.HEAD_REVIEW,
        PrWorkflowStage.INTERNAL_REVIEW: PrApprovalStage.INTERNAL_REVIEW,
    }
)

#: The only AI review type that moves anything. The other three are recorded
#: and visible to a human reviewer, and change no state - a brand-tone check
#: that flags something is advice, not a gate.
GATING_AI_REVIEW_TYPE: PrAiReviewType = PrAiReviewType.FULL_REVIEW

#: What a gating AI verdict does to content sitting at ``AI_REVIEW``.
#: ``PASS`` and ``PASS_WITH_WARNINGS`` lead to the same place on purpose: both
#: hand the work to a human, and the difference between them is what that
#: human is asked to read, not where the work goes.
AI_REVIEW_OUTCOMES: Mapping[PrAiReviewResult, PrWorkflowStage] = MappingProxyType(
    {
        PrAiReviewResult.PASS: PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrAiReviewResult.PASS_WITH_WARNINGS: PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrAiReviewResult.REVISION_REQUIRED: PrWorkflowStage.SCRIPTING,
    }
)

#: What a human decision does, per gate. ``REJECTED`` cancels at every gate;
#: ``REVISION_REQUIRED`` returns the work to wherever it can be fixed, which is
#: ``SCRIPTING`` before approval and ``PRODUCTION`` after it - an internal
#: reviewer is looking at a cut, not a script.
APPROVAL_OUTCOMES: Mapping[PrApprovalStage, Mapping[PrApprovalDecision, PrWorkflowStage]] = (
    MappingProxyType(
        {
            PrApprovalStage.TEAM_LEAD_REVIEW: MappingProxyType(
                {
                    PrApprovalDecision.APPROVED: PrWorkflowStage.HEAD_REVIEW,
                    PrApprovalDecision.REVISION_REQUIRED: PrWorkflowStage.SCRIPTING,
                    PrApprovalDecision.REJECTED: PrWorkflowStage.CANCELLED,
                }
            ),
            PrApprovalStage.HEAD_REVIEW: MappingProxyType(
                {
                    PrApprovalDecision.APPROVED: PrWorkflowStage.APPROVED,
                    PrApprovalDecision.REVISION_REQUIRED: PrWorkflowStage.SCRIPTING,
                    PrApprovalDecision.REJECTED: PrWorkflowStage.CANCELLED,
                }
            ),
            PrApprovalStage.INTERNAL_REVIEW: MappingProxyType(
                {
                    PrApprovalDecision.APPROVED: PrWorkflowStage.READY_TO_PUBLISH,
                    PrApprovalDecision.REVISION_REQUIRED: PrWorkflowStage.PRODUCTION,
                    PrApprovalDecision.REJECTED: PrWorkflowStage.CANCELLED,
                }
            ),
        }
    )
)


def _content_transitions() -> Mapping[
    PrWorkflowStage, Mapping[PrWorkflowStage, frozenset[PrTransitionTrigger]]
]:
    """Build the matrix once, from the three sources that define it.

    Derived rather than hand-written so the same fact is not stated twice:
    :data:`AI_REVIEW_OUTCOMES` and :data:`APPROVAL_OUTCOMES` are what the
    review services act on, so an edge they can produce is by construction an
    edge the matrix allows. Only the manual edges and the cancellations are
    listed literally, because nothing else defines them.

    An edge carries a **set** of triggers, not one, because some edges are
    genuinely reachable two ways. ``TEAM_LEAD_REVIEW -> CANCELLED`` is the
    example: a reviewer rejecting the work gets there, *and* somebody may
    abandon it outright without filing a decision. Recording only one of those
    would either forbid rejection or forbid abandonment.
    """
    edges: dict[PrWorkflowStage, dict[PrWorkflowStage, set[PrTransitionTrigger]]] = {
        stage: {} for stage in PrWorkflowStage
    }

    def add(source: PrWorkflowStage, target: PrWorkflowStage, trigger: PrTransitionTrigger) -> None:
        edges[source].setdefault(target, set()).add(trigger)

    manual: tuple[tuple[PrWorkflowStage, PrWorkflowStage], ...] = (
        (PrWorkflowStage.IDEA, PrWorkflowStage.BRIEFING),
        (PrWorkflowStage.BRIEFING, PrWorkflowStage.SCRIPTING),
        (PrWorkflowStage.SCRIPTING, PrWorkflowStage.AI_REVIEW),
        # Step 1F.2.10. The **direct** submission: a finished script handed to
        # the Team Lead without asking the AI first. Deliberately the same
        # destination the AI path reaches - see ``DIRECT_TEAM_LEAD_SUBMISSION``
        # - and deliberately not a bypass of anything *after* it: the human
        # gates, production and publication are untouched, and nothing about
        # this edge records or implies an AI verdict.
        DIRECT_TEAM_LEAD_SUBMISSION,
        (PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION),
        (PrWorkflowStage.PRODUCTION, PrWorkflowStage.INTERNAL_REVIEW),
        # ``READY_TO_PUBLISH -> PUBLISHED`` is deliberately **not** here since
        # Step 1F.2.3f.1 - see ``PrTransitionTrigger.PUBLICATION`` below.
        #
        # **``PUBLISHED -> ARCHIVED`` is direct.** Step 1F.2.3f.5 retired
        # ``MEASURED``: the tail of the lifecycle used to be ``PUBLISHED ->
        # MEASURED -> ARCHIVED``, so archiving was only reachable *through* a
        # measurement claim, and a piece nobody had measured could never be put
        # away. Publication is now the terminal normal state and archiving is one
        # deliberate step from it.
        (PrWorkflowStage.PUBLISHED, PrWorkflowStage.ARCHIVED),
    )
    for source, target in manual:
        add(source, target, PrTransitionTrigger.MANUAL)

    for source in CANCELLABLE_STAGES:
        add(source, PrWorkflowStage.CANCELLED, PrTransitionTrigger.MANUAL)

    # Step 1F.2.3f.1. Publishing, and un-publishing, as one pair of edges nobody
    # can drive by naming a stage. The forward edge is written by
    # ``register_publication`` in the same transaction as the row that says
    # *where* it went; the backward one by ``reverse_publication``, and only
    # when it is safe - see that method for the five conditions.
    add(
        PrWorkflowStage.READY_TO_PUBLISH,
        PrWorkflowStage.PUBLISHED,
        PrTransitionTrigger.PUBLICATION,
    )
    add(PrWorkflowStage.PUBLISHED, PrWorkflowStage.READY_TO_PUBLISH, PrTransitionTrigger.UNDO)

    for target in AI_REVIEW_OUTCOMES.values():
        add(PrWorkflowStage.AI_REVIEW, target, PrTransitionTrigger.AI_REVIEW)

    for stage, gate in STAGE_APPROVAL_GATES.items():
        for target in APPROVAL_OUTCOMES[gate].values():
            add(stage, target, PrTransitionTrigger.HUMAN_APPROVAL)

    # Step 1F.2.3b. Every reversible human decision, backwards. Derived from
    # ``APPROVAL_OUTCOMES`` rather than listed, so an edge that exists forwards
    # is the only kind that can exist backwards - and ``REJECTED`` is excluded,
    # which is why "hoàn tác" can never become "uncancel": reopening abandoned
    # work is its own decision and does not belong behind an undo button.
    for stage, gate in STAGE_APPROVAL_GATES.items():
        for decision, target in APPROVAL_OUTCOMES[gate].items():
            if decision is not PrApprovalDecision.REJECTED:
                add(target, stage, PrTransitionTrigger.UNDO)

    return MappingProxyType(
        {
            stage: MappingProxyType(
                {target: frozenset(triggers) for target, triggers in targets.items()}
            )
            for stage, targets in edges.items()
        }
    )


#: Every legal content-stage edge, and which kinds of driver may take it.
#: Anything not listed here is rejected.
CONTENT_TRANSITIONS: Mapping[
    PrWorkflowStage, Mapping[PrWorkflowStage, frozenset[PrTransitionTrigger]]
] = _content_transitions()


def allowed_content_targets(
    current: PrWorkflowStage, *, trigger: PrTransitionTrigger | None = None
) -> frozenset[PrWorkflowStage]:
    """Stages reachable from ``current``, optionally for one kind of driver."""
    targets = CONTENT_TRANSITIONS[current]
    if trigger is None:
        return frozenset(targets)
    return frozenset(stage for stage, triggers in targets.items() if trigger in triggers)


def can_transition_content(
    current: PrWorkflowStage, target: PrWorkflowStage, *, trigger: PrTransitionTrigger | None = None
) -> bool:
    """True when this edge exists and ``trigger`` is entitled to drive it."""
    triggers = CONTENT_TRANSITIONS[current].get(target)
    if triggers is None:
        return False
    return trigger is None or trigger in triggers


def assert_content_transition(
    current: PrWorkflowStage, target: PrWorkflowStage, *, trigger: PrTransitionTrigger | None = None
) -> None:
    """Raise :class:`PrWorkflowTransitionError` when the edge is not available.

    ``details`` carries the current stage, the requested one, the trigger and
    the set that *would* have been accepted, so a client can offer the real
    next steps instead of restating the refusal.
    """
    if can_transition_content(current, target, trigger=trigger):
        return
    allowed = sorted(stage.value for stage in allowed_content_targets(current, trigger=trigger))
    details: dict[str, object] = {
        "current": current.value,
        "target": target.value,
        "trigger": trigger.value if trigger else None,
        "allowed": allowed,
    }
    if target in RETIRED_STAGES:
        # Step 1F.2.3f.5. A **specific** refusal rather than the generic one,
        # because the two mean different things to whoever is asking: "that edge
        # does not exist from where you are" is answerable by moving somewhere
        # else, and "that stage no longer exists" is not.
        #
        # A stale browser is the likely caller - it rendered the *Đã đo hiệu
        # quả* action before this shipped - and it is told so, rather than being
        # silently sent to ``PUBLISHED``. Guessing at what somebody meant is how
        # a retired stage turns into a wrong lifecycle event nobody asked for.
        details["reason"] = f"{target.value.lower()}_stage_retired"
    raise PrWorkflowTransitionError(
        f"Cannot move PR content from {current.value!r} to {target.value!r}",
        details=details,
    )


def approval_target(stage: PrApprovalStage, decision: PrApprovalDecision) -> PrWorkflowStage:
    """Where a human decision at ``stage`` sends the content."""
    return APPROVAL_OUTCOMES[stage][decision]


def is_reversible_decision(decision: PrApprovalDecision) -> bool:
    """Whether an undo may take back a decision of this kind.

    ``APPROVED`` and ``REVISION_REQUIRED`` yes; ``REJECTED`` no. A rejection
    cancels the content, and un-cancelling is a decision to restart abandoned
    work - which deserves its own action, its own authorization and its own
    audit line rather than arriving as a side effect of the word "hoàn tác".
    """
    return decision is not PrApprovalDecision.REJECTED


def ai_review_target(result: PrAiReviewResult) -> PrWorkflowStage:
    """Where a gating AI verdict sends content standing at ``AI_REVIEW``."""
    return AI_REVIEW_OUTCOMES[result]


def is_gating_review(review_type: PrAiReviewType) -> bool:
    """True when this AI review type is allowed to move the content."""
    return review_type is GATING_AI_REVIEW_TYPE


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

#: Legal task transitions. Anything not listed here is rejected, including the
#: pairs a caller most often expects to work: ``TODO -> DONE`` skips the record
#: that anybody did the thing, and ``DONE -> IN_PROGRESS`` would make "done"
#: mean "done for now". Reopening is a new task.
TASK_TRANSITIONS: Mapping[PrTaskStatus, frozenset[PrTaskStatus]] = MappingProxyType(
    {
        PrTaskStatus.TODO: frozenset({PrTaskStatus.IN_PROGRESS, PrTaskStatus.CANCELLED}),
        PrTaskStatus.IN_PROGRESS: frozenset(
            {
                PrTaskStatus.BLOCKED,
                PrTaskStatus.IN_REVIEW,
                PrTaskStatus.DONE,
                PrTaskStatus.CANCELLED,
            }
        ),
        PrTaskStatus.BLOCKED: frozenset({PrTaskStatus.IN_PROGRESS, PrTaskStatus.CANCELLED}),
        PrTaskStatus.IN_REVIEW: frozenset(
            {PrTaskStatus.REVISION_REQUIRED, PrTaskStatus.DONE, PrTaskStatus.CANCELLED}
        ),
        PrTaskStatus.REVISION_REQUIRED: frozenset(
            {PrTaskStatus.IN_PROGRESS, PrTaskStatus.CANCELLED}
        ),
        PrTaskStatus.DONE: frozenset(),
        PrTaskStatus.CANCELLED: frozenset(),
    }
)

#: Task statuses nothing moves out of.
TERMINAL_TASK_STATUSES: frozenset[PrTaskStatus] = frozenset(
    {PrTaskStatus.DONE, PrTaskStatus.CANCELLED}
)


def can_transition_task(current: PrTaskStatus, target: PrTaskStatus) -> bool:
    """True when ``current -> target`` is a legal task transition."""
    return target in TASK_TRANSITIONS[current]


def assert_task_transition(current: PrTaskStatus, target: PrTaskStatus) -> None:
    """Raise :class:`PrWorkflowTransitionError` when the transition is illegal."""
    if can_transition_task(current, target):
        return
    allowed = sorted(status.value for status in TASK_TRANSITIONS[current])
    raise PrWorkflowTransitionError(
        f"Cannot move PR task from {current.value!r} to {target.value!r}",
        details={"current": current.value, "target": target.value, "allowed": allowed},
    )


__all__: list[str] = [
    "AI_REVIEW_OUTCOMES",
    "APPROVAL_OUTCOMES",
    "CANCELLABLE_STAGES",
    "CONTENT_TRANSITIONS",
    "DIRECT_TEAM_LEAD_SUBMISSION",
    "EDITABLE_STAGES",
    "GATING_AI_REVIEW_TYPE",
    "PUBLISHABLE_STAGES",
    "RETIRED_STAGES",
    "SCRIPT_SUBMISSION_TARGETS",
    "STAGE_APPROVAL_GATES",
    "TASK_TRANSITIONS",
    "TERMINAL_STAGES",
    "TERMINAL_TASK_STATUSES",
    "PrTransitionTrigger",
    "ai_review_target",
    "allowed_content_targets",
    "approval_target",
    "assert_content_transition",
    "assert_task_transition",
    "can_transition_content",
    "can_transition_task",
    "is_direct_team_lead_submission",
    "is_gating_review",
    "is_reversible_decision",
]
