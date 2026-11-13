"""Tài chính đợt 2: the four fees, service cost prices, revenue KPI, FEES.

Revision ID: 0106
Revises: 0105
Create Date: 2026-11-13

* ``finance_fees`` - per BCLT row (CRM order x day): card fee, referral
  commission, car cost, hospital fee / cost price typed by Kế toán (``NULL`` =
  the automatic value);
* ``finance_service_costs`` - a CRM service's cost per unit;
* ``finance_kpis`` - a month's thực thu KPI for the company / a cơ sở / a
  Marketing team (unique per cell);
* permission ``FINANCE FEES`` seeded: Phòng Kế toán (``KT``) HEAD + MEMBER,
  ``VPTCKT`` HEAD.

Downgrade
---------

Drops the three tables and the FEES grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0106"
down_revision: str | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(16, 2)
_ROW_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
SEED = (("KT", "HEAD"), ("KT", "MEMBER"), ("VPTCKT", "HEAD"))


def _who() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "finance_fees",
        sa.Column("order_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("card_fee", _MONEY, nullable=True),
        sa.Column("referral_fee", _MONEY, nullable=True),
        sa.Column("car_fee", _MONEY, nullable=True),
        sa.Column("cost", _MONEY, nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        *_who(),
    )
    op.create_table(
        "finance_service_costs",
        sa.Column("service_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("cost", _MONEY, nullable=False),
        *_who(),
    )
    op.create_table(
        "finance_kpis",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("dimension", sa.String(10), nullable=False),
        sa.Column("key", sa.String(20), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        *_who(),
        sa.UniqueConstraint("month", "dimension", "key", name="uq_finance_kpis_cell"),
    )
    now = datetime.now(UTC)
    grants = sa.table(
        "department_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("module", sa.String()),
        sa.column("permission", sa.String()),
        sa.column("scope", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    rows = []
    for code, position in SEED:
        for (department_id,) in bind.execute(
            sa.text("SELECT id FROM departments WHERE code = :code"), {"code": code}
        ).all():
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "department_id": department_id,
                    "position": position,
                    "module": "FINANCE",
                    "permission": "FEES",
                    "scope": "ALL",
                    "created_at": now,
                    "updated_at": now,
                }
            )
    if rows:
        op.bulk_insert(grants, rows)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM department_permissions WHERE module = 'FINANCE' AND permission = 'FEES'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE user_permissions SET revoked_at = now() "
            "WHERE module = 'FINANCE' AND permission = 'FEES' AND revoked_at IS NULL"
        )
    )
    op.drop_table("finance_kpis")
    op.drop_table("finance_service_costs")
    op.drop_table("finance_fees")
