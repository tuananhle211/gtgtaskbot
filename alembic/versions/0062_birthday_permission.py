"""ORD: the birthday-order board is a permission like every other one.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-14

Data only. "Order sinh nhật" was the Design team's by role, plus a switch on
each member's ORD tag (0059, ``org_unit_members.birthday_orders``). It is now
the order flow's ``BIRTHDAY`` permission, granted per department and position
on the departments screen (``ALL``: every birthday order; ``OWN``: one's own):

* every Thiết Kế department (function ``THIET_KE``) grants it ``ALL`` to its
  heads and members - what the Design team had;
* a member who had the per-person switch on keeps the board: their
  departments other than Thiết Kế grant it ``OWN`` to that position - the
  closest the department model comes to a personal grant. (Production had
  nobody switched on when this was written.)

The ``birthday_orders`` column stays, unread.

Downgrade
---------

Removes the ``BIRTHDAY`` grants. The column was never touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-006200620062")


def upgrade() -> None:
    bind = op.get_bind()
    cells: dict[tuple[uuid.UUID, str], str] = {}
    for (department_id,) in bind.execute(
        sa.text("SELECT id FROM departments WHERE function = 'THIET_KE'")
    ).all():
        for position in ("HEAD", "MEMBER"):
            cells[(department_id, position)] = "ALL"
    for department_id, position in bind.execute(
        sa.text(
            "SELECT DISTINCT s.department_id, s.position "
            "FROM org_unit_members m "
            "JOIN org_units u ON u.id = m.unit_id AND u.code = 'ADS' "
            "JOIN department_members s ON s.user_id = m.user_id AND s.left_at IS NULL "
            "WHERE m.birthday_orders AND m.left_at IS NULL"
        )
    ).all():
        cells.setdefault((department_id, position), "OWN")
    for (department_id, position), scope in cells.items():
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM department_permissions WHERE department_id = :d "
                "AND position = :p AND module = 'ORDER' AND permission = 'BIRTHDAY'"
            ),
            {"d": department_id, "p": position},
        ).first()
        if exists is None:
            bind.execute(
                sa.text(
                    "INSERT INTO department_permissions "
                    "(id, department_id, position, module, permission, scope) "
                    "VALUES (:id, :d, :p, 'ORDER', 'BIRTHDAY', :s)"
                ),
                {
                    "id": uuid.uuid5(SEED_NAMESPACE, f"{department_id}:{position}"),
                    "d": department_id,
                    "p": position,
                    "s": scope,
                },
            )


def downgrade() -> None:
    op.execute("DELETE FROM department_permissions WHERE permission = 'BIRTHDAY'")
