"""Đề xuất tài nguyên (0070): resource purchase requests.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-21

* ``resource_requests`` - one request: what, how many, where, why, the
  budget and the date, its stage (HEAD_REVIEW -> FUNDING -> PURCHASING ->
  DELIVERED -> COMPLETED, or REJECTED / CANCELLED), the heads who may decide
  its first step, and the purchase's actual cost and supplier;
* ``resource_request_events`` - its history, one line per step;
* ``resources.request_id`` - every resource bought for a request points at
  it (the request's page lists them).

Downgrade
---------

Drops the column and both tables (the requests are lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column(
            "requester_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "type_id",
            sa.Uuid(),
            sa.ForeignKey("resource_options.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("site", sa.String(12), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("requirements", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("replaces", sa.String(200), nullable=True),
        sa.Column("budget", sa.BigInteger(), nullable=False),
        sa.Column("needed_by", sa.Date(), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("head_approver_ids", sa.JSON(), nullable=False),
        sa.Column("actual_cost", sa.BigInteger(), nullable=True),
        sa.Column("supplier", sa.String(200), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_resource_requests_quantity_positive")),
        sa.CheckConstraint("budget >= 0", name=op.f("ck_resource_requests_budget_not_negative")),
    )
    op.create_index(
        "ix_resource_requests_requester_user_id", "resource_requests", ["requester_user_id"]
    )
    op.create_table(
        "resource_request_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("resource_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_resource_request_events_request_id", "resource_request_events", ["request_id"]
    )
    op.add_column(
        "resources",
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey(
                "resource_requests.id",
                ondelete="RESTRICT",
                name=op.f("fk_resources_request_id_resource_requests"),
            ),
            nullable=True,
        ),
    )
    op.create_index("ix_resources_request_id", "resources", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_resources_request_id", table_name="resources")
    op.drop_constraint(op.f("fk_resources_request_id_resource_requests"), "resources", type_="foreignkey")
    op.drop_column("resources", "request_id")
    op.drop_index("ix_resource_request_events_request_id", table_name="resource_request_events")
    op.drop_table("resource_request_events")
    op.drop_index("ix_resource_requests_requester_user_id", table_name="resource_requests")
    op.drop_table("resource_requests")
