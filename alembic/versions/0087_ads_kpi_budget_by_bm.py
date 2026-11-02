"""Phòng Ads: KPI doanh thu và ngân sách tuần theo BM (2026-11-02).

Revision ID: 0087
Revises: 0086
Create Date: 2026-11-02

* ``ads_kpis.region`` and ``ads_budgets.region`` - the BM a KPI / a weekly
  budget is for, by the BM's cơ sở: ``HN`` = BM Hà Nội, ``HCM`` = BM Sài Gòn;
* every row already there becomes BM Hà Nội's (the user's call: "đẩy hết
  data kpi hiện tại cho BM Hà Nội, BM SG nhập sau");
* the unique keys now include the region.

Downgrade
---------
Keeps BM Hà Nội's rows (BM Sài Gòn's are deleted - the old tables hold one
figure per cell), drops the columns and restores the old unique keys.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KPIS = "ads_kpis"
BUDGETS = "ads_budgets"
KPI_KEY = "uq_ads_kpis_month_subject_key"
BUDGET_KEY = "uq_ads_budgets_month_user_id_week"
NEW_KPI_KEY = "uq_ads_kpis_month_subject_key_region"
NEW_BUDGET_KEY = "uq_ads_budgets_month_user_id_week_region"


def _region() -> sa.Column[str]:
    return sa.Column("region", sa.String(length=4), server_default="HN", nullable=False)


def upgrade() -> None:
    op.add_column(KPIS, _region())
    op.add_column(BUDGETS, _region())
    # Every writer names the BM from now on.
    op.alter_column(KPIS, "region", server_default=None)
    op.alter_column(BUDGETS, "region", server_default=None)
    op.drop_constraint(KPI_KEY, KPIS, type_="unique")
    op.create_unique_constraint(NEW_KPI_KEY, KPIS, ["month", "subject_key", "region"])
    op.drop_constraint(BUDGET_KEY, BUDGETS, type_="unique")
    op.create_unique_constraint(NEW_BUDGET_KEY, BUDGETS, ["month", "user_id", "week", "region"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ads_kpis WHERE region <> 'HN'"))
    op.execute(sa.text("DELETE FROM ads_budgets WHERE region <> 'HN'"))
    op.drop_constraint(NEW_BUDGET_KEY, BUDGETS, type_="unique")
    op.create_unique_constraint(BUDGET_KEY, BUDGETS, ["month", "user_id", "week"])
    op.drop_constraint(NEW_KPI_KEY, KPIS, type_="unique")
    op.create_unique_constraint(KPI_KEY, KPIS, ["month", "subject_key"])
    op.drop_column(BUDGETS, "region")
    op.drop_column(KPIS, "region")
