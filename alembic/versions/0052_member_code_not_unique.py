"""ORD member codes: no longer unique.

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-09

Rules for member codes come later; for now nothing about them stops a
member being tagged. Two open tags may share a code - their orders share one
counter per day (``order_code_counters``), so order codes stay unique.

Downgrade
---------

Recreates the partial unique index. It fails if duplicates exist by then.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_org_unit_members_unit_member_code"


def upgrade() -> None:
    op.drop_index(INDEX, table_name="org_unit_members")


def downgrade() -> None:
    op.create_index(
        INDEX,
        "org_unit_members",
        ["unit_id", "member_code"],
        unique=True,
        postgresql_where=sa.text("member_code IS NOT NULL AND left_at IS NULL"),
        sqlite_where=sa.text("member_code IS NOT NULL AND left_at IS NULL"),
    )
