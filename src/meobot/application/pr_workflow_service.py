"""The one place ``pr_content_items.workflow_stage`` is written.

Every stage change in the PR module goes through :meth:`PrContentWorkflowService.apply`
- the manual ones a person drives, the ones an AI verdict causes, and the ones
a human decision causes. No other service assigns ``workflow_stage``, and the
tests assert that by grepping the service package, because a single assignment
somewhere else is all it takes for content to reach ``APPROVED`` with no
approval behind it.

Three things are true of every transition:

* **It is validated against the matrix in** :mod:`meobot.domain.pr.workflow`,
  which is a table rather than a chain of ``if``s, so "can this move there" has
  one answer and a test can enumerate it.
* **It is validated under the content row's lock**, taken by the command that
  is about to write. Reading the stage, deciding, and writing the new stage
  happen inside one lock; without it two concurrent approvals both read
  ``TEAM_LEAD_REVIEW``, both find their decision legal, and the second
  overwrites the first's outcome.
* **It records the move twice, on purpose.** ``pr.content.stage_changed`` goes
  to the audit trail as it always has, *and* Step 1F.2.3b appends a row to
  ``pr_content_transition_events``. The first is the log a person reads; the
  second is the structured history undo reads, with the stages in columns, the
  approval or submission that caused the move as foreign keys, and a place to
  record that the move was later taken back. Two writers of the same fact would
  be a problem; one writer of two shapes, in one transaction, is what lets undo
  ask a business question in SQL instead of parsing a JSON payload.

The trigger
-----------

:meth:`request_transition` is the public, person-facing entry point and accepts
only :attr:`~meobot.domain.pr.workflow.PrTransitionTrigger.MANUAL` edges. Since
Step 1F.2.10 one of those is ``SCRIPTING -> TEAM_LEAD_REVIEW`` - a finished
script submitted straight to the Team Lead - and :meth:`submit_to_team_lead_review`
names it. It writes no AI review of any kind; what makes the move honest is the
history row itself, which no other path produces. The
review-driven edges are reachable only through :meth:`apply`, which the AI
review and approval services call *after* writing their record, in the same
transaction. That is the whole enforcement of "an approval stage change implies
an approval": the row and the transition are one unit of work.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_ai_review_run_service import PrAiReviewRunService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_work_projector import request_content_work_projection
from meobot.application.pr_policy_readiness_service import PrPolicyReadinessService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrNotFoundError, PrWorkflowTransitionError
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import (
    PrTransitionTrigger,
    allowed_content_targets,
    assert_content_transition,
    is_direct_team_lead_submission,
)

logger = get_logger(__name__)


def capability_for_target(target: PrWorkflowStage) -> PrCapability:
    """Which capability a person needs to drive a manual edge to ``target``.

    Cancelling takes the approval right rather than the submit right, because
    it ends work - see :mod:`meobot.domain.pr.policy`. Everything else takes
    ``PR_CONTENT_TRANSITION``.

    A module-level function so :meth:`PrContentWorkflowService.request_transition`
    and the read-only
    :class:`~meobot.application.pr_action_service.PrAvailableActionService` ask
    the same question of the same table. A screen offering a move the write
    path would refuse - or hiding one it would allow - is the failure this
    prevents.
    """
    return (
        PrCapability.PR_CONTENT_CANCEL
        if target is PrWorkflowStage.CANCELLED
        else PrCapability.PR_CONTENT_TRANSITION
    )


class PrContentWorkflowService:
    """Owns content-stage transitions.

    Args:
        session: Unit of work. The caller owns the transaction boundary, as
            everywhere else in this application layer.
        audit: Event writer sharing that session.
        capabilities: Resolves PR capabilities against roles and grants.
        policy: Step 1F.1 policy readiness. Optional for the same reason
            ``runs`` is; ``build_pr_services`` always supplies it. Absent, the
            gate simply does not apply, which is the pre-1F.1 behaviour.
        runs: Durable AI-review executions. Optional so a caller that only
            needs transitions can build this service alone; ``build_pr_services``
            always supplies it, and both clients go through that. When it is
            absent, entering ``AI_REVIEW`` moves the stage and queues nothing -
            which is the pre-Step-1F behaviour and is safe, not silent breakage.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        runs: PrAiReviewRunService | None = None,
        policy: PrPolicyReadinessService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._runs = runs
        self._policy = policy

    # --- The public, manual entry point -----------------------------------
    async def request_transition(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        target: PrWorkflowStage,
        note: str | None = None,
    ) -> PrContentItem:
        """Move content along an edge a person is entitled to drive.

        Refuses every review-driven edge. Asking for ``TEAM_LEAD_REVIEW ->
        APPROVED`` here fails even though that pair is legal *as the outcome of
        a head-review decision*, because the decision is what makes it legal
        and this call has not recorded one.

        Raises:
            PrPermissionDeniedError: The actor lacks the capability.
            PrNotFoundError: No such content.
            PrWorkflowTransitionError: The edge is not manually drivable from
                the current stage, or its precondition is unmet.
        """
        await self._capabilities.require(actor, capability_for_target(target))

        content = await self._lock(content_id)
        assert_content_transition(
            content.workflow_stage, target, trigger=PrTransitionTrigger.MANUAL
        )
        if target is PrWorkflowStage.PRODUCTION:
            # Step 1F.2.3b. Production without a producer is a stage that says
            # work has started and a row that says nobody is doing it. Checked
            # here, on the generic route, and not only behind the
            # ``START_PRODUCTION`` button: the Telegram tool and any script reach
            # this method too, and a rule that only the panel enforced would be a
            # rule the panel enforced.
            _require_producer(content)
        if target is PrWorkflowStage.INTERNAL_REVIEW:
            # And the same shape for the gate after it: an internal reviewer is
            # asked to watch a cut, so there has to be one. ``submit_production``
            # writes the submission and calls :meth:`apply` directly, so the
            # honest path is unaffected; what this closes is the generic route
            # being used to put content in front of a reviewer with nothing to
            # review.
            await self._require_submission(content)
        if target is PrWorkflowStage.AI_REVIEW:
            # Step 1F.1. Fail closed *before* the stage moves: a supported
            # platform whose distribution mode nobody set, or whose policy pack
            # nobody activated, must not enter a review that would silently be
            # ungrounded. Asked of the one service the read path also asks.
            await self._require_policy_ready(content.id)
        if is_direct_team_lead_submission(content.workflow_stage, target):
            # Step 1F.2.10. The direct submission skips the AI review and
            # **nothing else**: the draft has to exist and be as complete as the
            # AI path demands - a planned channel, Organic or Paid decided on
            # every grounded target - before a person is asked to read it. The
            # one thing not asked is whether a policy pack is active, because
            # no review will run. Asked of the same readiness service, so the
            # offer in ``available-actions`` and this refusal cannot disagree.
            await self._require_human_review_ready(content.id)

        return await self.apply(
            actor=actor,
            request_id=request_id,
            content=content,
            target=target,
            trigger=PrTransitionTrigger.MANUAL,
            reason=note,
        )

    async def cancel(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        note: str | None = None,
    ) -> PrContentItem:
        """Abandon a piece of work before it is published."""
        return await self.request_transition(
            actor=actor,
            request_id=request_id,
            content_id=content_id,
            target=PrWorkflowStage.CANCELLED,
            note=note,
        )

    async def submit_to_team_lead_review(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        note: str | None = None,
    ) -> PrContentItem:
        """Hand a finished script straight to the Team Lead. Step 1F.2.10.

        ``SCRIPTING -> TEAM_LEAD_REVIEW`` by the same rules as ``SCRIPTING ->
        AI_REVIEW`` - same capability, same lock, same completeness checks, same
        audit and history rows - with the AI review deliberately not asked. No
        AI review row, no run, no score and no "passed" mark is written, and the
        history says so by the edge itself: this is the only way content ever
        moves from ``SCRIPTING`` to ``TEAM_LEAD_REVIEW`` by hand.

        A named entry point beside :meth:`cancel`, for the same reason: a
        caller that means *this business step* should be able to say so rather
        than name a stage. It grants nothing downstream - the Team Lead's own
        decision, the Head's, production and publication are unchanged.

        Raises:
            PrPermissionDeniedError: The actor may not submit content.
            PrNotFoundError: No such content.
            PrWorkflowTransitionError: The content is not at ``SCRIPTING``, or
                the draft is not complete enough to submit.
        """
        return await self.request_transition(
            actor=actor,
            request_id=request_id,
            content_id=content_id,
            target=PrWorkflowStage.TEAM_LEAD_REVIEW,
            note=note,
        )

    # --- The shared path every transition takes ---------------------------
    async def apply(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content: PrContentItem,
        target: PrWorkflowStage,
        trigger: PrTransitionTrigger,
        reason: str | None = None,
        approval_event_id: uuid.UUID | None = None,
        production_submission_id: uuid.UUID | None = None,
        reverses: PrContentTransitionEvent | None = None,
    ) -> PrContentItem:
        """Validate and write one stage change on an already-locked row.

        ``content`` must have been loaded by :meth:`lock` (or by a caller that
        locked it) in this transaction. The AI review and approval services
        call this after writing their record, so the record and the stage move
        commit together.

        A transition to the same stage is refused rather than treated as a
        no-op: the matrix has no self-edges, and silently accepting one would
        let a caller believe something moved when nothing did.

        Args:
            approval_event_id: The decision that caused this move, for a
                ``HUMAN_APPROVAL`` edge. Recorded on the transition row, which is
                what later lets an undo take the approval back without editing
                the append-only approval table.
            production_submission_id: The cut that caused it, for
                ``PRODUCTION -> INTERNAL_REVIEW``.
            reverses: The transition this one takes back, for an ``UNDO`` edge.
                Linked in both directions - see
                :class:`~meobot.db.models.pr_transition.PrContentTransitionEvent`.

        Raises:
            PrWorkflowTransitionError: The edge is not available.
        """
        assert_content_transition(content.workflow_stage, target, trigger=trigger)

        before = content.workflow_stage
        # Read under the lock, before anything moves: the draft that is current
        # at this instant is the one the transition row pins and - for a direct
        # submission - the one the audit line names as having skipped the AI.
        version = await self._current_version(content.id)
        content.workflow_stage = target
        if target is PrWorkflowStage.ARCHIVED and content.archived_at is None:
            # A stored fact, not a computed one - but archiving *is* the
            # decision this column records, so this is the moment it is made.
            content.archived_at = utcnow()
        if target is PrWorkflowStage.PRODUCTION and content.production_started_at is None:
            # Step 1F.2.3, and the same shape as ``archived_at`` above: entering
            # production for the first time is the fact this column records, and
            # this is the one place it can be observed. Stamped **once** - the
            # ``is None`` guard is what makes a second entry, after an internal
            # reviewer sends the cut back, leave the original date alone. That is
            # the whole reason the member delete rule can rely on it.
            content.production_started_at = utcnow()
        await self._session.flush()

        after: dict[str, object] = {
            "workflow_stage": target.value,
            "trigger": trigger.value,
            "content_code": content.code,
            "reason": reason,
        }
        if trigger is PrTransitionTrigger.MANUAL and is_direct_team_lead_submission(before, target):
            # Step 1F.2.10. Said in the audit line, not only implied by the
            # edge: whoever reads the trail later must not have to know that
            # ``SCRIPTING -> TEAM_LEAD_REVIEW`` is the one move that skips the
            # AI - and must never mistake the absence of an AI review event for
            # a review that was lost.
            after["action"] = "submit_team_lead_review"
            after["ai_review_bypassed"] = True
            after["content_version_no"] = version.version_no if version is not None else None
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_STAGE_CHANGED,
            entity_type="pr_content_item",
            entity_id=content.id,
            before={"workflow_stage": before.value},
            after=after,
        )
        logger.info(
            "pr_content_stage_changed",
            extra={
                "pr_content_id": str(content.id),
                "stage_before": before.value,
                "stage_after": target.value,
                "trigger": trigger.value,
            },
        )

        await self._record_transition(
            content=content,
            actor=actor,
            before=before,
            target=target,
            trigger=trigger,
            reason=reason,
            content_version_id=version.id if version is not None else None,
            approval_event_id=approval_event_id,
            production_submission_id=production_submission_id,
            reverses=reverses,
        )

        if target is PrWorkflowStage.AI_REVIEW:
            await self._queue_ai_review(content)
        return content

    async def _record_transition(
        self,
        *,
        content: PrContentItem,
        actor: Actor,
        before: PrWorkflowStage,
        target: PrWorkflowStage,
        trigger: PrTransitionTrigger,
        reason: str | None,
        content_version_id: uuid.UUID | None,
        approval_event_id: uuid.UUID | None,
        production_submission_id: uuid.UUID | None,
        reverses: PrContentTransitionEvent | None,
    ) -> PrContentTransitionEvent:
        """Append the structured half of the history, and close a reversal pair.

        Step 1F.2.3b. The draft id is read by :meth:`apply` under the lock, at
        the moment the stage changes, for the same reason the AI-review pin is:
        it has to be the version that was current *then*, and an id a caller
        read before somebody revised would pin the wrong draft.

        Setting ``reversed_by_event_id`` on the original is the one update this
        table ever takes, and it happens here so that a reversal pair cannot be
        half-written: both rows land in the same flush as the stage change.
        """
        event = PrContentTransitionEvent(
            content_id=content.id,
            content_version_id=content_version_id,
            from_stage=before,
            to_stage=target,
            trigger=trigger,
            actor_user_id=actor.user_id,
            approval_event_id=approval_event_id,
            production_submission_id=production_submission_id,
            reverses_event_id=reverses.id if reverses is not None else None,
            note=reason,
        )
        self._session.add(event)
        await self._session.flush()
        if reverses is not None:
            reverses.reversed_by_event_id = event.id
            await self._session.flush()
        # M3. One upsert saying "this content may need projecting", written in
        # the same transaction as the stage change - so if the transition
        # committed, so did the request, and no after-commit hook can lose it.
        #
        # Here rather than in the four callers because this is the chokepoint:
        # every stage change in the module goes through ``apply`` and every
        # ``apply`` writes exactly one of these events, including the backward
        # ones an undo takes. A projector that had to be told separately about
        # undo would be a projector that silently kept counting withdrawn work.
        #
        # It takes no capability, reads no content and raises no domain error -
        # see ``request_content_work_projection``. Content operations do not
        # fail because work projection has an opinion.
        await request_content_work_projection(self._session, content.id)
        return event

    async def _queue_ai_review(self, content: PrContentItem) -> None:
        """Ask for an automatic review of whatever draft is current now.

        Attached here, to the one method every stage change goes through, and
        deliberately not to a route or a Telegram tool: the web panel, the bot
        and any future caller all arrive at ``apply`` and must all get a review.
        A trigger in a transport layer is a trigger the other transports do not
        have.

        The run row is written in **this** transaction, so it commits with the
        stage change or not at all - a worker can never read a queued review for
        content that never entered ``AI_REVIEW``. Dispatch happens afterwards,
        from the sweeper, which by construction only sees committed rows.

        Enqueueing is idempotent: a second entry into ``AI_REVIEW`` for the same
        draft finds the active run and adds nothing. An item with no draft at
        all queues nothing rather than pinning a version that does not exist.
        """
        if self._runs is None:
            return
        version = await self._current_version_id(content.id)
        if version is None:
            logger.warning(
                "ai_review_not_queued_no_version",
                extra={"pr_content_id": str(content.id)},
            )
            return
        readiness = await self._policy.evaluate(content.id) if self._policy is not None else None
        await self._runs.enqueue(
            content_id=content.id,
            content_version_id=version,
            policy_pins=readiness.pins if readiness else (),
        )

    async def _require_human_review_ready(self, content_id: uuid.UUID) -> None:
        """Refuse the direct submission when the draft is not complete. Step 1F.2.10.

        Two refusals, both the AI path's own: there must be a draft to read,
        and the draft must carry the channel and Organic/Paid facts the AI path
        requires. The pack check is deliberately not among them - see
        :meth:`PrPolicyReadinessService.evaluate_for_human_review`.
        """
        if await self._current_version(content_id) is None:
            raise PrWorkflowTransitionError(
                "There is no draft to submit for review",
                details={
                    "content_id": str(content_id),
                    "current": PrWorkflowStage.SCRIPTING.value,
                    "target": PrWorkflowStage.TEAM_LEAD_REVIEW.value,
                    "reason": "no_content_version",
                },
            )
        if self._policy is None:
            return
        readiness = await self._policy.evaluate_for_human_review(content_id)
        readiness.raise_if_blocked(content_id)

    async def _require_policy_ready(self, content_id: uuid.UUID) -> None:
        """Refuse the move when policy context is incomplete.

        Delegates to :class:`PrPolicyReadinessService`, which
        :class:`~meobot.application.pr_action_service.PrAvailableActionService`
        also asks - so the button the panel offers and the transition the server
        accepts can never disagree about this.
        """
        if self._policy is None:
            return
        readiness = await self._policy.evaluate(content_id)
        readiness.raise_if_blocked(content_id)

    async def _current_version_id(self, content_id: uuid.UUID) -> uuid.UUID | None:
        """The newest draft's id. Read here rather than taken from a caller.

        The pin has to be the version that is current *at the moment the stage
        changed*, under the same lock as the transition - a caller-supplied id
        could be one the caller read before somebody else revised.
        """
        version = await self._current_version(content_id)
        return version.id if version is not None else None

    async def _current_version(self, content_id: uuid.UUID) -> PrContentVersion | None:
        """The newest draft row, or ``None`` before version 1 exists."""
        result = await self._session.execute(
            select(PrContentVersion)
            .where(PrContentVersion.content_id == content_id)
            .order_by(PrContentVersion.version_no.desc())
            .limit(1)
        )
        return result.scalars().first()

    # --- Loading ----------------------------------------------------------
    async def lock(self, content_id: uuid.UUID) -> PrContentItem:
        """Public alias for :meth:`_lock`, for services that then call :meth:`apply`."""
        return await self._lock(content_id)

    async def _lock(self, content_id: uuid.UUID) -> PrContentItem:
        content = await lock_row(self._session, PrContentItem, content_id)
        if content is None:
            raise PrNotFoundError(
                "No PR content item with that id",
                details={"content_id": str(content_id)},
            )
        return content

    async def _require_submission(self, content: PrContentItem) -> None:
        """Refuse ``PRODUCTION -> INTERNAL_REVIEW`` with no cut handed in."""
        found = await self._session.execute(
            select(exists().where(PrProductionSubmission.content_id == content.id))
        )
        if bool(found.scalar()):
            return
        raise PrWorkflowTransitionError(
            "Internal review needs a production file to review",
            details={
                "content_id": str(content.id),
                "current": content.workflow_stage.value,
                "target": PrWorkflowStage.INTERNAL_REVIEW.value,
                "reason": "no_production_submission",
            },
        )

    # --- The one precondition that needs a query --------------------------
    # --- Reading ----------------------------------------------------------
    def manual_targets(self, content: PrContentItem) -> frozenset[PrWorkflowStage]:
        """Stages a person may move this content to right now.

        What a client renders as buttons.
        """
        return allowed_content_targets(content.workflow_stage, trigger=PrTransitionTrigger.MANUAL)


def _require_producer(content: PrContentItem) -> None:
    """Refuse ``APPROVED -> PRODUCTION`` while nobody holds the production.

    Step 1F.2.3b, and the correction this half of the step is for: approval used
    to hand straight to a stage called "đang sản xuất" with ``producer_user_id``
    null, so the board showed work in progress that nobody had picked up.
    Starting production is now a separate, explicit act that needs somebody to
    have taken the job first - by assignment or by claiming it.

    A ``PrWorkflowTransitionError`` rather than a permission error: the actor may
    be perfectly entitled and the *content* is not ready, and ``details.reason``
    says which so a client can offer "phân công" instead of "xin quyền".
    """
    if content.producer_user_id is not None:
        return
    raise PrWorkflowTransitionError(
        "Production cannot start before somebody holds it",
        details={
            "content_id": str(content.id),
            "current": content.workflow_stage.value,
            "target": PrWorkflowStage.PRODUCTION.value,
            "reason": "no_producer_assigned",
        },
    )


__all__: list[str] = ["PrContentWorkflowService", "capability_for_target"]
