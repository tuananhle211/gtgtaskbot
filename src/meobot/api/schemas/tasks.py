"""Wire shapes for the unified task page. Every label comes from the server."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.tasks.detail_service import TaskDetail


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
    inputs: list[Literal["note", "link", "text", "assignee", "tokens", "deadline"]]
    assignee_options: list[AssigneeOptionResponse]
    #: Inputs that may not be left empty (the last node's product link).
    required_inputs: list[Literal["note", "link", "text", "assignee", "tokens", "deadline"]] = []
    #: ORD (0053): ``ESTIMATE`` (the node's plan) or ``REVISION`` (this
    #: round's "Token sửa" / "Deadline sửa"), and the values to start from.
    plan_mode: Literal["ESTIMATE", "REVISION"] | None = None
    defaults: PlanDefaultsResponse | None = None


class TaskDetailResponse(_Out):
    task: TaskSummaryResponse
    steps: list[TaskStepResponse]
    people: list[TaskPersonResponse]
    fields: list[TaskFieldResponse]
    submissions: list[TaskSubmissionResponse]
    timeline: list[TaskTimelineResponse]
    actions: list[TaskActionResponse]

    @classmethod
    def from_domain(cls, detail: TaskDetail) -> TaskDetailResponse:
        return cls.model_validate(detail)


class TaskActionRequest(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    #: The version the screen was drawn from: ``task.version`` in the GET.
    version: int = Field(ge=0)
    note: str | None = None
    link: str | None = Field(default=None, max_length=2000)
    text: str | None = None
    assignee_user_id: uuid.UUID | None = None
    #: 0053: a node's tokens and deadline (Giao việc, Nhận việc by a Leader,
    #: Sửa token/deadline) or, on Trả sửa, the revision tokens and deadline.
    tokens: Decimal | None = Field(default=None, ge=0, le=Decimal("999.99"))
    deadline_at: datetime | None = None


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
