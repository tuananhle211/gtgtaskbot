"""The Ads order engine's tables.

Seven tables, added by ``0042``, and **none of them is a PR table**. The PR
content workflow keeps every column and every row it had; an Ads order lives
here, in its own rows, and meets PR only in the read layer that shows both on
one board.

* ``orders`` - one video the Ads department asked for. Its ``stage`` is the
  order's own position, including the approval gates that belong to no node.
* ``order_nodes`` - exactly four rows per order, one per node type, created
  with the order. Nodes the video type skips are ``BO_QUA`` from the start, so
  "which nodes does this order visit" is answered by the rows, not recomputed.
  A partial unique index holds the engine's invariant: **at most one node per
  order is active**.
* ``order_submissions`` - every hand-in and every product link, numbered
  ``V1``, ``V2``… per node. Append-only.
* ``order_events`` - the log. One row per action, never edited. Also the
  source the KPI recorder reads from.
* ``order_approvals`` - the decisions at the three gates (order, script-lead
  video review, final), one row per round. Append-only.
* ``order_code_counters`` - the per-(unit, member, day) number behind
  ``TUAN-D-261003-01``. Bumped by an atomic upsert, like ``pr_code_counters``.
* ``order_work_rules`` - which Work type a node's approval counts as.

Every optimistic write checks ``version``: the web and Telegram both hold
stale buttons for minutes, and "this was already handled" must be a 409, not
a double approval.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.org_unit import ORG_UNITS, UNIT_DURATIONS, UNIT_PLATFORMS, UNIT_VIDEO_KINDS
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.db.models.pr_work import WORK_TYPES
from meobot.domain.orders.models import (
    OrderApprovalDecision,
    OrderApprovalGate,
    OrderEventKind,
    OrderNodeStatus,
    OrderNodeType,
    OrderScriptSource,
    OrderStage,
    OrderVideoType,
)

ORDERS = "orders"
ORDER_NODES = "order_nodes"
ORDER_SUBMISSIONS = "order_submissions"
ORDER_EVENTS = "order_events"
ORDER_APPROVALS = "order_approvals"
ORDER_CODE_COUNTERS = "order_code_counters"
ORDER_WORK_RULES = "order_work_rules"

#: The SQL twin of :data:`~meobot.domain.orders.models.ACTIVE_NODE_STATUSES`.
#: Spelled out so the migration and the model declare the same index.
ACTIVE_NODE = text("status IN ('CHUA_GIAO', 'DANG_LAM', 'AI_DANG_REVIEW', 'CHO_DUYET', 'DANG_SUA')")
#: The two halves of the work-rule uniqueness, as for ``pr_content_work_rules``.
TYPED_WORK_RULE = text("video_type IS NOT NULL")
DEFAULT_WORK_RULE = text("video_type IS NULL")


def _not_empty(column: str) -> str:
    return f"length(trim({column})) > 0"


#: The processes that edit without a design node, spelled out for the CHECK.
DESIGN_LINK_RULE = "video_type NOT IN ('D', 'BD') OR design_link IS NOT NULL"


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One video the Ads department ordered."""

    __tablename__ = ORDERS
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("title"), name="title_not_empty"),
        # An edit with no design node (process D or BD): the designer's work
        # must arrive with the order. The SQL twin of ``needs_design_link``.
        CheckConstraint(DESIGN_LINK_RULE, name="design_link_required_without_design"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_orders_unit_submitted", "unit_id", "submitted_at"),
        Index("ix_orders_unit_owner_submitted", "unit_id", "owner_user_id", "submitted_at"),
        Index("ix_orders_unit_stage", "unit_id", "stage"),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False
    )
    #: ``TUAN-D-261003-01``: member code, process code, day, number in the day.
    #: Allocated at the first submission and never changed.
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: The process code ("Quy trình"): which production nodes the order visits.
    video_type: Mapped[OrderVideoType] = mapped_column(
        value_enum(OrderVideoType, name="order_video_type", length=5), nullable=False
    )
    #: The video kind ("Loại video") picked from the unit's catalogue, and a
    #: snapshot of its name and points taken at submission (and resubmission)
    #: so a later edit of the catalogue never rewrites this order.
    video_kind_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{UNIT_VIDEO_KINDS}.id", ondelete=RESTRICT), nullable=True, index=True
    )
    video_kind_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    video_kind_points: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: The target platform ("Nền tảng") and a snapshot of its name.
    platform_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{UNIT_PLATFORMS}.id", ondelete=RESTRICT), nullable=True, index=True
    )
    platform_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The video duration ("Thời lượng") and a snapshot of its name and points.
    duration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{UNIT_DURATIONS}.id", ondelete=RESTRICT), nullable=True, index=True
    )
    duration_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duration_points: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: The brief: idea, hook, wording, voice script when it is an AI script.
    order_content: Mapped[str] = mapped_column(Text, nullable=False)
    script_source: Mapped[OrderScriptSource | None] = mapped_column(
        value_enum(OrderScriptSource, name="order_script_source", length=10), nullable=True
    )
    design_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Free-text requirement note from the orderer.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    stage: Mapped[OrderStage] = mapped_column(
        value_enum(OrderStage, name="order_stage", length=20),
        nullable=False,
        default=OrderStage.ORDER_PENDING,
        server_default=OrderStage.ORDER_PENDING.value,
    )
    #: The first submission. The urgency clock and the day in the code both
    #: read this, and a return-and-resubmit does not move it.
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    order_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    order_approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    returned_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_priority: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The delivered video, shown to the orderer once the head approves it.
    product_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class OrderNode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One production step of one order."""

    __tablename__ = ORDER_NODES
    __table_args__ = (
        UniqueConstraint("order_id", "node_type", name="uq_order_nodes_order_id_node_type"),
        CheckConstraint("revision_count >= 0", name="revision_count_not_negative"),
        CheckConstraint("submission_count >= 0", name="submission_count_not_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        # The engine's invariant, held by the database: one active node per
        # order. The service flushes a node's completion before it activates
        # the next one so the two never overlap inside one statement.
        Index(
            "uq_order_nodes_active_per_order",
            "order_id",
            unique=True,
            postgresql_where=ACTIVE_NODE,
            sqlite_where=ACTIVE_NODE,
        ),
        Index("ix_order_nodes_assignee_status", "assignee_user_id", "status"),
        Index("ix_order_nodes_preassigned_user_id", "preassigned_user_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORDERS}.id", ondelete=RESTRICT), nullable=False
    )
    node_type: Mapped[OrderNodeType] = mapped_column(
        value_enum(OrderNodeType, name="order_node_type", length=10), nullable=False
    )
    status: Mapped[OrderNodeStatus] = mapped_column(
        value_enum(OrderNodeStatus, name="order_node_status", length=15),
        nullable=False,
        default=OrderNodeStatus.CHUA_TOI,
        server_default=OrderNodeStatus.CHUA_TOI.value,
    )
    #: Chosen by the orderer on the form; becomes the assignee when the node
    #: activates. The Leader may still hand the work to somebody else.
    preassigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The first approval only; a later round after a return keeps it. The
    #: KPI recorder keys on this being ``NULL`` before the approval.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: How many times the node was sent back.
    revision_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: The number of the last submission - the ``V2`` in a file name. Kept on
    #: the row so numbering never needs a count over the submissions.
    submission_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class OrderSubmission(Base, UUIDPrimaryKeyMixin):
    """One hand-in. Append-only: a correction is the next submission."""

    __tablename__ = ORDER_SUBMISSIONS
    __table_args__ = (
        UniqueConstraint(
            "node_id", "submission_no", name="uq_order_submissions_node_id_submission_no"
        ),
        CheckConstraint("submission_no >= 1", name="submission_no_positive"),
        CheckConstraint("link IS NOT NULL OR script_text IS NOT NULL", name="has_payload"),
    )

    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORDER_NODES}.id", ondelete=RESTRICT), nullable=False
    )
    submission_no: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The script as text, so a future AI review can read it.
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Stamped in Python as well as by the database, so the log keeps its order
    #: on a backend whose ``now()`` has only seconds (the offline suite).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class OrderEvent(Base, UUIDPrimaryKeyMixin):
    """One thing that happened to one order. Never edited, never deleted."""

    __tablename__ = ORDER_EVENTS
    __table_args__ = (Index("ix_order_events_order_created", "order_id", "created_at"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORDERS}.id", ondelete=RESTRICT), nullable=False
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{ORDER_NODES}.id", ondelete=RESTRICT), nullable=True
    )
    kind: Mapped[OrderEventKind] = mapped_column(
        value_enum(OrderEventKind, name="order_event_kind", length=30), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: The person the action concerned, when it concerned one: who was
    #: assigned, whose work was approved.
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{ORDER_SUBMISSIONS}.id", ondelete=RESTRICT), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Stamped in Python as well as by the database, so the log keeps its order
    #: on a backend whose ``now()`` has only seconds (the offline suite).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class OrderApproval(Base, UUIDPrimaryKeyMixin):
    """One decision at one gate. Append-only; a return opens the next round."""

    __tablename__ = ORDER_APPROVALS
    __table_args__ = (
        UniqueConstraint(
            "order_id", "gate", "round_no", name="uq_order_approvals_order_id_gate_round_no"
        ),
        CheckConstraint("round_no >= 1", name="round_no_positive"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORDERS}.id", ondelete=RESTRICT), nullable=False
    )
    gate: Mapped[OrderApprovalGate] = mapped_column(
        value_enum(OrderApprovalGate, name="order_approval_gate", length=10), nullable=False
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[OrderApprovalDecision] = mapped_column(
        value_enum(OrderApprovalDecision, name="order_approval_decision", length=10),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{ORDER_SUBMISSIONS}.id", ondelete=RESTRICT), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Stamped in Python as well as by the database, so the log keeps its order
    #: on a backend whose ``now()`` has only seconds (the offline suite).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class OrderCodeCounter(Base, UUIDPrimaryKeyMixin):
    """The last number handed out for one (unit, member, day).

    ``last_no`` rather than ``pr_code_counters``' "next value": the upsert
    inserts ``1`` or adds one, and ``RETURNING last_no`` is the number in both
    cases. No nullable column takes part in the key, so a plain unique is
    enough for ``ON CONFLICT``.
    """

    __tablename__ = ORDER_CODE_COUNTERS
    __table_args__ = (
        UniqueConstraint(
            "unit_id", "member_code", "day", name="uq_order_code_counters_unit_id_member_code_day"
        ),
        CheckConstraint(_not_empty("member_code"), name="member_code_not_empty"),
        CheckConstraint("last_no >= 0", name="last_no_not_negative"),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False
    )
    member_code: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The calendar day in the application timezone.
    day: Mapped[date] = mapped_column(Date, nullable=False)
    last_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class OrderWorkRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Which Work type one node's approval counts as.

    Exact beats default, as for ``pr_content_work_rules``: a rule with a
    ``video_type`` wins over one without for the same node. Two partial
    indexes hold "one per (node, type)" and "one default per node", because a
    plain unique would let ``NULL`` repeat.
    """

    __tablename__ = ORDER_WORK_RULES
    __table_args__ = (
        Index(
            "uq_order_work_rules_unit_node_type",
            "unit_id",
            "node_type",
            "video_type",
            unique=True,
            postgresql_where=TYPED_WORK_RULE,
            sqlite_where=TYPED_WORK_RULE,
        ),
        Index(
            "uq_order_work_rules_unit_node_default",
            "unit_id",
            "node_type",
            unique=True,
            postgresql_where=DEFAULT_WORK_RULE,
            sqlite_where=DEFAULT_WORK_RULE,
        ),
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{ORG_UNITS}.id", ondelete=RESTRICT), nullable=False
    )
    node_type: Mapped[OrderNodeType] = mapped_column(
        value_enum(OrderNodeType, name="order_work_rule_node_type", length=10), nullable=False
    )
    video_type: Mapped[OrderVideoType | None] = mapped_column(
        value_enum(OrderVideoType, name="order_work_rule_video_type", length=5), nullable=True
    )
    work_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{WORK_TYPES}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


__all__ = [
    "ACTIVE_NODE",
    "DEFAULT_WORK_RULE",
    "DESIGN_LINK_RULE",
    "ORDERS",
    "ORDER_APPROVALS",
    "ORDER_CODE_COUNTERS",
    "ORDER_EVENTS",
    "ORDER_NODES",
    "ORDER_SUBMISSIONS",
    "ORDER_WORK_RULES",
    "TYPED_WORK_RULE",
    "Order",
    "OrderApproval",
    "OrderCodeCounter",
    "OrderEvent",
    "OrderNode",
    "OrderSubmission",
    "OrderWorkRule",
]
