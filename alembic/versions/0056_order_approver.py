"""ORD: the orderer picks who approves the order.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-12

Additive. ``orders.approver_user_id`` - the Trưởng phòng ORD the orderer chose
on the form. The order waits on that person only (named, notified, "Cần
làm"); NULL = as before (the orderer's own head, else every head). A head's
own order needs no approval and carries none.

Downgrade
---------

Drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("approver_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_orders_approver_user_id_users"),
        "orders",
        "users",
        ["approver_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_orders_approver_user_id_users"), "orders", type_="foreignkey")
    op.drop_column("orders", "approver_user_id")
