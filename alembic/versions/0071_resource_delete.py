"""Deleting resources, and two optional request fields (0071).

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-21

* ``resources.deleted_at`` / ``deleted_by_user_id``: "Xoá tài nguyên" is
  soft - its handover, status and change histories are append-only and keep
  pointing at it. A deleted resource is left out everywhere.
* ``uq_resources_type_id_external_id`` becomes a partial unique index over
  the living rows, so a deleted resource's id can be used again.
* The resources permission ``DELETE`` ("Xoá tài nguyên"): ALL for the Phòng
  Tài nguyên's HEAD and MEMBER seats.
* ``resource_requests.budget`` / ``needed_by`` ("Dự toán tối đa", "Cần có
  trước ngày") may be left blank.

Downgrade
---------

Forgets the deletions (deleted rows come back), restores the plain unique
constraint (fails if a reused id now exists twice), drops the grants, and
fills blank budgets with 0 and blank dates with the creation day.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMESPACE = uuid.UUID("6f1c9a52-0b7e-4a59-9d55-0071c0de0071")


def upgrade() -> None:
    op.add_column("resources", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "resources",
        sa.Column(
            "deleted_by_user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id",
                ondelete="RESTRICT",
                name=op.f("fk_resources_deleted_by_user_id_users"),
            ),
            nullable=True,
        ),
    )
    op.drop_constraint(op.f("uq_resources_type_id_external_id"), "resources", type_="unique")
    op.create_index(
        "uq_resources_type_id_external_id",
        "resources",
        ["type_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    with op.batch_alter_table("resource_requests") as batch:
        batch.alter_column("budget", existing_type=sa.BigInteger(), nullable=True)
        batch.alter_column("needed_by", existing_type=sa.Date(), nullable=True)
    bind = op.get_bind()
    departments = bind.execute(
        sa.text("SELECT id FROM departments WHERE function = 'RESOURCES'")
    ).all()
    for (department_id,) in departments:
        for position in ("HEAD", "MEMBER"):
            taken = bind.execute(
                sa.text(
                    "SELECT 1 FROM department_permissions WHERE department_id = :d "
                    "AND position = :p AND module = 'RESOURCES' AND permission = 'DELETE'"
                ),
                {"d": department_id, "p": position},
            ).first()
            if taken:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO department_permissions "
                    "(id, department_id, position, module, permission, scope) "
                    "VALUES (:id, :d, :p, 'RESOURCES', 'DELETE', 'ALL')"
                ),
                {
                    "id": uuid.uuid5(_NAMESPACE, f"{department_id}:{position}"),
                    "d": department_id,
                    "p": position,
                },
            )


def downgrade() -> None:
    op.execute(
        "DELETE FROM department_permissions WHERE module = 'RESOURCES' AND permission = 'DELETE'"
    )
    op.execute("UPDATE resource_requests SET budget = 0 WHERE budget IS NULL")
    op.execute(
        "UPDATE resource_requests SET needed_by = CAST(created_at AS DATE) WHERE needed_by IS NULL"
    )
    with op.batch_alter_table("resource_requests") as batch:
        batch.alter_column("budget", existing_type=sa.BigInteger(), nullable=False)
        batch.alter_column("needed_by", existing_type=sa.Date(), nullable=False)
    op.drop_index("uq_resources_type_id_external_id", table_name="resources")
    op.create_unique_constraint(
        op.f("uq_resources_type_id_external_id"), "resources", ["type_id", "external_id"]
    )
    op.drop_constraint(
        op.f("fk_resources_deleted_by_user_id_users"), "resources", type_="foreignkey"
    )
    op.drop_column("resources", "deleted_by_user_id")
    op.drop_column("resources", "deleted_at")
