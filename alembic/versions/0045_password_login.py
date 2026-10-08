"""Password login for the web panel.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-08

Additive. Nothing PR or Ads reads or writes changes, and the Telegram login
link keeps working exactly as before.

``users``
---------

* ``password_hash`` (nullable) - ``scrypt$16384$8$1$<salt>$<hash>``. ``NULL``
  means "still on the default password": nothing plain text is ever stored,
  and a ``CHECK`` refuses any value that does not start with ``scrypt$`` so a
  plain-text write fails at the database instead of landing on disk;
* ``password_changed_at`` (nullable) - when the person last chose one;
* ``failed_login_count`` (``NOT NULL DEFAULT 0``, never negative) and
  ``locked_until`` (nullable) - the lockout after consecutive failures.

``web_sessions``
----------------

* ``auth_method`` (``NOT NULL DEFAULT 'TELEGRAM_LINK'``) - how the session was
  created. A session opened with the password while the account is still on
  the default password must change it before doing anything else; a session
  opened from the Telegram link is never held to that. Every existing row is a
  Telegram-link row, which is what the default records.

Downgrade
---------

Drops the five columns. Every chosen password is lost (everybody is back on
"sign in through the bot") and the lockout state goes with them; sessions stay.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
WEB_SESSIONS = "web_sessions"

PASSWORD_HASH_CHECK = "password_hash_is_scrypt"
PASSWORD_HASH_RULE = "password_hash IS NULL OR password_hash LIKE 'scrypt$%'"
FAILED_COUNT_CHECK = "failed_login_count_not_negative"
FAILED_COUNT_RULE = "failed_login_count >= 0"
AUTH_METHOD_CHECK = "auth_method_known"
AUTH_METHOD_RULE = "auth_method IN ('TELEGRAM_LINK', 'PASSWORD')"


def upgrade() -> None:
    op.add_column(USERS, sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        USERS, sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        USERS,
        sa.Column(
            "failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(USERS, sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(op.f(f"ck_users_{PASSWORD_HASH_CHECK}"), USERS, PASSWORD_HASH_RULE)
    op.create_check_constraint(op.f(f"ck_users_{FAILED_COUNT_CHECK}"), USERS, FAILED_COUNT_RULE)

    op.add_column(
        WEB_SESSIONS,
        sa.Column(
            "auth_method",
            sa.String(length=20),
            server_default=sa.text("'TELEGRAM_LINK'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f(f"ck_web_sessions_{AUTH_METHOD_CHECK}"), WEB_SESSIONS, AUTH_METHOD_RULE
    )


def downgrade() -> None:
    op.drop_constraint(op.f(f"ck_web_sessions_{AUTH_METHOD_CHECK}"), WEB_SESSIONS, type_="check")
    op.drop_column(WEB_SESSIONS, "auth_method")
    op.drop_constraint(op.f(f"ck_users_{FAILED_COUNT_CHECK}"), USERS, type_="check")
    op.drop_constraint(op.f(f"ck_users_{PASSWORD_HASH_CHECK}"), USERS, type_="check")
    op.drop_column(USERS, "locked_until")
    op.drop_column(USERS, "failed_login_count")
    op.drop_column(USERS, "password_changed_at")
    op.drop_column(USERS, "password_hash")
