"""Wire shapes for Ads orders. Every label comes from the server."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from meobot.application.orders.query_service import OrderDetail
from meobot.domain.orders.labels import (
    event_label,
    node_status_label,
    node_type_label,
    stage_label,
    video_type_label,
)
from meobot.domain.orders.models import (
    PRODUCTION_NODES,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    production_nodes,
)


class OrderNodeResponse(BaseModel):
    id: uuid.UUID
    node_type: str
    node_type_label: str
    status: str
    status_label: str
    is_current: bool
    preassigned_user_id: uuid.UUID | None
    preassigned_name: str | None
    assignee_user_id: uuid.UUID | None
    assignee_name: str | None
    approved_by_user_id: uuid.UUID | None
    approved_by_name: str | None
    activated_at: datetime | None
    assigned_at: datetime | None
    accepted_at: datetime | None
    submitted_at: datetime | None
    approved_at: datetime | None
    revision_count: int
    submission_count: int
    version: int


class OrderSubmissionResponse(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    node_type: str
    submission_no: int
    label: str
    submitted_by_user_id: uuid.UUID
    submitted_by_name: str | None
    link: str | None
    script_text: str | None
    note: str | None
    created_at: datetime


class OrderApprovalResponse(BaseModel):
    id: uuid.UUID
    gate: str
    round_no: int
    decision: str
    actor_user_id: uuid.UUID
    actor_name: str | None
    submission_id: uuid.UUID | None
    comment: str | None
    created_at: datetime


class OrderEventResponse(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    node_id: uuid.UUID | None
    node_type: str | None
    actor_user_id: uuid.UUID
    actor_name: str | None
    assignee_user_id: uuid.UUID | None
    assignee_name: str | None
    submission_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class OrderActionResponse(BaseModel):
    kind: str
    label: str
    node_id: uuid.UUID | None
    requires_note: bool


class OrderResponse(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    #: The process code ("BTD", "BD"...) and its label ("Biên kịch, Dựng").
    video_type: str
    video_type_label: str
    #: The production nodes the process visits, in pipeline order.
    process: list[str]
    video_kind_id: uuid.UUID | None
    video_kind_name: str | None
    video_kind_points: float | None
    platform_id: uuid.UUID | None
    platform_name: str | None
    duration_id: uuid.UUID | None
    duration_name: str | None
    duration_points: float | None
    order_content: str
    script_source: str | None
    design_link: str | None
    reference_link: str | None
    source_link: str | None
    note: str | None
    owner_user_id: uuid.UUID
    owner_name: str | None
    stage: str
    stage_label: str
    submitted_at: datetime
    order_approved_at: datetime | None
    order_approved_by_user_id: uuid.UUID | None
    returned_reason: str | None
    is_priority: bool
    urgent: bool
    product_link: str | None
    completed_at: datetime | None
    cancelled_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class OrderDetailResponse(BaseModel):
    order: OrderResponse
    nodes: list[OrderNodeResponse]
    submissions: list[OrderSubmissionResponse]
    approvals: list[OrderApprovalResponse]
    events: list[OrderEventResponse]
    available_actions: list[OrderActionResponse]

    @classmethod
    def from_domain(cls, detail: OrderDetail) -> OrderDetailResponse:
        names = detail.names

        def name(user_id: uuid.UUID | None) -> str | None:
            return None if user_id is None else names.get(user_id)

        order = detail.order
        active = {
            OrderNodeStatus.CHUA_GIAO,
            OrderNodeStatus.DANG_LAM,
            OrderNodeStatus.AI_DANG_REVIEW,
            OrderNodeStatus.CHO_DUYET,
            OrderNodeStatus.DANG_SUA,
        }
        node_types = {node.id: node.node_type for node in detail.nodes}
        return cls(
            order=OrderResponse(
                id=order.id,
                code=order.code,
                title=order.title,
                video_type=order.video_type.value,
                video_type_label=video_type_label(order.video_type),
                process=[node.value for node in production_nodes(order.video_type)],
                video_kind_id=order.video_kind_id,
                video_kind_name=order.video_kind_name,
                video_kind_points=(
                    None if order.video_kind_points is None else float(order.video_kind_points)
                ),
                platform_id=order.platform_id,
                platform_name=order.platform_name,
                duration_id=order.duration_id,
                duration_name=order.duration_name,
                duration_points=(
                    None if order.duration_points is None else float(order.duration_points)
                ),
                order_content=order.order_content,
                script_source=None if order.script_source is None else order.script_source.value,
                design_link=order.design_link,
                reference_link=order.reference_link,
                source_link=order.source_link,
                note=order.note,
                owner_user_id=order.owner_user_id,
                owner_name=name(order.owner_user_id),
                stage=order.stage.value,
                stage_label=stage_label(order.stage),
                submitted_at=order.submitted_at,
                order_approved_at=order.order_approved_at,
                order_approved_by_user_id=order.order_approved_by_user_id,
                returned_reason=order.returned_reason,
                is_priority=order.is_priority,
                urgent=detail.urgent,
                product_link=order.product_link,
                completed_at=order.completed_at,
                cancelled_reason=order.cancelled_reason,
                version=order.version,
                created_at=order.created_at,
                updated_at=order.updated_at,
            ),
            nodes=[
                OrderNodeResponse(
                    id=node.id,
                    node_type=node.node_type.value,
                    node_type_label=node_type_label(node.node_type),
                    status=node.status.value,
                    status_label=node_status_label(node.status),
                    is_current=node.status in active,
                    preassigned_user_id=node.preassigned_user_id,
                    preassigned_name=name(node.preassigned_user_id),
                    assignee_user_id=node.assignee_user_id,
                    assignee_name=name(node.assignee_user_id),
                    approved_by_user_id=node.approved_by_user_id,
                    approved_by_name=name(node.approved_by_user_id),
                    activated_at=node.activated_at,
                    assigned_at=node.assigned_at,
                    accepted_at=node.accepted_at,
                    submitted_at=node.submitted_at,
                    approved_at=node.approved_at,
                    revision_count=node.revision_count,
                    submission_count=node.submission_count,
                    version=node.version,
                )
                # A legacy link node is no step any more: hidden.
                for node in detail.nodes
                if node.node_type is not OrderNodeType.GAN_LINK
            ],
            submissions=[
                OrderSubmissionResponse(
                    id=item.id,
                    node_id=item.node_id,
                    node_type=node_types[item.node_id].value,
                    submission_no=item.submission_no,
                    label=f"{order.code}_V{item.submission_no}",
                    submitted_by_user_id=item.submitted_by_user_id,
                    submitted_by_name=name(item.submitted_by_user_id),
                    link=item.link,
                    script_text=item.script_text,
                    note=item.note,
                    created_at=item.created_at,
                )
                for item in detail.submissions
            ],
            approvals=[
                OrderApprovalResponse(
                    id=item.id,
                    gate=item.gate.value,
                    round_no=item.round_no,
                    decision=item.decision.value,
                    actor_user_id=item.actor_user_id,
                    actor_name=name(item.actor_user_id),
                    submission_id=item.submission_id,
                    comment=item.comment,
                    created_at=item.created_at,
                )
                for item in detail.approvals
            ],
            events=[
                OrderEventResponse(
                    id=item.id,
                    kind=item.kind.value,
                    kind_label=event_label(item.kind),
                    node_id=item.node_id,
                    node_type=None if item.node_id is None else node_types[item.node_id].value,
                    actor_user_id=item.actor_user_id,
                    actor_name=name(item.actor_user_id),
                    assignee_user_id=item.assignee_user_id,
                    assignee_name=name(item.assignee_user_id),
                    submission_id=item.submission_id,
                    note=item.note,
                    created_at=item.created_at,
                )
                for item in detail.events
            ],
            available_actions=[
                OrderActionResponse(
                    kind=action.kind.value,
                    label=action.label,
                    node_id=action.node_id,
                    requires_note=action.requires_note,
                )
                for action in detail.actions
            ],
        )


# --- requests --------------------------------------------------------------------


class CreateOrderRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    #: The process, as a code ("BD")...
    video_type: str | None = None
    #: ...or as the ticked production nodes, in any order. Either one; when
    #: both are sent they must agree.
    process: list[str] | None = None
    #: The video kind from the unit's catalogue (``/api/units/ADS/video-kinds``).
    video_kind_id: uuid.UUID | None = None
    order_content: str = Field(min_length=1)
    script_source: str | None = None
    design_link: str | None = Field(default=None, max_length=2000)
    reference_link: str | None = Field(default=None, max_length=2000)
    source_link: str | None = Field(default=None, max_length=2000)
    platform_id: uuid.UUID | None = None
    duration_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=5000)
    #: Node type → user id, for the nodes the orderer wants to name a person for.
    preassigned: dict[str, uuid.UUID] = Field(default_factory=dict)


class VersionedRequest(BaseModel):
    version: int = Field(ge=1)


class ResubmitOrderRequest(VersionedRequest):
    title: str | None = Field(default=None, max_length=300)
    order_content: str | None = None
    script_source: str | None = None
    design_link: str | None = Field(default=None, max_length=2000)
    reference_link: str | None = Field(default=None, max_length=2000)
    source_link: str | None = Field(default=None, max_length=2000)
    preassigned: dict[str, uuid.UUID] | None = None
    video_kind_id: uuid.UUID | None = None
    platform_id: uuid.UUID | None = None
    duration_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=5000)


class NoteRequest(VersionedRequest):
    note: str | None = None


class AssignRequest(VersionedRequest):
    assignee_user_id: uuid.UUID


class SubmitWorkRequest(VersionedRequest):
    link: str | None = Field(default=None, max_length=2000)
    script_text: str | None = None
    note: str | None = None


class ApproveFinalRequest(VersionedRequest):
    product_link: str | None = Field(default=None, max_length=2000)


class PriorityRequest(VersionedRequest):
    is_priority: bool


class StageOption(BaseModel):
    value: str
    label: str


def stage_options() -> list[StageOption]:
    # The legacy link stage is no filter anybody picks any more.
    return [
        StageOption(value=stage.value, label=stage_label(stage))
        for stage in OrderStage
        if stage is not OrderStage.GAN_LINK
    ]


def node_type_options() -> list[StageOption]:
    return [StageOption(value=item.value, label=node_type_label(item)) for item in PRODUCTION_NODES]


__all__ = [
    "ApproveFinalRequest",
    "AssignRequest",
    "CreateOrderRequest",
    "NoteRequest",
    "OrderActionResponse",
    "OrderApprovalResponse",
    "OrderDetailResponse",
    "OrderEventResponse",
    "OrderNodeResponse",
    "OrderResponse",
    "OrderSubmissionResponse",
    "PriorityRequest",
    "ResubmitOrderRequest",
    "StageOption",
    "SubmitWorkRequest",
    "VersionedRequest",
    "node_type_options",
    "stage_options",
]
