"""ORD: token effort and deadlines (docs/ord/ORD_TOKEN_DEADLINE_PLAN.md).

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-10

Additive. Nothing PR reads or writes changes.

* ``orders.desired_deadline_at`` - the orderer's wished finish (required for
  new orders by the API; old rows keep NULL) and ``over_deadline_count`` -
  how often a node deadline was set past it, or the order finished after it.
* ``order_nodes``: ``token_estimate`` / ``token_revision`` (the effort the
  Leader puts on the node, and the revision tokens on top), ``deadline_at`` /
  ``revision_deadline_at``, ``deadline_met`` (fixed at the first completion)
  and ``revision_tokens_pending`` (sent back by the orderer or the script lead:
  the Leader still has to enter the revision tokens).
* ``org_unit_members.daily_tokens`` - a member's own daily budget; NULL = the
  unit default (``UnitSettings.default_daily_tokens``).
* ``order_token_ledger`` - append-only: the tokens taken off a person's day
  when a node (or a revision of it) is approved. Unique per
  ``(node_id, kind, revision_no)`` so a retry never takes them twice.

Downgrade
---------

Drops the ledger and every column above.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDERS = "orders"
NODES = "order_nodes"
MEMBERS = "org_unit_members"
LEDGER = "order_token_ledger"
RESTRICT = "RESTRICT"

#: Every ``order_events.kind`` value after this revision (0042 wrote the first
#: eighteen; the column is a plain VARCHAR, so the two new ones need no DDL).
EVENT_KIND_VALUES = (
    "SUBMITTED",
    "RESUBMITTED",
    "ORDER_APPROVED",
    "ORDER_RETURNED",
    "NODE_ACTIVATED",
    "NODE_ASSIGNED",
    "NODE_ACCEPTED",
    "WORK_SUBMITTED",
    "NODE_APPROVED",
    "NODE_RETURNED",
    "LINK_ATTACHED",
    "VIDEO_APPROVED",
    "VIDEO_RETURNED",
    "FINAL_APPROVED",
    "FINAL_RETURNED",
    "PRIORITY_SET",
    "PRIORITY_CLEARED",
    "CANCELLED",
    "PLAN_SET",
    "DEADLINE_EXCEEDED",
)


def upgrade() -> None:
    op.add_column(ORDERS, sa.Column("desired_deadline_at", sa.DateTime(timezone=True)))
    op.add_column(
        ORDERS,
        sa.Column("over_deadline_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(NODES, sa.Column("token_estimate", sa.Numeric(precision=6, scale=2)))
    op.add_column(
        NODES,
        sa.Column(
            "token_revision",
            sa.Numeric(precision=6, scale=2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(NODES, sa.Column("deadline_at", sa.DateTime(timezone=True)))
    op.add_column(NODES, sa.Column("revision_deadline_at", sa.DateTime(timezone=True)))
    op.add_column(NODES, sa.Column("deadline_met", sa.Boolean()))
    op.add_column(
        NODES,
        sa.Column(
            "revision_tokens_pending", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(MEMBERS, sa.Column("daily_tokens", sa.Numeric(precision=6, scale=2)))

    op.create_table(
        LEDGER,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("tokens", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("ESTIMATE", "REVISION", name="order_token_kind", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["org_units.id"],
            name=op.f("fk_order_token_ledger_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_order_token_ledger_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_token_ledger_order_id_orders"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["order_nodes.id"],
            name=op.f("fk_order_token_ledger_node_id_order_nodes"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_token_ledger")),
        sa.UniqueConstraint(
            "node_id", "kind", "revision_no", name="uq_order_token_ledger_node_kind_revision"
        ),
        sa.CheckConstraint("tokens >= 0", name=op.f("ck_order_token_ledger_tokens_not_negative")),
    )
    op.create_index(
        "ix_order_token_ledger_unit_user_day", LEDGER, ["unit_id", "user_id", "work_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_order_token_ledger_unit_user_day", table_name=LEDGER)
    op.drop_table(LEDGER)
    op.drop_column(MEMBERS, "daily_tokens")
    for column in (
        "revision_tokens_pending",
        "deadline_met",
        "revision_deadline_at",
        "deadline_at",
        "token_revision",
        "token_estimate",
    ):
        op.drop_column(NODES, column)
    op.drop_column(ORDERS, "over_deadline_count")
    op.drop_column(ORDERS, "desired_deadline_at")
