"""Đề xuất tài nguyên (2026-10-21): asking for resources to be bought.

Five steps, in order::

    Tạo đề xuất -> Trưởng phòng duyệt -> Duyệt chi -> Mua & nhập kho -> Xác nhận nhận

* anybody may ask (every account);
* the head step belongs to the requester's head(s) - the head(s) they report
  to ("Trưởng quản lý"), else every head of their departments; nobody above
  them (they head their own department, or sit nowhere) = the step is skipped;
* "Duyệt chi" and "Mua & nhập kho" belong to the Phòng Tài nguyên (the
  resources ``CREATE`` holders) until there is a finance department;
* the requester confirms the hand-over, which completes the request.

The OWNER / an ADMIN may act at every step.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class RequestStage(StrEnum):
    #: Waiting for the requester's head.
    HEAD_REVIEW = "HEAD_REVIEW"
    #: Waiting for the spend approval ("Duyệt chi").
    FUNDING = "FUNDING"
    #: Waiting to be bought and put into the inventory.
    PURCHASING = "PURCHASING"
    #: Handed over, waiting for the requester to confirm.
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


STAGE_LABELS: Mapping[RequestStage, str] = MappingProxyType(
    {
        RequestStage.HEAD_REVIEW: "Chờ trưởng phòng duyệt",
        RequestStage.FUNDING: "Chờ duyệt chi",
        RequestStage.PURCHASING: "Chờ mua & nhập kho",
        RequestStage.DELIVERED: "Chờ xác nhận nhận",
        RequestStage.COMPLETED: "Hoàn tất",
        RequestStage.REJECTED: "Từ chối",
        RequestStage.CANCELLED: "Đã huỷ",
    }
)

OPEN_STAGES: frozenset[RequestStage] = frozenset(
    {
        RequestStage.HEAD_REVIEW,
        RequestStage.FUNDING,
        RequestStage.PURCHASING,
        RequestStage.DELIVERED,
    }
)

#: The five steps of the stepper, in order: (key, label).
STEPS: tuple[tuple[str, str], ...] = (
    ("CREATED", "Tạo đề xuất"),
    ("HEAD", "Trưởng phòng duyệt"),
    ("FUNDING", "Duyệt chi"),
    ("PURCHASING", "Mua & nhập kho"),
    ("DELIVERED", "Xác nhận nhận"),
)


class RequestSite(StrEnum):
    HANOI = "HANOI"
    SAIGON = "SAIGON"
    ALL = "ALL"


SITE_LABELS: Mapping[RequestSite, str] = MappingProxyType(
    {
        RequestSite.HANOI: "Hà Nội",
        RequestSite.SAIGON: "Sài Gòn",
        RequestSite.ALL: "Toàn hệ thống",
    }
)


class RequestEventKind(StrEnum):
    SUBMITTED = "SUBMITTED"
    HEAD_SKIPPED = "HEAD_SKIPPED"
    HEAD_APPROVED = "HEAD_APPROVED"
    FUNDED = "FUNDED"
    DELIVERED = "DELIVERED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


EVENT_LABELS: Mapping[RequestEventKind, str] = MappingProxyType(
    {
        RequestEventKind.SUBMITTED: "Gửi đề xuất",
        RequestEventKind.HEAD_SKIPPED: "Bỏ qua bước trưởng phòng",
        RequestEventKind.HEAD_APPROVED: "Trưởng phòng duyệt",
        RequestEventKind.FUNDED: "Duyệt chi",
        RequestEventKind.DELIVERED: "Nhập kho & bàn giao",
        RequestEventKind.CONFIRMED: "Xác nhận đã nhận",
        RequestEventKind.REJECTED: "Từ chối",
        RequestEventKind.CANCELLED: "Huỷ đề xuất",
    }
)

#: At most this many items per request (one line each on hand-over).
MAX_QUANTITY = 500


def format_request_code(number: int) -> str:
    return f"DX-{number:04d}"


__all__ = [
    "EVENT_LABELS",
    "MAX_QUANTITY",
    "OPEN_STAGES",
    "SITE_LABELS",
    "STAGE_LABELS",
    "STEPS",
    "RequestEventKind",
    "RequestSite",
    "RequestStage",
    "format_request_code",
]
