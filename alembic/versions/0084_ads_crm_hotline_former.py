"""Phòng Ads: Hotline theo loại và nhân viên đã nghỉ trên CRM (2026-10-30).

Revision ID: 0084
Revises: 0083
Create Date: 2026-10-30

* ``ads_crm_accounts.hotline_kind`` - a ◆ Hotline account's hotline: FB,
  TIKTOK or GOOGLE;
* ``ads_crm_accounts.former`` - a marketer who left and has no account in the
  app: their revenue is a line of its own under the CRM name;
* the check ``one_owner`` now allows one of person / ◆ source / former.

Downgrade
---------
Drops the two columns (a former account then counts for nobody) and restores
the old check.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNTS = "ads_crm_accounts"
CHECK = "ck_ads_crm_accounts_one_owner"
ONE_OWNER = (
    "(CASE WHEN user_id IS NULL THEN 0 ELSE 1 END)"
    " + (CASE WHEN source IS NULL THEN 0 ELSE 1 END)"
    " + (CASE WHEN former THEN 1 ELSE 0 END) <= 1"
)


def upgrade() -> None:
    op.add_column(ACCOUNTS, sa.Column("hotline_kind", sa.String(length=10), nullable=True))
    op.add_column(
        ACCOUNTS,
        sa.Column("former", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.drop_constraint(op.f(CHECK), ACCOUNTS, type_="check")
    op.create_check_constraint(op.f(CHECK), ACCOUNTS, ONE_OWNER)


def downgrade() -> None:
    op.drop_constraint(op.f(CHECK), ACCOUNTS, type_="check")
    op.drop_column(ACCOUNTS, "former")
    op.drop_column(ACCOUNTS, "hotline_kind")
    op.create_check_constraint(op.f(CHECK), ACCOUNTS, "user_id IS NULL OR source IS NULL")
