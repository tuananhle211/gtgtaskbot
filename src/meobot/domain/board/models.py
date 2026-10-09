"""One board for two engines.

PR keeps its fourteen workflow stages and Ads has its own pipeline; the
screens that show both - the dashboard and the task table - see neither.
They see **five phases**, ``Order → Duyệt → Sản xuất → Duyệt → Hoàn thành``
(plus ``Huỷ``), and one row shape, :class:`TaskRow`, that each unit's source
fills in its own way. The mapping from a PR stage to a phase lives here and
is read-only: it changes what a PR item is *called* on the board, never what
it *is*.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.orders.models import OrderStage
from meobot.domain.pr.models import PrWorkflowStage
from meobot.domain.units.models import UnitCode


class Phase(StrEnum):
    """The five phases every unit's work is shown in, plus cancelled."""

    ORDER = "ORDER"
    REVIEW = "REVIEW"
    PRODUCTION = "PRODUCTION"
    FINAL_REVIEW = "FINAL_REVIEW"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


PHASE_LABELS: MappingProxyType[Phase, str] = MappingProxyType(
    {
        Phase.ORDER: "Order",
        Phase.REVIEW: "Duyệt",
        Phase.PRODUCTION: "Sản xuất",
        Phase.FINAL_REVIEW: "Duyệt",
        Phase.DONE: "Hoàn thành",
        Phase.CANCELLED: "Huỷ",
    }
)

#: The five phases in board order (cancelled is shown apart).
PHASE_ORDER: tuple[Phase, ...] = (
    Phase.ORDER,
    Phase.REVIEW,
    Phase.PRODUCTION,
    Phase.FINAL_REVIEW,
    Phase.DONE,
)

#: Design §3.1: every PR stage, mapped, none left out.
PR_STAGE_PHASE: MappingProxyType[PrWorkflowStage, Phase] = MappingProxyType(
    {
        PrWorkflowStage.IDEA: Phase.ORDER,
        PrWorkflowStage.BRIEFING: Phase.ORDER,
        PrWorkflowStage.SCRIPTING: Phase.ORDER,
        PrWorkflowStage.AI_REVIEW: Phase.REVIEW,
        PrWorkflowStage.TEAM_LEAD_REVIEW: Phase.REVIEW,
        PrWorkflowStage.HEAD_REVIEW: Phase.REVIEW,
        PrWorkflowStage.APPROVED: Phase.REVIEW,
        PrWorkflowStage.PRODUCTION: Phase.PRODUCTION,
        PrWorkflowStage.INTERNAL_REVIEW: Phase.FINAL_REVIEW,
        PrWorkflowStage.READY_TO_PUBLISH: Phase.DONE,
        PrWorkflowStage.PUBLISHED: Phase.DONE,
        PrWorkflowStage.MEASURED: Phase.DONE,
        PrWorkflowStage.ARCHIVED: Phase.DONE,
        PrWorkflowStage.CANCELLED: Phase.CANCELLED,
    }
)

#: Design §5.1: every Ads stage, mapped.
ADS_STAGE_PHASE: MappingProxyType[OrderStage, Phase] = MappingProxyType(
    {
        OrderStage.ORDER_PENDING: Phase.REVIEW,
        OrderStage.ORDER_RETURNED: Phase.ORDER,
        OrderStage.BIEN_TAP: Phase.PRODUCTION,
        OrderStage.THIET_KE: Phase.PRODUCTION,
        OrderStage.DUNG: Phase.PRODUCTION,
        OrderStage.GAN_LINK: Phase.PRODUCTION,
        OrderStage.DUYET_VIDEO_BT: Phase.FINAL_REVIEW,
        OrderStage.FINAL_REVIEW: Phase.FINAL_REVIEW,
        OrderStage.COMPLETED: Phase.DONE,
        OrderStage.CANCELLED: Phase.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class TaskCell:
    """One column of a row: a person and a status, and whether it is the step
    the row is at. The Ads board has four of these per row (the nodes); the PR
    board shows its five phases the same way."""

    key: str
    label: str
    person_name: str | None
    status: str
    status_label: str
    is_current: bool
    #: When this cell last changed hands: activated, submitted or approved.
    since: datetime | None = None
    #: How many times the work in this cell was sent back.
    revisions: int = 0


@dataclass(frozen=True, slots=True)
class TaskRow:
    unit: UnitCode
    id: uuid.UUID
    code: str
    title: str
    kind: str
    kind_label: str
    owner_user_id: uuid.UUID
    owner_name: str
    created_at: datetime
    phase: Phase
    phase_label: str
    #: The unit's own fine-grained state, for the "trạng thái chi tiết" column.
    status: str
    status_label: str
    cells: tuple[TaskCell, ...]
    product_link: str | None
    returned_at: datetime | None
    is_priority: bool
    urgent: bool
    detail_path: str
    version: int
    #: When the row entered the step it is at now, for "đã ở bước này N ngày".
    stage_since: datetime | None = None
    #: Returns across the whole row (nodes and gates).
    revisions: int = 0
    #: Who holds the row right now - always one named member. "Chờ giao" (with
    #: ``awaiting_assignment``) when the step needs somebody and nobody is set;
    #: ``None`` only for finished and cancelled rows.
    current_person_name: str | None = None
    current_person_user_id: uuid.UUID | None = None
    awaiting_assignment: bool = False
    #: The newest file handed in, so the table can open it without the detail.
    latest_link: str | None = None
    #: When the finished link was handed over: Ads = the product link attached
    #: on "Gắn link", PR = the newest production hand-in.
    delivered_at: datetime | None = None
    #: Unit-specific facts worth a glance, as label/value pairs.
    extras: tuple[tuple[str, str], ...] = ()
    #: Whether the row waits on the viewer (the ``awaiting_me`` definition):
    #: the table's "Cần làm" marker.
    awaiting_me: bool = False


@dataclass(frozen=True, slots=True)
class BoardQuery:
    """Every filter the table and the dashboard accept, in one place.

    Dates bound ``created_at`` (PR) or ``submitted_at`` (Ads), inclusive, in
    the application timezone. The person filters take user ids.
    """

    date_from: date | None = None
    date_to: date | None = None
    phase: Phase | None = None
    #: Ads only: one of :data:`ADS_STEPS`.
    step: str | None = None
    #: PR: a content type. Ads: a process code (``BTD``, ``BD``...).
    kind: str | None = None
    #: Ads only: one video kind of the unit's catalogue.
    video_kind_id: uuid.UUID | None = None
    status: str | None = None
    owner_user_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    #: Everything one person takes part in: Ads = ordered it or holds/held a
    #: node; PR = owns it or has an open task on it (PR's own "phụ trách" rule).
    person_user_id: uuid.UUID | None = None
    mine: bool = False
    awaiting_me: bool = False
    priority: bool = False
    urgent: bool = False
    search: str | None = None
    #: ``order=todo_first``: the rows awaiting the viewer first, then the rest
    #: of the filter, each part in the usual priority-then-newest order.
    todo_first: bool = False
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class BoardPage:
    rows: tuple[TaskRow, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class PersonStat:
    user_id: uuid.UUID
    name: str
    #: Orders placed / finished / late, for an orderer; nodes done / returned
    #: for a worker. The labels say which.
    opened: int
    done: int
    late: int


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    unit: UnitCode
    date_from: date
    date_to: date
    total: int
    completed: int
    pending_review: int
    urgent: int
    #: ``completed / total`` in percent, or ``None`` when there is nothing.
    progress_percent: int | None
    by_phase: dict[Phase, int] = field(default_factory=dict)
    by_owner: tuple[PersonStat, ...] = ()
    by_worker: tuple[PersonStat, ...] = ()


#: The Ads table's "Pha" filter: the four places an order can be waiting -
#: the order itself, then each production node. Keyed by the value the URL
#: carries (``?step=``).
ADS_STEPS: MappingProxyType[str, tuple[str, frozenset[OrderStage]]] = MappingProxyType(
    {
        "ORDER": ("Order", frozenset({OrderStage.ORDER_PENDING, OrderStage.ORDER_RETURNED})),
        "BIEN_TAP": ("Biên tập", frozenset({OrderStage.BIEN_TAP})),
        "THIET_KE": ("Thiết kế", frozenset({OrderStage.THIET_KE})),
        "DUNG": ("Dựng", frozenset({OrderStage.DUNG})),
    }
)


__all__ = [
    "ADS_STAGE_PHASE",
    "ADS_STEPS",
    "PHASE_LABELS",
    "PHASE_ORDER",
    "PR_STAGE_PHASE",
    "BoardPage",
    "BoardQuery",
    "DashboardSummary",
    "PersonStat",
    "Phase",
    "TaskCell",
    "TaskRow",
]
