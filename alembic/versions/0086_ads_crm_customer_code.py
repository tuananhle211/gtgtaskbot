"""Phòng Ads: mã khách trên khoản thu CRM để đối soát (2026-10-31).

Revision ID: 0086
Revises: 0085
Create Date: 2026-10-31

* ``ads_crm_payments.customer_code`` - the CRM's customer code (``KH…``) of
  each payment, so "Báo cáo ngày" can list who paid what on a day. Only the
  code: the customer's name and phone are still never stored.

Rows synced before stay without a code until the next sync of their days
(this month at the next hourly run; older months by "Kéo lại khoảng ngày").

Downgrade
---------
Drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAYMENTS = "ads_crm_payments"


def upgrade() -> None:
    op.add_column(PAYMENTS, sa.Column("customer_code", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column(PAYMENTS, "customer_code")
