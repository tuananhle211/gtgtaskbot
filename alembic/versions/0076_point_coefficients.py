"""Members' points coefficients by month (0076).

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-26

``user_point_coefficients``: "hệ số điểm" - from ``effective_month`` on, the
person's points are multiplied by ``coefficient`` (1 when no row applies).
Append-only: a new row supersedes. Set by the OWNER (permission
``PERFORMANCE COEFFICIENT``, OWNER-only). See
``docs/perf/PERFORMANCE_REPORTS_PLAN.md``.

Downgrade
---------
Drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "user_point_coefficients"
USERS = "users"
RESTRICT = "RESTRICT"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("effective_month", sa.Date(), nullable=False),
        sa.Column("coefficient", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "coefficient > 0 AND coefficient <= 10",
            name=op.f("ck_user_point_coefficients_coefficient_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_user_point_coefficients_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_user_point_coefficients_created_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_point_coefficients")),
    )
    op.create_index(
        "ix_user_point_coefficients_user_month", TABLE, ["user_id", "effective_month"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_point_coefficients_user_month", table_name=TABLE)
    op.drop_table(TABLE)
