"""Who produces a piece, and the file they hand over when they are done.

Step 1F.2.3. Three commands and their read models. Everything here happens on
the caller's transaction, under the content row's lock, exactly as the rest of
the module does - see :mod:`meobot.application.pr_support`.

Producer is not owner, and not an assignee
-------------------------------------------

``pr_content_items.producer_user_id`` is a new relationship rather than a reuse
of an existing one, and the alternatives were all considered:

* ``owner_user_id`` is *"who is accountable for this now"* from brief to
  measurement. Overwriting it at ``PRODUCTION`` would hand the whole piece to
  the editor and take "Của tôi" away from the person who wrote it;
* ``created_by_user_id`` never moves, by design;
* a ``pr_task_assignments`` row is how *work items* are handed out, and a task
  is optional - most content in this module has none. Making production depend
  on somebody first creating an EDIT task would put a bookkeeping step between
  approval and work, and the state "in production, nobody assigned" would then
  be indistinguishable from "nobody has made the task yet".

So the producer is its own nullable column, and "Người phụ trách" keeps meaning
what it meant. The panel shows both, separately.

The handoff, and where it happens
----------------------------------

Step 1F.2.3b moved it earlier, and the move is the point. A Head approval ends at
``APPROVED`` and stops there: the script is signed off and nobody has been handed
the edit. The handoff - assign, or claim - happens **at ``APPROVED``**, and only
then does somebody explicitly start production:

```
HEAD_REVIEW --approve--> APPROVED  (chờ nhận sản xuất)
                            |
                assign / claim  ->  APPROVED  (sẵn sàng sản xuất)
                            |
                    start production
                            v
                        PRODUCTION  (đang sản xuất)
```

Before this, assignment was only possible once the item was already at
``PRODUCTION``, so the board showed work in progress that nobody had picked up
and the only way to give somebody the job was to first pretend they had started
it. ``producer_user_id`` is still nullable and ``APPROVED`` with nobody holding
it is still a valid, expected state - it is now a *visible* one, rendered as
"Chờ nhận sản xuất", which is the state the whole claim flow exists to resolve.

**Holding a production is not doing it.** Neither assignment nor a claim moves
the stage, and neither stamps ``production_started_at``; only
:meth:`PrProductionService.start_production` does, and that invariant is what
the permanent-delete rule rests on.

Claiming, and the two people who press at once
-----------------------------------------------

:meth:`PrProductionService.claim_production` is the one place in this module
where two people genuinely race for the same row, so it does not read-then-write.
The write is a **conditional update** - ``SET producer_user_id = :me WHERE id =
:id AND producer_user_id IS NULL`` - and the winner is whoever the database says
changed a row. The loser gets
:class:`~meobot.domain.pr.errors.PrProductionClaimConflictError` naming who
holds it, not a raw integrity error and not a silent overwrite.

The row lock is taken as well, and is not what makes this safe: on PostgreSQL it
serialises the two transactions, and on the offline SQLite suite
:func:`~meobot.application.pr_support.lock_row` is a plain ``get``. The
conditional update is correct on both, which is the point of writing it that way
rather than trusting the lock.

Eligibility, and what it is not
--------------------------------

Who may hold a production is ``PR_PRODUCTION_EXECUTE``, and nothing else. In
particular it is **not** the channel-assignment relation: that decides whose
queue an unclaimed item shows up in - a display question, answered in
:mod:`meobot.application.pr_content_query` - and Step 1F.2.2 established that
display scope and authorization are separate concerns that must not be collapsed.
Somebody who is handed a link to a piece outside their usual channels may still
take it; they simply were not shown it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import CursorResult, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_service import PrContentService
from meobot.application.pr_notifications import PrNotificationService
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService
from meobot.core.logging import get_logger
from meobot.db.models.pr import PrContentItem
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrProductionClaimConflictError,
    PrValidationError,
    PrWorkflowTransitionError,
)
from meobot.domain.pr.models import PrProductionArtifactType, PrWorkflowStage
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.production import HANDOFF_STAGES, normalize_artifact
from meobot.domain.pr.workflow import PrTransitionTrigger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SubmitProductionCommand:
    """One handover of a finished file.

    ``label`` and ``note`` are optional and free prose. ``location`` is not
    optional and is not free: it goes through
    :func:`~meobot.domain.pr.production.normalize_artifact`, which is what makes
    "gửi duyệt nội bộ" impossible to press with nothing attached.
    """

    content_id: uuid.UUID
    artifact_type: PrProductionArtifactType
    location: str
    label: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class CorrectSubmissionCommand:
    """A correction to where a handed-in production file lives. Step 1F.2.3f.2.

    ``None`` means "leave alone" on every field.

    **There is no ``submission_no``, ``content_version_id``, ``producer_user_id``
    or ``submitted_by_user_id``.** Those are the row's identity and its place in
    the append-only sequence; leaving them out of the command is what makes
    changing one unrepresentable rather than merely refused.
    """

    submission_id: uuid.UUID
    artifact_type: PrProductionArtifactType | None = None
    location: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ProductionState:
    """What a screen needs to render the production half of a content item.

    Submissions are newest first and **all** of them are here, not just the
    latest: an internal reviewer looking at the second cut should be able to see
    that there was a first one and what was said about it. That history is the
    reason the submissions are a table.
    """

    content: PrContentItem
    submissions: tuple[PrProductionSubmission, ...] = ()

    @property
    def producer_user_id(self) -> uuid.UUID | None:
        return self.content.producer_user_id

    @property
    def latest(self) -> PrProductionSubmission | None:
        """The cut an internal review would be about right now."""
        return self.submissions[0] if self.submissions else None


class PrProductionService:
    """Producer assignment and production submissions.

    Args:
        session: Unit of work. The caller owns the transaction boundary; nothing
            here commits, so the submission row, the audit event and the stage
            change are one unit of work or none of them.
        audit: Event writer sharing that session.
        content: Reader for the current draft. A submission names the script it
            was cut from, and this is where that comes from.
        workflow: The only writer of ``workflow_stage``. ``PRODUCTION ->
            INTERNAL_REVIEW`` goes through :meth:`PrContentWorkflowService.apply`
            like every other transition, after the row is written.
        capabilities: Resolves PR capabilities against roles and grants.
        notifications: Step 1F.2.3b. Told when somebody is given a production, so
            they learn about it without watching the board. Optional; absent, the
            assignment still works and nobody is told.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        content: PrContentService,
        workflow: PrContentWorkflowService,
        capabilities: PrCapabilityService,
        notifications: PrNotificationService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._content = content
        self._workflow = workflow
        self._capabilities = capabilities
        self._notifications = notifications

    # --- Assignment -------------------------------------------------------
    async def assign_producer(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        content_id: uuid.UUID,
        producer_user_id: uuid.UUID | None,
    ) -> PrContentItem:
        """Say who produces this piece, or take it off them.

        ``producer_user_id=None`` un-assigns, which is how a manager hands a
        piece back to the pool after somebody goes on leave. It is the same
        command because it is the same decision - *who is producing this* - and
        two endpoints would be two places to check the same rules.

        Reassignment is allowed while the item is at ``PRODUCTION``, including
        over somebody who already holds it: that is what "Đổi người sản xuất"
        means, and it needs ``PR_PRODUCTION_ASSIGN``, which no ``EMPLOYEE``
        holds. A member cannot reach this path at all, which is what stops one
        producer taking another's work.

        Raises:
            PrPermissionDeniedError: The actor may not assign production.
            PrNotFoundError: No such content, or no such user.
            PrValidationError: The named person is not an active user, or may
                not produce.
            PrWorkflowTransitionError: The item is not at ``PRODUCTION``.
        """
        await self._capabilities.require(actor, PrCapability.PR_PRODUCTION_ASSIGN)
        content = await self._locked_in(content_id, stages=HANDOFF_STAGES)
        if producer_user_id is not None:
            await self._require_eligible_producer(producer_user_id)

        before = content.producer_user_id
        content.producer_user_id = producer_user_id
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PRODUCTION_ASSIGNED,
            entity_type="pr_content_item",
            entity_id=content.id,
            before={"producer_user_id": str(before) if before else None},
            after={
                "producer_user_id": str(producer_user_id) if producer_user_id else None,
                "content_code": content.code,
            },
        )
        if self._notifications is not None and producer_user_id is not None:
            # Only on assignment, and only to the person given the work. Clearing
            # a producer tells nobody: "you are no longer producing this" is a
            # conversation, not a notification.
            await self._notifications.on_producer_assigned(
                content=content, producer_user_id=producer_user_id, actor=actor
            )

        logger.info(
            "pr_production_assigned",
            extra={
                "pr_content_id": str(content.id),
                "producer_user_id": str(producer_user_id) if producer_user_id else None,
            },
        )
        return content

    async def claim_production(
        self, *, actor: Actor, request_id: uuid.UUID, content_id: uuid.UUID
    ) -> PrContentItem:
        """Take an unclaimed production for yourself.

        The concurrency-safe half of this module - see the module docstring. The
        conditional update is the decision: if it changes no row, somebody else
        got there first, and this refuses with their name rather than overwriting
        them.

        Raises:
            PrPermissionDeniedError: The actor may not produce.
            PrNotFoundError: No such content.
            PrWorkflowTransitionError: The item is not at ``PRODUCTION``.
            PrProductionClaimConflictError: Somebody already holds it.
        """
        await self._capabilities.require(actor, PrCapability.PR_PRODUCTION_EXECUTE)
        if actor.user_id is None:
            # A principal with no ``users`` row cannot be a producer: the column
            # is a foreign key, and a claim nobody can be attributed to is not a
            # claim. Refused here rather than at the database.
            raise PrPermissionDeniedError(
                "This principal has no user record and cannot hold production",
                details={"content_id": str(content_id)},
            )

        content = await self._locked_in(content_id, stages=HANDOFF_STAGES)
        # Typed as ``CursorResult`` for ``rowcount`` - the same annotation
        # ``QuotaService`` uses for its conditional update, and for the same
        # reason: the count of rows this statement changed *is* the decision.
        claimed: CursorResult[Any] = await self._session.execute(  # type: ignore[assignment]
            update(PrContentItem)
            .where(PrContentItem.id == content.id, PrContentItem.producer_user_id.is_(None))
            .values(producer_user_id=actor.user_id)
        )
        if claimed.rowcount == 0:
            # Re-read rather than reporting the stale row: the whole point is
            # that somebody else wrote it, so the value this transaction loaded
            # is the one that is wrong.
            await self._session.refresh(content)
            raise PrProductionClaimConflictError(
                "This production has already been claimed",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "producer_user_id": (
                        str(content.producer_user_id) if content.producer_user_id else None
                    ),
                },
            )
        await self._session.refresh(content)

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PRODUCTION_CLAIMED,
            entity_type="pr_content_item",
            entity_id=content.id,
            after={"producer_user_id": str(actor.user_id), "content_code": content.code},
        )
        logger.info(
            "pr_production_claimed",
            extra={"pr_content_id": str(content.id), "producer_user_id": str(actor.user_id)},
        )
        return content

    async def start_production(
        self, *, actor: Actor, request_id: uuid.UUID, content_id: uuid.UUID
    ) -> PrContentItem:
        """Begin production: ``APPROVED -> PRODUCTION``.

        Step 1F.2.3b, and the only thing in this module that stamps
        ``production_started_at``. Separating it from the handoff is what makes
        the board honest - a piece nobody has started is not "đang sản xuất" -
        and it is what makes the delete rule meaningful, because that stamp is
        irreversible and now means what it says.

        **Normally the producer starts their own work.** A manager may start it
        too, because they hold ``PR_PRODUCTION_ASSIGN`` and could have assigned
        it to themselves a second earlier; the transition event records who
        actually did it. An unrelated member cannot, even holding
        ``PR_PRODUCTION_EXECUTE``: the piece is not theirs.

        Deliberately **not undoable** - see
        :class:`~meobot.application.pr_undo_service.PrWorkflowUndoService`. Once
        the stamp is set the content has been produced for the rest of its life,
        and a button that quietly un-stamped it would hand a member back a delete
        right the rule spent a whole step making permanent.

        Raises:
            PrPermissionDeniedError: The actor may not produce, or holds neither
                this production nor the right to assign it.
            PrNotFoundError: No such content.
            PrWorkflowTransitionError: Not at ``APPROVED``, or nobody holds it.
        """
        await self._capabilities.require(actor, PrCapability.PR_PRODUCTION_EXECUTE)
        content = await self._locked_in(content_id, stages=frozenset({PrWorkflowStage.APPROVED}))
        await self._require_may_act_as_producer(actor, content, reason="not_the_producer")

        # ``_require_producer`` inside the workflow refuses a producerless start,
        # so this call is the check as well as the write - one authority, asked
        # once, rather than a condition repeated here and there.
        await self._workflow.request_transition(
            actor=actor,
            request_id=request_id,
            content_id=content.id,
            target=PrWorkflowStage.PRODUCTION,
            note="production_started",
        )
        logger.info(
            "pr_production_started",
            extra={
                "pr_content_id": str(content.id),
                "producer_user_id": (
                    str(content.producer_user_id) if content.producer_user_id else None
                ),
            },
        )
        return content

    # --- The handover -----------------------------------------------------
    async def submit_production(
        self, *, actor: Actor, request_id: uuid.UUID, command: SubmitProductionCommand
    ) -> PrProductionSubmission:
        """Record a finished cut and send the item to internal review.

        One transaction, in this order, and the order is the rule: validate,
        write the submission, then move the stage through
        :meth:`PrContentWorkflowService.apply`. Nothing commits in between, so
        ``INTERNAL_REVIEW`` with no submission behind it is not a state this
        module can produce - which is what lets the internal reviewer's screen
        assume there is a file to watch.

        **Only the producer submits.** An unrelated member with the capability
        may not hand in somebody else's work; a manager may, because they hold
        ``PR_PRODUCTION_ASSIGN`` and could have assigned it to themselves a
        moment earlier - and the submission records both people, so who actually
        pressed the button is never lost.

        Raises:
            PrPermissionDeniedError: The actor may not produce, or is not the
                producer and does not manage production.
            PrNotFoundError: No such content, or it has no draft.
            PrWorkflowTransitionError: The item is not at ``PRODUCTION``, or has
                no producer yet.
            PrValidationError: The artifact reference is missing or unusable.
        """
        await self._capabilities.require(actor, PrCapability.PR_PRODUCTION_EXECUTE)
        content = await self._locked_in(
            command.content_id, stages=frozenset({PrWorkflowStage.PRODUCTION})
        )

        producer_id = content.producer_user_id
        if producer_id is None:
            raise PrWorkflowTransitionError(
                "This content has no producer, so there is nothing to submit",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "reason": "no_producer",
                },
            )
        await self._require_may_act_as_producer(actor, content, reason="not_the_producer")

        # Validated before anything is written, so a bad paste costs nothing and
        # leaves no half-submission behind.
        location = normalize_artifact(command.artifact_type, command.location)
        version = await self._content.require_current_version(content.id)

        submission = PrProductionSubmission(
            content_id=content.id,
            content_version_id=version.id,
            submission_no=await self._next_submission_no(content.id),
            producer_user_id=producer_id,
            submitted_by_user_id=self._submitter(actor),
            artifact_type=command.artifact_type,
            location=location,
            label=_clean(command.label),
            note=_clean(command.note),
        )
        self._session.add(submission)
        await self._session.flush()

        await self._workflow.apply(
            actor=actor,
            request_id=request_id,
            content=content,
            target=PrWorkflowStage.INTERNAL_REVIEW,
            trigger=PrTransitionTrigger.MANUAL,
            reason=f"production_submitted:{submission.submission_no}",
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PRODUCTION_SUBMITTED,
            entity_type="pr_production_submission",
            entity_id=submission.id,
            after={
                "content_id": str(content.id),
                "content_code": content.code,
                "submission_no": submission.submission_no,
                "artifact_type": submission.artifact_type.value,
                "producer_user_id": str(producer_id),
                "content_version_id": str(version.id),
            },
        )
        logger.info(
            "pr_production_submitted",
            extra={
                "pr_content_id": str(content.id),
                "submission_no": submission.submission_no,
                "artifact_type": submission.artifact_type.value,
            },
        )
        return submission

    # --- Reading ----------------------------------------------------------
    async def correct_submission(
        self, *, actor: Actor, request_id: uuid.UUID, command: CorrectSubmissionCommand
    ) -> PrProductionSubmission:
        """Fix where a handed-in file lives. Step 1F.2.3f.2.

        The **one** operation that writes to ``pr_production_submissions`` after
        the row is created, and it is narrow on purpose. That table is
        append-only by design - the file a reviewer looked at must still exist,
        unchanged, after they have decided about it - so this does not relax the
        design, it carves one audited exception out of it for the thing people
        actually get wrong: a mistyped path, or a link that was right until
        somebody moved the folder.

        What it may change: ``artifact_type``, ``location`` and ``note``. What it
        may not, and has no field for: ``submission_no``, ``content_version_id``,
        ``producer_user_id`` and ``submitted_by_user_id``. Those are the row's
        identity and its place in the sequence, and the whole point of the table
        is that they do not move.

        **Who.** The person who handed it in - ``submitted_by_user_id`` - or
        whoever manages this piece's production
        (:meth:`may_manage_production_output`). A member who submitted output A
        may correct output A and nothing else: not somebody else's submission,
        not the producer assignment, not the stage.

        **When.** Never once any publication row references this submission -
        including one marked ``REVERSED``, because that row still records that
        this exact file was used publicly at some point, and rewriting where it
        points would rewrite what the publication says happened. Archived content
        is refused too, matching every other correction in this module's
        neighbours.

        Raises:
            PrPermissionDeniedError: Not the submitter and not production
                management.
            PrNotFoundError: No such submission.
            PrConflictError: A publication references it, or the content is
                archived.
            PrValidationError: The new location is unusable for its type.
        """
        submission = await self._require_submission_row(command.submission_id)
        content = await self._content.require_content(submission.content_id)
        await self._require_may_correct(actor, content, submission)

        if content.workflow_stage is PrWorkflowStage.ARCHIVED:
            raise PrConflictError(
                "Archived content's production history is read-only",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "submission_id": str(submission.id),
                    "workflow_stage": content.workflow_stage.value,
                    "reason": "archived",
                },
            )
        if await self._is_published_output(submission.id):
            raise PrConflictError(
                "This output has already been published, so where it points may not be changed",
                details={
                    "content_id": str(content.id),
                    "content_code": content.code,
                    "submission_id": str(submission.id),
                    "reason": "published_output_is_immutable",
                },
            )

        before = _submission_snapshot(submission)
        artifact_type = command.artifact_type or submission.artifact_type
        if command.location is not None or command.artifact_type is not None:
            # Re-validated against the type in force **after** any type change in
            # the same command, so switching a row to ``DRIVE_LINK`` without also
            # changing the location is refused rather than stored - the rule
            # ``update_resource`` already follows.
            submission.location = normalize_artifact(
                artifact_type, command.location or submission.location
            )
            submission.artifact_type = artifact_type
        if command.note is not None:
            submission.note = _clean(command.note)

        await self._session.flush()
        after = _submission_snapshot(submission)
        if before == after:
            return submission

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_PRODUCTION_SUBMISSION_CORRECTED,
            entity_type="pr_production_submission",
            entity_id=submission.id,
            before={key: value for key, value in before.items() if after[key] != value},
            after={
                "content_id": str(content.id),
                "content_code": content.code,
                "submission_no": submission.submission_no,
                **{key: value for key, value in after.items() if before[key] != value},
            },
        )
        logger.info(
            "pr_production_submission_corrected",
            extra={
                "pr_content_id": str(content.id),
                "pr_submission_id": str(submission.id),
                "changed": sorted(key for key in after if after[key] != before[key]),
            },
        )
        return submission

    async def may_correct_submission(
        self, actor: Actor, content: PrContentItem, submission: PrProductionSubmission
    ) -> bool:
        """May this actor correct where **this** handed-in file lives?

        The authorization half of :meth:`correct_submission`, exported so the
        action list offers the control on exactly the rows the write accepts.
        Read-only and never raises; the *history* half - published, archived - is
        checked by the write and surfaced per row.
        """
        if actor.user_id is not None and actor.user_id == submission.submitted_by_user_id:
            return await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_EXECUTE)
        return await self.may_manage_production_output(actor, content)

    async def _require_may_correct(
        self, actor: Actor, content: PrContentItem, submission: PrProductionSubmission
    ) -> None:
        if await self.may_correct_submission(actor, content, submission):
            return
        raise PrPermissionDeniedError(
            "Chỉ người đã nộp file này hoặc người phụ trách sản xuất mới sửa được đường dẫn.",
            details={
                "content_id": str(content.id),
                "content_code": content.code,
                "submission_id": str(submission.id),
                "capability": PrCapability.PR_PRODUCTION_EXECUTE.value,
                "reason": "not_the_submitter",
            },
        )

    async def _require_submission_row(self, submission_id: uuid.UUID) -> PrProductionSubmission:
        found = await self._session.get(PrProductionSubmission, submission_id)
        if found is None:
            raise PrNotFoundError(
                "No production submission with that id",
                details={"submission_id": str(submission_id)},
            )
        return found

    async def _is_published_output(self, submission_id: uuid.UUID) -> bool:
        """Does any publication name this submission as what went out?

        **Every** publication row counts, reversed ones included - the same rule
        the derivative service and the permanent-delete floor use, and for the
        same reason: a reversed publication still records that this exact file
        was posted at some point, and a correction that rewrote its location
        would rewrite what that record says.
        """
        found = await self._session.execute(
            select(exists().where(PrPublication.production_submission_id == submission_id))
        )
        return bool(found.scalar())

    async def state(self, content: PrContentItem) -> ProductionState:
        """The producer and every submission, newest first."""
        return ProductionState(
            content=content, submissions=tuple(await self.list_submissions(content.id))
        )

    async def list_submissions(
        self, content_id: uuid.UUID, *, limit: int = 50
    ) -> Sequence[PrProductionSubmission]:
        """Submissions for one item, newest first.

        Ordered by ``submission_no`` rather than ``created_at``: the number is
        allocated under the content lock and is therefore total, while two
        timestamps can tie.
        """
        result = await self._session.execute(
            select(PrProductionSubmission)
            .where(PrProductionSubmission.content_id == content_id)
            .order_by(PrProductionSubmission.submission_no.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def latest_submission(self, content_id: uuid.UUID) -> PrProductionSubmission | None:
        """The cut an internal review decision is about.

        Read by :class:`~meobot.application.pr_approval_service.PrApprovalService`
        when it files an ``INTERNAL_REVIEW`` decision, so the event names the file
        that was judged rather than leaving it to be inferred from timestamps.
        """
        result = await self._session.execute(
            select(PrProductionSubmission)
            .where(PrProductionSubmission.content_id == content_id)
            .order_by(PrProductionSubmission.submission_no.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def may_claim(self, actor: Actor, content: PrContentItem) -> bool:
        """Whether ``Nhận sản xuất`` would work right now.

        For the action list. The same three conditions the write checks - the
        capability, the stage, and the producer being unset - asked without
        taking a lock, because a screen is not authorization and
        :meth:`claim_production` asks again.
        """
        return (
            content.workflow_stage in HANDOFF_STAGES
            and content.producer_user_id is None
            and actor.user_id is not None
            and await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_EXECUTE)
        )

    async def may_assign(self, actor: Actor, content: PrContentItem) -> bool:
        """Whether this actor may set or change the producer right now.

        Both handoff stages: at ``APPROVED`` this is the assignment that hands
        the job over, and at ``PRODUCTION`` it is the reassignment that moves it
        when somebody goes on leave.
        """
        return content.workflow_stage in HANDOFF_STAGES and await self._capabilities.allows(
            actor, PrCapability.PR_PRODUCTION_ASSIGN
        )

    async def may_start(self, actor: Actor, content: PrContentItem) -> bool:
        """Whether ``Bắt đầu sản xuất`` would work right now.

        Three conditions, and the middle one is the correction: the item is at
        ``APPROVED``, **somebody holds it**, and this actor is that somebody or
        may say who is. Without a producer the action is absent from the list and
        the write refuses - the button is not the rule, and
        :func:`~meobot.application.pr_workflow_service._require_producer` is.
        """
        if (
            content.workflow_stage is not PrWorkflowStage.APPROVED
            or content.producer_user_id is None
            or not await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_EXECUTE)
        ):
            return False
        if actor.user_id == content.producer_user_id:
            return True
        return await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_ASSIGN)

    async def may_submit(self, actor: Actor, content: PrContentItem) -> bool:
        """Whether this actor may hand in the file right now.

        Deliberately does not look at whether they have *typed* a location: the
        action list says the button belongs on screen, and the form says whether
        it is filled in. A list that hid "Gửi duyệt nội bộ" until a field had
        content would be a workflow rule expressed as a disappearing button.
        """
        if (
            content.workflow_stage is not PrWorkflowStage.PRODUCTION
            or content.producer_user_id is None
            or not await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_EXECUTE)
        ):
            return False
        if actor.user_id == content.producer_user_id:
            return True
        return await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_ASSIGN)

    # --- Internals --------------------------------------------------------
    async def _locked_in(
        self, content_id: uuid.UUID, *, stages: frozenset[PrWorkflowStage]
    ) -> PrContentItem:
        """Lock the row and refuse anything outside ``stages``.

        Two callers with two different sets, and the difference is Step
        1F.2.3b's whole correction:

        * **assigning and claiming** take
          :data:`~meobot.domain.pr.production.HANDOFF_STAGES` - ``APPROVED`` and
          ``PRODUCTION``. Handing somebody the job at ``APPROVED`` is the normal
          case, and reassigning during production is the exception that still has
          to work;
        * **starting and submitting** take one stage each, because each is a
          single step in the sequence.

        Outside them the operation is meaningless: assigning a producer to a
        piece at ``READY_TO_PUBLISH`` records a responsibility nobody has, and
        submitting from ``INTERNAL_REVIEW`` would mean re-cutting behind a
        reviewer's back rather than after their decision.

        The lock is also what serialises these commands against a permanent
        deletion and against an undo: whichever transaction takes the row first
        finishes, and the other either finds no content (``PrNotFoundError``) or
        waits and then re-reads a stage that has moved.
        """
        content = await self._workflow.lock(content_id)
        if content.workflow_stage not in stages:
            raise PrWorkflowTransitionError(
                "This content is not at a stage where that is possible",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "expected": sorted(stage.value for stage in stages),
                },
            )
        return content

    async def may_manage_production_output(self, actor: Actor, content: PrContentItem) -> bool:
        """May this actor record produced work against this item?

        Step 1F.2.3f, and the predicate the derivative service authorises
        against - exported here rather than written there, so "who may hand in a
        cut" and "who may record a cutdown of it" cannot drift into two answers.

        The same two positive branches :meth:`_require_may_act_as_producer`
        uses, and no third:

        * **the producer.** Whoever holds the production work on this piece.
          Being the producer *is* the assignment, so no further capability is
          asked of them beyond ``PR_PRODUCTION_EXECUTE``, which the caller
          requires first;
        * **whoever may say who the producer is** - ``PR_PRODUCTION_ASSIGN``.
          Management of production, and the same pairing that already decides
          who may submit on somebody else's behalf.

        What is deliberately *not* here: an unrelated member holding
        ``PR_PRODUCTION_EXECUTE``. Every ``EMPLOYEE`` holds it, so it alone would
        let anybody attach a file to anybody's content. And there is no role
        comparison and no ``OWNER`` bypass - an owner with no grant may not
        record production work, exactly as they may not submit one.

        Unlike :meth:`_require_may_act_as_producer` this tolerates a content item
        with **no producer at all**, which is not an error here: a piece nobody
        was ever assigned can still be re-cut, and the answer is then simply
        "management only". Read-only, and never raises.
        """
        if actor.user_id is not None and actor.user_id == content.producer_user_id:
            return True
        return await self._capabilities.allows(actor, PrCapability.PR_PRODUCTION_ASSIGN)

    async def _require_may_act_as_producer(
        self, actor: Actor, content: PrContentItem, *, reason: str
    ) -> None:
        """The actor is the producer, or may say who the producer is.

        One rule for both acts that operate *on* somebody's production - starting
        it and handing it in - because they have the same answer: it is the
        producer's work, and management may act on it because management decides
        whose work it is. An unrelated member with the capability is refused, and
        the refusal names the reason so a client can say "chỉ người đang nhận sản
        xuất" rather than "bạn không có quyền".
        """
        if content.producer_user_id is None:
            raise PrWorkflowTransitionError(
                "This content has no producer yet",
                details={
                    "content_id": str(content.id),
                    "current": content.workflow_stage.value,
                    "reason": "no_producer",
                },
            )
        if await self.may_manage_production_output(actor, content):
            return
        raise PrPermissionDeniedError(
            "Only the assigned producer may act on this production",
            details={
                "content_id": str(content.id),
                "producer_user_id": str(content.producer_user_id),
                "reason": reason,
            },
        )

    async def _require_eligible_producer(self, user_id: uuid.UUID) -> None:
        """The named person exists, is active, and may hold production.

        Eligibility is the capability and nothing else - see the module
        docstring. Checked through
        :meth:`PrCapabilityService.allows` against a constructed
        :class:`Actor` rather than by reading the role directly, so assignment
        and claiming ask the same question of the same table.
        """
        user = await self._session.get(User, user_id)
        if user is None:
            raise PrNotFoundError(
                "No user with that id to assign as producer", details={"user_id": str(user_id)}
            )
        if not user.active:
            raise PrValidationError(
                "That person's account is not active",
                details={"user_id": str(user_id), "reason": "inactive_user"},
            )
        candidate = Actor(user_id=user.id, full_name=user.full_name, role=user.role)
        if not await self._capabilities.allows(candidate, PrCapability.PR_PRODUCTION_EXECUTE):
            raise PrValidationError(
                "That person may not be assigned production work",
                details={
                    "user_id": str(user_id),
                    "reason": "not_eligible",
                    "capability": PrCapability.PR_PRODUCTION_EXECUTE.value,
                },
            )

    async def _next_submission_no(self, content_id: uuid.UUID) -> int:
        """``latest + 1``, allocated under the content row's lock.

        The same allocation ``PrContentService`` uses for ``version_no``, and
        safe for the same reason: the caller holds the content row, and the
        unique index on ``(content_id, submission_no)`` fails the loser of any
        race that gets past it rather than letting two rows claim one number.
        """
        latest = await self.latest_submission(content_id)
        return 1 if latest is None else latest.submission_no + 1

    @staticmethod
    def _submitter(actor: Actor) -> uuid.UUID:
        """The actor's ``users.id``, or a refusal.

        ``submitted_by_user_id`` is not nullable and must not become so: a file
        handed in by nobody is a record with no author to ask about it. The web
        session always has a user row; a bootstrap principal does not, and is
        refused rather than substituted.
        """
        if actor.user_id is None:
            raise PrPermissionDeniedError(
                "This principal has no user record and cannot submit production",
                details={"reason": "no_user_record"},
            )
        return actor.user_id


def _submission_snapshot(submission: PrProductionSubmission) -> dict[str, object]:
    """The correctable fields of a handed-in file, as the audit trail records them.

    Three, and no more. Everything that fixes the row's identity - its number,
    the draft it was cut from, the two people - is absent from both sides of a
    change because it cannot change. Step 1F.2.3f.2.
    """
    return {
        "artifact_type": submission.artifact_type.value,
        "location": submission.location,
        "note": submission.note,
    }


def _clean(value: str | None) -> str | None:
    """Trim, and turn a whitespace-only box into the ``NULL`` it means."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


__all__: list[str] = [
    "CorrectSubmissionCommand",
    "PrProductionService",
    "ProductionState",
    "SubmitProductionCommand",
]
