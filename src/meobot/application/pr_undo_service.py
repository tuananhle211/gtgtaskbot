"""Taking back the last decision, without pretending it never happened.

Step 1F.2.3b. "Hoàn tác" is the button somebody presses ten seconds after
approving the wrong piece, and everything about this module is shaped by the two
ways that button usually goes wrong.

**It is not a stage picker.** There is no destination parameter anywhere in this
path - not in the route, not in the command, not in the service. The server
reads the content's own transition history, finds the last reversible thing that
happened, and reverses *that*. A client that could name a stage would be a client
that could move content anywhere, which is the transition matrix's job and not a
button's.

**It is not an eraser.** The original transition stays, the original approval
stays, the audit row stays. An undo *appends*: a backward transition event linked
to the one it takes back, and the pair is what the history tab renders as
"14:05 Duyệt Trưởng phòng / 14:08 Hoàn tác duyệt Trưởng phòng". Nothing in this
module deletes a row.

What "reversible" means
-----------------------

Exactly one thing: the latest transition event for this content that is not
already reversed, is not itself an undo, and **still describes where the content
is** - ``event.to_stage == content.workflow_stage``. That last clause is the
whole safety property, and it is worth reading twice:

* it makes "only the latest action" true by construction. A Team Lead approval
  under a Head approval has ``to_stage = HEAD_REVIEW`` while the content sits at
  ``APPROVED``, so it is not the candidate - the Head decision has to be taken
  back first;
* it makes "no undo after somebody else moved on" true for free, including moves
  this module has never heard of.

On top of that, each kind of decision has its own downstream check, because
"nothing has moved" is not the same as "nothing has been built on it":

===========================  ==========================================
Head approval                blocked once a producer holds the piece,
                             production has started, or a cut exists
Internal-review approval     blocked once anything has been published
Revision (back to writing)   blocked once a newer draft exists
Revision (back to producing) blocked once a newer cut exists
===========================  ==========================================

What is deliberately not undoable
----------------------------------

* **Starting production.** ``production_started_at`` is irreversible on purpose -
  the permanent-delete rule reads it, and a member's delete right ends there
  forever. A button that un-stamped it would hand that right back. If the team
  ever needs "back to approved", it is a distinct correction with its own name.
* **Rejections.** ``REJECTED`` cancels the content, and un-cancelling abandoned
  work is a decision, not an undo. The matrix reflects that: no ``UNDO`` edge
  leaves ``CANCELLED``.
* **AI review.** Undoing ``SCRIPTING -> AI_REVIEW`` while a run is queued or
  executing would mean cancelling a job this repository has no cancellation
  state for. Faking it - moving the stage and hoping the worker notices - is how
  a stale ``PASS`` ends up advancing content nobody re-checked. Omitted, and
  documented as omitted, rather than approximated.
* **Anything published.** No undo at ``PUBLISHED``, ``MEASURED`` or
  ``ARCHIVED``, and no capability that changes that.
* **Deletion.** Permanent means permanent; there is no row left to undo.

Who may
-------

Not "an undo capability". The rule is: **hold the capability the original action
needed, and either be the person who did it or be management.** So a Team Lead
takes back their own approval with ``PR_TEAM_LEAD_REVIEW``; a second Team Lead
takes back somebody else's only if they also hold ``PR_CONTENT_CANCEL``, the
existing "may end this piece of work" right. There is no undo-anything power, and
nobody gains authority they did not already have over the action itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrPermissionDeniedError, PrUndoNotAvailableError
from meobot.domain.pr.lifecycle import is_published_onward
from meobot.domain.pr.models import PrApprovalDecision, PrApprovalStage, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import PrTransitionTrigger, is_reversible_decision

logger = get_logger(__name__)


class PrUndoKind(StrEnum):
    """Which decision an undo would take back.

    Named per gate rather than one generic value, because the client renders a
    different sentence for each - *"Hoàn tác duyệt Trưởng phòng"* is a different
    promise from *"Hoàn tác yêu cầu sửa"* - and because a person about to press a
    destructive-looking button deserves to be told what it undoes rather than
    "the last thing".
    """

    UNDO_TEAM_LEAD_APPROVAL = "UNDO_TEAM_LEAD_APPROVAL"
    UNDO_HEAD_APPROVAL = "UNDO_HEAD_APPROVAL"
    UNDO_INTERNAL_REVIEW = "UNDO_INTERNAL_REVIEW"
    UNDO_REVISION = "UNDO_REVISION"


#: Which gate produced which undo, for an ``APPROVED`` decision.
_APPROVAL_KINDS: dict[PrApprovalStage, PrUndoKind] = {
    PrApprovalStage.TEAM_LEAD_REVIEW: PrUndoKind.UNDO_TEAM_LEAD_APPROVAL,
    PrApprovalStage.HEAD_REVIEW: PrUndoKind.UNDO_HEAD_APPROVAL,
    PrApprovalStage.INTERNAL_REVIEW: PrUndoKind.UNDO_INTERNAL_REVIEW,
}


@dataclass(frozen=True, slots=True)
class UndoCandidate:
    """The one action that could be taken back, and where it would go.

    Carried to the action list so a client can word the button and the
    confirmation without inspecting history itself: the kind says *what* is being
    reversed, ``target_stage`` says where the content lands, and the approval is
    there for a caller that wants to name the version.
    """

    kind: PrUndoKind
    transition: PrContentTransitionEvent
    target_stage: PrWorkflowStage
    approval: PrApprovalEvent | None = None

    @property
    def approval_stage(self) -> PrApprovalStage | None:
        return self.approval.approval_stage if self.approval is not None else None


class PrWorkflowUndoService:
    """Finds the last reversible action and takes it back.

    Args:
        session: Unit of work. The caller owns the transaction boundary - the
            reversal event, the link on the original, the stage change, the audit
            row and any notification commit together or not at all.
        audit: Event writer sharing that session.
        workflow: The only writer of ``workflow_stage``. The backward edge goes
            through :meth:`PrContentWorkflowService.apply` with the ``UNDO``
            trigger, so it is validated against the same matrix as every forward
            move and records its own transition event.
        capabilities: Resolves the capability the *original* action needed.
        notifications: Optional. Tells whoever was waiting on the decision that
            it has been withdrawn - see
            :class:`~meobot.application.pr_notifications.PrNotificationService`.
            Absent, the undo still works and nobody is told, which is the
            pre-1F.2.3b shape.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        workflow: PrContentWorkflowService,
        capabilities: PrCapabilityService,
        notifications: object | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._workflow = workflow
        self._capabilities = capabilities
        self._notifications = notifications

    # --- Reading ----------------------------------------------------------
    async def candidate(self, content: PrContentItem) -> UndoCandidate | None:
        """The action an undo would reverse, or ``None``.

        ``None`` covers every reason there is nothing to do: no history at all
        (content that predates Step 1F.2.3b), a last action of a kind that is not
        reversible, an action that no longer describes where the content is, and
        anything at or past publication. The caller does not need to know which -
        the button is simply absent - and :meth:`undo_last` raises with a reason
        for the caller who presses anyway.
        """
        if is_published_onward(content.workflow_stage):
            return None
        event = await self._latest_effective(content.id)
        if event is None or event.to_stage is not content.workflow_stage:
            return None
        return await self._as_candidate(event)

    async def may_undo(self, actor: Actor, content: PrContentItem) -> bool:
        """Whether pressing it would work, for this actor, right now.

        Runs the **whole** decision, including the downstream checks - not just
        the authorization. An action list that offered "Hoàn tác" on a piece
        somebody has already taken into production would be offering a button
        that 409s, and this module is the one place in the step where that
        matters most: undo is the feature people press when they are already
        flustered.
        """
        found = await self.candidate(content)
        if found is None or not await self._authorized(actor, content, found):
            return False
        try:
            await self._require_no_downstream_work(content, found)
        except PrUndoNotAvailableError:
            return False
        return True

    async def history(
        self, content_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[PrContentTransitionEvent]:
        """Every transition, oldest first, reversal links included.

        Oldest first because this is read as a story. The reversal pairs are
        rendered from ``reverses_event_id`` / ``reversed_by_event_id``, so the
        client does not have to infer which undo cancelled which move by
        comparing timestamps.
        """
        result = await self._session.execute(
            select(PrContentTransitionEvent)
            .where(PrContentTransitionEvent.content_id == content_id)
            .order_by(PrContentTransitionEvent.created_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    # --- Writing ----------------------------------------------------------
    async def undo_last(
        self, *, actor: Actor, request_id: uuid.UUID, content_id: uuid.UUID
    ) -> UndoCandidate:
        """Reverse the last reversible action on this content.

        The order is the safety property:

        1. **lock** the content row - so an undo racing an approval, a claim, a
           production start or a publication resolves one way round or the
           other, never both;
        2. **resolve** the candidate from history, *after* the lock, so it is
           read from the state this transaction will act on;
        3. **authorize** against the original action's capability;
        4. **check downstream work**, per kind;
        5. **append** the reversal and move the stage back, through the workflow;
        6. **audit**, and tell whoever was waiting.

        Raises:
            PrNotFoundError: No such content.
            PrUndoNotAvailableError: Nothing to undo, the action is no longer the
                latest, or later work depends on it. ``details['reason']``
                distinguishes them.
            PrPermissionDeniedError: Not this actor's to undo.
        """
        content = await self._workflow.lock(content_id)
        found = await self._require_candidate(content)
        if not await self._authorized(actor, content, found):
            raise PrPermissionDeniedError(
                "This action may not be undone by this actor",
                details={
                    "content_id": str(content.id),
                    "undo_kind": found.kind.value,
                    "reason": "not_your_action",
                },
            )
        await self._require_no_downstream_work(content, found)

        before = content.workflow_stage
        await self._workflow.apply(
            actor=actor,
            request_id=request_id,
            content=content,
            target=found.target_stage,
            trigger=PrTransitionTrigger.UNDO,
            reason=f"undo:{found.kind.value}",
            reverses=found.transition,
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORKFLOW_UNDONE,
            entity_type="pr_content_item",
            entity_id=content.id,
            before={"workflow_stage": before.value},
            after={
                "workflow_stage": found.target_stage.value,
                "content_code": content.code,
                "undo_kind": found.kind.value,
                "reversed_transition_id": str(found.transition.id),
                "reversed_approval_event_id": (
                    str(found.approval.id) if found.approval is not None else None
                ),
            },
        )
        logger.info(
            "pr_workflow_undone",
            extra={
                "pr_content_id": str(content.id),
                "undo_kind": found.kind.value,
                "stage_before": before.value,
                "stage_after": found.target_stage.value,
            },
        )
        await self._notify(actor=actor, content=content, found=found)
        return found

    # --- Internals --------------------------------------------------------
    async def _require_candidate(self, content: PrContentItem) -> UndoCandidate:
        """The candidate, or the reason there is not one.

        Split from :meth:`candidate` so the read model can be quiet and the write
        can be specific: "nothing to undo" and "somebody has moved this on" are
        different sentences, and the second is the one a person needs when the
        button they were looking at a minute ago stops working.
        """
        if is_published_onward(content.workflow_stage):
            raise PrUndoNotAvailableError(
                "Published content cannot be rolled back",
                details={
                    "content_id": str(content.id),
                    "workflow_stage": content.workflow_stage.value,
                    "reason": "published",
                },
            )
        event = await self._latest_effective(content.id)
        if event is None:
            raise PrUndoNotAvailableError(
                "There is no reversible action on this content",
                details={"content_id": str(content.id), "reason": "nothing_to_undo"},
            )
        if event.to_stage is not content.workflow_stage:
            raise PrUndoNotAvailableError(
                "A later step has already moved this content",
                details={
                    "content_id": str(content.id),
                    "workflow_stage": content.workflow_stage.value,
                    "reason": "superseded",
                },
            )
        found = await self._as_candidate(event)
        if found is None:
            raise PrUndoNotAvailableError(
                "The last action on this content cannot be undone",
                details={
                    "content_id": str(content.id),
                    "reason": "not_reversible",
                    "trigger": event.trigger.value,
                },
            )
        return found

    async def _latest_effective(self, content_id: uuid.UUID) -> PrContentTransitionEvent | None:
        """The newest transition that is neither an undo nor already reversed.

        Skipping ``UNDO`` rows is what makes undo repeatable in the right
        direction: after taking back a Head approval, the newest *forward*
        transition still standing is the Team Lead's, and if the content is now
        back at ``HEAD_REVIEW`` that one is legitimately next. Skipping reversed
        rows stops a second press from undoing the same decision twice.
        """
        result = await self._session.execute(
            select(PrContentTransitionEvent)
            .where(
                PrContentTransitionEvent.content_id == content_id,
                PrContentTransitionEvent.reversed_by_event_id.is_(None),
                PrContentTransitionEvent.trigger != PrTransitionTrigger.UNDO,
            )
            .order_by(PrContentTransitionEvent.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _as_candidate(self, event: PrContentTransitionEvent) -> UndoCandidate | None:
        """Classify one transition, or refuse to.

        Only ``HUMAN_APPROVAL`` edges qualify. A manual move is somebody driving
        the workflow rather than deciding something - and the two manual moves
        that would matter most, starting production and publishing, are exactly
        the ones this step will not reverse.
        """
        if event.trigger is not PrTransitionTrigger.HUMAN_APPROVAL:
            return None
        if event.approval_event_id is None:
            # A pre-1F.2.3b row, or an approval recorded without its link. There
            # is no way to say which decision to reverse, so nothing is offered.
            return None
        approval = await self._session.get(PrApprovalEvent, event.approval_event_id)
        if approval is None or not is_reversible_decision(approval.decision):
            return None
        kind = (
            _APPROVAL_KINDS[approval.approval_stage]
            if approval.decision is PrApprovalDecision.APPROVED
            else PrUndoKind.UNDO_REVISION
        )
        return UndoCandidate(
            kind=kind,
            transition=event,
            target_stage=event.from_stage,
            approval=approval,
        )

    async def _authorized(self, actor: Actor, content: PrContentItem, found: UndoCandidate) -> bool:
        """Hold the original action's capability *for this item*, and own it or
        manage it.

        Two conditions, and neither is a new power. The capability is the one the
        decision itself needed - somebody who may not approve at a gate may not
        un-approve at it either - and beyond that it is your own action, or you
        hold ``PR_CONTENT_CANCEL``, the right to end this piece of work that
        ``TEAM_LEAD`` and above already have.

        Step 1F.2.7 passes the item, so the scope applies here too: withdrawing
        an approval is an act at the gate, and a grant that does not reach this
        content does not reach un-approving it either. Same service, same
        method, same rule as the approval itself.
        """
        stage = found.approval_stage
        if stage is None:
            return False
        if not await self._capabilities.can_approve(actor, content, stage):
            return False
        if actor.user_id is not None and actor.user_id == found.transition.actor_user_id:
            return True
        return await self._capabilities.allows(actor, PrCapability.PR_CONTENT_CANCEL)

    async def _require_no_downstream_work(
        self, content: PrContentItem, found: UndoCandidate
    ) -> None:
        """Per-kind: has anything been built on the decision being taken back?

        Structured business checks, never "was it recent". A Head approval
        undone ten seconds later is unsafe if somebody claimed the production in
        those ten seconds; one undone an hour later is fine if nobody did.
        """
        if found.kind is PrUndoKind.UNDO_HEAD_APPROVAL:
            # The approval has been acted on the moment somebody takes the job:
            # the whole point of ``APPROVED`` is that it is the handoff, and
            # silently clearing the producer to make the undo fit would be this
            # module deciding somebody else's work does not count.
            if content.producer_user_id is not None:
                raise self._blocked(content, found, "production_handed_off")
            if content.production_started_at is not None:
                raise self._blocked(content, found, "production_started")
            if await self._exists(
                PrProductionSubmission.content_id == content.id, PrProductionSubmission
            ):
                raise self._blocked(content, found, "production_submitted")
        elif found.kind is PrUndoKind.UNDO_INTERNAL_REVIEW:
            if await self._exists(PrPublication.content_id == content.id, PrPublication):
                raise self._blocked(content, found, "already_published")
        elif found.kind is PrUndoKind.UNDO_REVISION:
            await self._require_no_new_work_since(content, found)

    async def _require_no_new_work_since(
        self, content: PrContentItem, found: UndoCandidate
    ) -> None:
        """A revision may only be taken back before somebody acts on it.

        The two shapes of "acting on it", and both are answered by **identity
        rather than by time**:

        * a revision to ``SCRIPTING`` is spent once a **new draft** exists. The
          transition recorded which version was current when it happened, so the
          check is whether the newest version is still that one;
        * a revision to ``PRODUCTION`` is spent once a **new cut** exists. The
          reversed decision recorded which submission it judged, so the check is
          whether the newest submission is still that one.

        Comparing timestamps was the obvious implementation and is wrong twice
        over: rows written in one transaction share ``now()`` to the microsecond,
        so a real "newer" row can compare equal, and a clock that moves backwards
        would make the answer depend on the machine. An id either changed or it
        did not.

        Undoing either would put the content back at a gate while the work the
        revision asked for sits unreviewed - or, worse, invite somebody to
        approve the version that was sent back.
        """
        if found.transition.to_stage is PrWorkflowStage.SCRIPTING:
            current = await self._session.execute(
                select(PrContentVersion.id)
                .where(PrContentVersion.content_id == content.id)
                .order_by(PrContentVersion.version_no.desc())
                .limit(1)
            )
            newest = current.scalars().first()
            if newest is not None and newest != found.transition.content_version_id:
                raise self._blocked(content, found, "new_version_written")
        if found.transition.to_stage is PrWorkflowStage.PRODUCTION:
            latest = await self._session.execute(
                select(PrProductionSubmission.id)
                .where(PrProductionSubmission.content_id == content.id)
                .order_by(PrProductionSubmission.submission_no.desc())
                .limit(1)
            )
            newest_cut = latest.scalars().first()
            judged = found.approval.production_submission_id if found.approval is not None else None
            if newest_cut is not None and judged is not None and newest_cut != judged:
                raise self._blocked(content, found, "new_submission")

    async def _exists(self, condition: object, model: type) -> bool:
        found = await self._session.execute(
            select(exists().where(condition))  # type: ignore[arg-type]
        )
        return bool(found.scalar())

    @staticmethod
    def _blocked(
        content: PrContentItem, found: UndoCandidate, reason: str
    ) -> PrUndoNotAvailableError:
        return PrUndoNotAvailableError(
            "This action can no longer be undone",
            details={
                "content_id": str(content.id),
                "content_code": content.code,
                "undo_kind": found.kind.value,
                "reason": reason,
            },
        )

    async def _notify(self, *, actor: Actor, content: PrContentItem, found: UndoCandidate) -> None:
        """Tell whoever was told the decision, that it has been taken back.

        Only where somebody's queue materially changed. A withdrawn Head approval
        is the one that matters most: the responsible person was told to go and
        arrange production, and leaving that standing is how a piece gets
        produced against a script nobody has approved.
        """
        if self._notifications is None:
            return
        notifier = self._notifications
        on_undone = getattr(notifier, "on_workflow_undone", None)
        if on_undone is None:  # pragma: no cover - defensive, wiring always supplies it
            return
        await on_undone(content=content, kind=found.kind, actor=actor)


__all__: list[str] = ["PrUndoKind", "PrWorkflowUndoService", "UndoCandidate"]
