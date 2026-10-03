"""Human review decisions: the only thing that can approve PR content.

``pr_approval_events`` is append-only and this service only ever inserts into
it. A reviewer who changes their mind adds a second event; both stay true, each
against the version it was made at. Nothing in the PR module updates a row in
that table, and nothing outside this service writes one at all.

What is checked before a decision is recorded
---------------------------------------------

* **The gate matches the stage.** Content at ``TEAM_LEAD_REVIEW`` takes a
  ``TEAM_LEAD_REVIEW`` decision and nothing else. Filing a head-review approval
  while the work is still at the team lead would otherwise skip a gate by
  mislabelling a form field.
* **The version is current.** ``version_reviewed`` must equal the newest draft,
  for the same reason it must on an AI review: an approval is an answer about
  a specific text.
* **The draft reached the gate honestly.** Either a gating AI review exists
  for it, or - since Step 1F.2.10 - it was submitted straight to the Team Lead
  by the one manual ``SCRIPTING -> TEAM_LEAD_REVIEW`` edge, which pins the
  draft it carried. Both are per version, so this is a second check at the
  point of the write, in the shape
  :meth:`~meobot.application.script_service.ScriptService._require_current_version`
  established. It is what makes the rule enforceable rather than merely
  emergent from the transition matrix - see :meth:`PrApprovalService._require_ai_gate`.
* **A Head approval has a Team Lead approval behind it.** Approving at
  ``HEAD_REVIEW`` needs a successful ``TEAM_LEAD_REVIEW`` decision *on the same
  draft* already on file - see
  :meth:`PrApprovalService._require_prior_team_lead_approval`.

What is *no longer* checked: **whether the two approvals came from two people.**
Step 1F.2.2 removed that requirement. One person who independently holds
``PR_TEAM_LEAD_REVIEW`` and ``PR_HEAD_REVIEW`` may sign both gates for one
draft, because a small team's only two entitled reviewers are sometimes one
person and the rule was blocking work rather than protecting it. Everything that
made it two gates survives: both stages are walked, each needs its own
capability, and **two separate append-only events are written** - one per stage,
each with its own actor, version and timestamp. Neither is ever read as evidence
for the other gate.

What is deliberately *not* checked: whether the AI passed. **A Team Lead may
reject work the AI approved, and approve work it wanted revised.** The AI
verdict is evidence placed in front of a person, and a service that refused a
human decision on the strength of a model's opinion would invert the
responsibility this whole module is built to keep.

The third gate
--------------

``INTERNAL_REVIEW`` has always been in the vocabulary - ``PrApprovalStage``,
``APPROVAL_CAPABILITIES`` and ``APPROVAL_OUTCOMES`` all carried it before Step
1F.2.3 - and it goes through this method like the other two: its own capability
(``PR_INTERNAL_REVIEW``, held independently of the script gates), its own
append-only event, and its outcomes from the same table - ``APPROVED`` to
``READY_TO_PUBLISH``, ``REVISION_REQUIRED`` back to ``PRODUCTION``.

The one thing it needs that a script gate does not is a way to say **which cut**
was judged, because ``version_reviewed`` identifies a script and a piece that is
sent back is re-cut against the same script. So an internal-review event also
carries ``production_submission_id``, read from the item's latest submission -
see :meth:`PrApprovalService._reviewed_submission_id`. Without it, two decisions
on one version would be two rows saying opposite things about, apparently, the
same thing.

And note what one person holding all three grants means here: they may decide at
all three gates for one piece, and it is still three separate events with three
stages, three timestamps and three decisions. Step 1F.2.2 removed the rule that
said two of them had to be two people; nothing collapses them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_ai_review_service import PrAiReviewService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_notifications import PrNotificationService
from meobot.application.pr_production_service import PrProductionService
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrApprovalEvent, PrContentItem, PrTask
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrAiReviewRequiredError,
    PrApprovalStageMismatchError,
    PrNotFoundError,
    PrReviewVersionMismatchError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import PrApprovalDecision, PrApprovalStage, PrWorkflowStage
from meobot.domain.pr.policy import APPROVAL_CAPABILITIES
from meobot.domain.pr.workflow import (
    DIRECT_TEAM_LEAD_SUBMISSION,
    STAGE_APPROVAL_GATES,
    PrTransitionTrigger,
    approval_target,
)

logger = get_logger(__name__)


def is_effective_approval() -> ColumnElement[bool]:
    """The predicate for "this approval has not been withdrawn".

    Step 1F.2.3b. Written as a correlated ``NOT EXISTS`` over
    ``pr_content_transition_events`` rather than as a column on the approval,
    because ``pr_approval_events`` is append-only and marking a row "cancelled"
    in place is exactly the edit that table exists to forbid. An undo appends a
    reversal and links it to the transition that recorded the decision; this asks
    whether that happened.

    One function, used wherever an approval is read *as authority* - which today
    is the Head gate's prerequisite. Read as *history* the same rows are returned
    unfiltered, because a withdrawn approval genuinely happened and the review
    tab says so.
    """
    return ~exists().where(
        PrContentTransitionEvent.approval_event_id == PrApprovalEvent.id,
        PrContentTransitionEvent.reversed_by_event_id.is_not(None),
    )


#: Gates at which the current draft must already carry a gating AI verdict.
#: Only the first one: ``HEAD_REVIEW`` and ``INTERNAL_REVIEW`` are reached by a
#: human decision, and the AI gate was passed on the way in.
AI_GATED_APPROVAL_STAGES: frozenset[PrApprovalStage] = frozenset({PrApprovalStage.TEAM_LEAD_REVIEW})


@dataclass(frozen=True, slots=True)
class RecordApprovalCommand:
    """One human decision at one gate."""

    content_id: uuid.UUID
    reviewer_user_id: uuid.UUID
    approval_stage: PrApprovalStage
    decision: PrApprovalDecision
    version_reviewed: int
    comment: str | None = None
    task_id: uuid.UUID | None = None
    #: When the person decided, which is not when the row is written. Defaults
    #: to now for the ordinary case of a decision taken in the moment.
    decided_at: datetime | None = None
    #: Step 1F.2.8. The bulk action this decision was part of, or ``None`` for
    #: the ordinary one-item decision - which is every existing caller, so the
    #: single-approval path is byte-for-byte what it was.
    #:
    #: **Audit correlation only.** It is written into the
    #: ``pr.approval.recorded`` audit row's ``after`` payload and nowhere else:
    #: it changes no check, no stage, no event column and no notification. Each
    #: item still gets its own ``pr_approval_events`` row and its own audit
    #: entry - the batch id is what lets somebody ask *which decisions were taken
    #: together*, not a replacement for any of them. See
    #: :class:`~meobot.application.pr_bulk_approval_service.PrBulkApprovalService`
    #: for why it needed no schema change: ``audit_logs.after_data`` is JSON.
    batch_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """The decision that was recorded and where it sent the content."""

    event: PrApprovalEvent
    new_stage: PrWorkflowStage


class PrApprovalService:
    """Records human review decisions and applies what follows from them.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        content: Reader for the current version.
        workflow: The only writer of ``workflow_stage``.
        ai_reviews: Reader used to confirm the draft has been machine-checked.
        capabilities: Resolves the per-gate review capability, which is the
            only thing that can tell a Team Lead from a Head.
        notifications: Step 1F.2.3b. Told when a Head approval lands, so whoever
            is responsible for the content learns that it needs a producer.
            Optional; absent, the approval still works and nobody is told.
        production: Step 1F.2.3. Asked which cut an ``INTERNAL_REVIEW`` decision
            is about, so the event names the file rather than leaving it to be
            inferred from timestamps. Optional so a caller that only records
            script-gate decisions can build this service alone; without it an
            internal-review event simply carries no submission id, which is the
            pre-1F.2.3 shape. ``build_pr_services`` always supplies it.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        content: PrContentService,
        workflow: PrContentWorkflowService,
        ai_reviews: PrAiReviewService,
        capabilities: PrCapabilityService,
        production: PrProductionService | None = None,
        notifications: PrNotificationService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._content = content
        self._workflow = workflow
        self._ai_reviews = ai_reviews
        self._capabilities = capabilities
        self._production = production
        self._notifications = notifications

    async def record_decision(
        self, *, actor: Actor, request_id: uuid.UUID, command: RecordApprovalCommand
    ) -> ApprovalOutcome:
        """Append one approval event and move the content accordingly.

        Raises:
            PrPermissionDeniedError: The actor may not decide at this gate.
            PrNotFoundError: No such content, reviewer or task.
            PrApprovalStageMismatchError: The gate does not match the stage.
            PrReviewVersionMismatchError: The decision judged a stale draft.
            PrAiReviewRequiredError: The draft has no gating AI verdict.
        """
        # Asked twice, and the two questions are different. This one is
        # unscoped - *could this person ever decide at this gate* - so somebody
        # who holds no grant at all is refused before the row is even read, and
        # a bogus content id cannot be used to tell an ungranted caller whether
        # an item exists.
        await self._capabilities.require(actor, APPROVAL_CAPABILITIES[command.approval_stage])

        # The lock is what serialises this against a permanent deletion: either
        # the delete gets the row first and this call finds no content, or this
        # one does and the delete waits behind it. There is no third state, and
        # no "deleted" flag to check - Step 1F.2.3a removed the soft delete.
        content = await self._workflow.lock(command.content_id)

        # And this one is the real check: Step 1F.2.7's scoped rule, against the
        # item's classification and every channel it is going to. It is the
        # single authorization for an approval anywhere in the module - see
        # :meth:`~meobot.application.pr_capability_service.PrCapabilityService.require_approval`
        # - and it is asked here, inside the lock, so the scope is judged
        # against the item as it is when the decision is written rather than as
        # it was when the screen was drawn.
        await self._capabilities.require_approval(actor, content, command.approval_stage)
        expected_gate = STAGE_APPROVAL_GATES.get(content.workflow_stage)
        if expected_gate is None:
            raise PrApprovalStageMismatchError(
                "PR content is not standing at a human review gate",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "gates": sorted(stage.value for stage in STAGE_APPROVAL_GATES),
                },
            )
        if expected_gate is not command.approval_stage:
            raise PrApprovalStageMismatchError(
                "The decision was filed at a different gate from the one the content is at",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "expected_stage": expected_gate.value,
                    "submitted_stage": command.approval_stage.value,
                },
            )

        if await self._session.get(User, command.reviewer_user_id) is None:
            raise PrNotFoundError(
                "No user with that id to record as reviewer",
                details={"reviewer_user_id": str(command.reviewer_user_id)},
            )
        if command.task_id is not None and await self._session.get(PrTask, command.task_id) is None:
            raise PrNotFoundError(
                "No PR task with that id", details={"task_id": str(command.task_id)}
            )

        current = await self._content.require_current_version(content.id)
        if command.version_reviewed != current.version_no:
            raise PrReviewVersionMismatchError(
                "The decision judged a draft that is no longer current",
                details={
                    "content_id": str(content.id),
                    "version_reviewed": command.version_reviewed,
                    "current_version": current.version_no,
                },
            )

        await self._require_ai_gate(
            content.id, approval_stage=command.approval_stage, version_no=current.version_no
        )

        if (
            command.approval_stage is PrApprovalStage.HEAD_REVIEW
            and command.decision is PrApprovalDecision.APPROVED
        ):
            await self._require_prior_team_lead_approval(
                content_id=content.id, version_reviewed=command.version_reviewed
            )

        event = PrApprovalEvent(
            content_id=content.id,
            task_id=command.task_id,
            approval_stage=command.approval_stage,
            reviewer_user_id=command.reviewer_user_id,
            decision=command.decision,
            version_reviewed=command.version_reviewed,
            comment=command.comment,
            decided_at=command.decided_at or utcnow(),
            production_submission_id=await self._reviewed_submission_id(
                content.id, approval_stage=command.approval_stage
            ),
        )
        self._session.add(event)
        await self._session.flush()

        target = approval_target(command.approval_stage, command.decision)
        await self._workflow.apply(
            actor=actor,
            request_id=request_id,
            content=content,
            target=target,
            trigger=PrTransitionTrigger.HUMAN_APPROVAL,
            reason=f"approval:{command.approval_stage.value}:{command.decision.value}",
            # Step 1F.2.3b. The transition row points at the decision that caused
            # it, which is what later lets an undo take the decision back without
            # editing this append-only table.
            approval_event_id=event.id,
        )

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_APPROVAL_RECORDED,
            entity_type="pr_approval_event",
            entity_id=event.id,
            after={
                "content_id": str(content.id),
                "content_code": content.code,
                "approval_stage": command.approval_stage.value,
                "decision": command.decision.value,
                "version_reviewed": command.version_reviewed,
                "reviewer_user_id": str(command.reviewer_user_id),
                "new_stage": target.value,
                "production_submission_id": (
                    str(event.production_submission_id) if event.production_submission_id else None
                ),
                # Step 1F.2.8. ``None`` for a one-item decision, which keeps the
                # payload shape stable for every existing reader rather than
                # adding a key that appears only sometimes.
                "batch_id": str(command.batch_id) if command.batch_id else None,
            },
        )
        if self._notifications is not None:
            # Queued in this transaction, so every message and the decision that
            # caused it commit together. Which gate and which decision decides
            # who hears about it - see
            # :mod:`meobot.application.pr_notifications` for the recipient rules
            # and for what is deliberately *not* announced.
            await self._announce(content=content, command=command, event=event)

        logger.info(
            "pr_approval_recorded",
            extra={
                "pr_content_id": str(content.id),
                "approval_stage": command.approval_stage.value,
                "decision": command.decision.value,
                "new_stage": target.value,
            },
        )
        return ApprovalOutcome(event=event, new_stage=target)

    async def _announce(
        self,
        *,
        content: PrContentItem,
        command: RecordApprovalCommand,
        event: PrApprovalEvent,
    ) -> None:
        """Tell whoever this decision is news for.

        One place mapping (gate, decision) to a notification, rather than a
        chain of ``if`` blocks at the call site: four of the nine combinations
        produce a message and the other five deliberately do not, and that is
        easier to check as a table than as conditions strung together.

        =====================  ==================  ==============================
        Gate                   Decision            Who hears
        =====================  ==================  ==============================
        ``TEAM_LEAD_REVIEW``   ``APPROVED``        responsible person (status)
        ``HEAD_REVIEW``        ``APPROVED``        responsible person (a task)
        ``INTERNAL_REVIEW``    ``APPROVED``        producer + responsible person
        ``INTERNAL_REVIEW``    ``REVISION_REQ``    producer
        =====================  ==================  ==============================

        Nothing for a **rejection** at any gate, and nothing for a revision at
        the two script gates. Both are omissions with a reason rather than gaps:
        a rejection ends the piece, and the person who wrote it is looking at the
        panel that just told them; a script revision returns the draft to a stage
        the author already occupies, and ``MY_ACTIONS`` puts it back in front of
        them. Neither is a person being told to go somewhere they are not.

        ``self._notifications`` is checked by the caller, so this is only reached
        when a notifier is configured.
        """
        if self._notifications is None:  # pragma: no cover - guarded by caller
            return
        gate = command.approval_stage
        decision = command.decision

        if decision is PrApprovalDecision.APPROVED:
            if gate is PrApprovalStage.HEAD_REVIEW:
                # The approval ends at ``APPROVED`` and the piece now needs
                # somebody to produce it - a fact nobody would otherwise learn
                # until they happened to open the board.
                await self._notifications.on_head_approved(content=content, approval=event)
            elif gate is PrApprovalStage.TEAM_LEAD_REVIEW:
                await self._notifications.on_team_lead_approved(content=content, approval=event)
            elif gate is PrApprovalStage.INTERNAL_REVIEW:
                await self._notifications.on_internal_review_approved(
                    content=content, approval=event
                )
        elif (
            decision is PrApprovalDecision.REVISION_REQUIRED
            and gate is PrApprovalStage.INTERNAL_REVIEW
        ):
            await self._notifications.on_production_revision_required(
                content=content, approval=event, note=command.comment
            )

    # --- Which cut an internal review judged ------------------------------
    async def _reviewed_submission_id(
        self, content_id: uuid.UUID, *, approval_stage: PrApprovalStage
    ) -> uuid.UUID | None:
        """The production submission an ``INTERNAL_REVIEW`` decision is about.

        The **latest** one, which is the only defensible reading: the item
        reached ``INTERNAL_REVIEW`` because a submission put it there, in the
        same transaction, and a reviewer looking at the screen is looking at what
        the screen shows as current. It is read here rather than accepted from the
        request for the same reason ``approval_stage`` is derived from the stage:
        a caller who could name the submission could file a verdict against a cut
        nobody is looking at.

        ``None`` for the two script gates - they judge ``version_reviewed``, and
        a Team Lead approval pointing at a video would be a claim about evidence
        that was not in front of them. ``None`` too when this service was built
        without the production reader, which is the pre-1F.2.3 shape.
        """
        if approval_stage is not PrApprovalStage.INTERNAL_REVIEW or self._production is None:
            return None
        submission = await self._production.latest_submission(content_id)
        return submission.id if submission is not None else None

    # --- The AI gate ------------------------------------------------------
    async def _require_ai_gate(
        self, content_id: uuid.UUID, *, approval_stage: PrApprovalStage, version_no: int
    ) -> None:
        """Refuse a decision at an AI-gated stage on a draft that arrived nowhere.

        Extracted from :meth:`record_decision` in Step 1E.2.1 so the read model
        behind ``/available-actions`` can ask the same question rather than
        restating it. Which stages are gated is :data:`AI_GATED_APPROVAL_STAGES`,
        and the draft standing there must have reached it honestly - by one of
        exactly two routes, each of which leaves its own record **for this exact
        draft**:

        * a ``FULL_REVIEW`` verdict on the version - the AI path; or
        * a ``SCRIPTING -> TEAM_LEAD_REVIEW`` transition pinned to the version -
          the direct submission of Step 1F.2.10, which skipped the AI review on
          purpose and says so by being the only move of that shape.

        The second is not a relaxation of the first. What the gate has always
        protected is that a reviewer decides on *the draft that was submitted*,
        not one rewritten since: both records are pinned to a version, a newer
        draft carries neither, and the direct route is not satisfied by an
        older version's verdict any more than the AI route is.

        Note what is *not* checked: the verdict's ``result``. A
        ``REVISION_REQUIRED`` verdict sends the work back to ``SCRIPTING``
        rather than to a gate, so content standing at ``TEAM_LEAD_REVIEW`` with
        a verdict on file has by construction a passing one - and a reviewer who
        wants to overrule a machine is exactly who this gate exists for.
        """
        if approval_stage not in AI_GATED_APPROVAL_STAGES:
            return
        gating = await self._ai_reviews.latest_gating_review(content_id, version_no=version_no)
        if gating is not None:
            return
        if await self._submitted_directly(content_id, version_no=version_no):
            return
        raise PrAiReviewRequiredError(
            "This draft has no FULL_REVIEW on file and was not submitted directly "
            "to the Team Lead, so it cannot be decided yet",
            details={
                "content_id": str(content_id),
                "version_no": version_no,
                "approval_stage": approval_stage.value,
            },
        )

    async def _submitted_directly(self, content_id: uuid.UUID, *, version_no: int) -> bool:
        """Whether this draft was handed straight to the Team Lead. Step 1F.2.10.

        Read off the transition history, not off a flag: the direct submission
        is the one manual ``SCRIPTING -> TEAM_LEAD_REVIEW`` move, and the row
        that records it pins the draft that was current when it happened. An
        undo of a revision request takes the same edge with ``trigger = UNDO``
        and is deliberately not this - it restores a state that had already
        satisfied the gate, so the earlier record still answers for it.
        """
        source, target = DIRECT_TEAM_LEAD_SUBMISSION
        found = await self._session.execute(
            select(PrContentTransitionEvent.id)
            .join(
                PrContentVersion,
                PrContentVersion.id == PrContentTransitionEvent.content_version_id,
            )
            .where(
                PrContentTransitionEvent.content_id == content_id,
                PrContentTransitionEvent.from_stage == source,
                PrContentTransitionEvent.to_stage == target,
                PrContentTransitionEvent.trigger == PrTransitionTrigger.MANUAL,
                PrContentVersion.version_no == version_no,
            )
            .limit(1)
        )
        return found.scalars().first() is not None

    async def ai_gate_satisfied(
        self, content_id: uuid.UUID, *, approval_stage: PrApprovalStage, version_no: int
    ) -> bool:
        """The same question as :meth:`_require_ai_gate`, as a boolean.

        For a client deciding which buttons to draw. Reached by catching the
        refusal, not by a second query, so there is one implementation of "has
        this draft been machine-checked" and a screen cannot disagree with the
        write about it.
        """
        try:
            await self._require_ai_gate(
                content_id, approval_stage=approval_stage, version_no=version_no
            )
        except PrAiReviewRequiredError:
            return False
        return True

    async def head_approval_permitted(
        self, content_id: uuid.UUID, *, version_reviewed: int
    ) -> bool:
        """Whether the Head gate's own prerequisite is met for this draft.

        The remaining Head prerequisite as a boolean, over
        :meth:`_require_prior_team_lead_approval`. Step 1E.2.1 added it because
        the Head-review "Duyệt" was being offered and then refused; Step 1F.2.2
        narrowed what it asks, because the refusal it mostly reported - the same
        person twice - is no longer a refusal.

        Takes no ``reviewer_user_id``, and that absence is the change. **Who**
        the actor is no longer bears on this question: it is settled by
        :data:`~meobot.domain.pr.policy.APPROVAL_CAPABILITIES` and
        :meth:`PrCapabilityService.require`, which the caller asks separately and
        which the write re-asks. What is left here is a fact about the draft.
        """
        try:
            await self._require_prior_team_lead_approval(
                content_id=content_id, version_reviewed=version_reviewed
            )
        except PrWorkflowTransitionError:
            return False
        return True

    # --- The Head gate's prerequisite -------------------------------------
    async def _require_prior_team_lead_approval(
        self, *, content_id: uuid.UUID, version_reviewed: int
    ) -> None:
        """Refuse a Head approval of a draft no Team Lead has approved.

        This is what is left of the check after Step 1F.2.2. It used to also
        refuse a Head approval from whoever gave the Team Lead one - a
        four-eyes rule - and that requirement is **gone**: a person who
        independently holds ``PR_TEAM_LEAD_REVIEW`` *and* ``PR_HEAD_REVIEW`` may
        now sign both gates for one draft. Small teams have one person holding
        both grants, and a rule that made the work unapprovable by the only
        people entitled to approve it was stopping the workflow rather than
        strengthening it.

        What is deliberately unchanged, so the two gates remain two gates:

        * **both stages are still walked.** ``TEAM_LEAD_REVIEW`` ->
          ``HEAD_REVIEW`` -> ``APPROVED``, one decision each, and no edge
          skips one. Nothing here collapses them and nothing auto-approves the
          second from the first;
        * **two separate events are written.** Each ``pr_approval_events`` row
          carries its own stage, actor, version, timestamp and decision. The
          Team Lead row is the evidence for the Team Lead gate and the Head row
          for the Head gate; neither is reused as evidence for the other, which
          is exactly what this method reads to establish;
        * **the capability is still per stage.** Holding one grant implies
          nothing about the other, and there is no role inheritance and no
          bypass. The check for that is
          :meth:`PrCapabilityService.require`, called at the top of
          :meth:`record_decision` before anything here runs.

        Scoped to **this content and this version**: a rewrite invalidates the
        Team Lead approval along with the draft it was about, so the new draft
        needs its own.
        """
        team_lead = await self.successful_team_lead_approval(
            content_id, version_reviewed=version_reviewed
        )
        if team_lead is not None:
            return
        # Structurally unreachable - the only edge into HEAD_REVIEW is a
        # team-lead APPROVED - so this is the second check at the point of
        # the write, in the shape the script service established.
        raise PrWorkflowTransitionError(
            "No successful TEAM_LEAD_REVIEW approval exists for this draft",
            details={
                "content_id": str(content_id),
                "version_reviewed": version_reviewed,
                "reason": "missing_team_lead_approval",
            },
        )

    async def successful_team_lead_approval(
        self, content_id: uuid.UUID, *, version_reviewed: int
    ) -> PrApprovalEvent | None:
        """The Team Lead's **effective** ``APPROVED`` for one draft, if any.

        Newest first: a version that went to the team lead, came back and went
        again has more than one, and it is the most recent that authorised the
        draft's current position.

        Step 1F.2.3b added the word *effective*, and it is the load-bearing one.
        An approval that has been undone is still a row - the table is
        append-only and nothing rewrites history - but it must stop counting as
        authority, or a Head could approve on the strength of a team-lead
        sign-off somebody withdrew. "Withdrawn" is not a flag on the approval;
        it is the transition that recorded it having been reversed, which is what
        :func:`_reversed_approvals` reads.
        """
        result = await self._session.execute(
            select(PrApprovalEvent)
            .where(
                PrApprovalEvent.content_id == content_id,
                PrApprovalEvent.version_reviewed == version_reviewed,
                PrApprovalEvent.approval_stage == PrApprovalStage.TEAM_LEAD_REVIEW,
                PrApprovalEvent.decision == PrApprovalDecision.APPROVED,
                is_effective_approval(),
            )
            .order_by(PrApprovalEvent.decided_at.desc(), PrApprovalEvent.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    # --- Reading ----------------------------------------------------------
    async def history(
        self, content_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[PrApprovalEvent]:
        """Every human decision about one item, oldest first.

        Ascending because this is a narrative - who saw it, said what, at which
        version - and reading it backwards loses the sense of it.
        """
        result = await self._session.execute(
            select(PrApprovalEvent)
            .where(PrApprovalEvent.content_id == content_id)
            .order_by(PrApprovalEvent.decided_at.asc(), PrApprovalEvent.created_at.asc())
            .limit(limit)
        )
        return result.scalars().all()


__all__: list[str] = ["ApprovalOutcome", "PrApprovalService", "RecordApprovalCommand"]
