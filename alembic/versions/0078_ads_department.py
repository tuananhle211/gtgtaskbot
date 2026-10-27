"""Phòng Ads: Meta Business Managers, ad accounts, daily spend and charges.

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-27

The department's ad spend now comes straight from the Meta Marketing API
instead of a Google Sheet fed by Apps Script:

* ``ads_connections`` - one Business Manager per row, tied to a cơ sở, with its
  System User token encrypted (only ``••••abcd`` in clear) and the last
  connection test / sync results;
* ``ads_accounts`` - every ad account seen in a BM, its brand and the marketer
  who runs it (``user_id``, NULL = "Chưa gán");
* ``ads_spend_daily`` - spend, messages, phone numbers per account and day;
* ``ads_charges_daily`` - Meta's billing charges per account and day.

See ``docs/ads-report/ADS_DEPARTMENT_PLAN.md``. Empty tables change nothing.

Downgrade
---------
Drops the four tables (tokens and history are lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONNECTIONS = "ads_connections"
ACCOUNTS = "ads_accounts"
SPEND = "ads_spend_daily"
CHARGES = "ads_charges_daily"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}_users"), ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        CONNECTIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("business_id", sa.String(length=40), nullable=False),
        sa.Column("site", sa.String(length=60), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.String(length=12), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("meta_name", sa.String(length=200), nullable=True),
        sa.Column("account_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.String(length=300), nullable=True),
        sa.Column("last_spend_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_spend_sync_ok", sa.Boolean(), nullable=True),
        sa.Column("last_spend_sync_message", sa.String(length=300), nullable=True),
        sa.Column("last_charge_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_charge_sync_ok", sa.Boolean(), nullable=True),
        sa.Column("last_charge_sync_message", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name=op.f("ck_ads_connections_name_not_empty")
        ),
        sa.CheckConstraint(
            "length(trim(business_id)) > 0", name=op.f("ck_ads_connections_business_id_not_empty")
        ),
        _user_fk(CONNECTIONS, "created_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ads_connections")),
        sa.UniqueConstraint("business_id", name=op.f("uq_ads_connections_business_id")),
    )
    op.create_table(
        ACCOUNTS,
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("brand", sa.String(length=10), nullable=False),
        sa.Column("brand_custom", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            [f"{CONNECTIONS}.id"],
            name=op.f("fk_ads_accounts_connection_id_ads_connections"),
            ondelete="RESTRICT",
        ),
        _user_fk(ACCOUNTS, "user_id"),
        _user_fk(ACCOUNTS, "assigned_by_user_id"),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_ads_accounts")),
    )
    op.create_index("ix_ads_accounts_user", ACCOUNTS, ["user_id"])
    for table, value, extra in (
        (SPEND, "spend", ("messages", "phones")),
        (CHARGES, "amount", ("charges",)),
    ):
        op.create_table(
            table,
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("account_id", sa.String(length=40), nullable=False),
            sa.Column(value, sa.Numeric(16, 2), nullable=False),
            *[sa.Column(name, sa.Integer(), nullable=False) for name in extra],
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["account_id"],
                [f"{ACCOUNTS}.account_id"],
                name=op.f(f"fk_{table}_account_id_ads_accounts"),
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("day", "account_id", name=op.f(f"pk_{table}")),
        )


def downgrade() -> None:
    op.drop_table(CHARGES)
    op.drop_table(SPEND)
    op.drop_index("ix_ads_accounts_user", table_name=ACCOUNTS)
    op.drop_table(ACCOUNTS)
    op.drop_table(CONNECTIONS)
