"""Phòng Ads: doanh thu từ CRM aicrmtech.vn (2026-10-29).

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-29

* ``ads_crm_accounts`` - the CRM's marketing accounts: the app person who
  holds each (several per person) or the ◆ source it fills, and its region
  (HCM -> "Doanh số SG", HN -> "Doanh số HN");
* ``ads_crm_payments`` - every payment read from the CRM (thực thu on the day
  it was paid): id, day, amount, type, marketing account, branch, order,
  new/old customer, customer source - no customer name or phone.

See ``docs/ads-report/CRM_REVENUE_PLAN.md``.

Downgrade
---------
Drops both tables (the mapping and the pulled payments are lost; the
dashboards fall back to the revenue typed by hand).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNTS = "ads_crm_accounts"
PAYMENTS = "ads_crm_payments"


def _user_fk(column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{ACCOUNTS}_{column}_users"), ondelete="RESTRICT"
    )


def _now(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        ACCOUNTS,
        sa.Column("crm_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("in_directory", sa.Boolean(), nullable=False),
        sa.Column("region", sa.String(length=4), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=12), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        _now("synced_at"),
        sa.CheckConstraint(
            "user_id IS NULL OR source IS NULL", name=op.f("ck_ads_crm_accounts_one_owner")
        ),
        _user_fk("user_id"),
        _user_fk("updated_by_user_id"),
        sa.PrimaryKeyConstraint("crm_id", name=op.f("pk_ads_crm_accounts")),
    )
    op.create_table(
        PAYMENTS,
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(16, 2), nullable=False),
        sa.Column("payment_type", sa.Integer(), nullable=False),
        sa.Column("marketing_id", sa.Integer(), nullable=True),
        sa.Column("branch_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("is_upsale", sa.Boolean(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        _now("synced_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ads_crm_payments")),
    )
    op.create_index("ix_ads_crm_payments_day_marketing", PAYMENTS, ["payment_date", "marketing_id"])


def downgrade() -> None:
    op.drop_index("ix_ads_crm_payments_day_marketing", table_name=PAYMENTS)
    op.drop_table(PAYMENTS)
    op.drop_table(ACCOUNTS)
