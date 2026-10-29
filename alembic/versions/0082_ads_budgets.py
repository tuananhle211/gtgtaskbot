"""Phòng Ads: Ngân sách chạy ads theo tuần (2026-10-29).

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-29

* ``ads_budgets`` - a marketer's ad budget for one of the month's four weeks
  (days 1-7, 8-14, 15-21, the 22nd to the month's end), set by the Trưởng
  phòng ADS; the dashboards set it against the week's spend.

Downgrade
---------
Drops the table (the budgets are lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUDGETS = "ads_budgets"


def upgrade() -> None:
    op.create_table(
        BUDGETS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("week BETWEEN 1 AND 4", name=op.f("ck_ads_budgets_week_range")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ads_budgets_user_id_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_ads_budgets_updated_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ads_budgets")),
        sa.UniqueConstraint("month", "user_id", "week", name="uq_ads_budgets_month_user_id_week"),
    )


def downgrade() -> None:
    op.drop_table(BUDGETS)
