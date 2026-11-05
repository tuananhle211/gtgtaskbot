"""Phòng họp agent: ``agent_rooms`` + agents, people, messages of a room.

Revision ID: 0093
Revises: 0092
Create Date: 2026-11-05

A room holds two to six agents who speak in rounds (the chair opens, every
other agent speaks once, the chair closes), and people the owner invites, who
read and write. Each agent keeps its own Hermes transcript of the room
(``apm-room-<agent_room_agents.id>``); ``seen_until`` marks what it was already
shown. Deleting a room deletes everything in it (CASCADE).

Downgrade
---------

Drops the four tables (every room and its history is lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROOMS = "agent_rooms"
AGENTS = "agent_room_agents"
MEMBERS = "agent_room_members"
MESSAGES = "agent_room_messages"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.create_table(
        ROOMS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("chair_agent_id", sa.Uuid(), nullable=True),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_agent_rooms_title_not_empty")),
        _fk(ROOMS, "owner_user_id", "users", "RESTRICT"),
        _fk(ROOMS, "chair_agent_id", "agent_profiles", "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_rooms")),
    )
    op.create_index("ix_agent_rooms_owner_last", ROOMS, ["owner_user_id", "last_message_at"])

    op.create_table(
        AGENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("seen_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        _fk(AGENTS, "room_id", ROOMS, "CASCADE"),
        _fk(AGENTS, "agent_id", "agent_profiles", "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_room_agents")),
        sa.UniqueConstraint(
            "room_id", "agent_id", name=op.f("uq_agent_room_agents_room_id_agent_id")
        ),
    )

    op.create_table(
        MEMBERS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        _fk(MEMBERS, "room_id", ROOMS, "CASCADE"),
        _fk(MEMBERS, "user_id", "users", "RESTRICT"),
        _fk(MEMBERS, "added_by_user_id", "users", "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_room_members")),
        sa.UniqueConstraint(
            "room_id", "user_id", name=op.f("uq_agent_room_members_room_id_user_id")
        ),
    )
    op.create_index("ix_agent_room_members_user", MEMBERS, ["user_id"])

    op.create_table(
        MESSAGES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("speaker", sa.String(length=8), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("speaker_label", sa.String(length=200), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=True),
        sa.Column("step", sa.String(length=8), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("tools_used", _JSON, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(speaker = 'PERSON' AND user_id IS NOT NULL AND agent_id IS NULL)"
            " OR (speaker = 'AGENT' AND agent_id IS NOT NULL AND user_id IS NULL)",
            name=op.f("ck_agent_room_messages_one_speaker"),
        ),
        _fk(MESSAGES, "room_id", ROOMS, "CASCADE"),
        _fk(MESSAGES, "user_id", "users", "RESTRICT"),
        _fk(MESSAGES, "agent_id", "agent_profiles", "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_room_messages")),
    )
    op.create_index("ix_agent_room_messages_room_created", MESSAGES, ["room_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_room_messages_room_created", table_name=MESSAGES)
    op.drop_table(MESSAGES)
    op.drop_index("ix_agent_room_members_user", table_name=MEMBERS)
    op.drop_table(MEMBERS)
    op.drop_table(AGENTS)
    op.drop_index("ix_agent_rooms_owner_last", table_name=ROOMS)
    op.drop_table(ROOMS)
