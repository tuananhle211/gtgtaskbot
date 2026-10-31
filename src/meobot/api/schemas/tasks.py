"""Wire shapes for the unified task page. Every label comes from the server."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from meobot.api.schemas.board import (
    BirthdayResponse,
    DesignCodeItemResponse,
    DesignViewResponse,
)
from meobot.application.tasks.detail_service import TaskDetail
from meobot.domain.orders.design_codes import DesignPick
from meobot.domain.orders.models import OrderReturnReason

ActionInput = Literal[
    "note",
    "link",
    "text",
    "assignee",
    "tokens",
    "deadline",
    "design_codes",
    "task_codes",
    "return_reason",
]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PersonRefResponse(_Out):
    user_id: uuid.UUID | None
    name: str


class AssigneeOptionResponse(PersonRefResponse):
    """A "Giao việc" option (0053): today's effort and what they hold open."""

    tokens_budget_today: float | None = None
    tokens_left_today: float | None = None
    tokens_open: float | None = None
    open_tasks: int | None = None
    #: 0069: ORD + PR workload - assigned (not started) and doing.
    assigned_count: int = 0
    doing_count: int = 0


class TaskSourceResponse(_Out):
    type: Literal["PR_CONTENT", "ORDER"]
    id: uuid.UUID


class TaskSummaryResponse(_Out):
    id: uuid.UUID
    unit: Literal["PR", "ADS"]
    unit_label: str
    #: The chip tag: "PR" / "ORD".
    unit_short_label: str
    code: str
    title: str
    kind: str
    kind_label: str
    phase: str
    phase_label: str
    stage: str
    stage_label: str
    #: What the task waits on (``CHO_DUYET``, ``CHO_PHAN_CONG``, ``DA_GIAO``,
    #: ``CHUA_GIAO``) or the node's own status; the screens colour by it.
    state: str | None = None
    owner: PersonRefResponse
    current_person: PersonRefResponse | None
    is_priority: bool
    urgent: bool
    created_at: datetime
    updated_at: datetime
    stage_since: datetime | None
    finished_at: datetime | None
    product_link: str | None
    latest_link: str | None
    revisions: int
    version: int
    source: TaskSourceResponse
    #: ORD (0053): the orderer's wish, the deadline that counts now and where
    #: it stands, how often the wish was overrun.
    desired_deadline_at: datetime | None = None
    deadline_at: datetime | None = None
    deadline_status: str | None = None
    over_deadline_count: int = 0


class TaskStepResponse(_Out):
    key: str
    label: str
    person_name: str | None
    status: str
    status_label: str
    is_current: bool
    since: datetime | None
    revisions: int
    deadline_at: datetime | None = None
    deadline_status: str | None = None
    tokens: float | None = None


class TaskPersonResponse(_Out):
    role_label: str
    user_id: uuid.UUID | None
    name: str


class TaskFieldResponse(_Out):
    key: str
    label: str
    value: str | None
    type: Literal["text", "longtext", "link", "date"]
    group: Literal["common", "pr", "ads"]


class TaskSubmissionResponse(_Out):
    id: uuid.UUID
    label: str
    step_label: str
    person_name: str | None
    link: str | None
    text: str | None
    note: str | None
    submitted_at: datetime
    status_label: str | None


class TaskTimelineResponse(_Out):
    at: datetime
    actor_name: str | None
    label: str
    note: str | None


class PlanDefaultsResponse(_Out):
    tokens: float | None = None
    deadline_at: datetime | None = None


class TaskActionResponse(_Out):
    key: str
    label: str
    emphasis: Literal["PRIMARY", "SECONDARY", "DANGER"]
    requires_note: bool
    inputs: list[ActionInput]
    assignee_options: list[AssigneeOptionResponse]
    #: Inputs that may not be left empty (the last node's product link).
    required_inputs: list[ActionInput] = []
    #: ORD (0053): ``ESTIMATE`` (the node's plan) or ``REVISION`` (this
    #: round's "Token sửa" / "Deadline sửa"), and the values to start from.
    plan_mode: Literal["ESTIMATE", "REVISION"] | None = None
    defaults: PlanDefaultsResponse | None = None
    #: ORD (0058): what the design-code picker starts from, and one line of
    #: explanation to show under it.
    design_codes: list[DesignCodeItemResponse] | None = None
    hint: str | None = None
    #: ORD Design (0069): "Giao việc" may pick several people at once.
    multi_assign: bool = False
    #: ORD (0074): the step's own task codes (Biên kịch / Dựng) - the phase
    #: whose catalogue to offer and the suggestion to start from.
    task_phase: str | None = None
    task_codes: list[DesignCodeItemResponse] | None = None
    #: ORD (0074): the link handed in last (to send a hand-in again as it was).
    link_default: str | None = None
    #: PR (2026-10-31): why the button is greyed out (no task codes for the
    #: phase being left).
    blocked: str | None = None


class TaskCodesPersonResponse(_Out):
    user_id: uuid.UUID
    name: str
    codes: list[DesignCodeItemResponse] = []
    points: int = 0
    earned: bool = False


class TaskCodesPhaseResponse(_Out):
    """0074: one production step's task codes and points."""

    phase: str
    label: str
    suggested: list[DesignCodeItemResponse] = []
    suggested_points: int = 0
    current: list[DesignCodeItemResponse] = []
    current_points: int = 0
    proposed: list[DesignCodeItemResponse] | None = None
    proposed_points: int | None = None
    change: str | None = None
    people: list[TaskCodesPersonResponse] = []


class TaskDetailResponse(_Out):
    task: TaskSummaryResponse
    steps: list[TaskStepResponse]
    people: list[TaskPersonResponse]
    fields: list[TaskFieldResponse]
    submissions: list[TaskSubmissionResponse]
    timeline: list[TaskTimelineResponse]
    actions: list[TaskActionResponse]
    #: ORD (0058). Absent on PR tasks.
    category: str | None = None
    design: DesignViewResponse | None = None
    birthday: BirthdayResponse | None = None
    #: "Xoá task" is offered to this viewer (the OWNER only).
    can_delete: bool = False
    #: ORD (0074): each production step's task codes and points.
    task_codes: list[TaskCodesPhaseResponse] = []

    @classmethod
    def from_domain(cls, detail: TaskDetail) -> TaskDetailResponse:
        return cls.model_validate(detail)


class DesignPickBody(BaseModel):
    code_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=999)
    #: 0074: the form ("hình thức") key; empty = "Làm mới".
    form: str | None = Field(default=None, max_length=30)
    #: 0074: a line's note - for "Việc khác", what was done (required).
    note: str | None = Field(default=None, max_length=1000)
    #: 0074: "Việc khác" only: the points typed in.
    points: int | None = Field(default=None, ge=1, le=1_000_000)

    def to_pick(self) -> DesignPick:
        return DesignPick(
            code_id=self.code_id,
            quantity=self.quantity,
            form=self.form,
            note=self.note,
            points=self.points,
        )


class TaskActionRequest(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    #: The version the screen was drawn from: ``task.version`` in the GET.
    version: int = Field(ge=0)
    note: str | None = None
    link: str | None = Field(default=None, max_length=2000)
    text: str | None = None
    assignee_user_id: uuid.UUID | None = None
    #: ORD Design (0069): several people handed one node at once ("Giao việc").
    assignee_user_ids: list[uuid.UUID] | None = Field(default=None, max_length=30)
    #: 0053: a node's tokens and deadline (Giao việc, Nhận việc by a Leader,
    #: Sửa token/deadline) or, on Trả sửa, the revision tokens and deadline.
    tokens: Decimal | None = Field(default=None, ge=0, le=Decimal("999.99"))
    deadline_at: datetime | None = None
    #: ORD (0058): the design codes picked on a hand-in or an approval.
    design_codes: list[DesignPickBody] | None = Field(default=None, max_length=100)
    #: ORD Design (0069): the Leader's optional split of codes per person, when
    #: giving one node to several designers. Keyed by their user id.
    design_codes_by_user: dict[uuid.UUID, list[DesignPickBody]] | None = Field(
        default=None, max_length=30
    )
    #: ORD (0074): the step's own task code(s) handed in (Biên kịch / Dựng).
    task_codes: list[DesignPickBody] | None = Field(default=None, max_length=100)
    #: ORD (0074): why "Không duyệt" (returns only).
    return_reason: OrderReturnReason | None = None


__all__ = [
    "PersonRefResponse",
    "TaskActionRequest",
    "TaskActionResponse",
    "TaskDetailResponse",
    "TaskFieldResponse",
    "TaskPersonResponse",
    "TaskSourceResponse",
    "TaskStepResponse",
    "TaskSubmissionResponse",
    "TaskSummaryResponse",
    "TaskTimelineResponse",
]
