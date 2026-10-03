"""What *this* person may do to *this* content item, right now.

Step 1E.2. The panel used to render every workflow stage as a button and let the
server refuse the illegal ones. That was correct and unusable: somebody at
``IDEA`` was shown twelve moves, eleven of which fail, and "Đã hủy" sat in the
same row as the one move that works.

This service answers the question the screen actually wants to ask - *what is
the next thing to do* - without moving a single rule into the browser:

* which stages are reachable comes from
  :func:`~meobot.domain.pr.workflow.allowed_content_targets` with the
  ``MANUAL`` trigger, the same table
  :meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.request_transition`
  validates against;
* which capability each move needs comes from
  :func:`~meobot.application.pr_workflow_service.capability_for_target`, the
  function that write path calls;
* which gate an item stands at comes from
  :data:`~meobot.domain.pr.workflow.STAGE_APPROVAL_GATES`, and what may be
  decided there from :data:`~meobot.domain.pr.workflow.APPROVAL_OUTCOMES`;
* whether the draft may still be rewritten comes from
  :data:`~meobot.domain.pr.workflow.EDITABLE_STAGES`, which
  :meth:`~meobot.application.pr_content_service.PrContentService.revise_content`
  enforces.

Nothing is restated here. Every one of those is imported from the module that
owns it, so an edge added to the matrix appears in this list without an edit.

It reads. It never writes
-------------------------

No session write, no lock, no audit row, no ``flush``. Asking what you may do
must not be a thing that changes anything, and a test asserts the stage is
unchanged after a call.

Prerequisites that need a query
-------------------------------

Step 1E.2 stopped at the matrix and the capability, and left three
preconditions to be discovered by pressing the button:

* ``PUBLISHED -> MEASURED`` needs a metric snapshot to exist;
* deciding at the team-lead gate needs a gating AI verdict for the draft - or,
  since Step 1F.2.10, the draft's direct submission to the Team Lead;
* the Head approval needs a team-lead approval of the same draft on file.

Step 1E.2.1 asks all three, and asks them **of the services that enforce them**:
:meth:`PrContentWorkflowService.is_measurable`,
:meth:`~meobot.application.pr_approval_service.PrApprovalService.ai_gate_satisfied`
and
:meth:`~meobot.application.pr_approval_service.PrApprovalService.head_approval_permitted`.
Each of those is the write path's own check with the refusal caught - the same
pattern :meth:`PrCapabilityService.allows` already used - so there is one
implementation of each rule and a screen cannot disagree with the write about
it.

The third of those used to be narrower. Until Step 1F.2.2 it also withheld
"Duyệt" at the Head gate from whoever had given the team-lead approval, because
the write would have refused it. The write no longer refuses it, so the list no
longer withholds it - and it changed here by *asking the same predicate*, with
no edit to the condition below. That is the whole benefit of not restating
rules: a business decision moved in one file and the buttons followed.

The endpoint is therefore a list of things that work. An action that is offered
and then refused teaches somebody that the panel lies; an action that is absent
because its precondition is unmet is simply the truth about right now.

This is still not authorization. Every write re-checks, because the item can
move between the read and the press.

Emphasis is presentation, not policy
------------------------------------

:class:`PrActionEmphasis` exists so a client can tell "the forward move" from
"the one that ends this piece of work" without pattern-matching on stage names
in the browser. It changes no rule: a ``DANGER`` action is exactly as legal as a
``PRIMARY`` one, and the write path checks both identically.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from meobot.application.pr_approval_service import PrApprovalService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_asset_service import PrContentAssetService
from meobot.application.pr_content_comment_service import PrContentCommentService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_lifecycle_service import PrContentLifecycleService
from meobot.application.pr_policy_readiness_service import PrPolicyReadinessService
from meobot.application.pr_production_service import PrProductionService
from meobot.application.pr_publication_service import PrPublicationService
from meobot.application.pr_undo_service import PrWorkflowUndoService
from meobot.application.pr_workflow_service import (
    PrContentWorkflowService,
    capability_for_target,
)
from meobot.db.models.pr import PrContentItem
from meobot.domain.identity.models import Actor
from meobot.domain.pr.models import PrApprovalDecision, PrApprovalStage, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.workflow import (
    APPROVAL_OUTCOMES,
    EDITABLE_STAGES,
    PUBLISHABLE_STAGES,
    STAGE_APPROVAL_GATES,
    PrTransitionTrigger,
    allowed_content_targets,
    is_direct_team_lead_submission,
)


class PrActionKind(StrEnum):
    """Which endpoint an offered action leads to.

    One per write path a content screen can reach. Step 1F.2.3 added four, and
    each of them is a distinct route with distinct rules, which is the test for
    whether something belongs here: ``CLAIM_PRODUCTION`` and ``ASSIGN_PRODUCER``
    both end with a producer on the row and are *not* the same action - one is a
    member volunteering and needs the piece to be unclaimed, the other is a
    manager deciding and does not.

    Note what is **not** here: approving or requesting revision at the internal
    review gate. Those are ``APPROVAL`` with a decision, exactly like the two
    script gates, because the server derives the gate from the stage and the
    client renders the wording. A separate ``APPROVE_INTERNAL_REVIEW`` kind would
    have been a fourth spelling of one write path and a second place to keep the
    gate table in step.
    """

    TRANSITION = "TRANSITION"
    APPROVAL = "APPROVAL"
    EDIT_CONTENT = "EDIT_CONTENT"
    #: Step 1F.2.3d. Retriage the piece. Deliberately **not** folded into
    #: ``EDIT_CONTENT``: that one means "write a new version of the script" and
    #: is withheld outside the editable stages, while this one stays available
    #: through review and production - which is where escalating a piece is the
    #: whole point. One kind covering both would have had to pick one of those
    #: two availabilities and be wrong about the other.
    SET_PRIORITY = "SET_PRIORITY"
    #: Step 1F.2.3e. Classify the piece, or correct its format. Beside
    #: ``SET_PRIORITY`` rather than folded into it: they are two fields with
    #: one authorization rule, and a client may well offer one control and not
    #: the other.
    SET_CONTENT_TYPE = "SET_CONTENT_TYPE"
    #: Step 1F.2.3e. Attach, correct or remove review material. One kind for
    #: all three verbs, because one rule decides all three - a client that may
    #: add may also edit and delete, and three kinds would be three spellings
    #: of one predicate.
    MANAGE_CONTENT_RESOURCES = "MANAGE_CONTENT_RESOURCES"
    #: Step 1F.2.3f. Record, correct or remove a derivative production output -
    #: a cutdown, a remix, a caption variant. One kind for all three verbs, for
    #: the reason ``MANAGE_CONTENT_RESOURCES`` is one: one rule decides all
    #: three.
    #:
    #: Deliberately **not** beside the metadata trio below it. A derivative is
    #: produced work, so it is authorised as produced work - the producer or
    #: whoever assigns production - and a writer responsible for a piece does not
    #: thereby get to file production files against it.
    MANAGE_CONTENT_DERIVATIVES = "MANAGE_CONTENT_DERIVATIVES"
    #: Step 1F.2.3g. **Record** a derivative production output, and nothing
    #: else. Its own kind rather than a widening of the one above, because the
    #: two now have genuinely different rules and a client needs both answers:
    #: this one is offered to anybody who may view the piece, and
    #: ``MANAGE_CONTENT_DERIVATIVES`` still means "you are production
    #: management here".
    #:
    #: Correcting or removing one is deliberately **not** an action kind. Since
    #: 1F.2.3g the answer differs down the list - a contributor may fix the cut
    #: they recorded and not the one beside it, and a published output may be
    #: deleted by nobody - so it is answered per row on
    #: :class:`~meobot.application.pr_content_asset_service.DerivativeView`,
    #: exactly as ``PublicationResponse.can_edit`` is. A content-level boolean
    #: could only have been wrong for half the rows.
    ADD_CONTENT_DERIVATIVE = "ADD_CONTENT_DERIVATIVE"
    #: Step 1F.2.3g. Say something on this content item's comment thread.
    #:
    #: Offered on the same rule as the derivative above - whoever may view the
    #: piece - and offered at every stage, ``ARCHIVED`` included. It is here at
    #: all so the panel does not conclude "there is a session, therefore there
    #: is a composer": editing and deleting a comment are per row and travel on
    #: the comment, and this is the one content-level question.
    ADD_CONTENT_COMMENT = "ADD_CONTENT_COMMENT"
    #: Step 1F.2.3f. Attach, correct or remove a commercial destination link.
    #: Beside the metadata trio and authorised with them: where a piece sends a
    #: customer is a fact about the campaign, not about a file.
    MANAGE_CONTENT_DESTINATIONS = "MANAGE_CONTENT_DESTINATIONS"
    #: Step 1F.2.3f. Record that a produced file went out on a channel. Offered
    #: from the publication capability and the publishable stages, which is
    #: exactly what the write checks - so a piece at ``MEASURED`` still offers
    #: it and a piece at ``ARCHIVED`` does not.
    RECORD_PUBLICATION = "RECORD_PUBLICATION"
    #: Step 1F.2.3f.2. Fix where a handed-in production file lives. Offered to
    #: the person who submitted it and to production management, and only while
    #: no publication has ever referenced it - which is a per-row fact, so this
    #: says "there is at least one output you could correct" and the row's own
    #: ``can_correct`` says which.
    CORRECT_PRODUCTION_OUTPUT = "CORRECT_PRODUCTION_OUTPUT"
    #: Step 1F.2.3f.1, renamed by 1F.2.3f.2. Correct **anybody's** publication on
    #: this content - the management half. Offered wherever the ``PATCH`` accepts
    #: one for every row: the administration capability, and content that is not
    #: archived.
    #:
    #: Correcting *your own* publication is deliberately not an action kind. It
    #: depends on which row is being looked at, so it is answered per row on
    #: ``PublicationResponse.can_edit`` - a content-level boolean could only have
    #: been wrong for half the list.
    EDIT_ANY_PUBLICATION = "EDIT_ANY_PUBLICATION"
    #: Step 1F.2.3f.1. Take a publication back as entered in error. Narrower than
    #: the edit on purpose: only at ``PUBLISHED``, because at ``MEASURED`` numbers
    #: have been read off the piece and at ``ARCHIVED`` it has been put away.
    REVERSE_PUBLICATION = "REVERSE_PUBLICATION"
    #: Step 1F.2.3. Remove the item from the workspace - see
    #: :class:`~meobot.application.pr_lifecycle_service.PrContentLifecycleService`
    #: for what that does and does not destroy.
    DELETE_CONTENT = "DELETE_CONTENT"
    #: Step 1F.2.3. Set or change who produces this piece.
    ASSIGN_PRODUCER = "ASSIGN_PRODUCER"
    #: Step 1F.2.3. Take an unclaimed production for yourself.
    CLAIM_PRODUCTION = "CLAIM_PRODUCTION"
    #: Step 1F.2.3b. Begin the work: ``APPROVED -> PRODUCTION``. Its own kind
    #: rather than the generic ``TRANSITION`` to that stage, because it is not a
    #: generic move - it needs somebody to hold the piece, it is refused to
    #: everybody else, and it stamps the irreversible marker the delete rule
    #: reads. The plain transition is withheld from this list for the same
    #: reason: two buttons that look identical and enforce different rules is how
    #: somebody starts production on a piece nobody is producing.
    START_PRODUCTION = "START_PRODUCTION"
    #: Step 1F.2.3. Hand the finished file over for internal review.
    SUBMIT_PRODUCTION = "SUBMIT_PRODUCTION"
    #: Step 1F.2.3b. Take back the last reversible decision. Carries
    #: :attr:`PrAvailableAction.undo_kind` and :attr:`target_stage`, so a client
    #: can say *"Hoàn tác duyệt Trưởng phòng"* and *"nội dung sẽ quay lại bước
    #: Chờ duyệt Trưởng phòng"* without reading history and guessing.
    UNDO_LAST_ACTION = "UNDO_LAST_ACTION"


class PrActionEmphasis(StrEnum):
    """How prominently a client should offer an action.

    Presentation metadata. ``DANGER`` marks the actions that end or reverse
    work - cancelling and rejecting - so a client can keep them out of the
    forward path instead of putting "Hủy" next to "Duyệt".
    """

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    DANGER = "DANGER"


@dataclass(frozen=True, slots=True)
class PrAvailableAction:
    """One thing this actor may currently do, in a shape a client can render.

    A dataclass of domain enums rather than strings or a rendered label: the
    Telegram bot, the web panel and any future client word things differently,
    and the sentence a person reads belongs to the client.
    """

    kind: PrActionKind
    emphasis: PrActionEmphasis
    #: Set for :attr:`PrActionKind.TRANSITION` - where the move goes - and for
    #: :attr:`PrActionKind.UNDO_LAST_ACTION`, where it is the stage the content
    #: would return to.
    target_stage: PrWorkflowStage | None = None
    #: Set for :attr:`PrActionKind.APPROVAL` - what would be decided.
    decision: PrApprovalDecision | None = None
    #: Set for :attr:`PrActionKind.UNDO_LAST_ACTION` - which decision would be
    #: taken back, as a :class:`~meobot.application.pr_undo_service.PrUndoKind`
    #: value. The client turns it into a sentence; the server does not send one.
    undo_kind: str | None = None


#: How each decision is offered. ``REJECTED`` cancels the content at every gate
#: (see :data:`~meobot.domain.pr.workflow.APPROVAL_OUTCOMES`), which is why it
#: is grouped with cancelling rather than with approving.
_DECISION_EMPHASIS: Mapping[PrApprovalDecision, PrActionEmphasis] = MappingProxyType(
    {
        PrApprovalDecision.APPROVED: PrActionEmphasis.PRIMARY,
        PrApprovalDecision.REVISION_REQUIRED: PrActionEmphasis.SECONDARY,
        PrApprovalDecision.REJECTED: PrActionEmphasis.DANGER,
    }
)

_EMPHASIS_ORDER: Mapping[PrActionEmphasis, int] = MappingProxyType(
    {
        PrActionEmphasis.PRIMARY: 0,
        PrActionEmphasis.SECONDARY: 1,
        PrActionEmphasis.DANGER: 2,
    }
)

#: The two forward moves that are *not* offered as plain transitions. Each has a
#: precondition the generic route cannot express as a stage - a producer, a cut -
#: and its own action kind that says so.
_PRODUCTION_MOVES: frozenset[PrWorkflowStage] = frozenset(
    {PrWorkflowStage.PRODUCTION, PrWorkflowStage.INTERNAL_REVIEW}
)

#: Canonical workflow order, read off the enum's member order - the only place
#: that order is written down (see :class:`PrWorkflowStage`).
_STAGE_ORDER: tuple[PrWorkflowStage, ...] = tuple(PrWorkflowStage)


def _transition_emphasis(source: PrWorkflowStage, target: PrWorkflowStage) -> PrActionEmphasis:
    """How a plain transition is offered. Presentation, not policy.

    Cancelling is ``DANGER``, as it always was. Step 1F.2.10 adds one
    ``SECONDARY``: the direct submission to the Team Lead, which stands beside
    *"Gửi đi AI review"* at ``SCRIPTING`` as the second way to submit the same
    draft. AI review stays the recommended default, so it keeps ``PRIMARY``; the
    direct path is exactly as legal and is simply not the one to press by
    reflex. Everything else is the forward move and is ``PRIMARY``.
    """
    if target is PrWorkflowStage.CANCELLED:
        return PrActionEmphasis.DANGER
    if is_direct_team_lead_submission(source, target):
        return PrActionEmphasis.SECONDARY
    return PrActionEmphasis.PRIMARY


class PrAvailableActionService:
    """Read-only: the actions one actor may take on one content item.

    Args:
        capabilities: Resolves PR capabilities against roles and grants. The
            same service every write calls, asked with
            :meth:`~meobot.application.pr_capability_service.PrCapabilityService.allows`
            instead of ``require`` - a screen drawn a minute ago is not
            authorization, and the write re-checks.
        workflow: Asked whether content may be marked ``MEASURED`` yet.
        approvals: Asked whether the AI gate is satisfied and whether this
            person may be the Head approver.
        content: Reader for the current draft, which the two approval
            prerequisites are both scoped to.
        policy: Step 1F.1 readiness. Asked before offering the move into
            ``AI_REVIEW`` - the same service the workflow asks before accepting
            it.
        production: Step 1F.2.3. Asked whether claiming, assigning or submitting
            would work - its own ``may_*`` predicates, which are the write's
            conditions read without a lock.
        assets: Step 1F.2.3g. Asked whether this actor may record a derivative
            against this item - which since that step is the *view* rule, and
            is asked of the service that enforces it rather than restated.
        comments: Step 1F.2.3g. Asked whether this actor may comment. Same rule,
            same reason, and a second service because the two writes live in
            two services and either could narrow without the other.
        lifecycle: Step 1F.2.3. Asked whether this actor may delete this item,
            by way of ``may_delete``, which runs the same rule
            :meth:`~meobot.application.pr_lifecycle_service.PrContentLifecycleService.delete_content`
            enforces and catches the refusal. Both are optional so a caller can
            build this service without the whole bundle; absent, the actions
            they own are simply not offered, which is the pre-1F.2.3 list.

    Every one of those is the service that *enforces* the rule at the write.
    None of them is consulted for a second opinion, and none of the rules is
    restated here.
    """

    def __init__(
        self,
        capabilities: PrCapabilityService,
        workflow: PrContentWorkflowService,
        approvals: PrApprovalService,
        content: PrContentService,
        policy: PrPolicyReadinessService | None = None,
        production: PrProductionService | None = None,
        lifecycle: PrContentLifecycleService | None = None,
        undo: PrWorkflowUndoService | None = None,
        publications: PrPublicationService | None = None,
        assets: PrContentAssetService | None = None,
        comments: PrContentCommentService | None = None,
    ) -> None:
        self._capabilities = capabilities
        self._workflow = workflow
        self._approvals = approvals
        self._content = content
        self._policy = policy
        self._production = production
        self._lifecycle = lifecycle
        self._undo = undo
        # Step 1F.2.3f.2. Optional like its neighbours, so a caller that only
        # wants transitions need not build the publication service - and the
        # publication offers are simply absent rather than guessed at.
        self._publications = publications
        # Step 1F.2.3g. The two open contributions, each asked of the service
        # that enforces it. Optional for the same reason as the rest.
        self._assets = assets
        self._comments = comments

    async def for_content(
        self, *, actor: Actor, content: PrContentItem, on: date | None = None
    ) -> tuple[PrAvailableAction, ...]:
        """Everything ``actor`` may do to ``content`` as it stands.

        Ordered ``PRIMARY``, then ``SECONDARY``, then ``DANGER``, so a client
        that renders the list in order gets the forward path first and the
        destructive actions last without sorting them itself.

        Args:
            actor: The authenticated principal. The caller has already proved
                they may *read* this item - this method adds no read check of
                its own, because it is always called with a row a read-gated
                query returned.
            content: The item, as loaded. Not locked: nothing is written.
            on: The day to judge dated grants against, passed straight to the
                capability service.

        Returns:
            Possibly empty. An item at ``ARCHIVED`` or ``CANCELLED`` offers
            nothing, and so does one whose reader holds no write capability -
            which is the honest answer rather than a row of buttons that 403.
        """
        actions: list[PrAvailableAction] = []

        # Read once, up front: the draft the two submissions would hand over,
        # the decision would be about, and the editor would rewrite. Without one
        # there is nothing to submit, decide or revise - the same conclusion
        # ``require_current_version`` reaches at the write.
        version = await self._content.current_version(content.id)

        reachable = allowed_content_targets(
            content.workflow_stage, trigger=PrTransitionTrigger.MANUAL
        )
        for target in sorted(reachable, key=_STAGE_ORDER.index):
            if target in _PRODUCTION_MOVES and self._production is not None:
                # Step 1F.2.3b: both of these are offered below with their real
                # rules attached - ``START_PRODUCTION`` needs somebody to hold
                # the piece, ``SUBMIT_PRODUCTION`` needs a file. Skipped here so
                # the panel never shows two buttons for one move, one of which
                # ignores the rule.
                continue
            if not await self._capabilities.allows(actor, capability_for_target(target), on=on):
                continue
            if (
                target is PrWorkflowStage.AI_REVIEW
                and self._policy is not None
                and not await self._policy.is_ready(content.id)
            ):
                # Step 1F.1: a supported platform with no distribution mode, or
                # no active pack. The same service the write path asks, so the
                # offer and the refusal cannot disagree.
                continue
            if is_direct_team_lead_submission(content.workflow_stage, target) and (
                version is None
                or (
                    self._policy is not None
                    and not await self._policy.is_ready_for_human_review(content.id)
                )
            ):
                # Step 1F.2.10: the direct submission needs a draft and the same
                # completeness the AI path needs - not the AI path's policy
                # pack. Asked of the same predicate ``request_transition``
                # enforces, so a button is never drawn for a refusal.
                continue
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.TRANSITION,
                    target_stage=target,
                    emphasis=_transition_emphasis(content.workflow_stage, target),
                )
            )

        gate = STAGE_APPROVAL_GATES.get(content.workflow_stage)
        if (
            gate is not None
            and version is not None
            # Nobody to attribute a decision to. ``_reviewer_id`` in the route
            # refuses this actor, so offering the button would be offering a 403.
            and actor.user_id is not None
            # Step 1F.2.7: with the item, so a scoped grant that does not cover
            # this classification or one of its channels draws no button. The
            # write asks the same service the same way - see
            # ``PrApprovalService.record_decision`` - so the offer and the
            # refusal cannot disagree.
            and await self._capabilities.can_approve(actor, content, gate, on=on)
            and await self._approvals.ai_gate_satisfied(
                content.id, approval_stage=gate, version_no=version.version_no
            )
        ):
            # The decisions the gate actually has an outcome for, from the
            # domain table - not a hand-written triple that could drift.
            for decision in APPROVAL_OUTCOMES[gate]:
                if not await self._may_decide(
                    content_id=content.id,
                    gate=gate,
                    decision=decision,
                    version_no=version.version_no,
                ):
                    continue
                actions.append(
                    PrAvailableAction(
                        kind=PrActionKind.APPROVAL,
                        decision=decision,
                        emphasis=_DECISION_EMPHASIS[decision],
                    )
                )

        if (
            content.workflow_stage in EDITABLE_STAGES
            and version is not None
            and await self._capabilities.allows(actor, PrCapability.PR_CONTENT_EDIT, on=on)
        ):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.EDIT_CONTENT, emphasis=PrActionEmphasis.SECONDARY
                )
            )

        # Step 1F.2.3d. No stage condition, on purpose: a piece at HEAD_REVIEW or
        # in production is exactly what somebody needs to escalate. The predicate
        # is the write's own - see
        # :meth:`~meobot.application.pr_content_service.PrContentService.may_edit_metadata`
        # - so the control is offered on precisely the rows the PATCH accepts.
        if await self._content.may_edit_metadata(actor, content):
            # Step 1F.2.3d and 1F.2.3e. One predicate, three offers: priority,
            # content type and review resources are the same class of change -
            # metadata about the work, none of it writing a version or moving a
            # stage - so they are offered and refused together.
            actions.extend(
                PrAvailableAction(kind=kind, emphasis=PrActionEmphasis.SECONDARY)
                for kind in (
                    PrActionKind.SET_PRIORITY,
                    PrActionKind.SET_CONTENT_TYPE,
                    PrActionKind.MANAGE_CONTENT_RESOURCES,
                    # Step 1F.2.3f. A destination link joins the trio rather
                    # than starting a fourth rule: it is metadata about the
                    # campaign, it writes no version and it moves no stage.
                    PrActionKind.MANAGE_CONTENT_DESTINATIONS,
                )
            )

        # Step 1F.2.3g. The two open contributions. No stage condition and no
        # ownership condition, on purpose: both are the module's view rule, and
        # both are asked of the service that enforces the write rather than
        # re-derived here - so a panel cannot conclude "logged in, therefore may
        # contribute", and cannot be told yes where the route would say no.
        if self._assets is not None and await self._assets.may_add_derivative(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.ADD_CONTENT_DERIVATIVE, emphasis=PrActionEmphasis.SECONDARY
                )
            )
        if self._comments is not None and await self._comments.may_comment(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.ADD_CONTENT_COMMENT, emphasis=PrActionEmphasis.SECONDARY
                )
            )

        actions.extend(await self._production_actions(actor, content))

        # Step 1F.2.3f. Recording that a produced file went out. Two conditions,
        # and both are the write's own rather than restated: what
        # ``register_publication`` requires of the actor, and the stages it
        # accepts - read from the domain, so ``MEASURED`` being publishable is
        # decided once.
        # Step 1F.2.3f.2 / 1F.2.3f.3. Three different rules, asked separately -
        # recording a posting is now open to anybody who may *view* the piece,
        # administering the history is not, and reversing one is narrower still.
        # Each is the write's own predicate rather than a restatement of it, so
        # the offer and the route cannot disagree.
        if self._publications is not None:
            if content.workflow_stage in PUBLISHABLE_STAGES and (
                await self._publications.may_record_publication(actor, content)
            ):
                actions.append(
                    PrAvailableAction(
                        kind=PrActionKind.RECORD_PUBLICATION, emphasis=PrActionEmphasis.SECONDARY
                    )
                )
            if await self._capabilities.allows(actor, PrCapability.PR_PUBLICATION_REGISTER, on=on):
                # Correcting is wider than reversing: a mistyped link on a
                # measured piece must be fixable, and un-publishing one must not.
                if content.workflow_stage is not PrWorkflowStage.ARCHIVED:
                    actions.append(
                        PrAvailableAction(
                            kind=PrActionKind.EDIT_ANY_PUBLICATION,
                            emphasis=PrActionEmphasis.SECONDARY,
                        )
                    )
                if content.workflow_stage is PrWorkflowStage.PUBLISHED:
                    actions.append(
                        PrAvailableAction(
                            kind=PrActionKind.REVERSE_PUBLICATION,
                            emphasis=PrActionEmphasis.SECONDARY,
                        )
                    )

        # Step 1F.2.3b. Last of the forward path and first of the corrections:
        # what the server would reverse, if anything, decided by the service that
        # would do the reversing.
        if self._undo is not None:
            undo = await self._undo.candidate(content)
            if undo is not None and await self._undo.may_undo(actor, content):
                actions.append(
                    PrAvailableAction(
                        kind=PrActionKind.UNDO_LAST_ACTION,
                        emphasis=PrActionEmphasis.SECONDARY,
                        target_stage=undo.target_stage,
                        undo_kind=undo.kind.value,
                    )
                )

        # Step 1F.2.3a. Last, and ``DANGER``: deleting is permanent and is the
        # action a client should keep furthest from the forward path. Offered
        # only when the lifecycle service says this actor may - which for a
        # member means the item is theirs *and* has never been produced, for
        # anybody means it has not been published, and in both cases is the
        # write's own predicate rather than a copy of it.
        if self._lifecycle is not None and await self._lifecycle.may_delete(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.DELETE_CONTENT, emphasis=PrActionEmphasis.DANGER
                )
            )

        # Stable, so within one emphasis the order above survives.
        return tuple(sorted(actions, key=lambda action: _EMPHASIS_ORDER[action.emphasis]))

    async def _production_actions(
        self, actor: Actor, content: PrContentItem
    ) -> list[PrAvailableAction]:
        """Claim, assign and submit, when the production service allows them.

        Three questions rather than one because they are genuinely independent:
        a manager at an unclaimed item may assign *and* claim, an assigned
        producer may submit and their lead may still reassign, and each is a
        different route. Asked of
        :class:`~meobot.application.pr_production_service.PrProductionService`,
        which is what the writes check.

        ``SUBMIT_PRODUCTION`` is ``PRIMARY`` and ``CLAIM_PRODUCTION`` is too:
        whichever of them applies is the forward move at ``PRODUCTION``, and they
        never both apply - claiming needs the producer unset, submitting needs it
        set.
        """
        if self._production is None:
            return []
        actions: list[PrAvailableAction] = []
        if await self._production.may_claim(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.CLAIM_PRODUCTION, emphasis=PrActionEmphasis.PRIMARY
                )
            )
        if await self._production.may_start(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.START_PRODUCTION, emphasis=PrActionEmphasis.PRIMARY
                )
            )
        if await self._production.may_submit(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.SUBMIT_PRODUCTION, emphasis=PrActionEmphasis.PRIMARY
                )
            )
        if await self._production.may_assign(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.ASSIGN_PRODUCER, emphasis=PrActionEmphasis.SECONDARY
                )
            )
        # Step 1F.2.3f. No stage condition, deliberately: a piece is re-cut
        # *after* it is finished, which is the whole feature, so the offer has to
        # survive ``PUBLISHED``, ``MEASURED`` and ``ARCHIVED``. The predicate is
        # the write's own - ``PrProductionService.may_manage_production_output``
        # - plus the capability the write requires first, so the control appears
        # on precisely the rows the POST accepts.
        if await self._capabilities.allows(
            actor, PrCapability.PR_PRODUCTION_EXECUTE
        ) and await self._production.may_manage_production_output(actor, content):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.MANAGE_CONTENT_DERIVATIVES,
                    emphasis=PrActionEmphasis.SECONDARY,
                )
            )
        # Step 1F.2.3f.2. "There is at least one handed-in file you could
        # correct." Which one is a per-row fact - a published output may not be
        # touched - and is answered on the row's own ``can_correct``.
        if content.workflow_stage is not PrWorkflowStage.ARCHIVED and await self._may_correct_any(
            actor, content
        ):
            actions.append(
                PrAvailableAction(
                    kind=PrActionKind.CORRECT_PRODUCTION_OUTPUT,
                    emphasis=PrActionEmphasis.SECONDARY,
                )
            )
        return actions

    async def _may_correct_any(self, actor: Actor, content: PrContentItem) -> bool:
        """Could this actor correct any of this item's handed-in files?

        Asked of the production service per submission rather than reasoned about
        here, so "the submitter, or production management" has one definition.
        """
        if self._production is None:
            return False
        for submission in await self._production.list_submissions(content.id):
            if await self._production.may_correct_submission(actor, content, submission):
                return True
        return False

    async def _may_decide(
        self,
        *,
        content_id: uuid.UUID,
        gate: PrApprovalStage,
        decision: PrApprovalDecision,
        version_no: int,
    ) -> bool:
        """The per-decision rules, for the one decision that has any.

        Only the Head ``APPROVED`` is narrower than its gate, and only by a fact
        about the draft: a team-lead approval of this version has to be on file.
        Requesting revision and rejecting are not sign-offs and carry no such
        prerequisite - a reviewer who cannot stop bad work is worse than one who
        signs in an unexpected order.

        Takes no actor. Until Step 1F.2.2 it did, because the rule was about
        *who* the actor was; now the only person-shaped question at this gate is
        the capability, which :meth:`for_content` asks once for every decision
        rather than per decision.
        """
        if gate is not PrApprovalStage.HEAD_REVIEW or decision is not PrApprovalDecision.APPROVED:
            return True
        return await self._approvals.head_approval_permitted(
            content_id, version_reviewed=version_no
        )


__all__: list[str] = [
    "PrActionEmphasis",
    "PrActionKind",
    "PrAvailableAction",
    "PrAvailableActionService",
]
