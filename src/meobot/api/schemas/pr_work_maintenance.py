"""What the work maintenance API accepts and sends. ``PR_WORK_CONFIGURE`` only.

Three rules run through every model here:

**The preview is the server's.** Every count comes from the projector's own dry
run over the scope, compared with the rows the ledger holds. A browser never
sees enough to compute any of these numbers itself, and must not try.

**Counts and small samples, never row dumps.** A period is hundreds of content
items; the preview carries how many are in each state and a bounded list of
examples, and the confirmation is written from those.

**A refusal is structured.** A closed month, a finalised figure, a type still
in use - each comes back as a 4xx with a reason the screen maps to Vietnamese,
never as a 500.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.pr_work_maintenance_service import (
    MAX_MAINTENANCE_CONTENT,
    MAX_MAINTENANCE_NOTE,
    FindingRow,
    LegacyWorkItemDeletion,
    MaintenancePreview,
    MaintenanceRun,
    MaintenanceScope,
    TerminalWorkItemDeletion,
    WorkTypeReferences,
)
from meobot.domain.pr.models import PrContentType


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaintenanceScopeRequest(_Body):
    """What one preview, sync or rebuild covers. The period is mandatory.

    ``limit`` bounds how many content items one run covers; a scope larger
    than that is reported as ``truncated`` and run again - every operation
    converges, so a second run over the same scope is the rest of the first.
    """

    period_id: uuid.UUID
    user_id: uuid.UUID | None = None
    content_type: PrContentType | None = None
    limit: int = Field(default=MAX_MAINTENANCE_CONTENT, ge=1, le=MAX_MAINTENANCE_CONTENT)
    #: Rebuild only. A short administrative note kept in the audit row.
    note: str | None = Field(default=None, max_length=MAX_MAINTENANCE_NOTE)

    def to_scope(self) -> MaintenanceScope:
        return MaintenanceScope(
            period_id=self.period_id,
            user_id=self.user_id,
            content_type=self.content_type,
            limit=self.limit,
        )


class AdminNoteRequest(_Body):
    """An optional note for a removal or a deletion."""

    note: str | None = Field(default=None, max_length=MAX_MAINTENANCE_NOTE)


class FindingRowResponse(BaseModel):
    content_id: uuid.UUID
    content_code: str
    contribution_kind: str
    #: ``CORRECT`` | ``MISSING`` | ``WRONG_WORK_TYPE`` | ``STALE`` |
    #: ``NEW_WORK_TYPE`` | ``UNMAPPED`` | ``UNRESOLVED`` | ``BLOCKED``.
    finding: str
    contributor_user_id: uuid.UUID | None
    current_work_type_id: uuid.UUID | None
    expected_work_type_id: uuid.UUID | None
    detail: str | None

    @classmethod
    def from_row(cls, row: FindingRow) -> FindingRowResponse:
        return cls(
            content_id=row.content_id,
            content_code=row.content_code,
            contribution_kind=row.contribution_kind,
            finding=row.finding,
            contributor_user_id=row.contributor_user_id,
            current_work_type_id=row.current_work_type_id,
            expected_work_type_id=row.expected_work_type_id,
            detail=row.detail,
        )


class MaintenancePreviewResponse(BaseModel):
    """What sync or rebuild would do, as counts and a few examples."""

    period_id: uuid.UUID
    period_code: str
    period_status: str
    user_id: uuid.UUID | None
    content_type: str | None
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
    affected_user_ids: list[uuid.UUID]
    affected_work_type_ids: list[uuid.UUID]
    #: Work type id -> how many results the rebuild would file under it.
    recreate_by_work_type: dict[str, int]
    #: Content type -> how many results would provision a new work type.
    provision_by_content_type: dict[str, int]
    #: What the rebuild leaves untouched, so the confirmation can say so.
    manual_result_count: int
    manual_item_count: int
    recurring_result_count: int
    #: How many finalised performance figures the period holds. Any number
    #: above zero means the rebuild is refused.
    finalized_performance_count: int
    samples: list[FindingRowResponse]

    @classmethod
    def from_preview(cls, preview: MaintenancePreview) -> MaintenancePreviewResponse:
        return cls(
            period_id=preview.period_id,
            period_code=preview.period_code,
            period_status=preview.period_status.value,
            user_id=preview.user_id,
            content_type=preview.content_type.value if preview.content_type else None,
            candidate_count=preview.candidate_count,
            truncated=preview.truncated,
            eligible_content_count=preview.eligible_content_count,
            correct_result_count=preview.correct_result_count,
            missing_result_count=preview.missing_result_count,
            wrong_work_type_count=preview.wrong_work_type_count,
            stale_result_count=preview.stale_result_count,
            new_work_type_count=preview.new_work_type_count,
            unmapped_count=preview.unmapped_count,
            unresolved_count=preview.unresolved_count,
            blocked_count=preview.blocked_count,
            results_to_remove=preview.results_to_remove,
            results_to_create=preview.results_to_create,
            affected_user_ids=list(preview.affected_user_ids),
            affected_work_type_ids=list(preview.affected_work_type_ids),
            recreate_by_work_type={
                str(key): value for key, value in preview.recreate_by_work_type.items()
            },
            provision_by_content_type=dict(preview.provision_by_content_type),
            manual_result_count=preview.manual_result_count,
            manual_item_count=preview.manual_item_count,
            recurring_result_count=preview.recurring_result_count,
            finalized_performance_count=preview.finalized_performance_count,
            samples=[FindingRowResponse.from_row(row) for row in preview.samples],
        )


class MaintenanceRunResponse(BaseModel):
    """What one sync or rebuild did."""

    #: ``sync`` | ``rebuild``.
    operation: str
    preview: MaintenancePreviewResponse
    content_items: int
    results_removed: int
    #: Keyed by projection outcome, including the unhappy ones.
    counts: dict[str, int]
    performance_refreshed: int

    @classmethod
    def from_run(cls, run: MaintenanceRun) -> MaintenanceRunResponse:
        return cls(
            operation=run.operation,
            preview=MaintenancePreviewResponse.from_preview(run.preview),
            content_items=run.content_items,
            results_removed=run.results_removed,
            counts=dict(run.counts),
            performance_refreshed=run.performance_refreshed,
        )


class WorkTypeReferencesResponse(BaseModel):
    """Everything that points at one work type, counted, and whether it may go."""

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
    #: The non-zero counts that refuse a delete.
    blocking: dict[str, int]
    deletable: bool

    @classmethod
    def from_references(cls, refs: WorkTypeReferences) -> WorkTypeReferencesResponse:
        return cls(
            work_type_id=refs.work_type_id,
            content_rules_active=refs.content_rules_active,
            content_rules_inactive=refs.content_rules_inactive,
            work_items=refs.work_items,
            period_containers=refs.period_containers,
            empty_containers_removable=refs.empty_containers_removable,
            results=refs.results,
            contributions=refs.contributions,
            recurring_templates=refs.recurring_templates,
            quotas=refs.quotas,
            quota_allocations=refs.quota_allocations,
            scoring_rules=refs.scoring_rules,
            score_allocations=refs.score_allocations,
            blocking=refs.blocking,
            deletable=refs.deletable,
        )


class ContainerCleanupResponse(BaseModel):
    """What a sweep of empty containers under one type removed."""

    removed: int
    remaining_references: WorkTypeReferencesResponse


class LegacyWorkItemDeleteResponse(BaseModel):
    """What deleting one legacy content work item removed.

    The row is gone, so this is not a detail. ``results_created`` and
    ``projection_requested`` are stated rather than implied: the operation
    writes no replacement and queues nothing, and a client that wants the
    content recorded again runs *Đồng bộ dữ liệu công việc* itself.
    """

    work_item_id: uuid.UUID
    code: str
    title: str
    work_type_id: uuid.UUID
    content_id: uuid.UUID | None
    content_code: str | None
    responsible_user_id: uuid.UUID | None
    period_code: str | None
    #: ``contributions``, ``counted_contributions``, ``quota_allocations``,
    #: ``score_allocations``, ``evidence``, ``history``.
    removed: dict[str, int]
    performance_refreshed: int
    results_created: int
    projection_requested: bool

    @classmethod
    def from_deletion(cls, deletion: LegacyWorkItemDeletion) -> LegacyWorkItemDeleteResponse:
        return cls(
            work_item_id=deletion.work_item_id,
            code=deletion.code,
            title=deletion.title,
            work_type_id=deletion.work_type_id,
            content_id=deletion.content_id,
            content_code=deletion.content_code,
            responsible_user_id=deletion.responsible_user_id,
            period_code=deletion.period_code,
            removed=dict(deletion.removed),
            performance_refreshed=deletion.performance_refreshed,
            results_created=deletion.results_created,
            projection_requested=deletion.projection_requested,
        )


class TerminalWorkItemDeleteResponse(BaseModel):
    """What deleting one terminal work item removed.

    The row is gone, so this is not a detail. ``previous_status`` is
    ``CANCELLED`` or ``REJECTED`` and stated so a reader does not have to
    know the route to know what kind of row went; ``results_created`` and
    ``projection_requested`` are stated for the same reason the legacy
    response states them - the operation writes no replacement and queues
    nothing.
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
    previous_status: str
    #: ``contributions``, ``evidence``, ``history``.
    removed: dict[str, int]
    results_created: int
    projection_requested: bool

    @classmethod
    def from_deletion(cls, deletion: TerminalWorkItemDeletion) -> TerminalWorkItemDeleteResponse:
        return cls(
            work_item_id=deletion.work_item_id,
            code=deletion.code,
            title=deletion.title,
            work_type_id=deletion.work_type_id,
            source_type=deletion.source_type,
            source_key=deletion.source_key,
            content_id=deletion.content_id,
            content_code=deletion.content_code,
            recurring_occurrence_id=deletion.recurring_occurrence_id,
            responsible_user_id=deletion.responsible_user_id,
            period_code=deletion.period_code,
            previous_status=deletion.previous_status,
            removed=dict(deletion.removed),
            results_created=deletion.results_created,
            projection_requested=deletion.projection_requested,
        )


__all__: list[str] = [
    "AdminNoteRequest",
    "ContainerCleanupResponse",
    "FindingRowResponse",
    "LegacyWorkItemDeleteResponse",
    "MaintenancePreviewResponse",
    "MaintenanceRunResponse",
    "MaintenanceScopeRequest",
    "TerminalWorkItemDeleteResponse",
    "WorkTypeReferencesResponse",
]
