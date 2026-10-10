"""The order engine's rules, with no I/O.

Three things live here and nowhere else:

* **the pipeline** - which nodes a process visits, which node comes next,
  which stage the order shows while a node is active, and how a node starts
  (assigned straight away when the orderer chose somebody, otherwise waiting
  for its Leader). The last production node's hand-in is the product: once
  it completes, the order goes to the gates (script lead's video review where
  it applies, then the orderer's final review);
* **the actions** - every verb a person can apply to an order, and from which
  stage each is allowed;
* **the policy** - who may apply which action, given their standing in the
  unit. :func:`available_actions` is the single authority: the API serialises
  it for the buttons, and the command service calls :func:`assert_allowed`
  before it writes, so a button and a refusal can never disagree.

Everything takes plain values (an :class:`OrderView`, a tuple of
:class:`NodeView`) rather than ORM rows, so the whole module is testable with
no database and the command service is the only place that knows about rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from meobot.domain.orders.errors import OrderActionNotAllowedError, OrderStateError
from meobot.domain.orders.models import (
    ACTIVE_NODE_STATUSES,
    NODE_PLAN,
    NODE_STAGE,
    PRODUCTION_NODES,
    TERMINAL_STAGES,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    OrderVideoType,
    last_production_node,
)
from meobot.domain.orders.permissions import (
    AdsPermission,
    AdsPermissions,
    AdsRoleKey,
    resolve_matrix,
)
from meobot.domain.units.models import UnitMemberRole, UnitMembership, UnitSettings

# --- the pipeline -------------------------------------------------------------


def initial_node_statuses(video_type: OrderVideoType) -> dict[OrderNodeType, OrderNodeStatus]:
    """The three production nodes at submission: planned ones wait, the rest
    are skipped. No link node: the last production node hands the link in."""
    planned = set(NODE_PLAN[video_type])
    return {
        node_type: (OrderNodeStatus.CHUA_TOI if node_type in planned else OrderNodeStatus.BO_QUA)
        for node_type in PRODUCTION_NODES
    }


def first_node(video_type: OrderVideoType) -> OrderNodeType:
    return NODE_PLAN[video_type][0]


def next_node(video_type: OrderVideoType, current: OrderNodeType) -> OrderNodeType | None:
    """The node after ``current`` for this video type, or ``None`` after the
    last (and for the legacy link node, which is past every production node)."""
    if current is OrderNodeType.GAN_LINK:
        return None
    plan = NODE_PLAN[video_type]
    index = plan.index(current)
    return plan[index + 1] if index + 1 < len(plan) else None


def stage_for_node(node_type: OrderNodeType) -> OrderStage:
    return NODE_STAGE[node_type]


def hands_in_product(video_type: OrderVideoType, node_type: OrderNodeType) -> bool:
    """Whether this node's hand-in is the finished product - the last
    production node of the process (or a legacy link node) - and so must
    carry the product link."""
    return node_type is OrderNodeType.GAN_LINK or node_type is last_production_node(video_type)


def activation_status(preassigned: bool) -> OrderNodeStatus:
    """How a node starts: working at once when somebody was chosen, else waiting."""
    return OrderNodeStatus.DANG_LAM if preassigned else OrderNodeStatus.CHUA_GIAO


def is_urgent(
    *, submitted_at: datetime, now: datetime, stage: OrderStage, urgent_days: int
) -> bool:
    """ "Gấp": older than the unit's limit and not finished. Derived, never stored."""
    if stage in TERMINAL_STAGES:
        return False
    return now - submitted_at > timedelta(days=urgent_days)


def format_order_code(
    member_code: str, video_type: OrderVideoType, day: datetime, number: int
) -> str:
    """``TUAN-D-261003-01``: who ordered, what kind, which day, which of that day."""
    return f"{member_code}-{video_type.value}-{day:%y%m%d}-{number:02d}"


# --- the actions ----------------------------------------------------------------


class OrderActionKind(StrEnum):
    RESUBMIT = "RESUBMIT"
    APPROVE_ORDER = "APPROVE_ORDER"
    RETURN_ORDER = "RETURN_ORDER"
    ASSIGN = "ASSIGN"
    ACCEPT = "ACCEPT"
    SUBMIT_WORK = "SUBMIT_WORK"
    APPROVE_NODE = "APPROVE_NODE"
    RETURN_NODE = "RETURN_NODE"
    APPROVE_VIDEO = "APPROVE_VIDEO"
    RETURN_VIDEO = "RETURN_VIDEO"
    APPROVE_FINAL = "APPROVE_FINAL"
    RETURN_FINAL = "RETURN_FINAL"
    SET_PRIORITY = "SET_PRIORITY"
    CANCEL = "CANCEL"
    #: 0053: change a node's tokens / deadline without handing it out again
    #: (also how the Leader enters the revision tokens after a final return).
    SET_NODE_PLAN = "SET_NODE_PLAN"


#: Which stage each action may be applied from. ``None`` = any non-terminal.
ACTION_STAGES: dict[OrderActionKind, frozenset[OrderStage] | None] = {
    OrderActionKind.RESUBMIT: frozenset({OrderStage.ORDER_RETURNED}),
    OrderActionKind.APPROVE_ORDER: frozenset({OrderStage.ORDER_PENDING}),
    OrderActionKind.RETURN_ORDER: frozenset({OrderStage.ORDER_PENDING}),
    OrderActionKind.ASSIGN: frozenset(
        {OrderStage.BIEN_TAP, OrderStage.THIET_KE, OrderStage.DUNG, OrderStage.GAN_LINK}
    ),
    OrderActionKind.ACCEPT: frozenset(
        {OrderStage.BIEN_TAP, OrderStage.THIET_KE, OrderStage.DUNG, OrderStage.GAN_LINK}
    ),
    # ``GAN_LINK``: a legacy order still waiting at the old link step hands
    # the link in as the last production node would.
    OrderActionKind.SUBMIT_WORK: frozenset(
        {OrderStage.BIEN_TAP, OrderStage.THIET_KE, OrderStage.DUNG, OrderStage.GAN_LINK}
    ),
    OrderActionKind.APPROVE_NODE: frozenset(
        {OrderStage.BIEN_TAP, OrderStage.THIET_KE, OrderStage.DUNG}
    ),
    OrderActionKind.RETURN_NODE: frozenset(
        {OrderStage.BIEN_TAP, OrderStage.THIET_KE, OrderStage.DUNG}
    ),
    OrderActionKind.APPROVE_VIDEO: frozenset({OrderStage.DUYET_VIDEO_BT}),
    OrderActionKind.RETURN_VIDEO: frozenset({OrderStage.DUYET_VIDEO_BT}),
    OrderActionKind.APPROVE_FINAL: frozenset({OrderStage.FINAL_REVIEW}),
    OrderActionKind.RETURN_FINAL: frozenset({OrderStage.FINAL_REVIEW}),
    OrderActionKind.SET_PRIORITY: None,
    OrderActionKind.CANCEL: None,
    OrderActionKind.SET_NODE_PLAN: frozenset(
        {OrderStage.BIEN_TAP, OrderStage.THIET_KE, OrderStage.DUNG}
    ),
}

#: The stages in which the active node's own actions apply.
NODE_ACTION_STAGES: frozenset[OrderStage] = frozenset(
    {OrderStage.BIEN_TAP, OrderStage.THIET_KE, OrderStage.DUNG, OrderStage.GAN_LINK}
)

#: Actions whose note is mandatory: every "send it back" carries a reason.
NOTE_REQUIRED: frozenset[OrderActionKind] = frozenset(
    {
        OrderActionKind.RETURN_ORDER,
        OrderActionKind.RETURN_NODE,
        OrderActionKind.RETURN_VIDEO,
        OrderActionKind.RETURN_FINAL,
        OrderActionKind.CANCEL,
    }
)

ACTION_LABELS: dict[OrderActionKind, str] = {
    OrderActionKind.RESUBMIT: "Gửi lại order",
    OrderActionKind.APPROVE_ORDER: "Duyệt order",
    OrderActionKind.RETURN_ORDER: "Không duyệt",
    OrderActionKind.ASSIGN: "Giao việc",
    OrderActionKind.ACCEPT: "Nhận việc",
    OrderActionKind.SUBMIT_WORK: "Nộp bài",
    OrderActionKind.APPROVE_NODE: "Duyệt",
    OrderActionKind.RETURN_NODE: "Trả sửa",
    OrderActionKind.APPROVE_VIDEO: "Duyệt video",
    OrderActionKind.RETURN_VIDEO: "Trả video",
    OrderActionKind.APPROVE_FINAL: "Duyệt Final",
    OrderActionKind.RETURN_FINAL: "Không duyệt Final",
    OrderActionKind.SET_PRIORITY: "Ưu tiên",
    OrderActionKind.CANCEL: "Huỷ order",
    OrderActionKind.SET_NODE_PLAN: "Sửa token/deadline",
}


# --- the views the policy reads ------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeView:
    id: uuid.UUID
    node_type: OrderNodeType
    status: OrderNodeStatus
    assignee_user_id: uuid.UUID | None
    preassigned_user_id: uuid.UUID | None
    accepted_at: datetime | None


@dataclass(frozen=True, slots=True)
class OrderView:
    id: uuid.UUID
    video_type: OrderVideoType
    stage: OrderStage
    owner_user_id: uuid.UUID
    is_priority: bool


@dataclass(frozen=True, slots=True)
class OrderActorContext:
    """What one person is, inside the Ads unit, for the policy's purposes."""

    user_id: uuid.UUID | None
    is_head: bool
    is_orderer: bool
    lead_node_types: frozenset[OrderNodeType]
    member_node_types: frozenset[OrderNodeType]
    #: What this person may manage, from the unit's permission matrix. Every
    #: decision below reads this, never the role directly.
    permissions: AdsPermissions = field(default_factory=AdsPermissions)

    @classmethod
    def from_membership(
        cls, membership: UnitMembership, settings: UnitSettings
    ) -> OrderActorContext:
        """Read the Ads tag. OWNER counts as head; others as their role says."""
        from meobot.domain.units.models import UnitCode

        entry = membership.entry(UnitCode.ADS)
        is_head = membership.is_owner or (entry is not None and entry.role is UnitMemberRole.HEAD)
        is_orderer = entry is not None and entry.role is UnitMemberRole.ORDERER
        leads: set[OrderNodeType] = set()
        members: set[OrderNodeType] = set()
        if entry is not None and entry.role in ROLE_NODES:
            node_type = ROLE_NODES[entry.role]
            members.add(node_type)
            if entry.is_lead:
                leads.add(node_type)
        if membership.is_owner:
            permissions = AdsPermissions.full()
        else:
            roles: list[AdsRoleKey] = []
            if entry is not None:
                if entry.role is UnitMemberRole.HEAD:
                    roles.append(AdsRoleKey.HEAD)
                if entry.role is UnitMemberRole.ORDERER:
                    roles.append(AdsRoleKey.ORDERER)
                if entry.role in ROLE_NODES:
                    roles.append(AdsRoleKey.STAFF)
                    if entry.is_lead:
                        roles.append(AdsRoleKey.LEAD)
            # An ADMIN sees every stream, tagged in it or not: the matrix's
            # Admin column applies either way.
            if membership.is_admin:
                roles.append(AdsRoleKey.ADMIN)
            permissions = AdsPermissions.compute(
                resolve_matrix(settings.permissions),
                roles=roles,
                function_nodes=frozenset(members),
                lead_nodes=frozenset(leads),
            )
        return cls(
            user_id=membership.user_id,
            is_head=is_head,
            is_orderer=is_orderer,
            lead_node_types=frozenset(leads),
            member_node_types=frozenset(members),
            permissions=permissions,
        )


ROLE_NODES: dict[UnitMemberRole, OrderNodeType] = {
    UnitMemberRole.BIEN_TAP: OrderNodeType.BIEN_TAP,
    UnitMemberRole.THIET_KE: OrderNodeType.THIET_KE,
    UnitMemberRole.DUNG: OrderNodeType.DUNG,
}


def function_node(node_type: OrderNodeType, video_type: OrderVideoType) -> OrderNodeType:
    """The function a node belongs to: itself, or for a legacy link node, the
    process's last production node (whose people hand the product in)."""
    if node_type is OrderNodeType.GAN_LINK:
        return last_production_node(video_type)
    return node_type


def video_review_applies(settings: UnitSettings, video_type: OrderVideoType) -> bool:
    """Whether the script lead reviews the finished cut before the final gate:
    the process has a script node and the unit switched the review on."""
    return OrderNodeType.BIEN_TAP in NODE_PLAN[video_type] and settings.review_video_by_script_lead


def node_needs_review(settings: UnitSettings, node_type: OrderNodeType) -> bool:
    """Whether handing in this node's work waits for its Leader."""
    return {
        OrderNodeType.BIEN_TAP: settings.review_bien_tap,
        OrderNodeType.THIET_KE: settings.review_thiet_ke,
        OrderNodeType.DUNG: settings.review_dung,
    }.get(node_type, True)


@dataclass(frozen=True, slots=True)
class OrderAction:
    kind: OrderActionKind
    label: str
    node_id: uuid.UUID | None
    requires_note: bool


# --- the policy -------------------------------------------------------------------


def active_node(nodes: tuple[NodeView, ...]) -> NodeView | None:
    for node in nodes:
        if node.status in ACTIVE_NODE_STATUSES:
            return node
    return None


def node_of(nodes: tuple[NodeView, ...], node_type: OrderNodeType) -> NodeView:
    found = find_node(nodes, node_type)
    if found is None:
        raise KeyError(node_type)
    return found


def find_node(nodes: tuple[NodeView, ...], node_type: OrderNodeType) -> NodeView | None:
    for node in nodes:
        if node.node_type is node_type:
            return node
    return None


def available_actions(
    order: OrderView, nodes: tuple[NodeView, ...], ctx: OrderActorContext
) -> tuple[OrderAction, ...]:
    """Every action this person may take on this order right now."""
    if order.stage in TERMINAL_STAGES or ctx.user_id is None:
        return ()
    actions: list[OrderAction] = []
    is_owner = ctx.user_id == order.owner_user_id
    current = active_node(nodes)

    def add(kind: OrderActionKind, node_id: uuid.UUID | None = None) -> None:
        actions.append(
            OrderAction(
                kind=kind,
                label=ACTION_LABELS[kind],
                node_id=node_id,
                requires_note=kind in NOTE_REQUIRED,
            )
        )

    may = ctx.permissions
    if order.stage is OrderStage.ORDER_PENDING and may.allows(AdsPermission.ORDER_APPROVE):
        add(OrderActionKind.APPROVE_ORDER)
        add(OrderActionKind.RETURN_ORDER)
    if order.stage is OrderStage.ORDER_RETURNED and is_owner:
        add(OrderActionKind.RESUBMIT)

    if current is not None and order.stage in NODE_ACTION_STAGES:
        # A legacy link node is managed by the last production node's function.
        managed = function_node(current.node_type, order.video_type)
        assigns = managed in may.nodes(AdsPermission.NODE_ASSIGN)
        reviews = current.node_type in may.nodes(AdsPermission.NODE_REVIEW)
        working = ctx.user_id == current.assignee_user_id and current.status in (
            OrderNodeStatus.DANG_LAM,
            OrderNodeStatus.DANG_SUA,
        )
        if assigns and current.status in (
            OrderNodeStatus.CHUA_GIAO,
            OrderNodeStatus.DANG_LAM,
            OrderNodeStatus.DANG_SUA,
        ):
            add(OrderActionKind.ASSIGN, current.id)
        if (
            assigns
            and current.node_type is not OrderNodeType.GAN_LINK
            and current.status
            in (
                OrderNodeStatus.CHUA_GIAO,
                OrderNodeStatus.DANG_LAM,
                OrderNodeStatus.DANG_SUA,
                OrderNodeStatus.CHO_DUYET,
            )
        ):
            add(OrderActionKind.SET_NODE_PLAN, current.id)
        # Assigned is not accepted: the assignee presses "Nhận việc" first,
        # and only then may hand the work in.
        if working and current.accepted_at is None:
            add(OrderActionKind.ACCEPT, current.id)
        if working and current.accepted_at is not None:
            add(OrderActionKind.SUBMIT_WORK, current.id)
        if (
            reviews
            and current.status is OrderNodeStatus.CHO_DUYET
            and current.node_type is not OrderNodeType.GAN_LINK
        ):
            add(OrderActionKind.APPROVE_NODE, current.id)
            add(OrderActionKind.RETURN_NODE, current.id)

    # The gates act on the product: the last production node's hand-in.
    product = find_node(nodes, last_production_node(order.video_type))
    product_id = None if product is None else product.id
    if order.stage is OrderStage.DUYET_VIDEO_BT and OrderNodeType.BIEN_TAP in may.nodes(
        AdsPermission.VIDEO_REVIEW
    ):
        add(OrderActionKind.APPROVE_VIDEO, product_id)
        add(OrderActionKind.RETURN_VIDEO, product_id)
    # The final review is the orderer's; a holder of FINAL_REVIEW stands in.
    if order.stage is OrderStage.FINAL_REVIEW and (
        is_owner or may.allows(AdsPermission.FINAL_REVIEW)
    ):
        add(OrderActionKind.APPROVE_FINAL, product_id)
        add(OrderActionKind.RETURN_FINAL, product_id)

    if may.allows(AdsPermission.PRIORITY):
        add(OrderActionKind.SET_PRIORITY)
    if may.allows(AdsPermission.CANCEL) or (
        is_owner
        and may.on_own_order(AdsPermission.CANCEL)
        and order.stage in (OrderStage.ORDER_PENDING, OrderStage.ORDER_RETURNED)
    ):
        add(OrderActionKind.CANCEL)
    return tuple(actions)


def assert_allowed(
    kind: OrderActionKind,
    order: OrderView,
    nodes: tuple[NodeView, ...],
    ctx: OrderActorContext,
    *,
    node_id: uuid.UUID | None = None,
) -> OrderAction:
    """The action, or the right refusal: a 409 when the stage forbids it for
    everyone, a 403 when the stage allows it but not for this person."""
    stages = ACTION_STAGES[kind]
    if order.stage in TERMINAL_STAGES or (stages is not None and order.stage not in stages):
        raise OrderStateError(
            "Order không ở trạng thái cho phép thao tác này.",
            details={
                "reason": "order_stage_mismatch",
                "stage": order.stage.value,
                "action": kind.value,
            },
        )
    for action in available_actions(order, nodes, ctx):
        if action.kind is kind and (node_id is None or action.node_id == node_id):
            return action
    raise OrderActionNotAllowedError(
        "Bạn không có quyền thực hiện thao tác này trên order.",
        details={"reason": "order_action_forbidden", "action": kind.value},
    )


__all__ = [
    "ACTION_LABELS",
    "ACTION_STAGES",
    "NOTE_REQUIRED",
    "ROLE_NODES",
    "NodeView",
    "OrderAction",
    "OrderActionKind",
    "OrderActorContext",
    "OrderView",
    "activation_status",
    "active_node",
    "assert_allowed",
    "available_actions",
    "find_node",
    "first_node",
    "format_order_code",
    "function_node",
    "hands_in_product",
    "initial_node_statuses",
    "is_urgent",
    "next_node",
    "node_of",
    "stage_for_node",
    "video_review_applies",
]
