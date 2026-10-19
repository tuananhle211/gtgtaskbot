"""Wire shapes for the shared board: one row shape for both units."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from meobot.domain.board.models import (
    ADS_STEPS,
    DESIGN_STATES,
    PHASE_LABELS,
    PHASE_ORDER,
    TASK_STATES,
    BirthdayView,
    BoardPage,
    DashboardSummary,
    DesignCodeView,
    DesignView,
    Phase,
    TaskRow,
)
from meobot.domain.units.labels import unit_label, unit_short_label


class TaskCellResponse(BaseModel):
    key: str
    label: str
    person_name: str | None
    #: Additive: the one account behind ``person_name`` (profile link), else null.
    person_user_id: uuid.UUID | None = None
    status: str
    status_label: str
    is_current: bool
    since: datetime | None = None
    revisions: int = 0
    #: ORD (0053): the step's deadline, where it stands, its tokens.
    deadline_at: datetime | None = None
    deadline_status: str | None = None
    tokens: float | None = None


class ExtraResponse(BaseModel):
    label: str
    value: str


class DesignCodeItemResponse(BaseModel):
    """One design code ("mã task design") on an order, with its quantity."""

    model_config = ConfigDict(from_attributes=True)

    code_id: uuid.UUID
    code: str
    name: str
    quantity: int

    @classmethod
    def from_domain(cls, item: DesignCodeView) -> DesignCodeItemResponse:
        return cls(code_id=item.code_id, code=item.code, name=item.name, quantity=item.quantity)


class DesignViewResponse(BaseModel):
    """The Design team's reading of an ORD row (0058)."""

    model_config = ConfigDict(from_attributes=True)

    script_link: str | None = None
    script_by: str | None = None
    reference_link: str | None = None
    #: Every reference link (a birthday order's "Ảnh minh hoạ" may be several);
    #: ``reference_link`` is the first.
    reference_links: list[str] = []
    note: str | None = None
    codes: list[DesignCodeItemResponse] = []
    #: The designer's different list, waiting for the Design Leader.
    proposed_codes: list[DesignCodeItemResponse] | None = None
    designer: str | None = None
    design_link: str | None = None
    #: When the newest Design link was handed in.
    design_submitted_at: datetime | None = None

    @classmethod
    def from_domain(cls, view: DesignView) -> DesignViewResponse:
        return cls(
            script_link=view.script_link,
            script_by=view.script_by,
            reference_link=view.reference_link,
            reference_links=list(view.reference_links),
            note=view.note,
            codes=[DesignCodeItemResponse.from_domain(item) for item in view.codes],
            proposed_codes=(
                None
                if view.proposed_codes is None
                else [DesignCodeItemResponse.from_domain(item) for item in view.proposed_codes]
            ),
            designer=view.designer,
            design_link=view.design_link,
            design_submitted_at=view.design_submitted_at,
        )


class BirthdayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    full_name: str
    branch: str | None = None
    #: ``MALE`` / ``FEMALE``.
    gender: str | None = None
    birthday_on: date | None = None

    @classmethod
    def from_domain(cls, view: BirthdayView) -> BirthdayResponse:
        return cls(
            full_name=view.full_name,
            branch=view.branch,
            gender=view.gender,
            birthday_on=view.birthday_on,
        )


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
    #: ORD (0053): the orderer's wish, the deadline that counts now and where
    #: it stands (``ON_TRACK``/``DUE_SOON``/``OVERDUE``/``MET``/``MISSED``).
    desired_deadline_at: datetime | None = None
    deadline_at: datetime | None = None
    deadline_status: str | None = None
    over_deadline_count: int = 0
    #: ORD (0058): the order's category, the Design team's view, and a
    #: birthday order's details. Absent on PR rows.
    category: str | None = None
    design: DesignViewResponse | None = None
    birthday: BirthdayResponse | None = None
    #: ORD: the action kinds this viewer has on the row now (``SUBMIT_WORK``,
    #: ``APPROVE_NODE``, ``ACCEPT``...). The keys to run them come from
    #: ``GET /api/tasks/{code}``.
    actions: list[str] = []

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
                    person_user_id=cell.person_user_id,
                    status=cell.status,
                    status_label=cell.status_label,
                    is_current=cell.is_current,
                    since=cell.since,
                    revisions=cell.revisions,
                    deadline_at=cell.deadline_at,
                    deadline_status=cell.deadline_status,
                    tokens=cell.tokens,
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
            desired_deadline_at=row.desired_deadline_at,
            deadline_at=row.deadline_at,
            deadline_status=row.deadline_status,
            over_deadline_count=row.over_deadline_count,
            category=row.category,
            design=None if row.design is None else DesignViewResponse.from_domain(row.design),
            birthday=None if row.birthday is None else BirthdayResponse.from_domain(row.birthday),
            actions=list(row.actions),
        )


class PhaseOption(BaseModel):
    value: str
    label: str


class BoardCountsResponse(BaseModel):
    """``GET /api/board/counts``: how many rows each quick filter of the task
    table would show, for the unit on screen (the other filters aside)."""

    mine: int
    awaiting_me: int
    priority: int
    urgent: int
    overdue: int


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
    #: Ads only: the Design team's status filter, sent as ``?design_status=``.
    design_states: list[PhaseOption] = []
    #: Ads only (2026-10-19): the "Trạng thái task" filter, sent as ``?state=``.
    task_states: list[PhaseOption] = []

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
            design_states=[
                PhaseOption(value=key, label=label) for key, label in DESIGN_STATES.items()
            ]
            if ads_steps
            else [],
            task_states=[PhaseOption(value=key, label=label) for key, label in TASK_STATES.items()]
            if ads_steps
            else [],
        )


class PersonStatResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    opened: int
    done: int
    late: int
    tokens: float = 0


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
    #: ORD (0053): rows past the deadline that counts now ("Trễ hạn").
    overdue: int = 0
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
            overdue=summary.overdue,
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
                    tokens=item.tokens,
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
                    tokens=item.tokens,
                )
                for item in summary.by_worker
            ],
        )


__all__ = [
    "BoardCountsResponse",
    "DashboardResponse",
    "PersonStatResponse",
    "PhaseCountResponse",
    "PhaseOption",
    "TaskCellResponse",
    "TaskPageResponse",
    "TaskRowResponse",
]
