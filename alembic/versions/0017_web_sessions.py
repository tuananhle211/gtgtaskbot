"""Step 1E: browser sessions for the PR web admin.

One new table, ``web_sessions``. No existing table gains a column, loses a
column, changes a type or changes a constraint; revisions 0001-0016 are left
exactly as they are. ``users`` is referenced and never altered.

Why a migration was needed
--------------------------

Step 1E was specified to require none, and it would not have if any web
authentication had existed. None did: ``get_current_system_actor`` returned a
synthetic ``OWNER`` for every request, there was no session store, no token
table, no signing secret and no auth middleware, and ``users`` carries no
email, password hash or external identity - only Telegram columns.

Every design that resolves a browser request to a real ``users.id`` therefore
needs somewhere to keep state:

* a session cookie needs a session store - this table;
* proxy-header identity (Cloudflare Access) would need ``users.email``;
* a shared service token needs no schema but cannot say *which person* is
  approving, and reviewer separation, grant attribution and approval history
  all depend on knowing that.

The magic-link design was chosen and this migration reported rather than
created silently. See ``docs/pr/STEP_1E_WEB_ADMIN.md``.

What the table holds
--------------------

Login tokens and sessions, separated by ``kind``. They are the same shape - a
hashed secret with a subject and an expiry - and a login token *becomes* a
session, which ``redeemed_at`` records. **Only hashes are stored**: a dump of
this table contains nothing replayable, which is why it hashes rather than
encrypts - there is no key to lose.

Delete behaviour
----------------

``user_id`` is ``ON DELETE RESTRICT``, as every foreign key in this schema is.
Deleting somebody who has signed in fails rather than quietly discarding the
record of when they did.

Downgrade
---------

Drops ``web_sessions`` and its indexes, and nothing else. **Everybody is signed
out** - every cookie stops resolving - and the web admin becomes unreachable
until the table exists again. No data outside this table is read or written, and
the Telegram bot is unaffected: it never used this table to authenticate
anybody.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The one identity table. Named once, as in every PR revision.
USERS = "users"

#: Nothing in this module cascades.
RESTRICT = "RESTRICT"

# --- The stored vocabulary --------------------------------------------------
# A literal rather than an import from ``meobot.db.models.web_session``: a
# migration has to keep meaning what it meant on the day it ran.
# ``tests/unit/test_web_auth.py`` asserts this list still matches the enum.
SESSION_KINDS = ("LOGIN_TOKEN", "SESSION")


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(*SESSION_KINDS, name="web_session_kind", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_web_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name="fk_web_sessions_user_id_users",
            ondelete=RESTRICT,
        ),
        # Bare names. ``NAMING_CONVENTION`` prefixes ``ck_web_sessions_``, so a
        # name written with the prefix already on it comes out doubled - and then
        # no longer matches what the ORM metadata would generate.
        sa.CheckConstraint("length(trim(token_hash)) > 0", name="token_hash_not_empty"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )
    op.create_index("ix_web_sessions_user_id", "web_sessions", ["user_id"])
    # Unique: two subjects sharing a credential is the failure this prevents.
    op.create_index("uq_web_sessions_token_hash", "web_sessions", ["token_hash"], unique=True)
    op.create_index(
        "ix_web_sessions_user_kind", "web_sessions", ["user_id", "kind", "revoked_at"]
    )


def downgrade() -> None:
    """Drop ``web_sessions`` and its indexes.

    Read the note in the module docstring: this signs everybody out and makes
    the web admin unreachable. Indexes are dropped explicitly before the table,
    matching how 0012-0016 reverse themselves. No ``DROP TYPE``: the enum column
    is ``VARCHAR`` and no PostgreSQL type was created.
    """
    op.drop_index("ix_web_sessions_user_kind", table_name="web_sessions")
    op.drop_index("uq_web_sessions_token_hash", table_name="web_sessions")
    op.drop_index("ix_web_sessions_user_id", table_name="web_sessions")
    op.drop_table("web_sessions")
