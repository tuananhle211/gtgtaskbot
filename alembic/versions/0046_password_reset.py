"""Password reset by Telegram: temporary passwords.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-08

Additive. Nothing PR or Ads reads or writes changes.

``users``
---------

* ``password_temporary`` (``NOT NULL DEFAULT false``) - the stored hash is a
  temporary password MeoBot generated and sent to the person's Telegram (the
  "Quên mật khẩu?" form or an admin reset). A password session on it must
  choose a new password, like a session on the default. Every existing row is
  a password the person chose (or the default), which is what ``false`` says;
* ``password_reset_at`` (nullable) - when the last temporary password was
  issued; a self-service reset within five minutes of it is ignored.

Downgrade
---------

Drops both columns. A temporary password then reads as an ordinary chosen
password (it still signs in, it is just no longer forced to change).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"


def upgrade() -> None:
    op.add_column(
        USERS,
        sa.Column("password_temporary", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(USERS, sa.Column("password_reset_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(USERS, "password_reset_at")
    op.drop_column(USERS, "password_temporary")
