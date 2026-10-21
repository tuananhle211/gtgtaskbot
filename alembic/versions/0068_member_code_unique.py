"""One member code per person (0068).

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-16

The member code ("mã nhân viên") is the one key that ties people to the
resources inventory and to their order codes, so no two living accounts may
share one. Any duplicate found is cleared from every account but the oldest
(the OWNER / ADMIN gives the others a code of their own on the members
screen; the number cleared is logged by the deploy's migration output as a
notice), then a partial unique index is added.

Downgrade
---------

Drops the index. Cleared codes are not restored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_users_member_code"


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT member_code FROM users WHERE member_code IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY member_code HAVING count(*) > 1"
        )
    ).all()
    for (code,) in duplicates:
        rows = bind.execute(
            sa.text(
                "SELECT id FROM users WHERE member_code = :code AND deleted_at IS NULL "
                "ORDER BY created_at, id"
            ),
            {"code": code},
        ).all()
        for (user_id,) in rows[1:]:
            bind.execute(
                sa.text("UPDATE users SET member_code = NULL WHERE id = :id"), {"id": user_id}
            )
            bind.execute(
                sa.text(
                    "UPDATE org_unit_members SET member_code = NULL "
                    "WHERE user_id = :id AND left_at IS NULL"
                ),
                {"id": user_id},
            )
    op.create_index(
        INDEX,
        "users",
        ["member_code"],
        unique=True,
        postgresql_where=sa.text("member_code IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="users")
