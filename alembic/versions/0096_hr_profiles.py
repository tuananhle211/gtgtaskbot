"""Hồ sơ nhân sự: ``hr_profiles``, ``hr_contract_types``, ``hr_contracts``,
``hr_documents``, ``hr_change_requests`` + the ``HR`` grants.

Revision ID: 0096
Revises: 0095
Create Date: 2026-11-10

HR đợt 2 (``docs/hr/HR_MODULE_PLAN.md`` §5): one profile per account (the ID
number, bank account and pay sealed in ``secret``), contracts against a list of
contract types (seeded with the six of the mock), private documents, and the
staff's own change requests that HCNS approves.

Grants (module ``HR``): the ``HCNS`` department, both positions, every
permission ``ALL``; every department's head ``VIEW OWN`` (the people of the
departments they head). Employment statuses gain ``MATERNITY`` and
``UNPAID_LEAVE`` (a plain string column: no schema change).

Downgrade
---------

Drops the five tables (profiles, contracts, files and requests are lost) and
the ``HR`` grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0096"
down_revision: str | None = "0095"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES_SEED: tuple[tuple[str, str, int], ...] = (
    ("TV", "HĐ thử việc", 2),
    ("XD12", "HĐ xác định thời hạn 12 tháng", 12),
    ("XD36", "HĐ xác định thời hạn 36 tháng", 36),
    ("KXD", "HĐ không xác định thời hạn", 0),
    ("KV", "HĐ khoán việc", 6),
    ("CTV", "HĐ cộng tác viên", 12),
)
HR_ALL = ("VIEW", "EDIT", "SENSITIVE", "EXPORT")


def _times() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _user_fk(table: str, column: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}_users"), ondelete=ondelete
    )


def upgrade() -> None:
    op.create_table(
        "hr_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("gender", sa.String(10), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("birth_place", sa.String(200), nullable=True),
        sa.Column("nationality", sa.String(60), nullable=True),
        sa.Column("ethnicity", sa.String(60), nullable=True),
        sa.Column("religion", sa.String(60), nullable=True),
        sa.Column("marital_status", sa.String(30), nullable=True),
        sa.Column("id_last4", sa.String(4), nullable=True),
        sa.Column("id_issued_on", sa.Date(), nullable=True),
        sa.Column("id_issued_by", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("work_email", sa.String(200), nullable=True),
        sa.Column("personal_email", sa.String(200), nullable=True),
        sa.Column("drive_email", sa.String(200), nullable=True),
        sa.Column("permanent_address", sa.Text(), nullable=True),
        sa.Column("current_address", sa.Text(), nullable=True),
        sa.Column("bank_name", sa.String(100), nullable=True),
        sa.Column("bank_last4", sa.String(4), nullable=True),
        sa.Column("bank_holder", sa.String(200), nullable=True),
        sa.Column("grade", sa.String(20), nullable=True),
        sa.Column("work_form", sa.String(40), nullable=True),
        sa.Column("joined_on", sa.Date(), nullable=True),
        sa.Column("official_on", sa.Date(), nullable=True),
        sa.Column("left_on", sa.Date(), nullable=True),
        sa.Column("leave_reason", sa.Text(), nullable=True),
        sa.Column("social_insurance_no", sa.String(30), nullable=True),
        sa.Column("social_insurance_from", sa.Date(), nullable=True),
        sa.Column("health_insurance_no", sa.String(30), nullable=True),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("dependants", sa.Integer(), nullable=True),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _user_fk("hr_profiles", "user_id", "RESTRICT"),
        _user_fk("hr_profiles", "updated_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_hr_profiles")),
    )

    op.create_table(
        "hr_contract_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_times(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_contract_types")),
        sa.UniqueConstraint("code", name=op.f("uq_hr_contract_types_code")),
        sa.CheckConstraint("months >= 0", name=op.f("ck_hr_contract_types_months_not_negative")),
    )
    now = datetime.now(UTC)
    types = sa.table(
        "hr_contract_types",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("months", sa.Integer()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        types,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "months": months,
                "active": True,
                "sort_order": index,
                "created_at": now,
                "updated_at": now,
            }
            for index, (code, name, months) in enumerate(TYPES_SEED)
        ],
    )

    op.create_table(
        "hr_contracts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(80), nullable=True),
        sa.Column("type_code", sa.String(20), nullable=False),
        sa.Column("start_on", sa.Date(), nullable=True),
        sa.Column("end_on", sa.Date(), nullable=True),
        sa.Column("signed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("signed_by", sa.String(200), nullable=True),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("salary_secret", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _user_fk("hr_contracts", "user_id", "RESTRICT"),
        _user_fk("hr_contracts", "created_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_contracts")),
    )
    op.create_index(op.f("ix_hr_contracts_user_id"), "hr_contracts", ["user_id"])

    op.create_table(
        "hr_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _user_fk("hr_documents", "user_id", "RESTRICT"),
        _user_fk("hr_documents", "uploaded_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_documents")),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_hr_documents_size_positive")),
    )
    op.create_index(op.f("ix_hr_documents_user_id"), "hr_documents", ["user_id"])

    op.create_table(
        "hr_change_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_times(),
        _user_fk("hr_change_requests", "user_id", "RESTRICT"),
        _user_fk("hr_change_requests", "decided_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_change_requests")),
    )
    op.create_index(op.f("ix_hr_change_requests_user_id"), "hr_change_requests", ["user_id"])
    op.create_index(op.f("ix_hr_change_requests_status"), "hr_change_requests", ["status"])

    # --- grants ----------------------------------------------------------------------
    bind = op.get_bind()
    departments = bind.execute(sa.text("SELECT id, code FROM departments")).all()
    cells: list[dict[str, object]] = []
    for department_id, code in departments:
        if code == "HCNS":
            for position in ("HEAD", "MEMBER"):
                cells += [
                    {"position": position, "permission": permission, "scope": "ALL"}
                    | {"department_id": department_id}
                    for permission in HR_ALL
                ]
        else:
            cells.append(
                {
                    "department_id": department_id,
                    "position": "HEAD",
                    "permission": "VIEW",
                    "scope": "OWN",
                }
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
    if cells:
        op.bulk_insert(
            grants,
            [
                {**cell, "id": uuid.uuid4(), "module": "HR", "created_at": now, "updated_at": now}
                for cell in cells
            ],
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM department_permissions WHERE module = 'HR'"))
    op.drop_index(op.f("ix_hr_change_requests_status"), table_name="hr_change_requests")
    op.drop_index(op.f("ix_hr_change_requests_user_id"), table_name="hr_change_requests")
    op.drop_table("hr_change_requests")
    op.drop_index(op.f("ix_hr_documents_user_id"), table_name="hr_documents")
    op.drop_table("hr_documents")
    op.drop_index(op.f("ix_hr_contracts_user_id"), table_name="hr_contracts")
    op.drop_table("hr_contracts")
    op.drop_table("hr_contract_types")
    op.drop_table("hr_profiles")
