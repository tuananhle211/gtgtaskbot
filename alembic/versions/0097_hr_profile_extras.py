"""Hồ sơ nhân sự: the HCNS master file's extra columns.

Revision ID: 0097
Revises: 0096
Create Date: 2026-11-10

The HR department's own workbook ("DANH SÁCH NHÂN SỰ", tab DS NHÂN SỰ) keeps
more than the "Sổ tay" sheet: the person's team, a second bank account (the
number sealed with the first, only its last four digits in the clear), the
job title on the social-insurance file, the time-clock ("vân tay") id with the
day and floor it was taken, the link to the scanned file, and which paper
documents were handed in (``paper_documents``, a list of document codes).

Downgrade
---------

Drops the columns (what was loaded into them is lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object]], ...] = (
    ("team", sa.String(120)),
    ("bank2_name", sa.String(100)),
    ("bank2_last4", sa.String(4)),
    ("bank2_holder", sa.String(200)),
    ("insurance_title", sa.String(200)),
    ("fingerprint_id", sa.String(30)),
    ("fingerprint_on", sa.Date()),
    ("fingerprint_floor", sa.String(30)),
    ("scan_link", sa.Text()),
)


def upgrade() -> None:
    with op.batch_alter_table("hr_profiles") as batch:
        for name, kind in COLUMNS:
            batch.add_column(sa.Column(name, kind, nullable=True))
        batch.add_column(
            sa.Column(
                "paper_documents",
                _JSON,
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("hr_profiles") as batch:
        batch.drop_column("paper_documents")
        for name, _kind in reversed(COLUMNS):
            batch.drop_column(name)
