"""Tài chính đợt 3: mã chi + tuyến duyệt, vai trò, hồ sơ chi, ngân sách, MISA.

Revision ID: 0107
Revises: 0106
Create Date: 2026-11-13

* ``finance_codes`` - the Sổ tay's budget codes with their route (seeded from
  ``domain/finance/codebook.py`` = tabs "Mã ngân sách" + "Phân quyền"),
  ``finance_code_changes`` their history;
* ``finance_role_assignments`` - who holds which finance role (pháp nhân /
  cơ sở, stand-in);
* ``finance_requests`` + ``finance_request_steps`` + ``finance_request_events``
  + ``finance_documents`` - hồ sơ chi;
* ``finance_budgets`` - pháp nhân x phòng ban x mã chi x tháng;
* ``finance_misa_balances`` - the MISA F01-DNN lines per pháp nhân / month;
* FINANCE grants: KT HEAD VIEW_REQUESTS + CODES + BUDGET + MISA + REPORT,
  KT MEMBER VIEW_REQUESTS + MISA, VPTCKT HEAD all five, BOD HEAD
  VIEW_REQUESTS + REPORT.

Downgrade
---------

Drops the tables and those grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from meobot.domain.finance.spend import parse_codebook

revision: str = "0107"
down_revision: str | None = "0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_MONEY = sa.Numeric(18, 2)
_ROW_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
NEW = ("VIEW_REQUESTS", "CODES", "BUDGET", "MISA", "REPORT")
SEED = (
    ("KT", "HEAD", NEW),
    ("KT", "MEMBER", ("VIEW_REQUESTS", "MISA")),
    ("VPTCKT", "HEAD", NEW),
    ("BOD", "HEAD", ("VIEW_REQUESTS", "REPORT")),
)


def _user(name: str, *, ondelete: str = "SET NULL", nullable: bool = True) -> sa.Column[object]:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey("users.id", ondelete=ondelete), nullable=nullable
    )


def _now(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "finance_codes",
        sa.Column("code", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("parent_code", sa.String(30), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("leaf", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("route", _JSON, nullable=False),
        _user("updated_by_user_id"),
        _now("updated_at"),
    )
    op.create_table(
        "finance_code_changes",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("before", _JSON, nullable=False),
        sa.Column("after", _JSON, nullable=False),
        _user("changed_by_user_id"),
        _now("changed_at"),
    )
    op.create_index("ix_finance_code_changes_code", "finance_code_changes", ["code"])
    op.create_table(
        "finance_role_assignments",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column("role", sa.String(30), nullable=False),
        _user("user_id", ondelete="CASCADE", nullable=False),
        sa.Column(
            "legal_entity_id",
            sa.Uuid(),
            sa.ForeignKey("legal_entities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("apm_branch", sa.String(10), nullable=True),
        _user("standin_user_id"),
        sa.Column("note", sa.String(300), nullable=True),
        _user("created_by_user_id"),
        _now("created_at"),
    )
    op.create_index("ix_finance_role_assignments_role", "finance_role_assignments", ["role"])
    op.create_table(
        "finance_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        _user("requester_id", ondelete="RESTRICT", nullable=False),
        sa.Column(
            "department_id",
            sa.Uuid(),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "legal_entity_id",
            sa.Uuid(),
            sa.ForeignKey("legal_entities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("apm_branch", sa.String(10), nullable=True),
        sa.Column("budget_code", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("vendor", sa.String(300), nullable=True),
        sa.Column("payee", sa.String(500), nullable=True),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("budget_month", sa.Date(), nullable=False),
        sa.Column("needed_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("phase", sa.String(20), nullable=True),
        sa.Column("route", _JSON, nullable=False),
        sa.Column("purchase_note", sa.Text(), nullable=True),
        _now("created_at"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("docs_due_on", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_finance_requests_requester", "finance_requests", ["requester_id"])
    op.create_index("ix_finance_requests_status", "finance_requests", ["status"])
    op.create_table(
        "finance_request_steps",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("finance_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        _user("approver_id"),
        _user("standin_id"),
        sa.Column("status", sa.String(20), nullable=False),
        _user("decided_by_user_id"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("remark", sa.String(300), nullable=True),
    )
    op.create_index("ix_finance_request_steps_request", "finance_request_steps", ["request_id"])
    op.create_table(
        "finance_request_events",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("finance_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user("actor_id"),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        _now("at"),
    )
    op.create_index("ix_finance_request_events_request", "finance_request_events", ["request_id"])
    op.create_table(
        "finance_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("finance_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("storage", sa.String(10), nullable=False),
        sa.Column("drive_file_id", sa.String(200), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        _user("uploaded_by_user_id"),
        _now("created_at"),
    )
    op.create_index("ix_finance_documents_request", "finance_documents", ["request_id"])
    op.create_table(
        "finance_budgets",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column(
            "legal_entity_id",
            sa.Uuid(),
            sa.ForeignKey("legal_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "department_id",
            sa.Uuid(),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        _user("updated_by_user_id"),
        _now("updated_at"),
        sa.UniqueConstraint(
            "month", "legal_entity_id", "department_id", "code", name="uq_finance_budgets_cell"
        ),
    )
    op.create_table(
        "finance_misa_balances",
        sa.Column("id", _ROW_ID, primary_key=True, autoincrement=True),
        sa.Column(
            "legal_entity_id",
            sa.Uuid(),
            sa.ForeignKey("legal_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("account", sa.String(20), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        *(
            sa.Column(name, _MONEY, nullable=False)
            for name in (
                "opening_debit",
                "opening_credit",
                "debit",
                "credit",
                "closing_debit",
                "closing_credit",
            )
        ),
        _user("imported_by_user_id"),
        _now("imported_at"),
        sa.UniqueConstraint(
            "legal_entity_id", "month", "account", name="uq_finance_misa_balances_line"
        ),
    )

    now = datetime.now(UTC)
    codes = sa.table(
        "finance_codes",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("parent_code", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("leaf", sa.Boolean()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("route", _JSON),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        codes,
        [
            {
                "code": seed.code,
                "name": seed.name,
                "parent_code": seed.parent,
                "kind": seed.kind,
                "leaf": seed.leaf,
                "active": True,
                "sort_order": index,
                "route": seed.route.as_value(),
                "updated_at": now,
            }
            for index, seed in enumerate(parse_codebook())
        ],
    )
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
        for (department_id,) in bind.execute(
            sa.text("SELECT id FROM departments WHERE code = :code"), {"code": code}
        ).all():
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
                for permission in permissions
            ]
    if rows:
        op.bulk_insert(grants, rows)


def downgrade() -> None:
    for permission in NEW:
        op.execute(
            sa.text(
                "DELETE FROM department_permissions "
                "WHERE module = 'FINANCE' AND permission = :permission"
            ).bindparams(permission=permission)
        )
        op.execute(
            sa.text(
                "UPDATE user_permissions SET revoked_at = now() WHERE module = 'FINANCE' "
                "AND permission = :permission AND revoked_at IS NULL"
            ).bindparams(permission=permission)
        )
    op.drop_table("finance_misa_balances")
    op.drop_table("finance_budgets")
    op.drop_index("ix_finance_documents_request", table_name="finance_documents")
    op.drop_table("finance_documents")
    op.drop_index("ix_finance_request_events_request", table_name="finance_request_events")
    op.drop_table("finance_request_events")
    op.drop_index("ix_finance_request_steps_request", table_name="finance_request_steps")
    op.drop_table("finance_request_steps")
    op.drop_index("ix_finance_requests_status", table_name="finance_requests")
    op.drop_index("ix_finance_requests_requester", table_name="finance_requests")
    op.drop_table("finance_requests")
    op.drop_index("ix_finance_role_assignments_role", table_name="finance_role_assignments")
    op.drop_table("finance_role_assignments")
    op.drop_index("ix_finance_code_changes_code", table_name="finance_code_changes")
    op.drop_table("finance_code_changes")
    op.drop_table("finance_codes")
