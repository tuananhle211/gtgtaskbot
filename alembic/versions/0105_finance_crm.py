"""Tài chính đợt 1: what one CRM run keeps for Phòng Ads and Tài chính.

Revision ID: 0105
Revises: 0104
Create Date: 2026-11-13

User 13/11: Phòng Ads' revenue and Tài chính's are read from the CRM in ONE run
(0h, 5h, 12h, 18h Vietnam). The payments stay where Phòng Ads reads them:

* ``ads_crm_payments`` + ``telesales_id`` / ``cskh_id`` / ``carepage_id`` /
  ``owner_id`` (the people on the customer's file, the order's owner),
  ``order_code``, ``customer_name`` (Tài chính only; never a phone);
* ``crm_branches`` - every CRM branch with its pháp nhân + APM cơ sở;
* ``crm_staff`` - every CRM person seen, and the APM account they are;
* ``crm_orders`` - orders by creation day (doanh số, VAT, còn nợ, lines);
* ``crm_shared_revenues`` - revenue shared to doctors / TVV / KTV / nurses;
* ``crm_tours`` - công tua;
* permission module ``FINANCE`` seeded: Phòng Kế toán (``KT``) HEAD
  REVENUE + CUSTOMERS + SETTINGS, MEMBER REVENUE + CUSTOMERS; ``VPTCKT`` HEAD
  all three; Ban giám đốc (``BOD``) HEAD REVENUE + CUSTOMERS;
* ``system_settings`` ``developing_modules`` = ``{"modules": ["FINANCE"]}``:
  "Tính năng đang phát triển" - Tài chính is hidden from all but the OWNER
  until the OWNER releases it (user 13/11).

Downgrade
---------

Drops the tables, the columns and the FINANCE grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0105"
down_revision: str | None = "0104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_MONEY = sa.Numeric(16, 2)
_ROW_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

#: (department code, position, permissions).
SEED = (
    ("KT", "HEAD", ("REVENUE", "CUSTOMERS", "SETTINGS")),
    ("KT", "MEMBER", ("REVENUE", "CUSTOMERS")),
    ("VPTCKT", "HEAD", ("REVENUE", "CUSTOMERS", "SETTINGS")),
    ("BOD", "HEAD", ("REVENUE", "CUSTOMERS")),
)


def _now_column() -> sa.Column[datetime]:
    return sa.Column(
        "synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    for name in ("telesales_id", "cskh_id", "carepage_id", "owner_id"):
        op.add_column("ads_crm_payments", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column("ads_crm_payments", sa.Column("order_code", sa.String(40), nullable=True))
    op.add_column("ads_crm_payments", sa.Column("customer_name", sa.String(200), nullable=True))

    op.create_table(
        "crm_branches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column(
            "legal_entity_id",
            sa.Uuid(),
            sa.ForeignKey("legal_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("apm_branch", sa.String(10), nullable=True),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "crm_staff",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("roles", _JSON, nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("department_name", sa.String(200), nullable=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_crm_staff_user", "crm_staff", ["user_id"])
    op.create_table(
        "crm_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("code", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_on", sa.Date(), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("customer_code", sa.String(40), nullable=True),
        sa.Column("customer_name", sa.String(200), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("all_total", _MONEY, nullable=False),
        sa.Column("amount_before_vat", _MONEY, nullable=False),
        sa.Column("vat_amount", _MONEY, nullable=False),
        sa.Column("vat_percent", sa.Numeric(6, 2), nullable=True),
        sa.Column("gross_revenue", _MONEY, nullable=False),
        sa.Column("the_rest", _MONEY, nullable=False),
        sa.Column("discount", _MONEY, nullable=False),
        sa.Column("payment_type", sa.Integer(), nullable=True),
        sa.Column("order_type", sa.Integer(), nullable=True),
        sa.Column("role_type", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("items", _JSON, nullable=False),
        _now_column(),
    )
    op.create_index("ix_crm_orders_created_on", "crm_orders", ["created_on"])
    op.create_index("ix_crm_orders_code", "crm_orders", ["code"])
    op.create_table(
        "crm_shared_revenues",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("order_code", sa.String(40), nullable=True),
        sa.Column("customer_code", sa.String(40), nullable=True),
        sa.Column("customer_name", sa.String(200), nullable=True),
        sa.Column("services", sa.Text(), nullable=True),
        sa.Column("payment_amount", _MONEY, nullable=False),
        sa.Column("payment_type", sa.Integer(), nullable=True),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("share_type", sa.Integer(), nullable=True),
        sa.Column("shared_revenue", _MONEY, nullable=False),
        sa.Column("branch_name", sa.String(200), nullable=True),
        _now_column(),
    )
    op.create_index("ix_crm_shared_revenues_day", "crm_shared_revenues", ["payment_date"])
    op.create_index("ix_crm_shared_revenues_employee", "crm_shared_revenues", ["employee_id"])
    op.create_table(
        "crm_tours",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("performed_on", sa.Date(), nullable=False),
        sa.Column("order_code", sa.String(40), nullable=True),
        sa.Column("customer_code", sa.String(40), nullable=True),
        sa.Column("customer_name", sa.String(200), nullable=True),
        sa.Column("service_name", sa.String(300), nullable=True),
        sa.Column("treatment_type", sa.Integer(), nullable=True),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("work_type", sa.String(20), nullable=True),
        sa.Column("procedure_name", sa.String(300), nullable=True),
        sa.Column("branch_name", sa.String(200), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        _now_column(),
    )
    op.create_index("ix_crm_tours_day", "crm_tours", ["performed_on"])
    op.create_index("ix_crm_tours_employee", "crm_tours", ["employee_id"])

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
    for code, position, permissions in SEED:
        found = bind.execute(
            sa.text("SELECT id FROM departments WHERE code = :code"), {"code": code}
        ).all()
        rows += [
            {
                "id": uuid.uuid4(),
                "department_id": department_id,
                "position": position,
                "module": "FINANCE",
                "permission": permission,
                "scope": "ALL",
                "created_at": now,
                "updated_at": now,
            }
            for (department_id,) in found
            for permission in permissions
        ]
    if rows:
        op.bulk_insert(grants, rows)

    exists = bind.execute(
        sa.text("SELECT 1 FROM system_settings WHERE key = 'developing_modules'")
    ).first()
    if exists is None:
        settings = sa.table(
            "system_settings",
            sa.column("id", sa.Uuid()),
            sa.column("key", sa.String()),
            sa.column("value", _JSON),
            sa.column("description", sa.Text()),
            sa.column("version", sa.Integer()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(
            settings,
            [
                {
                    "id": uuid.uuid4(),
                    "key": "developing_modules",
                    "value": {"modules": ["FINANCE"]},
                    "description": "Module đang phát triển: chỉ chủ sở hữu thấy.",
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM system_settings WHERE key = 'developing_modules'"))
    op.execute(sa.text("DELETE FROM department_permissions WHERE module = 'FINANCE'"))
    op.execute(
        sa.text(
            "UPDATE user_permissions SET revoked_at = now() "
            "WHERE module = 'FINANCE' AND revoked_at IS NULL"
        )
    )
    op.drop_index("ix_crm_tours_employee", table_name="crm_tours")
    op.drop_index("ix_crm_tours_day", table_name="crm_tours")
    op.drop_table("crm_tours")
    op.drop_index("ix_crm_shared_revenues_employee", table_name="crm_shared_revenues")
    op.drop_index("ix_crm_shared_revenues_day", table_name="crm_shared_revenues")
    op.drop_table("crm_shared_revenues")
    op.drop_index("ix_crm_orders_code", table_name="crm_orders")
    op.drop_index("ix_crm_orders_created_on", table_name="crm_orders")
    op.drop_table("crm_orders")
    op.drop_index("ix_crm_staff_user", table_name="crm_staff")
    op.drop_table("crm_staff")
    op.drop_table("crm_branches")
    for name in (
        "customer_name",
        "order_code",
        "owner_id",
        "carepage_id",
        "cskh_id",
        "telesales_id",
    ):
        op.drop_column("ads_crm_payments", name)
