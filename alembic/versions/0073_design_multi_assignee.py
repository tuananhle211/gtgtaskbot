"""Design node: one task, several designers, each scored (0069).

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-22

A Design (THIET_KE) node can now be handed to several people at once, each
with their own list of design work items ("mã task design" + quantity) that
their monthly score is read from. Two append-only-ish tables hold this:

* ``order_node_assignees`` - one row per person on a node (a single-assignee
  node keeps exactly one row), with their accept / submit stamps and their
  own ``work_items``. A partial unique index keeps one open row per
  (node, user).
* ``order_node_assignee_events`` - the history of each person's list, so the
  scoring can see what was claimed and when it changed.

Existing orders are untouched: nothing backfills rows, and single-assignee
nodes keep working off ``order_nodes.assignee_user_id`` until they next move.

Downgrade
---------

Drops both tables. No data is kept.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSIGNEES = "order_node_assignees"
EVENTS = "order_node_assignee_events"
ORDER_NODES = "order_nodes"
ORDER_SUBMISSIONS = "order_submissions"
USERS = "users"
RESTRICT = "RESTRICT"

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        ASSIGNEES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("work_items", _JSON, nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["node_id"],
            [f"{ORDER_NODES}.id"],
            name=op.f("fk_order_node_assignees_node_id_order_nodes"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_order_node_assignees_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{ORDER_SUBMISSIONS}.id"],
            name=op.f("fk_order_node_assignees_submission_id_order_submissions"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_node_assignees")),
    )
    op.create_index(
        "uq_order_node_assignees_node_user",
        ASSIGNEES,
        ["node_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
        sqlite_where=sa.text("removed_at IS NULL"),
    )
    op.create_index("ix_order_node_assignees_user", ASSIGNEES, ["user_id"], unique=False)

    op.create_table(
        EVENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("work_items", _JSON, nullable=True),
        sa.Column("revision_no", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            [f"{ORDER_NODES}.id"],
            name=op.f("fk_order_node_assignee_events_node_id_order_nodes"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_order_node_assignee_events_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_order_node_assignee_events_actor_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_node_assignee_events")),
    )
    op.create_index(
        "ix_order_node_assignee_events_node", EVENTS, ["node_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_order_node_assignee_events_node", table_name=EVENTS)
    op.drop_table(EVENTS)
    op.drop_index("ix_order_node_assignees_user", table_name=ASSIGNEES)
    op.drop_index("uq_order_node_assignees_node_user", table_name=ASSIGNEES)
    op.drop_table(ASSIGNEES)
