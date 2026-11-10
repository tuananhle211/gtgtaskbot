"""Phân quyền cá nhân: a personal row overrides the seats.

Revision ID: 0098
Revises: 0097
Create Date: 2026-11-10

The OWNER and an ADMIN may now tick or untick every box on a person's
"Quyền" page, the defaults their department seats give included (user,
2026-11-10). A ``user_permissions`` row therefore sets the person's scope of
that permission whatever the seats say: ``NONE`` (new) takes it away, ``OWN``
caps it, ``ALL`` gives all. The check on ``scope`` admits ``NONE``.

No row changes: the 7 personal rows on production all widen or repeat what a
seat gives (checked 10/11), so reading them as overrides changes nothing.

Downgrade
---------

Revokes the ``NONE`` rows (``revoked_at`` = now: the seat defaults come back)
and restores the old check.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0098"
down_revision: str | None = "0097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_permissions") as batch:
        batch.drop_constraint(op.f("ck_user_permissions_scope_known"), type_="check")
        batch.create_check_constraint(
            op.f("ck_user_permissions_scope_known"), sa.text("scope IN ('NONE', 'OWN', 'ALL')")
        )


def downgrade() -> None:
    op.execute(
        "UPDATE user_permissions SET revoked_at = now() "
        "WHERE scope = 'NONE' AND revoked_at IS NULL"
    )
    op.execute("UPDATE user_permissions SET scope = 'OWN' WHERE scope = 'NONE'")
    with op.batch_alter_table("user_permissions") as batch:
        batch.drop_constraint(op.f("ck_user_permissions_scope_known"), type_="check")
        batch.create_check_constraint(
            op.f("ck_user_permissions_scope_known"), sa.text("scope IN ('OWN', 'ALL')")
        )
