"""ORD: who may use the birthday-order board, per member.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-13

Additive. ``org_unit_members.birthday_orders`` - this member may open the
"Order sinh nhật" board and place birthday orders. The Design team has that
without the flag (the board is theirs), as do the OWNER and an ADMIN; anybody
else in ORD needs it switched on, on their tag. Nobody has it to begin with.

Downgrade
---------

Drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "org_unit_members",
        sa.Column("birthday_orders", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("org_unit_members", "birthday_orders")
