"""Vocabulary of the Ads order engine.

An **order** is one video the Ads department asks the Media team for. It moves
through a sequence of production **nodes** - script (``BIEN_TAP``), design
(``THIET_KE``) and edit (``DUNG``) - and which of them it visits is decided
once, by the **process** ("Quy trình") ticked when the order is placed. The
process is stored in ``orders.video_type`` as a code: the letters of the
ticked production nodes, in pipeline order - ``B`` (Biên kịch), ``T``
(Design), ``D`` (Dựng):

===== ==========================================
Code  Nodes
===== ==========================================
B     BIEN_TAP
T     THIET_KE
D     DUNG
BT    BIEN_TAP → THIET_KE
BD    BIEN_TAP → DUNG
TD    THIET_KE → DUNG
BTD   BIEN_TAP → THIET_KE → DUNG
===== ==========================================

The **last** production node's hand-in is the finished product: it must carry
the product link, and once that node completes (its Leader approves, or the
hand-in itself when the unit does not review it) the order goes straight to
the orderer's final review. There used to be a fourth node, ``GAN_LINK``
("Gắn link"), where somebody pasted the link after the edit; new orders no
longer get one. The enum value stays so that rows created before the change
still load; every read hides them and the engine treats them as skipped.

``D``, ``TD`` and ``BTD`` are the three fixed video types the engine started
with; the other four arrived with the flexible process (``0044``).

Two state machines run side by side and are stored separately: the order's
**stage** (where the order is, including the approval gates that are nobody's
node) and each node's **status**. ``orders.stage`` is the one the lists sort
and filter on; the node statuses are what the per-column cells show.

Nothing here is shared with :mod:`meobot.domain.pr`. The PR workflow keeps its
fourteen stages untouched; the two units meet only in the read layer that maps
both onto five phases.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from types import MappingProxyType


class OrderVideoType(StrEnum):
    """The process code: which production nodes an order visits. Chosen once,
    at submission. The first three are the original fixed video types."""

    D = "D"
    TD = "TD"
    BTD = "BTD"
    B = "B"
    T = "T"
    BT = "BT"
    BD = "BD"


class OrderScriptSource(StrEnum):
    """Where the voice script comes from: generated or shot for real."""

    AI = "AI"
    REAL = "REAL"


class OrderStage(StrEnum):
    """Where one order is.

    The production stages carry the name of the node that is active, so a
    list can show "Thiết kế" without joining the nodes. ``DUYET_VIDEO_BT`` is
    the script lead's review of the finished cut and exists only for orders
    whose process has a script node; ``FINAL_REVIEW`` is the orderer's last
    gate (a holder of the ``FINAL_REVIEW`` permission may stand in).
    """

    ORDER_PENDING = "ORDER_PENDING"
    ORDER_RETURNED = "ORDER_RETURNED"
    BIEN_TAP = "BIEN_TAP"
    THIET_KE = "THIET_KE"
    DUNG = "DUNG"
    #: Legacy: no new order reaches it (see the module docstring).
    GAN_LINK = "GAN_LINK"
    DUYET_VIDEO_BT = "DUYET_VIDEO_BT"
    FINAL_REVIEW = "FINAL_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class OrderNodeType(StrEnum):
    """The production nodes, in pipeline order. ``GAN_LINK`` is legacy: only
    orders created before the link step was folded into the last production
    node have one (see the module docstring)."""

    BIEN_TAP = "BIEN_TAP"
    THIET_KE = "THIET_KE"
    DUNG = "DUNG"
    GAN_LINK = "GAN_LINK"


class OrderNodeStatus(StrEnum):
    """One node's standing.

    ``BO_QUA`` is decided by the video type and never changes. ``CHUA_TOI``
    means the pipeline has not reached the node yet. ``AI_DANG_REVIEW`` is
    reserved for the script node once AI review is wired to Ads; no code sets
    it in this release, but the column already admits it so that wiring it is
    not a migration.
    """

    CHUA_TOI = "CHUA_TOI"
    BO_QUA = "BO_QUA"
    CHUA_GIAO = "CHUA_GIAO"
    DANG_LAM = "DANG_LAM"
    AI_DANG_REVIEW = "AI_DANG_REVIEW"
    CHO_DUYET = "CHO_DUYET"
    DANG_SUA = "DANG_SUA"
    HOAN_THANH = "HOAN_THANH"


class OrderEventKind(StrEnum):
    """What happened. One row per action in ``order_events``, never edited."""

    SUBMITTED = "SUBMITTED"
    RESUBMITTED = "RESUBMITTED"
    ORDER_APPROVED = "ORDER_APPROVED"
    ORDER_RETURNED = "ORDER_RETURNED"
    NODE_ACTIVATED = "NODE_ACTIVATED"
    NODE_ASSIGNED = "NODE_ASSIGNED"
    NODE_ACCEPTED = "NODE_ACCEPTED"
    WORK_SUBMITTED = "WORK_SUBMITTED"
    NODE_APPROVED = "NODE_APPROVED"
    NODE_RETURNED = "NODE_RETURNED"
    #: Legacy: written by the old link step only.
    LINK_ATTACHED = "LINK_ATTACHED"
    VIDEO_APPROVED = "VIDEO_APPROVED"
    VIDEO_RETURNED = "VIDEO_RETURNED"
    FINAL_APPROVED = "FINAL_APPROVED"
    FINAL_RETURNED = "FINAL_RETURNED"
    PRIORITY_SET = "PRIORITY_SET"
    PRIORITY_CLEARED = "PRIORITY_CLEARED"
    CANCELLED = "CANCELLED"


class OrderApprovalGate(StrEnum):
    """The three decisions that are not a node's review."""

    ORDER = "ORDER"
    VIDEO_BT = "VIDEO_BT"
    FINAL = "FINAL"


class OrderApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"


#: The production nodes, in pipeline order, and the letter each adds to a
#: process code.
PRODUCTION_NODES: tuple[OrderNodeType, ...] = (
    OrderNodeType.BIEN_TAP,
    OrderNodeType.THIET_KE,
    OrderNodeType.DUNG,
)
PROCESS_LETTERS: MappingProxyType[OrderNodeType, str] = MappingProxyType(
    {OrderNodeType.BIEN_TAP: "B", OrderNodeType.THIET_KE: "T", OrderNodeType.DUNG: "D"}
)


def _plan(code: OrderVideoType) -> tuple[OrderNodeType, ...]:
    return tuple(node for node in PRODUCTION_NODES if PROCESS_LETTERS[node] in code.value)


#: The nodes each process visits, in order. The last one hands in the product.
NODE_PLAN: MappingProxyType[OrderVideoType, tuple[OrderNodeType, ...]] = MappingProxyType(
    {code: _plan(code) for code in OrderVideoType}
)


def production_nodes(code: OrderVideoType) -> tuple[OrderNodeType, ...]:
    """The production nodes a process visits, in pipeline order."""
    return NODE_PLAN[code]


def last_production_node(code: OrderVideoType) -> OrderNodeType:
    """The node whose hand-in is the finished product (its link is the
    order's product link) and whose completion opens the final review."""
    return NODE_PLAN[code][-1]


def process_code(nodes: Iterable[OrderNodeType]) -> OrderVideoType | None:
    """The code for a set of ticked production nodes, in any order; ``None``
    when none is ticked. Raises ``ValueError`` for a node that is not a
    production node (the legacy ``GAN_LINK`` is never ticked)."""
    ticked = set(nodes)
    stray = ticked - set(PRODUCTION_NODES)
    if stray:
        raise ValueError(f"not a production node: {sorted(item.value for item in stray)}")
    letters = "".join(PROCESS_LETTERS[node] for node in PRODUCTION_NODES if node in ticked)
    return OrderVideoType(letters) if letters else None


def needs_design_link(code: OrderVideoType) -> bool:
    """An edit with no design node: the design must arrive with the order."""
    plan = NODE_PLAN[code]
    return OrderNodeType.DUNG in plan and OrderNodeType.THIET_KE not in plan


#: A node in one of these is "the" active node of its order. At most one per
#: order - held by a partial unique index, not only by the service.
ACTIVE_NODE_STATUSES: frozenset[OrderNodeStatus] = frozenset(
    {
        OrderNodeStatus.CHUA_GIAO,
        OrderNodeStatus.DANG_LAM,
        OrderNodeStatus.AI_DANG_REVIEW,
        OrderNodeStatus.CHO_DUYET,
        OrderNodeStatus.DANG_SUA,
    }
)

#: Nothing moves an order out of these.
TERMINAL_STAGES: frozenset[OrderStage] = frozenset({OrderStage.COMPLETED, OrderStage.CANCELLED})

#: The stage an order shows while a given node is active.
NODE_STAGE: MappingProxyType[OrderNodeType, OrderStage] = MappingProxyType(
    {
        OrderNodeType.BIEN_TAP: OrderStage.BIEN_TAP,
        OrderNodeType.THIET_KE: OrderStage.THIET_KE,
        OrderNodeType.DUNG: OrderStage.DUNG,
        # Legacy: an order created before the link step was folded in.
        OrderNodeType.GAN_LINK: OrderStage.GAN_LINK,
    }
)

#: The nodes whose first completion is a KPI result. The legacy link node
#: was a hand-off, never measured.
KPI_NODE_TYPES: frozenset[OrderNodeType] = frozenset(
    {OrderNodeType.BIEN_TAP, OrderNodeType.THIET_KE, OrderNodeType.DUNG}
)


__all__ = [
    "ACTIVE_NODE_STATUSES",
    "KPI_NODE_TYPES",
    "NODE_PLAN",
    "NODE_STAGE",
    "PROCESS_LETTERS",
    "PRODUCTION_NODES",
    "TERMINAL_STAGES",
    "OrderApprovalDecision",
    "OrderApprovalGate",
    "OrderEventKind",
    "OrderNodeStatus",
    "OrderNodeType",
    "OrderScriptSource",
    "OrderStage",
    "OrderVideoType",
    "last_production_node",
    "needs_design_link",
    "process_code",
    "production_nodes",
]
