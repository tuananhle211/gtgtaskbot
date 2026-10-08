"""The Vietnamese words for the order engine. Server-owned, one vocabulary."""

from __future__ import annotations

from meobot.domain.orders.models import (
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    OrderVideoType,
    production_nodes,
)

#: The words on the create form's "Quy trình" checkboxes.
PROCESS_STEP_LABELS: dict[OrderNodeType, str] = {
    OrderNodeType.BIEN_TAP: "Biên kịch",
    OrderNodeType.THIET_KE: "Design",
    OrderNodeType.DUNG: "Dựng",
}

#: Between two steps of a process label: a single right-pointing angle quote.
PROCESS_SEPARATOR = " \u203a "

#: A process reads as its ticked steps, e.g. "Biên kịch \u203a Design \u203a Dựng".
VIDEO_TYPE_LABELS: dict[OrderVideoType, str] = {
    code: PROCESS_SEPARATOR.join(PROCESS_STEP_LABELS[node] for node in production_nodes(code))
    for code in OrderVideoType
}

STAGE_LABELS: dict[OrderStage, str] = {
    OrderStage.ORDER_PENDING: "Chờ duyệt",
    OrderStage.ORDER_RETURNED: "Trả sửa",
    OrderStage.BIEN_TAP: "Biên tập",
    OrderStage.THIET_KE: "Thiết kế",
    OrderStage.DUNG: "Dựng",
    OrderStage.GAN_LINK: "Gắn link",
    OrderStage.DUYET_VIDEO_BT: "Trưởng phòng Biên kịch duyệt video",
    OrderStage.FINAL_REVIEW: "Duyệt lần cuối",
    OrderStage.COMPLETED: "Hoàn thành",
    OrderStage.CANCELLED: "Đã huỷ",
}

NODE_TYPE_LABELS: dict[OrderNodeType, str] = {
    OrderNodeType.BIEN_TAP: "Biên tập",
    OrderNodeType.THIET_KE: "Thiết kế",
    OrderNodeType.DUNG: "Dựng",
    OrderNodeType.GAN_LINK: "Gắn link",
}

NODE_STATUS_LABELS: dict[OrderNodeStatus, str] = {
    OrderNodeStatus.CHUA_TOI: "Chưa tới",
    OrderNodeStatus.BO_QUA: "Bỏ qua",
    OrderNodeStatus.CHUA_GIAO: "Chưa giao",
    OrderNodeStatus.DANG_LAM: "Đang làm",
    OrderNodeStatus.AI_DANG_REVIEW: "AI đang review",
    OrderNodeStatus.CHO_DUYET: "Chờ duyệt",
    OrderNodeStatus.DANG_SUA: "Đang sửa",
    OrderNodeStatus.HOAN_THANH: "Hoàn thành",
}

EVENT_LABELS: dict[OrderEventKind, str] = {
    OrderEventKind.SUBMITTED: "Gửi order",
    OrderEventKind.RESUBMITTED: "Gửi lại order",
    OrderEventKind.ORDER_APPROVED: "Trưởng phòng duyệt order",
    OrderEventKind.ORDER_RETURNED: "Trưởng phòng trả order",
    OrderEventKind.NODE_ACTIVATED: "Tới lượt công đoạn",
    OrderEventKind.NODE_ASSIGNED: "Giao việc",
    OrderEventKind.NODE_ACCEPTED: "Nhận việc",
    OrderEventKind.WORK_SUBMITTED: "Nộp bài",
    OrderEventKind.NODE_APPROVED: "Duyệt công đoạn",
    OrderEventKind.NODE_RETURNED: "Trả sửa công đoạn",
    OrderEventKind.LINK_ATTACHED: "Gắn link sản phẩm",
    OrderEventKind.VIDEO_APPROVED: "Trưởng phòng Biên kịch duyệt video",
    OrderEventKind.VIDEO_RETURNED: "Trưởng phòng Biên kịch trả video",
    OrderEventKind.FINAL_APPROVED: "Duyệt Final",
    OrderEventKind.FINAL_RETURNED: "Trả Final",
    OrderEventKind.PRIORITY_SET: "Đánh dấu Ưu tiên",
    OrderEventKind.PRIORITY_CLEARED: "Bỏ Ưu tiên",
    OrderEventKind.CANCELLED: "Huỷ order",
}


def video_type_label(value: OrderVideoType) -> str:
    """The process label: the ticked steps joined by :data:`PROCESS_SEPARATOR`."""
    return VIDEO_TYPE_LABELS[value]


def process_label(value: OrderVideoType) -> str:
    return VIDEO_TYPE_LABELS[value]


def stage_label(value: OrderStage) -> str:
    return STAGE_LABELS[value]


def node_type_label(value: OrderNodeType) -> str:
    return NODE_TYPE_LABELS[value]


def node_status_label(value: OrderNodeStatus) -> str:
    return NODE_STATUS_LABELS[value]


def event_label(value: OrderEventKind) -> str:
    return EVENT_LABELS[value]


__all__ = [
    "EVENT_LABELS",
    "NODE_STATUS_LABELS",
    "NODE_TYPE_LABELS",
    "PROCESS_SEPARATOR",
    "PROCESS_STEP_LABELS",
    "STAGE_LABELS",
    "VIDEO_TYPE_LABELS",
    "event_label",
    "node_status_label",
    "node_type_label",
    "process_label",
    "stage_label",
    "video_type_label",
]
