"""Milestone 4: actor profiles and resolvable conversation references.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-29

Notes:
* ``actor_profiles`` is descriptive context (job title, priorities, how the
  person wants to be addressed). It deliberately has **no role column**: the
  authorisation record stays in ``users``, so nothing a conversation writes
  here can widen anyone's permissions.
* Keyed by ``telegram_user_id``, not by ``users.id``, because the bootstrap
  owner (``MEOBOT_OWNER_TELEGRAM_ID``) has no ``users`` row until promoted and
  still needs a profile. ``user_id`` is filled in opportunistically.
* ``conversation_threads.recent_references`` is a bounded pointer list ("the
  script we just discussed"), capped in application code. It is not memory:
  the rolling summary remains the only place older content survives.
* Additive only. No column is dropped and no row is deleted by this revision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "actor_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("preferred_name", sa.String(length=100), nullable=True),
        sa.Column("preferred_address", sa.String(length=40), nullable=True),
        sa.Column("job_title", sa.String(length=200), nullable=True),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("department", sa.String(length=200), nullable=True),
        sa.Column("team", sa.String(length=200), nullable=True),
        sa.Column("responsibilities", JSONB, nullable=False, server_default="[]"),
        sa.Column("communication_preferences", JSONB, nullable=False, server_default="[]"),
        sa.Column("content_domains", JSONB, nullable=False, server_default="[]"),
        sa.Column("current_priorities", JSONB, nullable=False, server_default="[]"),
        sa.Column("profile_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_actor_profiles_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_actor_profiles"),
        sa.UniqueConstraint("telegram_user_id", name="uq_actor_profiles_telegram_user_id"),
    )
    op.create_index("ix_actor_profiles_telegram_user_id", "actor_profiles", ["telegram_user_id"])

    op.add_column(
        "conversation_threads",
        sa.Column("recent_references", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("conversation_threads", "recent_references")
    op.drop_table("actor_profiles")
