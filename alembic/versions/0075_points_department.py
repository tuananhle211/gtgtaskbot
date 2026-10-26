"""Points carry the department they count for (0075).

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-24

``order_node_points.department_id``: the department a step's points count for
in the reports - the person's department whose function is the step's phase
(Biên kịch -> BIEN_TAP, Design -> THIET_KE, Dựng -> DUNG), else the first one
they sit in. Fixed when the step is done, so a person who changes department
later keeps their earlier points where they were earned. Existing rows are
filled by the same rule from today's seats. See
``docs/perf/PERFORMANCE_REPORTS_PLAN.md``.

Downgrade
---------
Drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POINTS = "order_node_points"
DEPARTMENTS = "departments"
RESTRICT = "RESTRICT"


def upgrade() -> None:
    op.add_column(POINTS, sa.Column("department_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_order_node_points_department_id_departments"),
        POINTS,
        DEPARTMENTS,
        ["department_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_index(
        "ix_order_node_points_department_completed",
        POINTS,
        ["department_id", "first_completed_at"],
    )
    # Fill what is there: the department of the step's phase, else the first
    # open seat (by the departments' order).
    op.execute(
        sa.text(
            """
            UPDATE order_node_points AS p
            SET department_id = (
                SELECT d.id
                FROM department_members AS m
                JOIN departments AS d ON d.id = m.department_id
                WHERE m.user_id = p.user_id AND m.left_at IS NULL AND d.active
                ORDER BY (d.function = p.phase) DESC, d.sort_order, d.code
                LIMIT 1
            )
            WHERE p.department_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_order_node_points_department_completed", table_name=POINTS)
    op.drop_constraint(
        op.f("fk_order_node_points_department_id_departments"), POINTS, type_="foreignkey"
    )
    op.drop_column(POINTS, "department_id")
