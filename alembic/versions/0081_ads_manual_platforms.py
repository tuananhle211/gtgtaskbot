"""Phòng Ads: Google Ads and TikTok Ads typed by hand (chốt 28/10).

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-28

* ``ads_accounts.platform`` - FACEBOOK (read from Meta, every existing row),
  GOOGLE or TIKTOK (no API: the marketer adds the account and types its spend
  and invoices by day into ``ads_spend_daily`` / ``ads_charges_daily``);
* ``ads_accounts.connection_id`` becomes nullable - a typed-in account has no
  BM (a check keeps it required for Facebook); ``external_id`` (the id as
  typed), ``site`` (its cơ sở) and ``created_by_user_id`` for those;
* ``ads_manual_changes`` - append-only history of every typed number.

Downgrade
---------
Drops the history and the typed-in accounts with their numbers, then the
columns; ``connection_id`` is required again.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNTS = "ads_accounts"
CHANGES = "ads_manual_changes"


def upgrade() -> None:
    op.add_column(
        ACCOUNTS,
        sa.Column(
            "platform", sa.String(length=10), server_default=sa.text("'FACEBOOK'"), nullable=False
        ),
    )
    op.add_column(ACCOUNTS, sa.Column("external_id", sa.String(length=40), nullable=True))
    op.add_column(ACCOUNTS, sa.Column("site", sa.String(length=60), nullable=True))
    op.add_column(ACCOUNTS, sa.Column("created_by_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ads_accounts_created_by_user_id_users"),
        ACCOUNTS,
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(ACCOUNTS, "connection_id", existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint(
        op.f("ck_ads_accounts_facebook_has_connection"),
        ACCOUNTS,
        "platform <> 'FACEBOOK' OR connection_id IS NOT NULL",
    )
    op.create_table(
        CHANGES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("field", sa.String(length=10), nullable=False),
        sa.Column("old_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("new_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            [f"{ACCOUNTS}.account_id"],
            name=op.f("fk_ads_manual_changes_account_id_ads_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            name=op.f("fk_ads_manual_changes_changed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ads_manual_changes")),
    )
    op.create_index("ix_ads_manual_changes_account_day", CHANGES, ["account_id", "day"])


def downgrade() -> None:
    op.drop_index("ix_ads_manual_changes_account_day", table_name=CHANGES)
    op.drop_table(CHANGES)
    typed = f"SELECT account_id FROM {ACCOUNTS} WHERE platform <> 'FACEBOOK'"
    op.execute(f"DELETE FROM ads_spend_daily WHERE account_id IN ({typed})")
    op.execute(f"DELETE FROM ads_charges_daily WHERE account_id IN ({typed})")
    op.execute(f"DELETE FROM {ACCOUNTS} WHERE platform <> 'FACEBOOK'")
    op.drop_constraint(op.f("ck_ads_accounts_facebook_has_connection"), ACCOUNTS, type_="check")
    op.alter_column(ACCOUNTS, "connection_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint(
        op.f("fk_ads_accounts_created_by_user_id_users"), ACCOUNTS, type_="foreignkey"
    )
    for column in ("created_by_user_id", "site", "external_id", "platform"):
        op.drop_column(ACCOUNTS, column)
