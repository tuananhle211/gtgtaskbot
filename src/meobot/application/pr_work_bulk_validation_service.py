"""Validating many finished jobs in one act, or validating none of them.

Milestone **M4A**. Recurring and routine operational work is the reason this
exists: a hundred comment-seeding jobs a week arrive at ``COMPLETED`` one row at
a time, and a validator opening a hundred pages to press a hundred buttons is a
validator who will eventually stop looking. The single-item path is right for a
shoot; it is the wrong shape for a queue.

What this is, and what it deliberately is not
----------------------------------------------

**Not a second way to count work.** Every item in a batch is validated by
:meth:`~meobot.application.pr_work_service.PrWorkService.approve` - the same
method, with the same arguments, that the single-item route calls. This service
decides *which* items are in the batch and refuses the whole batch when any one
of them is wrong; it writes no status, stamps no ``counted_at``, and touches
``count_status`` nowhere. The self-validation rule, the transition matrix, the
history rows, the audit entry, the notification and the M2 eligibility handoff
are all reached exactly once per item, through that one method.

**Not a bulk anything-else.** There is no bulk complete, no bulk cancel and no
bulk reopen. Sending work back is a judgement about one job that carries a
reason about that job, and completing somebody else's work is not an act that
exists at all. A checkbox column that could do either would be inviting the
sweep this module is shaped to keep deliberate - the same reasoning
:mod:`meobot.application.pr_bulk_approval_service` sets out for content.

All or nothing, and how that is kept
-------------------------------------

"Xác nhận 40 công việc" validates forty or validates zero, structurally rather
than by care:

#. **one transaction**, opened by the API dependency and committed only when
   the route returns, so any refusal below rolls back the whole batch;
#. **every row locked up front, in a deterministic order** - ascending by id as
   text - so two validators sweeping overlapping selections queue on their
   first shared row instead of deadlocking on each other's second;
#. **three passes in order** - lock, then screen the batch, then write. A screen
   interleaved with the writes could have counted thirty jobs before finding
   that the thirty-first had no evidence.

The preflight, and why it is a separate call
---------------------------------------------

:meth:`PrWorkBulkValidationService.preflight` answers *"what would this batch
do"* without writing anything, and the panel calls it before opening its
confirmation dialog. That is what lets a validator see **which** four rows are
their own work and drop them, instead of being told the batch was refused and
left to find out why by bisection. It runs the same screens in the same order
as the write, against rows read without locks: a preflight is advice about a
moment, and the write re-checks everything under a lock because that moment has
passed by the time anybody presses the button.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_work_service import PrWorkService
from meobot.core.logging import get_logger
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkItem,
    PrWorkType,
)
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrBulkApprovalStaleError,
    PrValidationError,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import PrWorkStatus

logger = get_logger(__name__)

#: How many jobs one batch may validate.
#:
#: The same 200 as :data:`~meobot.domain.pr.policy.BULK_APPROVAL_MAX_ITEMS`, and
#: deliberately the same number rather than a second one to remember: both bound
#: an explicit list of ids in one transaction, for the same reason - a batch
#: nobody can read before pressing the button is not a decision, and a failure
#: at item 1 999 throws away all of it. A queue of 340 is two honest batches.
BULK_VALIDATION_MAX_ITEMS: int = 200


@dataclass(frozen=True, slots=True)
class BulkValidateCommand:
    """One validator confirming several finished jobs.

    ``work_item_ids`` is the **frozen** target set, always explicit. A "select
    all" in the panel is resolved to ids before the confirmation dialog opens,
    so a job that reached ``COMPLETED`` between the dialog and the button cannot
    join the batch.
    """

    work_item_ids: Sequence[uuid.UUID]
    #: One note for the batch rather than one per item: a bulk validation is one
    #: act, and a per-item field is a form nobody fills in for forty rows.
    note: str | None = None


@dataclass(frozen=True, slots=True)
class BulkValidationCandidate:
    """One row as the preflight sees it: what it is, and whether it may go."""

    work_item_id: uuid.UUID
    code: str | None
    title: str | None
    #: ``None`` when the row is fine. Otherwise the machine reason, which the
    #: panel turns into a sentence - see ``BULK_VALIDATION_REASONS`` in the
    #: frontend's labels.
    reason: str | None = None
    current_status: PrWorkStatus | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None

    def as_detail(self) -> dict[str, object]:
        return {
            "work_item_id": str(self.work_item_id),
            "code": self.code,
            "title": self.title,
            "reason": self.reason,
            "current_status": self.current_status.value if self.current_status else None,
        }


@dataclass(frozen=True, slots=True)
class BulkValidationPreflight:
    """What a batch would do, computed without writing anything."""

    #: In lock order, so the panel lists rows in the order the write would take
    #: them rather than in whatever order the checkboxes were ticked.
    candidates: tuple[BulkValidationCandidate, ...]
    requested: int
    duplicates_removed: int

    @property
    def blocked(self) -> tuple[BulkValidationCandidate, ...]:
        return tuple(one for one in self.candidates if not one.ok)

    @property
    def validatable(self) -> tuple[BulkValidationCandidate, ...]:
        return tuple(one for one in self.candidates if one.ok)


@dataclass(frozen=True, slots=True)
class BulkValidationOutcome:
    """What a successful batch did. Only ever returned when *all* of it worked."""

    batch_id: uuid.UUID
    #: In lock order - the order the validations were actually written in.
    validated: tuple[BulkValidationCandidate, ...]
    #: Contributions that moved to ``COUNTED``, across the whole batch. Larger
    #: than ``len(validated)`` whenever a shared job had several contributors.
    counted_contributions: int
    requested: int
    duplicates_removed: int


class PrWorkBulkValidationService:
    """Validates a whole batch of finished work, or refuses the whole batch.

    Args:
        session: Unit of work. The caller owns the transaction boundary, and
            that ownership is what makes all-or-nothing structural.
        audit: Event writer sharing that session. Used for the one batch-level
            row; the per-item rows are written by :class:`PrWorkService`.
        work: **The** validation writer. Every item goes through its
            :meth:`~meobot.application.pr_work_service.PrWorkService.approve`,
            so a bulk validation and a single validation are the same write.
    """

    def __init__(self, session: AsyncSession, audit: AuditService, work: PrWorkService) -> None:
        self._session = session
        self._audit = audit
        self._work = work

    # =====================================================================
    # Preflight
    # =====================================================================
    async def preflight(
        self, *, actor: Actor, work_item_ids: Sequence[uuid.UUID]
    ) -> BulkValidationPreflight:
        """What this batch would do. Writes nothing, locks nothing.

        Requires ``PR_WORK_VALIDATE`` before reading a row, so a caller who
        could never validate anything cannot use a list of ids to discover
        which of them exist.

        Every reason it reports is one the write below re-checks under a lock.
        This is advice about a moment, not a promise about the next one.
        """
        await self._work.capabilities.require(actor, PrCapability.PR_WORK_VALIDATE)
        requested = tuple(work_item_ids)
        unique = _deduplicate(requested)
        self._require_batch_size(unique)

        rows = await self._read(unique)
        contributed = await self._contributed_by(actor, unique)
        evidence_needed = await self._evidence_shortfall(unique)
        return BulkValidationPreflight(
            candidates=tuple(
                self._screen(
                    work_item_id,
                    rows.get(work_item_id),
                    contributed=work_item_id in contributed,
                    missing_evidence=work_item_id in evidence_needed,
                )
                for work_item_id in _lock_order(unique)
            ),
            requested=len(requested),
            duplicates_removed=len(requested) - len(unique),
        )

    # =====================================================================
    # The write
    # =====================================================================
    async def validate(
        self, *, actor: Actor, request_id: uuid.UUID, command: BulkValidateCommand
    ) -> BulkValidationOutcome:
        """Validate every job in the batch, or none of them. ``PR_WORK_VALIDATE``.

        **Duplicate ids are de-duplicated**, keeping the first occurrence, and
        the count dropped is reported. A repeat is a client bug that changes
        nothing about the operation's meaning - the same job cannot be
        validated twice either way - and refusing the batch over one would fail
        an operation whose intent is unambiguous. Validating twice is what
        neither choice allows, and de-duplicating is what makes that structural
        rather than something the loop has to remember.

        Raises:
            PrValidationError: Empty batch, or more ids than
                :data:`BULK_VALIDATION_MAX_ITEMS`.
            PrPermissionDeniedError: The actor may not validate at all - raised
                before any row is read.
            PrBulkApprovalStaleError: At least one job is missing, has moved off
                ``COMPLETED``, is missing required evidence, or is one the actor
                contributed to. **Nothing was validated**, and ``details`` names
                every row that caused it.
        """
        await self._work.capabilities.require(actor, PrCapability.PR_WORK_VALIDATE)
        requested = tuple(command.work_item_ids)
        unique = _deduplicate(requested)
        self._require_batch_size(unique)

        # Pass one: every lock, in an order two concurrent batches agree on.
        locked = await self._lock_all(unique)
        # Pass two: screen the batch as it is *under the lock*, and refuse the
        # whole thing before a single job has been counted.
        contributed = await self._contributed_by(actor, unique)
        evidence_needed = await self._evidence_shortfall(unique)
        candidates = tuple(
            self._screen(
                work_item_id,
                locked.get(work_item_id),
                contributed=work_item_id in contributed,
                missing_evidence=work_item_id in evidence_needed,
            )
            for work_item_id in _lock_order(unique)
        )
        blocked = tuple(one for one in candidates if not one.ok)
        if blocked:
            raise PrBulkApprovalStaleError(
                f"Không thể xác nhận vì {len(blocked)} công việc không hợp lệ. "
                "Không có công việc nào được xác nhận.",
                details={
                    "reason": blocked[0].reason,
                    "validated": 0,
                    "affected": [one.as_detail() for one in blocked],
                },
            )

        # Pass three: write, through the one method that may count work.
        batch_id = uuid.uuid4()
        counted = 0
        for candidate in candidates:
            detail = await self._work.approve(
                actor=actor,
                request_id=request_id,
                work_item_id=candidate.work_item_id,
                note=command.note,
            )
            counted += sum(1 for row in detail.contributions if row.counted_at is not None)

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_VALIDATION_BATCH_RECORDED,
            entity_type="pr_work_validation_batch",
            entity_id=batch_id,
            after={
                "batch_id": str(batch_id),
                "validated_count": len(candidates),
                "requested_count": len(requested),
                "duplicates_removed": len(requested) - len(unique),
                "counted_contributions": counted,
                # The codes as well as the ids: this row is what somebody reads
                # when they ask "what did that batch do", and a list of UUIDs is
                # not an answer to that question.
                "work_codes": [one.code for one in candidates],
                "work_item_ids": [str(one.work_item_id) for one in candidates],
            },
        )
        logger.info(
            "pr_work_bulk_validated",
            extra={
                "pr_batch_id": str(batch_id),
                "pr_work_validated": len(candidates),
                "pr_work_counted": counted,
            },
        )
        return BulkValidationOutcome(
            batch_id=batch_id,
            validated=candidates,
            counted_contributions=counted,
            requested=len(requested),
            duplicates_removed=len(requested) - len(unique),
        )

    # =====================================================================
    # The screens. One method, used by both the preflight and the write.
    # =====================================================================
    def _screen(
        self,
        work_item_id: uuid.UUID,
        item: PrWorkItem | None,
        *,
        contributed: bool,
        missing_evidence: bool,
    ) -> BulkValidationCandidate:
        """Why this row cannot go, or ``None`` if it can.

        The order matters only in what it reports first, and it reports the
        most specific thing: a job the actor worked on is refused as
        ``self_validation`` even when it is also missing evidence, because that
        is the reason they cannot fix by attaching a file.
        """
        if item is None:
            return BulkValidationCandidate(
                work_item_id=work_item_id, code=None, title=None, reason="missing"
            )
        base = BulkValidationCandidate(
            work_item_id=work_item_id,
            code=item.code,
            title=item.title,
            current_status=item.status,
        )
        if contributed:
            return _with_reason(base, "self_validation")
        if item.status is not PrWorkStatus.COMPLETED:
            return _with_reason(base, "not_completed")
        if missing_evidence:
            return _with_reason(base, "evidence_required")
        return base

    @staticmethod
    def _require_batch_size(unique: tuple[uuid.UUID, ...]) -> None:
        if not unique:
            raise PrValidationError(
                "Chưa chọn công việc nào để xác nhận.",
                details={"reason": "empty_batch"},
            )
        if len(unique) > BULK_VALIDATION_MAX_ITEMS:
            raise PrValidationError(
                f"Một lần xác nhận hàng loạt chỉ nhận tối đa "
                f"{BULK_VALIDATION_MAX_ITEMS} công việc.",
                details={
                    "reason": "batch_too_large",
                    "requested": len(unique),
                    "max_items": BULK_VALIDATION_MAX_ITEMS,
                },
            )

    # =====================================================================
    # Reads
    # =====================================================================
    async def _lock_all(self, work_item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, PrWorkItem]:
        """Take every row lock up front, ascending by id as text.

        A missing row is simply absent from the mapping rather than a raise:
        the batch is refused as a whole and the refusal has to name **every**
        row that caused it, so this pass collects and :meth:`_screen` decides.
        """
        locked: dict[uuid.UUID, PrWorkItem] = {}
        for work_item_id in _lock_order(work_item_ids):
            row = (
                await self._session.execute(
                    select(PrWorkItem).where(PrWorkItem.id == work_item_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is not None:
                locked[work_item_id] = row
        return locked

    async def _read(self, work_item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, PrWorkItem]:
        """The same rows without locks, for the preflight. One query."""
        rows = (
            (
                await self._session.execute(
                    select(PrWorkItem).where(PrWorkItem.id.in_(list(work_item_ids)))
                )
            )
            .scalars()
            .all()
        )
        return {row.id: row for row in rows}

    async def _contributed_by(
        self, actor: Actor, work_item_ids: Sequence[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        """Which of these jobs the actor worked on. **One query, not N.**

        The anti-gaming rule in set form. ``approve`` asks the same question
        per item and refuses on it; asking it here in one round trip is what
        lets the batch name all of the validator's own rows at once instead of
        failing on the first.
        """
        if actor.user_id is None:
            return frozenset()
        rows = (
            (
                await self._session.execute(
                    select(PrWorkContribution.work_item_id).where(
                        PrWorkContribution.work_item_id.in_(list(work_item_ids)),
                        PrWorkContribution.user_id == actor.user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return frozenset(rows)

    async def _evidence_shortfall(self, work_item_ids: Sequence[uuid.UUID]) -> frozenset[uuid.UUID]:
        """Which of these jobs require evidence and have none. **One query.**

        ``requires_evidence`` lives on the work type and is enforced at
        completion by ``complete``; it is re-checked here because a required
        file can be removed after completion, and a batch is exactly the shape
        in which nobody would notice.
        """
        rows = (
            (
                await self._session.execute(
                    select(PrWorkItem.id)
                    .join(PrWorkType, PrWorkType.id == PrWorkItem.work_type_id)
                    .where(
                        PrWorkItem.id.in_(list(work_item_ids)),
                        PrWorkType.requires_evidence.is_(True),
                        ~select(PrWorkEvidence.id)
                        .where(PrWorkEvidence.work_item_id == PrWorkItem.id)
                        .exists(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return frozenset(rows)


def _with_reason(base: BulkValidationCandidate, reason: str) -> BulkValidationCandidate:
    return BulkValidationCandidate(
        work_item_id=base.work_item_id,
        code=base.code,
        title=base.title,
        reason=reason,
        current_status=base.current_status,
    )


def _deduplicate(values: Sequence[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """Preserve order, drop repeats."""
    return tuple(dict.fromkeys(values))


def _lock_order(values: Sequence[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """The one order every batch takes its locks in. Ascending by id as text."""
    return tuple(sorted(values, key=str))


__all__: list[str] = [
    "BULK_VALIDATION_MAX_ITEMS",
    "BulkValidateCommand",
    "BulkValidationCandidate",
    "BulkValidationOutcome",
    "BulkValidationPreflight",
    "PrWorkBulkValidationService",
]
