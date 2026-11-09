"""Cơ cấu tổ chức + pháp nhân: ``departments.parent_id/block``, ``legal_entities``,
``users.legal_entity_id/job_title/branch``.

Revision ID: 0095
Revises: 0094
Create Date: 2026-11-09

The HR rollout (``docs/hr/HR_MODULE_PLAN.md``) brings the company's org chart:
Ban giám đốc -> Khối -> Phòng -> Bộ phận. A department may sit under another
(``parent_id``: "Phòng Media" holds Thiết kế / Biên tập / Quay dựng) and names
its division (``block``). Every account may name its company (``legal_entity_id``
-> ``legal_entities``, seeded with the four companies and the two household
businesses of the "Thông tin cơ bản" sheet), its job title and its branch.

Schema only, plus the legal-entity list: renaming departments and creating the
staff's accounts is ``scripts/hr_org_import.py``, run by hand after review.

Downgrade
---------

Drops the columns and the table (what was typed into them is lost).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITIES = "legal_entities"

#: (code, name, tax code) - the companies of the "Thông tin cơ bản" sheet. The
#: household businesses' numbers are personal ids, not kept.
SEED: tuple[tuple[str, str, str | None], ...] = (
    ("APEXMED", "Công ty TNHH Apexmed", "0111179828"),
    ("GPI", "Công ty TNHH GPI Tech", "0111406598"),
    ("CMG", "Công ty TNHH Coremind Group", "0319568598"),
    ("APM", "Công ty TNHH APM Group", "0319495942"),
    ("HKD-AP", "Hộ kinh doanh AP", None),
    ("HKD-APN", "Hộ kinh doanh APN", None),
)


def upgrade() -> None:
    op.create_table(
        ENTITIES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_entities")),
        sa.UniqueConstraint("code", name=op.f("uq_legal_entities_code")),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_legal_entities_code_not_empty")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_legal_entities_name_not_empty")),
    )
    table = sa.table(
        ENTITIES,
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("tax_code", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "tax_code": tax_code,
                "active": True,
                "sort_order": index,
                "created_at": now,
                "updated_at": now,
            }
            for index, (code, name, tax_code) in enumerate(SEED)
        ],
    )

    with op.batch_alter_table("departments") as batch:
        batch.add_column(sa.Column("parent_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("block", sa.String(60), nullable=True))
        batch.create_foreign_key(
            op.f("fk_departments_parent_id_departments"),
            "departments",
            ["parent_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(op.f("ix_departments_parent_id"), ["parent_id"])

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("legal_entity_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("job_title", sa.String(150), nullable=True))
        batch.add_column(sa.Column("branch", sa.String(20), nullable=True))
        batch.create_foreign_key(
            op.f("fk_users_legal_entity_id_legal_entities"),
            ENTITIES,
            ["legal_entity_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(op.f("ix_users_legal_entity_id"), ["legal_entity_id"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_index(op.f("ix_users_legal_entity_id"))
        batch.drop_constraint(op.f("fk_users_legal_entity_id_legal_entities"), type_="foreignkey")
        batch.drop_column("branch")
        batch.drop_column("job_title")
        batch.drop_column("legal_entity_id")
    with op.batch_alter_table("departments") as batch:
        batch.drop_index(op.f("ix_departments_parent_id"))
        batch.drop_constraint(op.f("fk_departments_parent_id_departments"), type_="foreignkey")
        batch.drop_column("block")
        batch.drop_column("parent_id")
    op.drop_table(ENTITIES)
