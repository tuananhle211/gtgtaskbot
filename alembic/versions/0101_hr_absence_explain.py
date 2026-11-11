"""Nhân sự: đơn giải trình chấm công (quên chấm) với ảnh xác minh.

Revision ID: 0101
Revises: 0100
Create Date: 2026-11-11

* ``hr_absence_requests.check_in`` / ``check_out``: the real times a
  "Giải trình chấm công" declares (kind ``EXPLAIN``);
* ``hr_absence_files``: the pictures proving it (uploaded first with no
  request, attached when it is sent; bytes deferred, sha256).

Downgrade drops the table and the two columns (and every EXPLAIN request).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0101"
down_revision: str | None = "0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("hr_absence_requests") as batch:
        batch.add_column(sa.Column("check_in", sa.Time(), nullable=True))
        batch.add_column(sa.Column("check_out", sa.Time(), nullable=True))
    op.create_table(
        "hr_absence_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_hr_absence_files_size_positive")),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["hr_absence_requests.id"],
            name=op.f("fk_hr_absence_files_request_id_hr_absence_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name=op.f("fk_hr_absence_files_uploaded_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_absence_files")),
    )
    op.create_index(op.f("ix_hr_absence_files_request_id"), "hr_absence_files", ["request_id"])
    op.create_index(
        op.f("ix_hr_absence_files_uploaded_by_user_id"),
        "hr_absence_files",
        ["uploaded_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_hr_absence_files_uploaded_by_user_id"), table_name="hr_absence_files")
    op.drop_index(op.f("ix_hr_absence_files_request_id"), table_name="hr_absence_files")
    op.drop_table("hr_absence_files")
    op.execute(sa.text("DELETE FROM hr_absence_requests WHERE kind = 'EXPLAIN'"))
    with op.batch_alter_table("hr_absence_requests") as batch:
        batch.drop_column("check_out")
        batch.drop_column("check_in")
