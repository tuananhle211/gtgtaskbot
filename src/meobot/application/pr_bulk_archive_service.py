"""Archiving one month's published output in one operation, or none of it.

Step 1F.2.3f.6. Until this step an older published piece was *shown* under
*Lưu trữ* the moment a later month was selected, at ``workflow_stage =
PUBLISHED`` throughout - a presentation that claimed a lifecycle event nobody
had made. That virtual archive is gone: *Lưu trữ* now holds what was actually
archived, in the month it was archived. Which leaves the manager with a real
job at the end of every month - putting last month's output away - and sixty
detail pages is not a way to do it. This service is that job as one action.

What it is not
--------------

**Not a sweep, and not a schedule.** Nothing here runs on the first of the
month; nothing here runs unless a person with the transition right asks for a
named month, sees how many pieces that means, and confirms. Content published
in the current month is never a candidate, however the request is shaped.

**Not a second lifecycle rule.** Every item is moved by
:meth:`~meobot.application.pr_workflow_service.PrContentWorkflowService.request_transition`
- the same call, with the same arguments, the detail page's *"Lưu trữ nội
dung"* button makes for one item. That call checks the capability, validates
``PUBLISHED -> ARCHIVED`` against the matrix, stamps ``archived_at``, writes
the audit row, appends the transition event and requests the work projection.
Nothing here re-implements any of it; this service decides **which** items are
in the batch and refuses the whole batch when any of them is wrong.

All or nothing, and how that is kept
------------------------------------

The same three properties as
:mod:`~meobot.application.pr_bulk_approval_service`, and for the same reasons:

#. **one transaction.** The API dependency opens one per request and commits
   it only when the route returns, so any refusal raised anywhere below rolls
   the whole thing back;
#. **every row locked before anything is written**, in ascending id-as-text
   order, so two overlapping batches serialise rather than deadlock;
#. **lock, then validate the batch, then write.** A validation interleaved
   with the writes could have archived twenty pieces before discovering the
   twenty-first had been corrected into a different month.

Staleness is one question asked under the lock: *is this still a ``PUBLISHED``
piece whose canonical publication instant is in the requested month*. The
instant is read by the same ``MIN`` over active publications the board draws
*Đã đăng* with, so a publication reversed or re-dated since the confirmation is
caught, and an item somebody archived a second earlier is reported as
``already_archived`` rather than archived twice - which is also what a retry
after success gets: a clean refusal naming every row, and no second write.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_query import period_bounds
from meobot.application.pr_query_service import PrQueryService
from meobot.application.pr_support import record_pr_event
from meobot.application.pr_workflow_service import PrContentWorkflowService, capability_for_target
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc
from meobot.db.models.pr import PrContentItem
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrBulkArchiveStaleError, PrNotFoundError, PrValidationError
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.pr.policy import BULK_ARCHIVE_MAX_ITEMS

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BulkArchiveCommand:
    """*"Lưu trữ N nội dung đã đăng trong kỳ MM/YYYY"*, as the server receives it.

    ``period`` is the month the ids were selected for, and it is **checked, not
    trusted**: an id whose publication is not in that month is refused. The
    ids are explicit - resolved by
    :meth:`~meobot.application.pr_query_service.PrQueryService.archive_candidates`
    at the moment the person asked - so the batch is what was confirmed and
    nothing published or re-dated since.
    """

    period: date
    content_ids: Sequence[uuid.UUID]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class BulkArchivedItem:
    content_id: uuid.UUID
    code: str
    title: str


@dataclass(frozen=True, slots=True)
class BulkArchiveOutcome:
    batch_id: uuid.UUID
    period: date
    archived: tuple[BulkArchivedItem, ...]
    requested: int
    duplicates_removed: int

    @property
    def archived_count(self) -> int:
        return len(self.archived)


@dataclass(frozen=True, slots=True)
class _Rejection:
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


class PrBulkArchiveService:
    """Archives the published output of one closed month, as one batch.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Answers the unscoped *"may this person archive at all"*
            before any row is read.
        queries: Reads the canonical publication instants under the lock.
        workflow: The one writer of ``workflow_stage``. Every item goes through
            its :meth:`request_transition`.
        timezone: The business calendar the month is read in - the same one the
            board reads it in.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        queries: PrQueryService,
        workflow: PrContentWorkflowService,
        timezone: ZoneInfo,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._queries = queries
        self._workflow = workflow
        self._timezone = timezone

    async def archive(
        self, *, actor: Actor, request_id: uuid.UUID, command: BulkArchiveCommand
    ) -> BulkArchiveOutcome:
        """Archive every item in the batch, or none of them.

        Duplicate ids are de-duplicated before validation, first occurrence
        kept, and the count dropped is reported - a repeat is a client bug that
        changes nothing about the operation's meaning.

        Raises:
            PrValidationError: Empty batch, more ids than
                :data:`~meobot.domain.pr.policy.BULK_ARCHIVE_MAX_ITEMS`, or a
                month that is not yet closed - the current month, or a later
                one - which is never a valid period archive.
            PrPermissionDeniedError: The actor may not drive
                ``PUBLISHED -> ARCHIVED`` at all. Raised before any row is
                read, so a batch of invented ids tells an unentitled caller
                nothing.
            PrBulkArchiveStaleError: At least one item is missing, already
                archived, no longer ``PUBLISHED``, or published outside the
                month. Nothing was archived.
        """
        requested = tuple(command.content_ids)
        unique = _deduplicate(requested)
        period = command.period.replace(day=1)
        if not unique:
            raise PrValidationError(
                "Không có nội dung nào để lưu trữ.",
                details={"reason": "empty_batch", "period": period.isoformat()},
            )
        if len(unique) > BULK_ARCHIVE_MAX_ITEMS:
            raise PrValidationError(
                f"Một lần lưu trữ hàng loạt chỉ nhận tối đa {BULK_ARCHIVE_MAX_ITEMS} nội dung.",
                details={
                    "reason": "batch_too_large",
                    "period": period.isoformat(),
                    "requested": len(unique),
                    "max_items": BULK_ARCHIVE_MAX_ITEMS,
                },
            )
        current = datetime.now(self._timezone).date().replace(day=1)
        if period >= current:
            # The current month is still being published into; archiving it
            # would put away work the team is in the middle of, and a "previous
            # period" action that accepted the present would not be one.
            raise PrValidationError(
                "Chỉ có thể lưu trữ nội dung của kỳ đã kết thúc.",
                details={
                    "reason": "period_not_closed",
                    "period": period.isoformat(),
                    "current_period": current.isoformat(),
                },
            )

        # The unscoped question, first: the same capability the single-item
        # transition demands, asked once before any row is touched.
        await self._capabilities.require(actor, capability_for_target(PrWorkflowStage.ARCHIVED))

        locked = await self._lock_all(unique)
        instants = await self._queries.publication_instants(
            [content_id for content_id, content in locked.items() if content is not None]
        )
        self._require_batch_published_in(locked, instants, period=period)

        batch_id = uuid.uuid4()
        archived: list[BulkArchivedItem] = []
        for content_id in _lock_order(unique):
            content = locked[content_id]
            assert content is not None  # ``_require_batch_published_in`` raised otherwise
            await self._workflow.request_transition(
                actor=actor,
                request_id=request_id,
                content_id=content_id,
                target=PrWorkflowStage.ARCHIVED,
                note=command.note,
            )
            archived.append(
                BulkArchivedItem(content_id=content_id, code=content.code, title=content.title)
            )

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_CONTENT_ARCHIVE_BATCH_RECORDED,
            entity_type="pr_content_archive_batch",
            entity_id=batch_id,
            after={
                "batch_id": str(batch_id),
                "period": period.isoformat()[:7],
                "archived_count": len(archived),
                "requested_count": len(requested),
                "duplicates_removed": len(requested) - len(unique),
                "content_codes": [item.code for item in archived],
                "content_ids": [str(item.content_id) for item in archived],
            },
        )
        logger.info(
            "pr_bulk_archive_recorded",
            extra={
                "pr_batch_id": str(batch_id),
                "period": period.isoformat()[:7],
                "archived_count": len(archived),
            },
        )
        return BulkArchiveOutcome(
            batch_id=batch_id,
            period=period,
            archived=tuple(archived),
            requested=len(requested),
            duplicates_removed=len(requested) - len(unique),
        )

    async def _lock_all(
        self, content_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, PrContentItem | None]:
        """Every row lock up front, in the one order every batch takes them in."""
        locked: dict[uuid.UUID, PrContentItem | None] = {}
        for content_id in _lock_order(content_ids):
            try:
                locked[content_id] = await self._workflow.lock(content_id)
            except PrNotFoundError:
                locked[content_id] = None
        return locked

    def _require_batch_published_in(
        self,
        locked: Mapping[uuid.UUID, PrContentItem | None],
        instants: Mapping[uuid.UUID, datetime],
        *,
        period: date,
    ) -> None:
        """Every item is ``PUBLISHED`` in ``period``, or nothing is."""
        lower, upper = period_bounds(period, tz=self._timezone)
        rejections = [
            rejection
            for content_id, content in locked.items()
            if (
                rejection := _rejection(
                    content_id, content, instants.get(content_id), lower=lower, upper=upper
                )
            )
            is not None
        ]
        if not rejections:
            return
        raise PrBulkArchiveStaleError(
            f"Không thể lưu trữ vì {len(rejections)} nội dung đã thay đổi. "
            "Không có nội dung nào được lưu trữ.",
            details={
                "reason": rejections[0].reason,
                "period": period.isoformat()[:7],
                "archived": 0,
                "affected": [rejection.as_detail() for rejection in rejections],
            },
        )


def _rejection(
    content_id: uuid.UUID,
    content: PrContentItem | None,
    instant: datetime | None,
    *,
    lower: datetime,
    upper: datetime,
) -> _Rejection | None:
    """Why this item is not part of this batch, or ``None`` when it is."""
    if content is None:
        return _Rejection(content_id=content_id, reason="missing")
    if content.workflow_stage is PrWorkflowStage.ARCHIVED:
        return _Rejection(
            content_id=content_id,
            reason="already_archived",
            code=content.code,
            current_stage=content.workflow_stage,
        )
    if content.workflow_stage is not PrWorkflowStage.PUBLISHED:
        return _Rejection(
            content_id=content_id,
            reason="not_published",
            code=content.code,
            current_stage=content.workflow_stage,
        )
    if instant is None or not (lower <= ensure_utc(instant) < upper):
        return _Rejection(
            content_id=content_id,
            reason="outside_period",
            code=content.code,
            current_stage=content.workflow_stage,
        )
    return None


def _deduplicate(content_ids: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    seen: dict[uuid.UUID, None] = {}
    for content_id in content_ids:
        seen.setdefault(content_id, None)
    return tuple(seen)


def _lock_order(content_ids: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    return tuple(sorted(set(content_ids), key=str))


__all__: list[str] = [
    "BulkArchiveCommand",
    "BulkArchiveOutcome",
    "BulkArchivedItem",
    "PrBulkArchiveService",
]
