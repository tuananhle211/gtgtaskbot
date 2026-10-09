"""Wire shapes for the shared board: one row shape for both units."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from meobot.domain.board.models import (
    ADS_STEPS,
    PHASE_LABELS,
    PHASE_ORDER,
    BoardPage,
    DashboardSummary,
    Phase,
    TaskRow,
)
from meobot.domain.units.labels import unit_label, unit_short_label


class TaskCellResponse(BaseModel):
    key: str
    label: str
    person_name: str | None
    status: str
    status_label: str
    is_current: bool
    since: datetime | None = None
    revisions: int = 0


class ExtraResponse(BaseModel):
    label: str
    value: str


class TaskRowResponse(BaseModel):
    unit: str
    unit_label: str
    #: The chip tag: "PR" / "ORD".
    unit_short_label: str
    id: uuid.UUID
    code: str
    title: str
    kind: str
    kind_label: str
    owner_user_id: uuid.UUID
    owner_name: str
    created_at: datetime
    phase: str
    phase_label: str
    status: str
    status_label: str
    #: What the row waits on (``CHO_DUYET``, ``CHO_PHAN_CONG``, ``DA_GIAO``,
    #: ``CHUA_GIAO``) or the node's own status; the screens colour by it.
    state: str | None = None
    cells: list[TaskCellResponse]
    product_link: str | None
    returned_at: datetime | None
    is_priority: bool
    urgent: bool
    detail_path: str
    version: int
    stage_since: datetime | None = None
    revisions: int = 0
    current_person_name: str | None = None
    current_person_user_id: uuid.UUID | None = None
    awaiting_assignment: bool = False
    latest_link: str | None = None
    delivered_at: datetime | None = None
    extras: list[ExtraResponse] = []
    #: The row waits on the viewer (same rule as ``awaiting_me``): "Cần làm".
    awaiting_me: bool = False

    @classmethod
    def from_domain(cls, row: TaskRow) -> TaskRowResponse:
        return cls(
            unit=row.unit.value,
            unit_label=unit_label(row.unit),
            unit_short_label=unit_short_label(row.unit),
            id=row.id,
            code=row.code,
            title=row.title,
            kind=row.kind,
            kind_label=row.kind_label,
            owner_user_id=row.owner_user_id,
            owner_name=row.owner_name,
            created_at=row.created_at,
            phase=row.phase.value,
            phase_label=row.phase_label,
            status=row.status,
            status_label=row.status_label,
            state=row.state,
            cells=[
                TaskCellResponse(
                    key=cell.key,
                    label=cell.label,
                    person_name=cell.person_name,
                    status=cell.status,
                    status_label=cell.status_label,
                    is_current=cell.is_current,
                    since=cell.since,
                    revisions=cell.revisions,
                )
                for cell in row.cells
            ],
            product_link=row.product_link,
            returned_at=row.returned_at,
            is_priority=row.is_priority,
            urgent=row.urgent,
            detail_path=row.detail_path,
            version=row.version,
            stage_since=row.stage_since,
            revisions=row.revisions,
            current_person_name=row.current_person_name,
            current_person_user_id=row.current_person_user_id,
            awaiting_assignment=row.awaiting_assignment,
            latest_link=row.latest_link,
            delivered_at=row.delivered_at,
            extras=[ExtraResponse(label=label, value=value) for label, value in row.extras],
            awaiting_me=row.awaiting_me,
        )


class PhaseOption(BaseModel):
    value: str
    label: str


class TaskPageResponse(BaseModel):
    unit: str
    items: list[TaskRowResponse]
    total: int
    limit: int
    offset: int
    phases: list[PhaseOption]
    #: Ads only: the "Pha" filter's four steps (Order, Biên tập, Thiết kế,
    #: Dựng), sent as ``?step=``. Empty for PR and the merged view.
    steps: list[PhaseOption] = []

    @classmethod
    def from_domain(
        cls, unit: str, page: BoardPage, *, ads_steps: bool = False
    ) -> TaskPageResponse:
        return cls(
            unit=unit,
            items=[TaskRowResponse.from_domain(row) for row in page.rows],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            phases=[
                PhaseOption(value=phase.value, label=PHASE_LABELS[phase]) for phase in PHASE_ORDER
            ],
            steps=[PhaseOption(value=key, label=label) for key, (label, _) in ADS_STEPS.items()]
            if ads_steps
            else [],
        )


class PersonStatResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    opened: int
    done: int
    late: int


class PhaseCountResponse(BaseModel):
    phase: str
    label: str
    count: int


class DashboardResponse(BaseModel):
    unit: str
    date_from: date
    date_to: date
    total: int
    completed: int
    pending_review: int
    urgent: int
    progress_percent: int | None
    by_phase: list[PhaseCountResponse]
    by_owner: list[PersonStatResponse]
    by_worker: list[PersonStatResponse]

    @classmethod
    def from_domain(cls, unit: str, summary: DashboardSummary) -> DashboardResponse:
        return cls(
            unit=unit,
            date_from=summary.date_from,
            date_to=summary.date_to,
            total=summary.total,
            completed=summary.completed,
            pending_review=summary.pending_review,
            urgent=summary.urgent,
            progress_percent=summary.progress_percent,
            by_phase=[
                PhaseCountResponse(
                    phase=phase.value,
                    label=PHASE_LABELS[phase],
                    count=summary.by_phase.get(phase, 0),
                )
                for phase in (*PHASE_ORDER, Phase.CANCELLED)
            ],
            by_owner=[
                PersonStatResponse(
                    user_id=item.user_id,
                    name=item.name,
                    opened=item.opened,
                    done=item.done,
                    late=item.late,
                )
                for item in summary.by_owner
            ],
            by_worker=[
                PersonStatResponse(
                    user_id=item.user_id,
                    name=item.name,
                    opened=item.opened,
                    done=item.done,
                    late=item.late,
                )
                for item in summary.by_worker
            ],
        )


__all__ = [
    "DashboardResponse",
    "PersonStatResponse",
    "PhaseCountResponse",
    "PhaseOption",
    "TaskCellResponse",
    "TaskPageResponse",
    "TaskRowResponse",
]
