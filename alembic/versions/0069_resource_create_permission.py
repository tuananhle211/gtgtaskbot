"""The resources module's "Thêm tài nguyên" permission (0069).

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-21

Adding a resource used to ride on ``EDIT``. Since 2026-10-21 everybody may
edit and update the status of what they hold (a department head: of what
their department's people hold), so ``EDIT`` now means "every resource" and
adding one is its own permission, ``CREATE``: granted ALL to the Phòng Tài
nguyên's HEAD and MEMBER seats, nobody else (the OWNER / ADMIN need none).
Data only.

Downgrade
---------

Deletes the ``CREATE`` grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMESPACE = uuid.UUID("6f1c9a52-0b7e-4a59-9d55-0069c0de0069")


def upgrade() -> None:
    bind = op.get_bind()
    departments = bind.execute(
        sa.text("SELECT id FROM departments WHERE function = 'RESOURCES'")
    ).all()
    for (department_id,) in departments:
        for position in ("HEAD", "MEMBER"):
            taken = bind.execute(
                sa.text(
                    "SELECT 1 FROM department_permissions WHERE department_id = :d "
                    "AND position = :p AND module = 'RESOURCES' AND permission = 'CREATE'"
                ),
                {"d": department_id, "p": position},
            ).first()
            if taken:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO department_permissions "
                    "(id, department_id, position, module, permission, scope) "
                    "VALUES (:id, :d, :p, 'RESOURCES', 'CREATE', 'ALL')"
                ),
                {
                    "id": uuid.uuid5(_NAMESPACE, f"{department_id}:{position}"),
                    "d": department_id,
                    "p": position,
                },
            )


def downgrade() -> None:
    op.execute(
        "DELETE FROM department_permissions WHERE module = 'RESOURCES' AND permission = 'CREATE'"
    )
