"""Bảng kê "BCLT new": the five marks Kế toán picks per row.

Revision ID: 0109
Revises: 0108
Create Date: 2026-11-13

User 13/11 ("chỉnh lại form như BCLT new"): each BCLT row carries Tình trạng 2,
Mới, Tình trạng, Up and Phân loại, picked from the workbook's "GHI CHÚ" lists
(``domain/finance/ledger_marks.py``). They live beside the typed fees on
``finance_fees`` (one row per order x day); NULL = APM's suggestion.

Downgrade
---------

Drops the five columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0109"
down_revision: str | None = "0108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("status2", "customer", "status", "upsale", "category")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("finance_fees", sa.Column(name, sa.String(40), nullable=True))


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column("finance_fees", name)
