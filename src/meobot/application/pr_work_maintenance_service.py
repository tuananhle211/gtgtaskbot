"""Administrative work maintenance: content sync, rebuild, removals, type deletion.

**``PR_WORK_CONFIGURE``, and nothing less, for every write in this module.**
That is the capability ADMIN and OWNER hold and TEAM_LEAD and EMPLOYEE do not,
and it is checked here, on the service, so a forged request reaches the same
refusal a screen without the buttons would have spared its user. Nothing in
this module reads a role name.

What this module is, and is not
--------------------------------

The Work taxonomy and the Content → Work mappings are still being normalised,
and an administrator correcting them needs a way to make the ledger agree with
the corrected configuration. Every operation here is that, and only that:

* **preview** - what the ledger would look like against the current mapping
  and the current content facts, as counts and small samples. Read-only.
* **sync missing** - additive: project the content that has no result yet.
* **rebuild** - the same projection over everything in scope, after taking a
  counted result out of a container the mapping no longer points at, so the
  projector refiles it under the right type.
* **remove one result** / **remove one empty container** / **delete an unused
  work type** - the explicit cleanups the workflow ends in. A removed result
  is ``EXCLUDED / ADMIN_REMOVED``: out of the accounting, and re-evaluated by
  the next projection. A result a validator **rejected** is refused here -
  see :meth:`admin_remove_result`;
* **delete one legacy content work item** - the pre-``0039`` shape, one work
  item per content milestone, removed outright by an administrator who opened
  it and decided. Never chosen by the system, never batched, and never
  followed by a projection: see :meth:`admin_delete_legacy_work_item`;
* **delete one terminal work item** - an ordinary job somebody cancelled or
  a proposal somebody rejected, removed outright by an administrator who
  opened it and decided. A second eligibility rule beside the legacy one, not a widening of
  it: cancelled is a lifecycle fact and legacy is a provenance fact, a row
  may satisfy either, and neither predicate reads the other. Refused whenever
  the row still holds accounting - a result, a counted contribution, an M2 or
  M6 allocation - because *cancelled* never meant *safe to destroy*: see
  :meth:`admin_delete_terminal_work_item`. Cancelling deletes nothing, and
  nothing here deletes without a person asking for that one row.

**No second projector.** Sync and rebuild call
:meth:`~meobot.application.pr_content_work_projector.PrContentWorkProjector.project_content`
for every piece of content, exactly as the worker does. The mapping precedence
(exact, default, auto-provision), the contributor, the validator and the
self-validation rule are the projector's, and this module neither restates nor
overrides any of them. What it adds is the one step the projector deliberately
does not take on its own: a counted result stays where its month reported it
(*counted result stability*), so moving it to a corrected type is an
administrative act with its own audit row.

Content-derived work is identified by **provenance** - ``source_type = CONTENT``
and the source key - never by a work type's name. Manual and recurring results
are not read by sync or rebuild at all.

Only an ``OPEN`` reporting period may be rebuilt or cleaned. A ``CLOSED`` or
``LOCKED`` month refuses with a structured reason, and there is no override.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_work_projector import (
    MAX_RECONCILE_CONTENT,
    ContentProjectionReport,
    PrContentWorkProjector,
    ProjectionResult,
)
from meobot.application.pr_performance_service import PrPerformanceService
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.application.pr_work_quota_service import PrWorkQuotaEligibilityService
from meobot.application.pr_work_result_service import PrWorkResultService
from meobot.application.pr_work_service import PrWorkService
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.base import Base
from meobot.db.models.pr import PrApprovalEvent, PrContentItem
from meobot.db.models.pr_content_work import PrContentWorkRule
from meobot.db.models.pr_performance import (
    PrPerformanceResult,
    PrWorkScoreAllocation,
    PrWorkScoringRule,
)
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import (
    PrWorkContribution,
    PrWorkEvidence,
    PrWorkHistory,
    PrWorkItem,
    PrWorkType,
)
from meobot.db.models.pr_work_quota import PrWorkQuota, PrWorkQuotaAllocation
from meobot.db.models.pr_work_recurring import PrWorkRecurringTemplate
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.content_work import (
    PrContentWorkOutcome,
    is_legacy_content_work_item,
)
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrValidationError,
)
from meobot.domain.pr.models import PrApprovalDecision, PrApprovalStage, PrContentType
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import (
    PrWorkContributionRole,
    PrWorkCountStatus,
    PrWorkEventType,
    PrWorkSourceType,
    PrWorkStatus,
)
from meobot.domain.pr.work_results import PrWorkExclusionKind, PrWorkResultSource

logger = get_logger(__name__)

#: How many content items one preview, sync or rebuild covers. The same bound
#: the existing reconcile route has: a run is a synchronous transaction, and a
#: scope larger than this is run again - every operation here converges, so a
#: second run over the same scope is the rest of the first, not a repeat.
MAX_MAINTENANCE_CONTENT = MAX_RECONCILE_CONTENT
#: The longest note an administrator may attach to a cleanup.
MAX_MAINTENANCE_NOTE = 2000
#: How many sample rows a preview carries per category.
SAMPLE_SIZE = 10

#: The structured reason a cleanup gets on a month that is not open.
PERIOD_NOT_OPEN_FOR_CLEANUP = "work_period_not_open_for_cleanup"
PERIOD_NOT_OPEN_MESSAGE = "Không thể chỉnh sửa dữ liệu công việc của kỳ đã đóng hoặc khóa."
#: The legacy-delete refusals, as ``details.reason``. Every one is a 4xx.
WORK_ITEM_NOT_FOUND = "work_item_not_found"
WORK_ITEM_NOT_LEGACY_CONTENT = "work_item_not_legacy_content"
WORK_ITEM_DELETE_BLOCKED = "work_item_delete_blocked"
PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE = "Không thể xóa dữ liệu công việc của kỳ đã đóng hoặc khóa."
#: ``details.operation`` on a legacy-delete refusal, so a screen can word the
#: shared period reason as a delete rather than an edit.
LEGACY_DELETE_OPERATION = "legacy_delete"
#: The terminal-delete refusals (cancelled or rejected rows), as
#: ``details.reason``. Every one is a 4xx.
#: ``work_item_not_found`` is shared with the legacy delete above.
WORK_ITEM_NOT_TERMINAL = "work_item_not_terminal"
TERMINAL_WORK_ITEM_DELETE_BLOCKED = "terminal_work_item_delete_blocked"
TERMINAL_DELETE_OPERATION = "terminal_delete"
WORK_ITEM_NOT_TERMINAL_MESSAGE = (
    "Chỉ xóa được công việc đã hủy hoặc không được chấp nhận bằng cách này."
)
#: The two terminal statuses an administrator may hard-delete from. Not a
#: lifecycle edge - ``WORK_TRANSITIONS`` is untouched - and deliberately not
#: ``APPROVED``, which is counted history, nor anything still in flight.
TERMINAL_DELETABLE_STATUSES: frozenset[PrWorkStatus] = frozenset(
    {PrWorkStatus.CANCELLED, PrWorkStatus.REJECTED}
)
TERMINAL_DELETE_BLOCKED_MESSAGE = (
    "Không thể xóa công việc này vì vẫn còn dữ liệu kết quả hoặc ghi nhận hiệu suất liên quan."
)
TERMINAL_CONTAINER_MESSAGE = "Luồng công việc theo kỳ không xóa bằng cách này."


class Finding:
    """One content milestone's standing against the current mapping."""

    CORRECT = "CORRECT"
    MISSING = "MISSING"
    WRONG_WORK_TYPE = "WRONG_WORK_TYPE"
    STALE = "STALE"
    NEW_WORK_TYPE = "NEW_WORK_TYPE"
    UNMAPPED = "UNMAPPED"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class MaintenanceScope:
    """What one maintenance run covers. The period is mandatory."""

    period_id: uuid.UUID
    user_id: uuid.UUID | None = None
    content_type: PrContentType | None = None
    limit: int = MAX_MAINTENANCE_CONTENT


@dataclass(frozen=True, slots=True)
class FindingRow:
    """One sample row of a preview category."""

    content_id: uuid.UUID
    content_code: str
    contribution_kind: str
    finding: str
    contributor_user_id: uuid.UUID | None
    current_work_type_id: uuid.UUID | None
    expected_work_type_id: uuid.UUID | None
    detail: str | None


@dataclass(frozen=True, slots=True)
class MaintenancePreview:
    """Counts and small samples. **Computed here, never in a browser.**"""

    period_id: uuid.UUID
    period_code: str
    period_status: PrPeriodStatus
    user_id: uuid.UUID | None
    content_type: PrContentType | None
    candidate_count: int
    truncated: bool
    eligible_content_count: int
    correct_result_count: int
    missing_result_count: int
    wrong_work_type_count: int
    stale_result_count: int
    new_work_type_count: int
    unmapped_count: int
    unresolved_count: int
    blocked_count: int
    results_to_remove: int
    results_to_create: int
    affected_user_ids: tuple[uuid.UUID, ...]
    affected_work_type_ids: tuple[uuid.UUID, ...]
    #: Work types the rebuild would file results under, with how many.
    recreate_by_work_type: dict[uuid.UUID, int]
    #: Content types that would provision a new work type, with how many.
    provision_by_content_type: dict[str, int]
    #: What the rebuild leaves alone in this period, so the confirmation can
    #: say so in numbers rather than in a promise.
    manual_result_count: int
    manual_item_count: int
    recurring_result_count: int
    samples: tuple[FindingRow, ...]
    finalized_performance_count: int


@dataclass(frozen=True, slots=True)
class MaintenanceRun:
    """What one sync or rebuild did."""

    operation: str
    preview: MaintenancePreview
    content_items: int
    results_removed: int
    counts: dict[str, int]
    performance_refreshed: int
    reports: tuple[ContentProjectionReport, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkTypeReferences:
    """Every row that keeps a work type from being deleted, counted."""

    work_type_id: uuid.UUID
    content_rules_active: int
    content_rules_inactive: int
    work_items: int
    period_containers: int
    empty_containers_removable: int
    results: int
    contributions: int
    recurring_templates: int
    quotas: int
    quota_allocations: int
    scoring_rules: int
    score_allocations: int

    @property
    def blocking(self) -> dict[str, int]:
        """The non-zero counts that refuse a delete. Inactive rules are not among them."""
        rows = {
            "content_rules_active": self.content_rules_active,
            "work_items": self.work_items,
            "results": self.results,
            "contributions": self.contributions,
            "recurring_templates": self.recurring_templates,
            "quotas": self.quotas,
            "quota_allocations": self.quota_allocations,
            "scoring_rules": self.scoring_rules,
            "score_allocations": self.score_allocations,
        }
        return {name: count for name, count in rows.items() if count}

    @property
    def deletable(self) -> bool:
        return not self.blocking


@dataclass(frozen=True, slots=True)
class LegacyWorkItemDeletion:
    """What deleting one legacy content work item removed. Deterministic.

    The item is gone by the time this is built, so it carries the facts a
    screen and an audit reader still need: which row, whose it was, which
    month it counted in, and how many rows of each kind went with it. What
    it deliberately does **not** carry is any replacement: no result id, no
    projection request, because the operation creates neither.
    """

    work_item_id: uuid.UUID
    code: str
    title: str
    work_type_id: uuid.UUID
    content_id: uuid.UUID | None
    content_code: str | None
    source_key: str | None
    responsible_user_id: uuid.UUID | None
    period_code: str | None
    #: ``contributions``, ``counted_contributions``, ``quota_allocations``,
    #: ``score_allocations``, ``evidence``, ``history``.
    removed: dict[str, int]
    performance_refreshed: int
    #: Always ``0``. Stated as a field so a test can pin it and a reader can
    #: see the promise rather than infer it from an absence.
    results_created: int = 0
    projection_requested: bool = False


@dataclass(frozen=True, slots=True)
class TerminalDeleteEligibility:
    """Whether one terminal work item may be hard-deleted **right now**, and why not.

    The server's answer for the detail screen, computed from the same counts
    the delete refuses on, so the button and the refusal cannot disagree. A
    row that is not cancelled, or is a period container, is not eligible
    whatever its counts say; a cancelled ordinary row is eligible exactly when
    ``blocking`` is all zeros and its month is open.

    ``blocking`` carries ``results``, ``counted_contributions``,
    ``quota_allocations`` and ``score_allocations`` - the four kinds of row
    that represent accounting this operation will never destroy on its own.
    """

    deletable: bool
    #: ``None`` when deletable; otherwise the ``details.reason`` the delete
    #: would refuse with.
    reason: str | None = None
    #: ``not_terminal`` · ``period_container`` · ``blocking_references`` ·
    #: ``period_not_open``.
    cause: str | None = None
    blocking: dict[str, int] = field(default_factory=dict)
    period_code: str | None = None
    message: str | None = None
    #: The row's status as read - ``CANCELLED`` or ``REJECTED`` when eligible,
    #: whatever it was when not - so a screen can say which terminal state it
    #: is deleting rather than calling every one of them "đã hủy".
    previous_status: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalWorkItemDeletion:
    """What deleting one cancelled work item removed. Deterministic.

    The row is gone by the time this is built. ``previous_status`` is stated
    as a field rather than implied by the operation's name so an audit reader
    and a test can pin it; it is always ``CANCELLED``.
    """

    work_item_id: uuid.UUID
    code: str
    title: str
    work_type_id: uuid.UUID
    source_type: str
    source_key: str | None
    content_id: uuid.UUID | None
    content_code: str | None
    recurring_occurrence_id: uuid.UUID | None
    responsible_user_id: uuid.UUID | None
    period_code: str | None
    #: ``contributions``, ``evidence``, ``history``. Never a counted
    #: contribution, a result or an allocation: any of those refuses instead.
    removed: dict[str, int]
    #: ``CANCELLED`` or ``REJECTED`` - the row's status the moment before it
    #: went. Stated so an audit reader and a test can pin which terminal state
    #: was deleted.
    previous_status: str
    results_created: int = 0
    projection_requested: bool = False


@dataclass
class _Tally:
    counts: dict[str, int] = field(default_factory=dict)
    #: Every finding, in candidate order. What sync selects its targets from.
    rows: list[FindingRow] = field(default_factory=list)
    samples: list[FindingRow] = field(default_factory=list)
    to_remove: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    users: set[uuid.UUID] = field(default_factory=set)
    work_types: set[uuid.UUID] = field(default_factory=set)
    recreate: dict[uuid.UUID, int] = field(default_factory=dict)
    provision: dict[str, int] = field(default_factory=dict)
    eligible: set[uuid.UUID] = field(default_factory=set)

    def add(self, row: FindingRow) -> None:
        self.counts[row.finding] = self.counts.get(row.finding, 0) + 1
        if len(self.samples) < SAMPLE_SIZE * 8 and row.finding != Finding.CORRECT:
            self.samples.append(row)


class PrWorkMaintenanceService:
    """Content sync, rebuild, removals and work type deletion. ``PR_WORK_CONFIGURE``."""

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        periods: PrWorkPeriodService,
        projector: PrContentWorkProjector,
        results: PrWorkResultService,
        work: PrWorkService,
        performance: PrPerformanceService,
        eligibility: PrWorkQuotaEligibilityService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._periods = periods
        self._projector = projector
        self._results = results
        self._work = work
        self._performance = performance
        #: M2's evaluator, for the one operation here that takes counted rows
        #: out of a month without going through a result or a contribution
        #: status change: deleting a legacy item. Optional, like everywhere
        #: else M2 is wired, and a no-op when absent.
        self._eligibility = eligibility

    # =====================================================================
    # Preview
    # =====================================================================
    async def preview(self, *, actor: Actor, scope: MaintenanceScope) -> MaintenancePreview:
        """What sync or rebuild would do. Read-only; ``PR_WORK_CONFIGURE``.

        Every candidate is put through the projector's own **dry run**, so the
        outcome vocabulary is the projector's: *would record* is missing,
        *unchanged* is correct, *reversed* is stale. The one judgement added
        here is ``WRONG_WORK_TYPE``: a result that exists and is filed under a
        different type from the one the mapping now resolves to.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        period = await self._periods.require_period(scope.period_id)
        candidates, truncated = await self._candidates(period, scope)
        tally = await self._classify(
            actor=actor, request_id=uuid.uuid4(), period=period, scope=scope, ids=candidates
        )
        return await self._preview_from(period, scope, candidates, truncated, tally)

    # =====================================================================
    # Sync and rebuild
    # =====================================================================
    async def sync_missing(
        self, *, actor: Actor, request_id: uuid.UUID, scope: MaintenanceScope
    ) -> MaintenanceRun:
        """Project the content in scope that has **no result yet**. Additive.

        Nothing existing is read back or changed: a piece whose result exists,
        correct or not, is not projected by this operation at all, so a stale
        result is not reversed and a wrongly-typed one is not moved. Those are
        the rebuild's business. Running this twice writes nothing the second
        time, because the second preview finds nothing missing.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        period = await self._periods.require_period(scope.period_id)
        self._require_open(period)
        candidates, truncated = await self._candidates(period, scope)
        tally = await self._classify(
            actor=actor, request_id=request_id, period=period, scope=scope, ids=candidates
        )
        preview = await self._preview_from(period, scope, candidates, truncated, tally)
        await self._audit_requested(
            actor, request_id, AuditAction.PR_WORK_CONTENT_SYNC_REQUESTED, preview
        )
        targets = sorted(
            {
                row.content_id
                for row in tally.rows
                if row.finding in (Finding.MISSING, Finding.NEW_WORK_TYPE)
            }
        )
        reports, counts = await self._project(actor, request_id, targets)
        refreshed = await self._refresh_performance(
            actor, request_id, period, self._subjects_of(reports)
        )
        run = MaintenanceRun(
            operation="sync",
            preview=preview,
            content_items=len(targets),
            results_removed=0,
            counts=counts,
            performance_refreshed=refreshed,
            reports=tuple(reports),
        )
        await self._audit_completed(
            actor, request_id, AuditAction.PR_WORK_CONTENT_SYNC_COMPLETED, run
        )
        return run

    async def rebuild(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        scope: MaintenanceScope,
        note: str | None = None,
    ) -> MaintenanceRun:
        """Make every content-derived result in scope match the current mapping.

        Two steps, in one transaction:

        1. every **counted** result filed under a type the mapping no longer
           resolves to is taken out of its container - an exclusion with this
           operation's reason, never a delete - so that the projector's own
           refile rule may move it;
        2. every piece of content in scope is projected, exactly as the worker
           would: missing results are recorded, moved results are refiled and
           recounted under the corrected type, withdrawn milestones are
           reversed, and a content type nobody mapped provisions its type.

        Manual and recurring results are not read. A second rebuild over the
        same scope finds nothing to take out and changes nothing.

        **A validator's rejection survives the rebuild.** The projector holds
        a ``VALIDATOR_REJECTED`` row whatever the source says
        (``HELD_BY_VALIDATOR``), and step 1 only takes out *counted* rows, so
        a rebuild is never a way to erase a reviewed decision. There is no
        override flag: the release is *Xem xét lại*.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        period = await self._periods.require_period(scope.period_id)
        self._require_open(period)
        await self._require_no_finalized_performance(period)
        candidates, truncated = await self._candidates(period, scope)
        tally = await self._classify(
            actor=actor, request_id=request_id, period=period, scope=scope, ids=candidates
        )
        preview = await self._preview_from(period, scope, candidates, truncated, tally)
        await self._audit_requested(
            actor,
            request_id,
            AuditAction.PR_WORK_CONTENT_REBUILD_REQUESTED,
            preview,
            note=note,
        )

        reason = "Xây dựng lại từ Nội dung" + (f": {note.strip()}" if note and note.strip() else "")
        removed = 0
        for _content_id, source_key in tally.to_remove:
            taken = await self._results.reverse_source_result(
                actor=actor,
                request_id=request_id,
                source_type=PrWorkResultSource.CONTENT,
                source_key=source_key,
                reason=reason[:MAX_MAINTENANCE_NOTE],
            )
            if taken is not None:
                removed += 1

        targets = sorted(tally.eligible)
        reports, counts = await self._project(actor, request_id, targets)
        affected = self._subjects_of(reports) | tally.users
        refreshed = await self._refresh_performance(actor, request_id, period, affected)
        run = MaintenanceRun(
            operation="rebuild",
            preview=preview,
            content_items=len(targets),
            results_removed=removed,
            counts=counts,
            performance_refreshed=refreshed,
            reports=tuple(reports),
        )
        await self._audit_completed(
            actor, request_id, AuditAction.PR_WORK_CONTENT_REBUILD_COMPLETED, run, note=note
        )
        return run

    # =====================================================================
    # Removing one result, one empty container
    # =====================================================================
    async def admin_remove_result(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        result_id: uuid.UUID,
        note: str | None = None,
    ) -> PrWorkResult:
        """Take one result out of the actual, whatever its source or status.

        ``PR_WORK_CONFIGURE``, open period only. An **exclusion**, like every
        correction in this ledger: the row stays, says who took it out and why,
        and the container's actual, its contribution, M2 and the stored
        performance figure follow. Distinct from a validator's exclusion in
        who may do it and in the audit action it writes.

        A content-derived result whose source is still accepted **may be
        recorded again** by the next projection - the content workflow is the
        source of truth, and this operation does not suppress it. The screen
        says so before the click. ``exclusion_kind = ADMIN_REMOVED`` is what
        tells the projector it may; this method itself projects nothing,
        queues nothing and touches no content.

        **A validator's rejection is not removable here.** Removing it would
        rewrite a reviewed decision as a resyncable one, and the next
        projection would bring the result back over the validator's head. The
        refusal is ``work_result_validator_rejected``; *Xem xét lại* is the
        release, and it belongs to ``PR_WORK_VALIDATE``.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        result = await self._session.get(PrWorkResult, result_id)
        if result is None:
            raise PrNotFoundError(
                "Không tìm thấy kết quả công việc.",
                details={"entity": "pr_work_result", "id": str(result_id)},
            )
        item = await lock_row(self._session, PrWorkItem, result.work_item_id)
        if item is None:  # pragma: no cover - RESTRICT key
            raise PrNotFoundError("Không tìm thấy công việc.", details={"id": str(result_id)})
        # Under the lock, the row as it is now - a validator may have decided
        # on it between the read above and the lock being granted, and that
        # decision is exactly what the refusal below is for.
        await self._session.refresh(result)
        period = await self._container_period(item)
        self._require_open(period)
        await self._require_no_finalized_performance(period, user_id=item.subject_user_id)
        if result.status is PrWorkCountStatus.EXCLUDED:
            if result.exclusion_kind is PrWorkExclusionKind.VALIDATOR_REJECTED:
                raise PrConflictError(
                    "Kết quả đã bị từ chối bởi người xác nhận. "
                    "Hãy dùng 'Xem xét lại' trước khi thay đổi trạng thái.",
                    details={
                        "reason": "work_result_validator_rejected",
                        "result_id": str(result.id),
                        "exclusion_kind": result.exclusion_kind.value,
                    },
                )
            raise PrConflictError(
                "Kết quả này đã được loại bỏ rồi.",
                details={
                    "reason": "work_result_not_admin_removable",
                    "result_id": str(result.id),
                    "exclusion_kind": (
                        result.exclusion_kind.value if result.exclusion_kind else None
                    ),
                },
            )
        before = result.status
        now = utcnow()
        text = "Quản trị viên gỡ kết quả" + (f": {note.strip()}" if note and note.strip() else "")
        result.status = PrWorkCountStatus.EXCLUDED
        result.counted_at = None
        result.counted_by_user_id = None
        result.excluded_at = now
        result.excluded_by_user_id = actor.user_id
        result.excluded_reason = text[:MAX_MAINTENANCE_NOTE]
        result.exclusion_kind = PrWorkExclusionKind.ADMIN_REMOVED
        await self._session.flush()
        await self._results.sync_container(item, now=now)
        await self._work.record_history(
            item,
            event=PrWorkEventType.RESULT_ADMIN_REMOVED,
            actor=actor,
            note=note.strip() if note and note.strip() else None,
            metadata={
                "result_id": str(result.id),
                "quantity": str(result.quantity),
                "source_type": result.source_type.value,
                "from_status": before.value,
                "to_status": result.status.value,
                "exclusion_kind": PrWorkExclusionKind.ADMIN_REMOVED.value,
                "admin": True,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_ADMIN_REMOVED,
            entity_type="pr_work_result",
            entity_id=result.id,
            before={
                "status": before.value,
                "exclusion_kind": None,
                "quantity": str(result.quantity),
            },
            after={
                "status": result.status.value,
                "exclusion_kind": PrWorkExclusionKind.ADMIN_REMOVED.value,
                "result_id": str(result.id),
                "work_item_id": str(item.id),
                "code": item.code,
                "work_type_id": str(item.work_type_id),
                "source_type": result.source_type.value,
                "source_key": result.source_key,
                "period": period.code if period else None,
                "reporting_period_id": str(period.id) if period else None,
                "subject_user_id": str(item.subject_user_id) if item.subject_user_id else None,
                "quantity": str(result.quantity),
                "actual_quantity": str(item.quantity),
                "note": note,
            },
        )
        if period is not None and item.subject_user_id is not None:
            await self._refresh_performance(actor, request_id, period, {item.subject_user_id})
        return result

    async def remove_empty_container(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        note: str | None = None,
    ) -> None:
        """Delete a period container that holds **no results at all**.

        ``PR_WORK_CONFIGURE``, open period only, and only a container nobody
        assigned: a stream a manager handed out (``assigned_by_user_id``) or a
        routine opened (``recurring_occurrence_id``) is real work whether or
        not anything has been reported into it yet, and stays. A stream the
        projector or a self-report opened and that has since been emptied - by
        a rebuild that refiled its results elsewhere - is what this removes,
        so the type it was under can be deleted once nothing else refers to
        it.

        Its own contribution, history rows and any allocation rows on that
        contribution go with it; they described a stream that no longer holds
        anything. Evidence refuses the delete - a file somebody attached is not
        the container's own bookkeeping.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        item = await lock_row(self._session, PrWorkItem, work_item_id)
        if item is None:
            raise PrNotFoundError(
                "Không tìm thấy công việc.",
                details={"entity": "pr_work_item", "id": str(work_item_id)},
            )
        refusal = await self._container_removal_refusal(item)
        if refusal is not None:
            raise PrValidationError(
                "Không thể xóa luồng công việc này.",
                details={"reason": "work_container_not_removable", "cause": refusal},
            )
        period = await self._container_period(item)
        self._require_open(period)
        contributions = (
            (
                await self._session.execute(
                    select(PrWorkContribution.id).where(PrWorkContribution.work_item_id == item.id)
                )
            )
            .scalars()
            .all()
        )
        summary = {
            "code": item.code,
            "work_type_id": str(item.work_type_id),
            "subject_user_id": str(item.subject_user_id) if item.subject_user_id else None,
            "period": period.code if period else None,
            "contributions": len(contributions),
            "history_rows": await self._count(
                select(func.count())
                .select_from(PrWorkHistory)
                .where(PrWorkHistory.work_item_id == item.id)
            ),
            "note": note,
        }
        if contributions:
            await self._session.execute(
                delete(PrWorkQuotaAllocation).where(
                    PrWorkQuotaAllocation.work_contribution_id.in_(contributions)
                )
            )
            await self._session.execute(
                delete(PrWorkScoreAllocation).where(
                    PrWorkScoreAllocation.work_contribution_id.in_(contributions)
                )
            )
        await self._session.execute(
            delete(PrWorkHistory).where(PrWorkHistory.work_item_id == item.id)
        )
        await self._session.execute(
            delete(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
        )
        await self._session.execute(delete(PrWorkItem).where(PrWorkItem.id == item.id))
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_CONTAINER_ADMIN_REMOVED,
            entity_type="pr_work_item",
            entity_id=work_item_id,
            before=summary,
        )

    async def removable_empty_containers(
        self, *, actor: Actor, work_type_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """The empty containers under one type that :meth:`remove_empty_container` would accept."""
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        rows = (
            (
                await self._session.execute(
                    select(PrWorkItem).where(
                        PrWorkItem.work_type_id == work_type_id,
                        PrWorkItem.reporting_period_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        out: list[uuid.UUID] = []
        for item in rows:
            if await self._container_removal_refusal(item) is None:
                period = await self._container_period(item)
                if period is not None and period.status is PrPeriodStatus.OPEN:
                    out.append(item.id)
        return out

    # =====================================================================
    # Deleting one legacy content work item
    # =====================================================================
    async def admin_delete_legacy_work_item(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        note: str | None = None,
    ) -> LegacyWorkItemDeletion:
        """Delete one **old-version, item-grain** content work item outright.

        ``PR_WORK_CONFIGURE``, open month only, one row per call, and only a
        row :func:`~meobot.domain.pr.content_work.is_legacy_content_work_item`
        accepts - ``source_type = CONTENT`` on a row that is not a period
        container. Manual work, recurring work, a container and a modern
        content result are all refused with ``work_item_not_legacy_content``;
        this is not a generic hard delete with a friendlier name.

        **The administrator decides, and only about this row.** Nothing here
        looks for other legacy rows, and nothing here re-records the content:
        no projection is requested, no result is written, no container is
        opened, and the content item itself - its lifecycle, approvals,
        versions, attachments and history - is not read for writing at all.
        If the content still qualifies, *Đồng bộ dữ liệu công việc* records it
        again as a modern result when, and only when, somebody runs it. A
        projection already queued for the same content by a content event is
        left exactly as it was: it is not this operation's, and cancelling it
        would be deciding something about the content workflow.

        What goes with the row is what the row owns and nothing else: its
        contributions, the M2 and M6 allocation rows written *against those
        contributions*, its evidence and its history. The audit row - written
        against the item's id, outside the item - stays, and one more is
        added with a summary of what was removed. A row that somehow holds
        results is refused (``work_item_delete_blocked``) rather than
        cascaded: results are a container's, and a legacy item with one is a
        state this code did not create and will not guess about.

        If any contribution was ``COUNTED``, the month's actual drops by that
        much and stays down: M2 is re-evaluated for the person and month, and
        the stored, unfinalised performance figure is recomputed. Neither
        formula changes. A ``CLOSED`` or ``LOCKED`` month, or a finalised
        figure, refuses the whole thing before anything is touched.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        item = await lock_row(self._session, PrWorkItem, work_item_id)
        if item is None:
            raise PrNotFoundError(
                "Không tìm thấy công việc.",
                details={
                    "entity": "pr_work_item",
                    "id": str(work_item_id),
                    "reason": WORK_ITEM_NOT_FOUND,
                },
            )
        cause = self._legacy_refusal(item)
        if cause is not None:
            raise PrConflictError(
                "Chỉ xóa được công việc cũ được tạo từ cơ chế Nội dung trước đây.",
                details={
                    "reason": WORK_ITEM_NOT_LEGACY_CONTENT,
                    "cause": cause,
                    "work_item_id": str(item.id),
                    "code": item.code,
                },
            )
        results = await self._count(
            select(func.count())
            .select_from(PrWorkResult)
            .where(PrWorkResult.work_item_id == item.id)
        )
        if results:
            raise PrConflictError(
                "Không thể xóa công việc này vì đang giữ kết quả trong kỳ.",
                details={
                    "reason": WORK_ITEM_DELETE_BLOCKED,
                    "cause": "has_results",
                    "work_item_id": str(item.id),
                    "results": results,
                },
            )

        contributions = (
            (
                await self._session.execute(
                    select(PrWorkContribution)
                    .where(PrWorkContribution.work_item_id == item.id)
                    .order_by(PrWorkContribution.assigned_at.asc())
                )
            )
            .scalars()
            .all()
        )
        counted = [row for row in contributions if row.count_status is PrWorkCountStatus.COUNTED]
        period = await self._legacy_period(item, counted)
        self._require_open(
            period, message=PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE, operation=LEGACY_DELETE_OPERATION
        )
        for row in counted:
            await self._require_no_finalized_performance(
                period,
                user_id=row.user_id,
                message=PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE,
                operation=LEGACY_DELETE_OPERATION,
            )
        if period is not None:
            # **Item, then period** - the order every M2 writer takes. A
            # reconcile locks the period before it materialises allocations;
            # without this lock it could name a contribution between the
            # allocation delete below and the contribution delete after it,
            # and the delete would fail on the foreign key it had just cleared.
            await lock_row(self._session, PrReportingPeriod, period.id)

        contribution_ids = [row.id for row in contributions]
        removed = {
            "contributions": len(contributions),
            "counted_contributions": len(counted),
            "quota_allocations": 0,
            "score_allocations": 0,
            "evidence": await self._count(
                select(func.count())
                .select_from(PrWorkEvidence)
                .where(PrWorkEvidence.work_item_id == item.id)
            ),
            "history": await self._count(
                select(func.count())
                .select_from(PrWorkHistory)
                .where(PrWorkHistory.work_item_id == item.id)
            ),
        }
        if contribution_ids:
            removed["quota_allocations"] = await self._count(
                select(func.count())
                .select_from(PrWorkQuotaAllocation)
                .where(PrWorkQuotaAllocation.work_contribution_id.in_(contribution_ids))
            )
            removed["score_allocations"] = await self._count(
                select(func.count())
                .select_from(PrWorkScoreAllocation)
                .where(PrWorkScoreAllocation.work_contribution_id.in_(contribution_ids))
            )
        primary = next(
            (
                row
                for row in contributions
                if row.contribution_role is PrWorkContributionRole.PRIMARY
            ),
            contributions[0] if contributions else None,
        )
        content_code: str | None = None
        if item.content_id is not None:
            content_code = (
                await self._session.execute(
                    select(PrContentItem.code).where(PrContentItem.id == item.content_id)
                )
            ).scalar_one_or_none()
        summary: dict[str, Any] = {
            "code": item.code,
            "title": item.title,
            "status": item.status.value,
            "source_type": item.source_type.value,
            "source_key": item.source_key,
            "content_id": str(item.content_id) if item.content_id else None,
            "content_code": content_code,
            "work_type_id": str(item.work_type_id),
            "responsible_user_id": str(primary.user_id) if primary else None,
            "contributor_user_ids": [str(row.user_id) for row in contributions],
            "counted_user_ids": [str(row.user_id) for row in counted],
            "quantity": str(item.quantity) if item.quantity is not None else None,
            "unit": item.unit.value if item.unit else None,
            "execution_at": item.execution_at.isoformat() if item.execution_at else None,
            "period": period.code if period else None,
            "removed": dict(removed),
            "note": note,
        }

        # Children first, in foreign-key order: history names contributions,
        # allocations name contributions, and everything names the item.
        # Only rows keyed on *this* item or *its* contributions are touched -
        # nothing here is a range delete over a user, a type or a month.
        if contribution_ids:
            await self._session.execute(
                delete(PrWorkQuotaAllocation).where(
                    PrWorkQuotaAllocation.work_contribution_id.in_(contribution_ids)
                )
            )
            await self._session.execute(
                delete(PrWorkScoreAllocation).where(
                    PrWorkScoreAllocation.work_contribution_id.in_(contribution_ids)
                )
            )
        await self._session.execute(
            delete(PrWorkHistory).where(PrWorkHistory.work_item_id == item.id)
        )
        await self._session.execute(
            delete(PrWorkEvidence).where(PrWorkEvidence.work_item_id == item.id)
        )
        await self._session.execute(
            delete(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
        )
        await self._session.execute(delete(PrWorkItem).where(PrWorkItem.id == item.id))
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_ITEM_ADMIN_DELETED,
            entity_type="pr_work_item",
            entity_id=work_item_id,
            before=summary,
            after={"deleted": True, "results_created": 0, "projection_requested": False},
        )

        # The month's actual has moved for whoever was counted. M2 recomputes
        # the whole (person, month) from what is *still* counted - the same
        # convergent recompute a reversal triggers - and the stored figure
        # follows. Both behind the same savepoint rule every other handoff
        # uses: a derived figure that cannot be computed is logged, and never
        # undoes the deletion the administrator confirmed.
        affected = {row.user_id for row in counted}
        now = utcnow()
        if period is not None and affected and self._eligibility is not None:
            for user_id in sorted(affected, key=str):
                try:
                    async with self._session.begin_nested():
                        await self._eligibility.evaluate(user_id=user_id, period=period, now=now)
                except Exception:  # deliberately broad; see PrWorkService._project_eligibility
                    logger.exception(
                        "pr_work_quota_reconcile_failed",
                        extra={"pr_work_item_id": str(work_item_id), "pr_user_id": str(user_id)},
                    )
        refreshed = await self._refresh_performance(actor, request_id, period, affected)
        logger.info(
            "pr_work_legacy_item_deleted",
            extra={
                "pr_work_item_id": str(work_item_id),
                "pr_work_code": summary["code"],
                "pr_period": summary["period"],
                "pr_removed": dict(removed),
            },
        )
        return LegacyWorkItemDeletion(
            work_item_id=work_item_id,
            code=item.code,
            title=item.title,
            work_type_id=item.work_type_id,
            content_id=item.content_id,
            content_code=content_code,
            source_key=item.source_key,
            responsible_user_id=primary.user_id if primary else None,
            period_code=period.code if period else None,
            removed=removed,
            performance_refreshed=refreshed,
        )

    # =====================================================================
    # Cancelled work items
    # =====================================================================
    async def terminal_delete_eligibility(self, item: PrWorkItem) -> TerminalDeleteEligibility:
        """May this row be deleted through :meth:`admin_delete_terminal_work_item` now?

        For a ``CANCELLED`` or ``REJECTED`` ordinary row - the two terminal
        states an administrator may clean up - and a refusal for everything
        else, with the cause.

        A **read**, and the detail screen's source for *Xóa công việc* and for
        the sentence shown instead of it. It checks no capability - the route
        that calls it has already decided whether this actor may see the
        answer - and it locks nothing: the delete re-derives every count under
        a row lock before it touches anything, so a stale ``True`` here costs
        one structured refusal and never a wrong delete.
        """
        previous = item.status.value
        if item.status not in TERMINAL_DELETABLE_STATUSES:
            return TerminalDeleteEligibility(
                deletable=False,
                reason=WORK_ITEM_NOT_TERMINAL,
                cause="not_terminal",
                message=WORK_ITEM_NOT_TERMINAL_MESSAGE,
                previous_status=previous,
            )
        if item.is_period_container:
            return TerminalDeleteEligibility(
                deletable=False,
                reason=TERMINAL_WORK_ITEM_DELETE_BLOCKED,
                cause="period_container",
                message=TERMINAL_CONTAINER_MESSAGE,
                previous_status=previous,
            )
        blocking = await self._terminal_blockers(item)
        period = await self._legacy_period(item, [])
        if any(blocking.values()):
            return TerminalDeleteEligibility(
                deletable=False,
                reason=TERMINAL_WORK_ITEM_DELETE_BLOCKED,
                cause="blocking_references",
                blocking=blocking,
                period_code=period.code if period else None,
                message=TERMINAL_DELETE_BLOCKED_MESSAGE,
                previous_status=previous,
            )
        if period is not None and period.status is not PrPeriodStatus.OPEN:
            return TerminalDeleteEligibility(
                deletable=False,
                reason=PERIOD_NOT_OPEN_FOR_CLEANUP,
                cause="period_not_open",
                blocking=blocking,
                period_code=period.code,
                message=PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE,
                previous_status=previous,
            )
        return TerminalDeleteEligibility(
            deletable=True,
            blocking=blocking,
            period_code=period.code if period else None,
            previous_status=previous,
        )

    async def admin_delete_terminal_work_item(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        note: str | None = None,
    ) -> TerminalWorkItemDeletion:
        """Delete one **terminal, ordinary** work item outright.

        ``PR_WORK_CONFIGURE``, one row per call, and only a row that is
        ``CANCELLED`` or ``REJECTED`` (:data:`TERMINAL_DELETABLE_STATUSES`) and
        not a period container. A rejected proposal is as finished as a
        cancelled job - the table admits no edge out of either - and needs no
        detour through ``CANCELLED`` (there is none) to be cleaned up; the
        status is recorded as ``previous_status`` so the trail says which. This is the second
        eligibility rule beside :meth:`admin_delete_legacy_work_item` and is
        deliberately not a generalisation of it: that one is about
        *provenance* (an old content projection, whatever its status), this
        one is about *lifecycle* (abandoned work, whatever its source). A
        manual job, a recurring occurrence's job and a legacy content row all
        qualify here once cancelled; an ``ACTIVE``, ``APPROVED``, ``REJECTED``
        or ``PROPOSED`` row is refused with ``work_item_not_cancelled``, and a
        container - which the lifecycle never lets into ``CANCELLED``, so this
        is only reachable by old data - with ``terminal_work_item_delete_blocked``.

        **Cancelled does not mean safe to destroy.** Cancelling excludes the
        row's pending credit; it does not promise there is nothing else. So
        before anything is touched the row's accounting is counted - results,
        ``COUNTED`` contributions, and the M2 and M6 allocation rows on its
        contributions - and any of them refuses the whole delete with
        ``terminal_work_item_delete_blocked`` and the counts in ``details``.
        Nothing is repaired and nothing is cascaded: a cancelled row that
        somehow holds accounting is a state this code did not create, and
        the administrator is told what it holds rather than having it
        quietly removed. A ``CLOSED`` or ``LOCKED`` month refuses too, the
        same way every cleanup does.

        What goes with the row is only what the row exclusively owns and
        that represents no accounting: its uncounted contributions, its
        evidence and its history. The audit rows written against the item's
        id stay - they live outside the item - and one more is added with a
        summary. Nothing is projected, synced, rebuilt or recomputed: with no
        counted credit removed, no month's actual has moved.

        Atomic: lock the row, verify, count, delete the children, audit,
        delete the row. A failure anywhere rolls all of it back; two
        administrators pressing at once get one winner and one
        ``work_item_not_found``.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        if note is not None and len(note) > MAX_MAINTENANCE_NOTE:
            raise PrValidationError(
                "Ghi chú quá dài.",
                details={"reason": "note_too_long", "max_length": MAX_MAINTENANCE_NOTE},
            )
        item = await lock_row(self._session, PrWorkItem, work_item_id)
        if item is None:
            raise PrNotFoundError(
                "Không tìm thấy công việc.",
                details={
                    "entity": "pr_work_item",
                    "id": str(work_item_id),
                    "reason": WORK_ITEM_NOT_FOUND,
                },
            )
        if item.status not in TERMINAL_DELETABLE_STATUSES:
            raise PrConflictError(
                WORK_ITEM_NOT_TERMINAL_MESSAGE,
                details={
                    "reason": WORK_ITEM_NOT_TERMINAL,
                    "cause": "not_terminal",
                    "status": item.status.value,
                    "work_item_id": str(item.id),
                    "code": item.code,
                    "operation": TERMINAL_DELETE_OPERATION,
                },
            )
        if item.is_period_container:
            raise PrConflictError(
                TERMINAL_CONTAINER_MESSAGE,
                details={
                    "reason": TERMINAL_WORK_ITEM_DELETE_BLOCKED,
                    "cause": "period_container",
                    "work_item_id": str(item.id),
                    "code": item.code,
                    "operation": TERMINAL_DELETE_OPERATION,
                },
            )
        blocking = await self._terminal_blockers(item)
        if any(blocking.values()):
            raise PrConflictError(
                TERMINAL_DELETE_BLOCKED_MESSAGE,
                details={
                    "reason": TERMINAL_WORK_ITEM_DELETE_BLOCKED,
                    "cause": "blocking_references",
                    "work_item_id": str(item.id),
                    "code": item.code,
                    "blocking": dict(blocking),
                    "operation": TERMINAL_DELETE_OPERATION,
                },
            )
        period = await self._legacy_period(item, [])
        self._require_open(
            period,
            message=PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE,
            operation=TERMINAL_DELETE_OPERATION,
        )
        if period is not None:
            # Item, then period - the order every other writer takes, so a
            # reconcile holding the month cannot deadlock against this row.
            await lock_row(self._session, PrReportingPeriod, period.id)

        contributions = (
            (
                await self._session.execute(
                    select(PrWorkContribution)
                    .where(PrWorkContribution.work_item_id == item.id)
                    .order_by(PrWorkContribution.assigned_at.asc())
                )
            )
            .scalars()
            .all()
        )
        # The count above said none is COUNTED; the assert keeps that a fact
        # of this transaction rather than of the moment before the lock.
        assert all(row.count_status is not PrWorkCountStatus.COUNTED for row in contributions)
        removed = {
            "contributions": len(contributions),
            "evidence": await self._count(
                select(func.count())
                .select_from(PrWorkEvidence)
                .where(PrWorkEvidence.work_item_id == item.id)
            ),
            "history": await self._count(
                select(func.count())
                .select_from(PrWorkHistory)
                .where(PrWorkHistory.work_item_id == item.id)
            ),
        }
        primary = next(
            (
                row
                for row in contributions
                if row.contribution_role is PrWorkContributionRole.PRIMARY
            ),
            contributions[0] if contributions else None,
        )
        content_code: str | None = None
        if item.content_id is not None:
            content_code = (
                await self._session.execute(
                    select(PrContentItem.code).where(PrContentItem.id == item.content_id)
                )
            ).scalar_one_or_none()
        summary: dict[str, Any] = {
            "operation": TERMINAL_DELETE_OPERATION,
            "code": item.code,
            "title": item.title,
            "previous_status": item.status.value,
            "work_type_id": str(item.work_type_id),
            "source_type": item.source_type.value,
            "source_key": item.source_key,
            "content_id": str(item.content_id) if item.content_id else None,
            "content_code": content_code,
            "recurring_occurrence_id": (
                str(item.recurring_occurrence_id) if item.recurring_occurrence_id else None
            ),
            "responsible_user_id": str(primary.user_id) if primary else None,
            "contributor_user_ids": [str(row.user_id) for row in contributions],
            "quantity": str(item.quantity) if item.quantity is not None else None,
            "unit": item.unit.value if item.unit else None,
            "execution_at": item.execution_at.isoformat() if item.execution_at else None,
            "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
            "cancelled_by_user_id": (
                str(item.cancelled_by_user_id) if item.cancelled_by_user_id else None
            ),
            "cancel_reason": item.cancel_reason,
            "period": period.code if period else None,
            "removed": dict(removed),
            "note": note,
        }

        # Children first, in foreign-key order: history names contributions,
        # and everything names the item. Only rows keyed on *this* item are
        # touched; there is no allocation to delete because any would have
        # refused above.
        await self._session.execute(
            delete(PrWorkHistory).where(PrWorkHistory.work_item_id == item.id)
        )
        await self._session.execute(
            delete(PrWorkEvidence).where(PrWorkEvidence.work_item_id == item.id)
        )
        await self._session.execute(
            delete(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
        )
        await self._session.execute(delete(PrWorkItem).where(PrWorkItem.id == item.id))
        await self._session.flush()

        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_ITEM_ADMIN_DELETED,
            entity_type="pr_work_item",
            entity_id=work_item_id,
            before=summary,
            after={"deleted": True, "results_created": 0, "projection_requested": False},
        )
        logger.info(
            "pr_work_terminal_item_deleted",
            extra={
                "pr_work_item_id": str(work_item_id),
                "pr_work_code": summary["code"],
                "pr_period": summary["period"],
                "pr_removed": dict(removed),
            },
        )
        return TerminalWorkItemDeletion(
            work_item_id=work_item_id,
            code=item.code,
            title=item.title,
            work_type_id=item.work_type_id,
            source_type=item.source_type.value,
            source_key=item.source_key,
            content_id=item.content_id,
            content_code=content_code,
            recurring_occurrence_id=item.recurring_occurrence_id,
            responsible_user_id=primary.user_id if primary else None,
            period_code=period.code if period else None,
            removed=removed,
            previous_status=summary["previous_status"],
        )

    async def _terminal_blockers(self, item: PrWorkItem) -> dict[str, int]:
        """The accounting rows a cancelled item still holds, by kind.

        Four counts, each a kind of row this operation will not delete:
        results (a container's, and a fact somebody reported), counted
        contributions (a month's actual), and the M2 and M6 allocation rows
        written against any of the item's contributions. Allocations are only
        ever written against counted contributions, so on a row the lifecycle
        cancelled all four are zero; a non-zero here is old or hand-edited
        data, and it is reported rather than repaired.
        """
        contribution_ids = select(PrWorkContribution.id).where(
            PrWorkContribution.work_item_id == item.id
        )
        return {
            "results": await self._count(
                select(func.count())
                .select_from(PrWorkResult)
                .where(PrWorkResult.work_item_id == item.id)
            ),
            "counted_contributions": await self._count(
                select(func.count())
                .select_from(PrWorkContribution)
                .where(
                    PrWorkContribution.work_item_id == item.id,
                    PrWorkContribution.count_status == PrWorkCountStatus.COUNTED,
                )
            ),
            "quota_allocations": await self._count(
                select(func.count())
                .select_from(PrWorkQuotaAllocation)
                .where(PrWorkQuotaAllocation.work_contribution_id.in_(contribution_ids))
            ),
            "score_allocations": await self._count(
                select(func.count())
                .select_from(PrWorkScoreAllocation)
                .where(PrWorkScoreAllocation.work_contribution_id.in_(contribution_ids))
            ),
        }

    @staticmethod
    def _legacy_refusal(item: PrWorkItem) -> str | None:
        """Why a row is not a legacy content item, or ``None`` when it is.

        The predicate is the domain's; this only names the cause for the
        refusal so the screen can say *which* kind of row was pointed at.
        """
        if is_legacy_content_work_item(item):
            return None
        if item.reporting_period_id is not None:
            return "period_container"
        if item.recurring_occurrence_id is not None:
            return "recurring"
        if item.source_type is PrWorkSourceType.MANUAL:
            return "manual"
        return item.source_type.value.lower()

    async def _legacy_period(
        self, item: PrWorkItem, counted: Sequence[PrWorkContribution]
    ) -> PrReportingPeriod | None:
        """The month a legacy item's accounting belongs to.

        Counted work belongs to the month containing ``counted_at`` - M1's
        period-attribution rule, unchanged. Uncounted work has no accounting
        in any month and is guarded by the month it was performed in, so a
        stale row from a locked month is not quietly removed either. ``None``
        - no period opened for that month - means there is nothing to refuse
        and nothing to recompute.
        """
        instants = [row.counted_at for row in counted if row.counted_at is not None]
        at = (
            min(instants)
            if instants
            else (item.execution_at or item.completed_at or item.created_at)
        )
        return await self._periods.period_for(at)

    # =====================================================================
    # Work types
    # =====================================================================
    async def work_type_references(
        self, *, actor: Actor, work_type_id: uuid.UUID
    ) -> WorkTypeReferences:
        """Everything that points at one work type, counted. ``PR_WORK_CONFIGURE``."""
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        await self._require_work_type(work_type_id)
        return await self._references(work_type_id)

    async def delete_work_type(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_type_id: uuid.UUID,
        note: str | None = None,
    ) -> WorkTypeReferences:
        """Delete a work type **nothing refers to**. ``PR_WORK_CONFIGURE``.

        Refused - with every blocking count - while any work item, result,
        contribution, routine, quota, allocation, scoring rule or active
        content mapping names it. Those are decisions and accounting, and none
        of them is removed on the way to deleting a heading. The one thing that
        goes with the type is its **inactive** content mappings: a rule
        somebody already turned off is configuration with nothing under it,
        and leaving it would leave a dangling reference the database refuses.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        row = await self._require_work_type(work_type_id)
        references = await self._references(work_type_id)
        if not references.deletable:
            raise PrConflictError(
                "Không thể xóa loại công việc này vì vẫn đang được sử dụng.",
                details={
                    "reason": "work_type_still_in_use",
                    "work_type_code": row.code,
                    "references": references.blocking,
                    "empty_containers_removable": references.empty_containers_removable,
                },
            )
        await self._session.execute(
            delete(PrContentWorkRule).where(PrContentWorkRule.work_type_id == row.id)
        )
        summary = {
            "code": row.code,
            "name": row.name,
            "category": row.category.value,
            "default_unit": row.default_unit.value,
            "default_quota_basis": row.default_quota_basis.value,
            "is_active": row.is_active,
            "inactive_rules_removed": references.content_rules_inactive,
            "note": note,
        }
        await self._session.execute(delete(PrWorkType).where(PrWorkType.id == row.id))
        await self._session.flush()
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_TYPE_DELETED,
            entity_type="pr_work_type",
            entity_id=work_type_id,
            before=summary,
        )
        return references

    # =====================================================================
    # Internals: scope and classification
    # =====================================================================
    async def _candidates(
        self, period: PrReportingPeriod, scope: MaintenanceScope
    ) -> tuple[list[uuid.UUID], bool]:
        """Content that could have work in this period, bounded.

        Two sources, united: pieces whose accepting decision - the head's
        approval of the script, the internal reviewer's acceptance of the cut -
        was taken inside the month, and pieces that already have a
        content-derived result in one of the month's containers. The second is
        what finds a stale result whose approval was since undone.
        """
        lower, upper = self._periods.bounds(period)
        approved = (
            select(PrApprovalEvent.content_id)
            .where(
                PrApprovalEvent.decision == PrApprovalDecision.APPROVED,
                PrApprovalEvent.approval_stage.in_(
                    [PrApprovalStage.HEAD_REVIEW, PrApprovalStage.INTERNAL_REVIEW]
                ),
                PrApprovalEvent.decided_at >= lower,
                PrApprovalEvent.decided_at < upper,
            )
            .order_by(PrApprovalEvent.decided_at.asc())
        )
        recorded = (
            select(PrWorkResult.source_key)
            .join(PrWorkItem, PrWorkItem.id == PrWorkResult.work_item_id)
            .where(
                PrWorkItem.reporting_period_id == period.id,
                PrWorkResult.source_type == PrWorkResultSource.CONTENT,
            )
        )
        ordered: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for content_id in (await self._session.execute(approved)).scalars():
            if content_id not in seen:
                seen.add(content_id)
                ordered.append(content_id)
        for key in (await self._session.execute(recorded)).scalars():
            entity = _entity_of(key)
            if entity is not None and entity not in seen:
                seen.add(entity)
                ordered.append(entity)
        if scope.content_type is not None and ordered:
            typed = set(
                (
                    await self._session.execute(
                        select(PrContentItem.id).where(
                            PrContentItem.id.in_(ordered),
                            PrContentItem.content_type == scope.content_type,
                        )
                    )
                ).scalars()
            )
            ordered = [one for one in ordered if one in typed]
        limit = max(1, min(scope.limit, MAX_MAINTENANCE_CONTENT))
        return ordered[:limit], len(ordered) > limit

    async def _classify(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        period: PrReportingPeriod,
        scope: MaintenanceScope,
        ids: Sequence[uuid.UUID],
    ) -> _Tally:
        """Put every candidate through the projector's dry run and file the outcomes."""
        tally = _Tally()
        resolver = await self._projector.resolver()
        for content_id in ids:
            report = await self._projector.project_content(
                actor=actor,
                request_id=request_id,
                content_id=content_id,
                resolver=resolver,
                dry_run=True,
            )
            for result in report.results:
                row = await self._finding(period, scope, report, result)
                if row is None:
                    continue
                tally.rows.append(row)
                tally.add(row)
                if row.finding != Finding.BLOCKED:
                    tally.eligible.add(row.content_id)
                if row.contributor_user_id is not None:
                    tally.users.add(row.contributor_user_id)
                for type_id in (row.current_work_type_id, row.expected_work_type_id):
                    if type_id is not None:
                        tally.work_types.add(type_id)
                if row.finding == Finding.WRONG_WORK_TYPE and result.source_key is not None:
                    tally.to_remove.append((row.content_id, result.source_key))
                if (
                    row.finding in (Finding.MISSING, Finding.WRONG_WORK_TYPE)
                    and row.expected_work_type_id is not None
                ):
                    tally.recreate[row.expected_work_type_id] = (
                        tally.recreate.get(row.expected_work_type_id, 0) + 1
                    )
                if row.finding == Finding.NEW_WORK_TYPE:
                    key = await self._content_type_of(row.content_id)
                    tally.provision[key] = tally.provision.get(key, 0) + 1
        return tally

    async def _finding(
        self,
        period: PrReportingPeriod,
        scope: MaintenanceScope,
        report: ContentProjectionReport,
        result: ProjectionResult,
    ) -> FindingRow | None:
        outcome = result.outcome
        if outcome is PrContentWorkOutcome.NOT_QUALIFIED:
            return None
        existing_type: uuid.UUID | None = None
        existing_period: uuid.UUID | None = None
        if result.work_item_id is not None:
            container = await self._session.get(PrWorkItem, result.work_item_id)
            if container is not None:
                existing_type = container.work_type_id
                existing_period = container.reporting_period_id
        contributor = result.contributor_user_id
        if contributor is None and result.work_item_id is not None:
            container = await self._session.get(PrWorkItem, result.work_item_id)
            contributor = container.subject_user_id if container is not None else None
        if scope.user_id is not None and contributor != scope.user_id:
            return None
        # A result that lives in another month is that month's business.
        if existing_period is not None and existing_period != period.id:
            return None

        if outcome is PrContentWorkOutcome.BLOCKED_BY_PERIOD:
            finding = Finding.BLOCKED
        elif outcome is PrContentWorkOutcome.REVERSED:
            finding = Finding.STALE
        elif outcome is PrContentWorkOutcome.HELD_BY_VALIDATOR:
            # The ledger says what a validator decided it should say. Not
            # missing, not wrongly typed, and nothing sync or rebuild will
            # change: the release is *Xem xét lại*, a validator's act.
            finding = Finding.CORRECT
        elif outcome is PrContentWorkOutcome.NO_MAPPING:
            finding = Finding.UNMAPPED
        elif outcome is PrContentWorkOutcome.UNRESOLVED_CONTRIBUTOR:
            finding = Finding.UNRESOLVED
        elif result.work_item_id is None:
            finding = Finding.NEW_WORK_TYPE if result.provisioned else Finding.MISSING
        elif result.work_type_id is not None and existing_type != result.work_type_id:
            finding = Finding.WRONG_WORK_TYPE
        else:
            finding = Finding.CORRECT
        return FindingRow(
            content_id=report.content_id,
            content_code=report.content_code,
            contribution_kind=result.kind.value,
            finding=finding,
            contributor_user_id=contributor,
            current_work_type_id=existing_type,
            expected_work_type_id=result.work_type_id,
            detail=result.detail,
        )

    async def _preview_from(
        self,
        period: PrReportingPeriod,
        scope: MaintenanceScope,
        candidates: Sequence[uuid.UUID],
        truncated: bool,
        tally: _Tally,
    ) -> MaintenancePreview:
        counts = tally.counts
        manual_results, manual_items, recurring_results = await self._untouched(period, scope)
        return MaintenancePreview(
            period_id=period.id,
            period_code=period.code,
            period_status=period.status,
            user_id=scope.user_id,
            content_type=scope.content_type,
            candidate_count=len(candidates),
            truncated=truncated,
            eligible_content_count=len(tally.eligible),
            correct_result_count=counts.get(Finding.CORRECT, 0),
            missing_result_count=(
                counts.get(Finding.MISSING, 0) + counts.get(Finding.NEW_WORK_TYPE, 0)
            ),
            wrong_work_type_count=counts.get(Finding.WRONG_WORK_TYPE, 0),
            stale_result_count=counts.get(Finding.STALE, 0),
            new_work_type_count=len(tally.provision),
            unmapped_count=counts.get(Finding.UNMAPPED, 0),
            unresolved_count=counts.get(Finding.UNRESOLVED, 0),
            blocked_count=counts.get(Finding.BLOCKED, 0),
            results_to_remove=counts.get(Finding.WRONG_WORK_TYPE, 0) + counts.get(Finding.STALE, 0),
            results_to_create=counts.get(Finding.MISSING, 0)
            + counts.get(Finding.NEW_WORK_TYPE, 0)
            + counts.get(Finding.WRONG_WORK_TYPE, 0),
            affected_user_ids=tuple(sorted(tally.users, key=str)),
            affected_work_type_ids=tuple(sorted(tally.work_types, key=str)),
            recreate_by_work_type=dict(tally.recreate),
            provision_by_content_type=dict(tally.provision),
            manual_result_count=manual_results,
            manual_item_count=manual_items,
            recurring_result_count=recurring_results,
            samples=tuple(tally.samples[: SAMPLE_SIZE * 4]),
            finalized_performance_count=await self._finalized_count(period),
        )

    async def _untouched(
        self, period: PrReportingPeriod, scope: MaintenanceScope
    ) -> tuple[int, int, int]:
        """What the period holds that a rebuild never reads: manual and recurring work."""
        lower, upper = self._periods.bounds(period)
        manual_results = (
            select(func.count())
            .select_from(PrWorkResult)
            .join(PrWorkItem, PrWorkItem.id == PrWorkResult.work_item_id)
            .where(
                PrWorkItem.reporting_period_id == period.id,
                PrWorkResult.source_type == PrWorkResultSource.MANUAL,
            )
        )
        manual_items = (
            select(func.count())
            .select_from(PrWorkItem)
            .where(
                PrWorkItem.reporting_period_id.is_(None),
                PrWorkItem.source_key.is_(None),
                PrWorkItem.recurring_occurrence_id.is_(None),
                PrWorkItem.created_at >= lower,
                PrWorkItem.created_at < upper,
            )
        )
        recurring = (
            select(func.count())
            .select_from(PrWorkItem)
            .where(
                PrWorkItem.recurring_occurrence_id.is_not(None),
                PrWorkItem.created_at >= lower,
                PrWorkItem.created_at < upper,
            )
        )
        if scope.user_id is not None:
            manual_results = manual_results.where(PrWorkItem.subject_user_id == scope.user_id)
        return (
            await self._count(manual_results),
            await self._count(manual_items),
            await self._count(recurring),
        )

    # =====================================================================
    # Internals: projection, performance, audit
    # =====================================================================
    async def _project(
        self, actor: Actor, request_id: uuid.UUID, targets: Sequence[uuid.UUID]
    ) -> tuple[list[ContentProjectionReport], dict[str, int]]:
        resolver = await self._projector.resolver()
        counts: dict[str, int] = {outcome.value: 0 for outcome in PrContentWorkOutcome}
        reports: list[ContentProjectionReport] = []
        for content_id in targets:
            report = await self._projector.project_content(
                actor=actor, request_id=request_id, content_id=content_id, resolver=resolver
            )
            reports.append(report)
            for one in report.results:
                counts[one.outcome.value] += 1
        return reports, counts

    @staticmethod
    def _subjects_of(reports: Sequence[ContentProjectionReport]) -> set[uuid.UUID]:
        return {
            one.contributor_user_id
            for report in reports
            for one in report.results
            if one.contributor_user_id is not None
        }

    async def _refresh_performance(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        period: PrReportingPeriod | None,
        user_ids: set[uuid.UUID],
    ) -> int:
        """Recompute the stored monthly figure of everybody whose actual moved.

        Only rows that exist and are not finalised are touched - the
        performance service refuses a finalised row itself, and this module
        has already refused the period before getting here. Nothing is
        computed for a person with no stored row: a snapshot is computed on
        read, so there is nothing stale to refresh.
        """
        if period is None or not user_ids:
            return 0
        refreshed = 0
        for user_id in sorted(user_ids, key=str):
            if await self._performance.refresh_stored(
                actor=actor, request_id=request_id, user_id=user_id, period=period
            ):
                refreshed += 1
        return refreshed

    async def _audit_requested(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        preview: MaintenancePreview,
        *,
        note: str | None = None,
    ) -> None:
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=action,
            entity_type="pr_reporting_period",
            entity_id=preview.period_id,
            after={**_preview_summary(preview), "note": note},
        )

    async def _audit_completed(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        action: AuditAction,
        run: MaintenanceRun,
        *,
        note: str | None = None,
    ) -> None:
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=action,
            entity_type="pr_reporting_period",
            entity_id=run.preview.period_id,
            before=_preview_summary(run.preview),
            after={
                "operation": run.operation,
                "content_items": run.content_items,
                "results_removed": run.results_removed,
                "performance_refreshed": run.performance_refreshed,
                "note": note,
                **run.counts,
            },
        )
        logger.info(
            "pr_work_maintenance_completed",
            extra={
                "pr_operation": run.operation,
                "pr_period": run.preview.period_code,
                "pr_content_items": run.content_items,
                "pr_results_removed": run.results_removed,
            },
        )

    # =====================================================================
    # Internals: guards and lookups
    # =====================================================================
    @staticmethod
    def _require_open(
        period: PrReportingPeriod | None,
        *,
        message: str = PERIOD_NOT_OPEN_MESSAGE,
        operation: str | None = None,
    ) -> None:
        if period is not None and period.status is not PrPeriodStatus.OPEN:
            raise PrConflictError(
                message,
                details={
                    "reason": PERIOD_NOT_OPEN_FOR_CLEANUP,
                    "period": period.code,
                    "status": period.status.value,
                    **({"operation": operation} if operation else {}),
                },
            )

    async def _require_no_finalized_performance(
        self,
        period: PrReportingPeriod | None,
        *,
        user_id: uuid.UUID | None = None,
        message: str = PERIOD_NOT_OPEN_MESSAGE,
        operation: str | None = None,
    ) -> None:
        """The invariant: a finalised month is not rebuilt, whatever its period says."""
        if period is None:
            return
        statement = (
            select(func.count())
            .select_from(PrPerformanceResult)
            .where(
                PrPerformanceResult.reporting_period_id == period.id,
                PrPerformanceResult.finalized_at.is_not(None),
            )
        )
        if user_id is not None:
            statement = statement.where(PrPerformanceResult.user_id == user_id)
        if await self._count(statement):
            raise PrConflictError(
                message,
                details={
                    "reason": PERIOD_NOT_OPEN_FOR_CLEANUP,
                    "cause": "performance_finalized",
                    "period": period.code,
                    **({"operation": operation} if operation else {}),
                },
            )

    async def _finalized_count(self, period: PrReportingPeriod) -> int:
        return await self._count(
            select(func.count())
            .select_from(PrPerformanceResult)
            .where(
                PrPerformanceResult.reporting_period_id == period.id,
                PrPerformanceResult.finalized_at.is_not(None),
            )
        )

    async def _container_period(self, item: PrWorkItem) -> PrReportingPeriod | None:
        if item.reporting_period_id is None:
            return None
        return await self._session.get(PrReportingPeriod, item.reporting_period_id)

    async def _container_removal_refusal(self, item: PrWorkItem) -> str | None:
        if item.reporting_period_id is None:
            return "not_a_period_container"
        # A container left ``CANCELLED`` by the pre-fix lifecycle is the one
        # kind of empty stream an assignment or a routine no longer protects:
        # it can never receive a result again (``ensure_container`` refuses
        # it), and removing it is the documented repair that lets the next
        # report open a live stream in the same slot.
        cancelled = item.status is PrWorkStatus.CANCELLED
        if item.assigned_by_user_id is not None and not cancelled:
            return "assigned_by_manager"
        if item.recurring_occurrence_id is not None and not cancelled:
            return "recurring"
        if await self._count(
            select(func.count())
            .select_from(PrWorkResult)
            .where(PrWorkResult.work_item_id == item.id)
        ):
            return "has_results"
        if await self._count(
            select(func.count())
            .select_from(PrWorkEvidence)
            .where(PrWorkEvidence.work_item_id == item.id)
        ):
            return "has_evidence"
        return None

    async def _references(self, work_type_id: uuid.UUID) -> WorkTypeReferences:
        def count_of(model: type[Base], column: InstrumentedAttribute[uuid.UUID]) -> Select[Any]:
            return select(func.count()).select_from(model).where(column == work_type_id)

        items = (
            (
                await self._session.execute(
                    select(PrWorkItem).where(PrWorkItem.work_type_id == work_type_id)
                )
            )
            .scalars()
            .all()
        )
        item_ids = [one.id for one in items]
        containers = [one for one in items if one.reporting_period_id is not None]
        removable = 0
        for one in containers:
            if await self._container_removal_refusal(one) is None:
                period = await self._container_period(one)
                if period is not None and period.status is PrPeriodStatus.OPEN:
                    removable += 1
        results = contributions = 0
        if item_ids:
            results = await self._count(
                select(func.count())
                .select_from(PrWorkResult)
                .where(PrWorkResult.work_item_id.in_(item_ids))
            )
            contributions = await self._count(
                select(func.count())
                .select_from(PrWorkContribution)
                .where(PrWorkContribution.work_item_id.in_(item_ids))
            )
        return WorkTypeReferences(
            work_type_id=work_type_id,
            content_rules_active=await self._count(
                count_of(PrContentWorkRule, PrContentWorkRule.work_type_id).where(
                    PrContentWorkRule.is_active.is_(True)
                )
            ),
            content_rules_inactive=await self._count(
                count_of(PrContentWorkRule, PrContentWorkRule.work_type_id).where(
                    PrContentWorkRule.is_active.is_(False)
                )
            ),
            work_items=len(items),
            period_containers=len(containers),
            empty_containers_removable=removable,
            results=results,
            contributions=contributions,
            recurring_templates=await self._count(
                count_of(PrWorkRecurringTemplate, PrWorkRecurringTemplate.work_type_id)
            ),
            quotas=await self._count(count_of(PrWorkQuota, PrWorkQuota.work_type_id)),
            quota_allocations=await self._count(
                count_of(PrWorkQuotaAllocation, PrWorkQuotaAllocation.work_type_id)
            ),
            scoring_rules=await self._count(
                count_of(PrWorkScoringRule, PrWorkScoringRule.work_type_id)
            ),
            score_allocations=await self._count(
                count_of(PrWorkScoreAllocation, PrWorkScoreAllocation.work_type_id)
            ),
        )

    async def _require_work_type(self, work_type_id: uuid.UUID) -> PrWorkType:
        row = await self._session.get(PrWorkType, work_type_id)
        if row is None:
            raise PrNotFoundError(
                "Không tìm thấy loại công việc.", details={"work_type_id": str(work_type_id)}
            )
        return row

    async def _content_type_of(self, content_id: uuid.UUID) -> str:
        content = await self._session.get(PrContentItem, content_id)
        if content is None or content.content_type is None:
            return "UNKNOWN"
        return content.content_type.value

    async def _count(self, statement) -> int:  # type: ignore[no-untyped-def]
        return int((await self._session.execute(statement)).scalar_one() or 0)


def _preview_summary(preview: MaintenancePreview) -> dict[str, object]:
    return {
        "period": preview.period_code,
        "user_id": str(preview.user_id) if preview.user_id else None,
        "content_type": preview.content_type.value if preview.content_type else None,
        "candidates": preview.candidate_count,
        "truncated": preview.truncated,
        "eligible": preview.eligible_content_count,
        "correct": preview.correct_result_count,
        "missing": preview.missing_result_count,
        "wrong_work_type": preview.wrong_work_type_count,
        "stale": preview.stale_result_count,
        "new_work_types": preview.new_work_type_count,
        "unmapped": preview.unmapped_count,
        "unresolved": preview.unresolved_count,
        "blocked": preview.blocked_count,
        "manual_results": preview.manual_result_count,
        "manual_items": preview.manual_item_count,
        "recurring_results": preview.recurring_result_count,
        "affected_users": len(preview.affected_user_ids),
        "affected_work_types": len(preview.affected_work_type_ids),
    }


def _entity_of(source_key: str | None) -> uuid.UUID | None:
    if not source_key:
        return None
    parts = source_key.split(":")
    if len(parts) != 3:
        return None
    try:
        return uuid.UUID(parts[1])
    except ValueError:
        return None


__all__: list[str] = [
    "LEGACY_DELETE_OPERATION",
    "MAX_MAINTENANCE_CONTENT",
    "MAX_MAINTENANCE_NOTE",
    "PERIOD_NOT_OPEN_FOR_CLEANUP",
    "PERIOD_NOT_OPEN_FOR_DELETE_MESSAGE",
    "TERMINAL_DELETABLE_STATUSES",
    "TERMINAL_DELETE_BLOCKED_MESSAGE",
    "TERMINAL_DELETE_OPERATION",
    "TERMINAL_WORK_ITEM_DELETE_BLOCKED",
    "WORK_ITEM_DELETE_BLOCKED",
    "WORK_ITEM_NOT_FOUND",
    "WORK_ITEM_NOT_LEGACY_CONTENT",
    "WORK_ITEM_NOT_TERMINAL",
    "Finding",
    "FindingRow",
    "LegacyWorkItemDeletion",
    "MaintenancePreview",
    "MaintenanceRun",
    "MaintenanceScope",
    "PrWorkMaintenanceService",
    "TerminalDeleteEligibility",
    "TerminalWorkItemDeletion",
    "WorkTypeReferences",
]
