"""Phòng Ads step 2: revenue entered by the Trưởng phòng, and monthly KPIs.

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-27

* ``ads_revenue_daily`` - one day's revenue for one marketer (``u:<id>``) or
  one source that is nobody's ad account (``s:HOTLINE``...); no row = not
  entered, which is not the same as 0. A marketer's line splits into ``amount``
  (customers served at the HCM site) and ``amount_hn`` (Sài Gòn customers
  served in Hà Nội: counts for their KPI, not for the HCM site's revenue);
* ``ads_accounts.card_last4`` - the last four digits of the card on the ad
  account, read from Meta (``funding_source_details``);
* ``ads_revenue_changes`` - append-only history of every edit;
* ``ads_kpis`` - a month's revenue target, the team's (``team``) or a
  marketer's.

See ``docs/ads-report/ADS_DEPARTMENT_PLAN.md`` (đợt 2).

Downgrade
---------
Drops the three tables (entered revenue and targets are lost) and the card column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVENUE = "ads_revenue_daily"
CHANGES = "ads_revenue_changes"
KPIS = "ads_kpis"


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}_users"), ondelete="RESTRICT"
    )


def _now(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.add_column("ads_accounts", sa.Column("card_last4", sa.String(length=8), nullable=True))
    op.create_table(
        REVENUE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subject_key", sa.String(length=60), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=12), nullable=True),
        sa.Column("amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("amount_hn", sa.Numeric(16, 2), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        _now("updated_at"),
        _user_fk(REVENUE, "user_id"),
        _user_fk(REVENUE, "updated_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ads_revenue_daily")),
        sa.UniqueConstraint("day", "subject_key", name="uq_ads_revenue_daily_day_subject_key"),
    )
    op.create_table(
        CHANGES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subject_key", sa.String(length=60), nullable=False),
        sa.Column("part", sa.String(length=4), nullable=False),
        sa.Column("old_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("new_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=True),
        _now("changed_at"),
        _user_fk(CHANGES, "changed_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ads_revenue_changes")),
    )
    op.create_index("ix_ads_revenue_changes_day", CHANGES, ["day", "subject_key"])
    op.create_table(
        KPIS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("subject_key", sa.String(length=60), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        _now("updated_at"),
        _user_fk(KPIS, "user_id"),
        _user_fk(KPIS, "updated_by_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ads_kpis")),
        sa.UniqueConstraint("month", "subject_key", name="uq_ads_kpis_month_subject_key"),
    )


def downgrade() -> None:
    op.drop_table(KPIS)
    op.drop_index("ix_ads_revenue_changes_day", table_name=CHANGES)
    op.drop_table(CHANGES)
    op.drop_table(REVENUE)
    op.drop_column("ads_accounts", "card_last4")
