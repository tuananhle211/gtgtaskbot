"""Approving many items at one gate, in one operation, or approving none of them.

Step 1F.2.8. A reviewer with eighty finished cuts waiting at *Duyệt nội bộ* was
opening eighty pages to press eighty buttons, and the panel had no way to say
"these, together". This service is that operation - and it is deliberately the
*only* thing this step adds to the write side.

What it is not
--------------

**Not a generic bulk workflow mechanism.** It records one
:class:`~meobot.domain.pr.models.PrApprovalDecision` - ``APPROVED`` - at one of
the three human review gates, and it can do nothing else. There is no bulk
transition, no bulk cancel, no bulk assignment and no bulk rejection: sending
work back is a judgement about one piece and carries a reason about that piece,
and a screen that made it a checkbox would be inviting exactly the sweep this
module exists to keep deliberate.

**Not a second authorization rule.** Every item is checked with
:meth:`~meobot.application.pr_capability_service.PrCapabilityService.require_approval`
- the same call, with the same arguments, that
:meth:`~meobot.application.pr_approval_service.PrApprovalService.record_decision`
makes for a single item - and every approval is then *written by that method*.
Nothing here re-implements a check, a stage move, a notification or an audit
entry; this service decides **which** items are in the batch and refuses the
whole batch when any of them is wrong.

All or nothing, and what that costs
-----------------------------------

The promise the panel makes is that "Duyệt 12 nội dung" either approves twelve
or approves zero, and it is kept structurally rather than by care:

#. **one transaction.** The API dependency opens one per request and commits it
   only when the route returns - see :func:`~meobot.api.deps.get_session`. Any
   refusal raised anywhere below rolls the whole thing back, so a partial commit
   is not a case that has to be handled;
#. **every row locked before anything is written.** The locks are taken in a
   deterministic order (ascending by id as text), which is what stops two
   reviewers bulk-approving two overlapping batches from deadlocking: both
   queue for the same first contended row instead of each holding what the other
   wants;
#. **three passes, in this order** - lock, then validate the batch, then
   authorize it, and only then write. A validation that ran interleaved with the
   writes could have recorded four approvals before discovering the fifth item
   had moved.

Staleness, and why the gate check is the whole of it
----------------------------------------------------

"Has this item changed since I ticked it?" is answered by two questions asked
**under the lock**: *is it still standing at this gate*, and *may this actor
still decide it*. That is not a subset of the ways an item can change - it is
all of them that matter, because at a review gate the item is otherwise frozen:

* another reviewer approving, rejecting or requesting revision moves the
  ``workflow_stage``, so the gate check catches it;
* a revision cannot happen at all - ``revise_content`` refuses outside
  :data:`~meobot.domain.pr.workflow.EDITABLE_STAGES`, so the text under an item
  at a gate cannot change without the item first leaving the gate;
* a grant expiring, being revoked, or the item's channels changing under it is
  caught by re-running the scope check against the row as it is now.

So no version fingerprint is sent from the browser and none is needed. The
version each decision is recorded against is read here, inside the lock, from
:meth:`~meobot.application.pr_content_service.PrContentService.require_current_version`
- the same reading ``record_decision`` then re-checks it against.

The batch id, and why there is no migration
-------------------------------------------

Each item keeps **its own** ``pr_approval_events`` row and **its own**
``pr.approval.recorded`` audit entry; nothing is collapsed. On top of that,
every audit entry produced by one bulk action carries the same ``batch_id`` in
its JSON ``after`` payload, and one further ``pr.approval.batch_recorded`` row
records the batch as a whole. ``audit_logs.after_data`` is already JSON and
``audit_logs.action`` is already a plain ``VARCHAR``, so correlating a batch
needed no column, no enum type and no migration - which is the bar requirement
27 sets and the reason none was written.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_approval_service import PrApprovalService, RecordApprovalCommand
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrContentItem
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrBulkApprovalStaleError,
    PrBulkApprovalUnauthorizedError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import PrApprovalDecision, PrApprovalStage, PrWorkflowStage
from meobot.domain.pr.policy import APPROVAL_CAPABILITIES, BULK_APPROVAL_MAX_ITEMS
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BulkApproveCommand:
    """One reviewer approving several items standing at one gate.

    ``content_ids`` is the **frozen** target set, always explicit. A "select all
    at this step" in the panel is resolved to ids by
    :meth:`~meobot.application.pr_query_service.PrQueryService.approvable_selection`
    *before* the confirmation dialog opens, so an item created between the
    dialog and the button cannot join the batch - see
    ``docs/pr/STEP_1F28_BULK_APPROVAL_AND_CONFIRMATION.md``.
    """

    gate: PrApprovalStage
    content_ids: Sequence[uuid.UUID]
    reviewer_user_id: uuid.UUID
    #: Recorded on every event in the batch. One comment for the batch rather
    #: than one per item: a bulk approval is one act, and a per-item note would
    #: be a form nobody could fill in for eighty rows.
    comment: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BulkApprovedItem:
    """One item this batch approved, and where the approval sent it."""

    content_id: uuid.UUID
    code: str
    title: str
    new_stage: PrWorkflowStage


@dataclass(frozen=True, slots=True)
class BulkApprovalOutcome:
    """What a successful batch did. Only ever returned when *all* of it worked."""

    batch_id: uuid.UUID
    gate: PrApprovalStage
    #: In **lock order** - ascending by id as text - rather than in the order the
    #: request listed them. That is the order the approvals were actually
    #: written in, and reporting a different one would describe a sequence that
    #: did not happen. Nothing downstream depends on the ordering: the panel
    #: shows a count.
    approved: tuple[BulkApprovedItem, ...]
    #: How many ids the request carried, before de-duplication.
    requested: int
    #: How many of them were repeats of an earlier id. See
    #: :meth:`PrBulkApprovalService.approve` on why they are dropped rather than
    #: refused.
    duplicates_removed: int


@dataclass(frozen=True, slots=True)
class _Rejection:
    """One item's reason for refusing the whole batch."""

    content_id: uuid.UUID
    reason: str
    code: str | None = None
    current_stage: PrWorkflowStage | None = None

    def as_detail(self) -> dict[str, object]:
        return {
            "content_id": str(self.content_id),
            "code": self.code,
            "current_stage": self.current_stage.value if self.current_stage else None,
            "reason": self.reason,
        }


class PrBulkApprovalService:
    """Approves a whole batch at one gate, or refuses the whole batch.

    Args:
        session: Unit of work. The caller owns the transaction boundary, and
            that ownership is what makes all-or-nothing structural.
        audit: Event writer sharing that session. Used for the one batch-level
            row; the per-item rows are written by :class:`PrApprovalService`.
        approvals: **The** approval writer. Every decision this service records
            goes through it, so a bulk approval and a single approval are the
            same write with the same checks, notifications and history.
        capabilities: The one authorization service. Asked the unscoped question
            once, and the scoped one per item, exactly as the single path does.
        content: Reader for the current draft of each item.
        workflow: Row locks. Nothing here moves a stage: the approval does.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        approvals: PrApprovalService,
        capabilities: PrCapabilityService,
        content: PrContentService,
        workflow: PrContentWorkflowService,
    ) -> None:
        self._session = session
        self._audit = audit
        self._approvals = approvals
        self._capabilities = capabilities
        self._content = content
        self._workflow = workflow

    async def approve(
        self, *, actor: Actor, request_id: uuid.UUID, command: BulkApproveCommand
    ) -> BulkApprovalOutcome:
        """Approve every item in the batch, or none of them.

        **Duplicate ids are de-duplicated before validation**, keeping the first
        occurrence, and the count of what was dropped is reported on the
        outcome. Refusing them instead was the alternative and it is the worse
        one: a repeat is a client bug that changes nothing about the operation's
        meaning - the same item cannot be approved twice in one batch either
        way - and turning it into a refusal would fail a batch whose intent is
        unambiguous. Silently *approving* twice is the outcome neither choice
        allows, and de-duplicating is what makes that structural rather than
        something the loop below has to remember.

        Raises:
            PrValidationError: Empty batch, or more ids than
                :data:`~meobot.domain.pr.policy.BULK_APPROVAL_MAX_ITEMS`.
            PrPermissionDeniedError: The actor holds no grant at this gate at
                all - the unscoped refusal, raised before any row is read so a
                batch of invented ids tells an ungranted caller nothing.
            PrBulkApprovalStaleError: At least one item has moved, is missing,
                or is not standing at a review gate. Nothing was approved.
            PrBulkApprovalUnauthorizedError: At least one item is outside this
                actor's grant scope. Nothing was approved.

        Any *other* refusal raised by the write below - a missing AI verdict, a
        Head approval with no Team Lead approval behind it - also approves
        nothing, because the caller's transaction rolls back. Those are
        structurally unreachable at a gate rather than cases this method
        screens for, and the point is that atomicity does not depend on the
        three passes above being exhaustive.
        """
        requested = tuple(command.content_ids)
        unique = _deduplicate(requested)
        if not unique:
            raise PrValidationError(
                "Chưa chọn nội dung nào để duyệt.",
                details={"reason": "empty_batch", "gate": command.gate.value},
            )
        if len(unique) > BULK_APPROVAL_MAX_ITEMS:
            raise PrValidationError(
                f"Một lần duyệt hàng loạt chỉ nhận tối đa {BULK_APPROVAL_MAX_ITEMS} nội dung.",
                details={
                    "reason": "batch_too_large",
                    "gate": command.gate.value,
                    "requested": len(unique),
                    "max_items": BULK_APPROVAL_MAX_ITEMS,
                },
            )

        # The unscoped question, asked once and first - *could this person ever
        # decide at this gate*. Identical in placement and meaning to the one
        # ``record_decision`` opens with, and it is what stops a caller with no
        # grant from using a batch of ids to probe which of them exist.
        await self._capabilities.require(actor, APPROVAL_CAPABILITIES[command.gate])

        locked = self._require_batch_at_gate(await self._lock_all(unique), gate=command.gate)
        await self._require_authorized(actor, locked, gate=command.gate)

        batch_id = uuid.uuid4()
        approved: list[BulkApprovedItem] = []
        for content_id in _lock_order(unique):
            content = locked[content_id]
            current = await self._content.require_current_version(content_id)
            outcome = await self._approvals.record_decision(
                actor=actor,
                request_id=request_id,
                command=RecordApprovalCommand(
                    content_id=content_id,
                    reviewer_user_id=command.reviewer_user_id,
                    approval_stage=command.gate,
                    decision=PrApprovalDecision.APPROVED,
                    version_reviewed=current.version_no,
                    comment=command.comment,
                    decided_at=command.decided_at,
                    batch_id=batch_id,
                ),
            )
            approved.append(
                BulkApprovedItem(
                    content_id=content_id,
                    code=content.code,
                    title=content.title,
                    new_stage=outcome.new_stage,
                )
            )

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_APPROVAL_BATCH_RECORDED,
            entity_type="pr_approval_batch",
            entity_id=batch_id,
            after={
                "batch_id": str(batch_id),
                "gate": command.gate.value,
                "decision": PrApprovalDecision.APPROVED.value,
                "approved_count": len(approved),
                "requested_count": len(requested),
                "duplicates_removed": len(requested) - len(unique),
                "reviewer_user_id": str(command.reviewer_user_id),
                # The codes rather than only the ids: this row is what somebody
                # reads when they ask "what did that batch do", and a list of
                # UUIDs is not an answer to that question.
                "content_codes": [item.code for item in approved],
                "content_ids": [str(item.content_id) for item in approved],
            },
        )
        logger.info(
            "pr_bulk_approval_recorded",
            extra={
                "pr_batch_id": str(batch_id),
                "approval_stage": command.gate.value,
                "approved_count": len(approved),
            },
        )
        return BulkApprovalOutcome(
            batch_id=batch_id,
            gate=command.gate,
            approved=tuple(approved),
            requested=len(requested),
            duplicates_removed=len(requested) - len(unique),
        )

    # --- The three passes --------------------------------------------------
    async def _lock_all(
        self, content_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, PrContentItem | None]:
        """Take every row lock up front, in a deterministic order.

        Ascending by id-as-text, which two concurrent batches will agree on
        whatever order their clients sent - so overlapping batches serialise on
        their first shared row rather than deadlocking on each other's second.

        A missing row is ``None`` rather than a raise: the batch is refused as a
        whole and the refusal has to be able to name **every** item that caused
        it, so this pass collects and :meth:`_require_batch_at_gate` decides.
        """
        locked: dict[uuid.UUID, PrContentItem | None] = {}
        for content_id in _lock_order(content_ids):
            try:
                locked[content_id] = await self._workflow.lock(content_id)
            except PrNotFoundError:
                locked[content_id] = None
        return locked

    def _require_batch_at_gate(
        self, locked: dict[uuid.UUID, PrContentItem | None], *, gate: PrApprovalStage
    ) -> dict[uuid.UUID, PrContentItem]:
        """Every item exists and is standing at exactly this gate, or nothing is.

        Returns the same mapping with the ``None``s gone, which is how the write
        loop knows every row is there without asserting it: this method either
        raised or every value is a row.

        The same-step rule (requirement 2) and the staleness rule (requirement 8)
        are one check, because they are one question: an item at a *different*
        gate is indistinguishable from an item that has moved to one, and both
        answers are "not this batch".
        """
        rejections = [
            rejection
            for content_id, content in locked.items()
            if (rejection := _gate_rejection(content_id, content, gate=gate)) is not None
        ]
        if not rejections:
            return {
                content_id: content for content_id, content in locked.items() if content is not None
            }
        # The count is in the sentence because it is the first thing somebody
        # wants to know - "2 nội dung đã thay đổi" tells them whether to look or
        # to re-select, and "có nội dung đã thay đổi" tells them neither.
        raise PrBulkApprovalStaleError(
            f"Không thể duyệt vì {len(rejections)} nội dung đã thay đổi. "
            "Không có nội dung nào được duyệt.",
            details={
                "reason": rejections[0].reason,
                "gate": gate.value,
                "approved": 0,
                "affected": [rejection.as_detail() for rejection in rejections],
            },
        )

    async def _require_authorized(
        self,
        actor: Actor,
        locked: dict[uuid.UUID, PrContentItem],
        *,
        gate: PrApprovalStage,
    ) -> None:
        """The scoped rule, per item, before a single approval is written.

        ``require_approval(actor, content, gate)`` - the identical call the
        single-item write makes, against the row as it is now rather than as the
        board drew it. ``record_decision`` will make it again for each item;
        this pass exists so the *first* refusal happens before any approval has
        been recorded, which is what makes the batch atomic in meaning as well
        as in transaction.
        """
        rejections: list[_Rejection] = []
        for content_id, content in locked.items():
            try:
                await self._capabilities.require_approval(actor, content, gate)
            except PrPermissionDeniedError as error:
                detail = error.details.get("reason")
                rejections.append(
                    _Rejection(
                        content_id=content_id,
                        reason=detail if isinstance(detail, str) else "not_permitted",
                        code=content.code,
                        current_stage=content.workflow_stage,
                    )
                )
        if not rejections:
            return
        # The **first** rejection's reason, in lock order - the same rule the
        # gate check follows, so a batch with two different refusals reports the
        # same summary whichever order the client happened to send its ids in.
        raise PrBulkApprovalUnauthorizedError(
            "Không thể duyệt vì quyền của bạn không còn áp dụng cho một hoặc nhiều nội dung. "
            "Không có nội dung nào được duyệt.",
            details={
                "reason": rejections[0].reason,
                "gate": gate.value,
                "approved": 0,
                "affected": [rejection.as_detail() for rejection in rejections],
            },
        )


def _deduplicate(content_ids: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """The ids, first occurrence kept, order preserved. See :meth:`approve`."""
    seen: dict[uuid.UUID, None] = {}
    for content_id in content_ids:
        seen.setdefault(content_id, None)
    return tuple(seen)


def _lock_order(content_ids: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """The one order every batch takes its locks in. See :meth:`_lock_all`."""
    return tuple(sorted(set(content_ids), key=str))


def _gate_rejection(
    content_id: uuid.UUID, content: PrContentItem | None, *, gate: PrApprovalStage
) -> _Rejection | None:
    """Why this item is not part of this batch, or ``None`` when it is."""
    if content is None:
        return _Rejection(content_id=content_id, reason="missing")
    standing = STAGE_APPROVAL_GATES.get(content.workflow_stage)
    if standing is None:
        return _Rejection(
            content_id=content_id,
            reason="not_at_a_gate",
            code=content.code,
            current_stage=content.workflow_stage,
        )
    if standing is not gate:
        return _Rejection(
            content_id=content_id,
            reason="moved",
            code=content.code,
            current_stage=content.workflow_stage,
        )
    return None


__all__: list[str] = [
    "BulkApprovalOutcome",
    "BulkApproveCommand",
    "BulkApprovedItem",
    "PrBulkApprovalService",
]
