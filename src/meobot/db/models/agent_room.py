"""Phòng họp agent (0093): rooms where several agents talk in turns.

See :mod:`meobot.domain.agents.rooms`. A room belongs to its owner; its people
read and write; its agents speak in rounds. Deleting a room deletes its
agents' seats, its people and its messages (CASCADE).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.agent_chat import AGENT_PROFILES, CASCADE
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.agents.models import MessageStatus
from meobot.domain.agents.rooms import RoomSpeaker, RoomStep

AGENT_ROOMS = "agent_rooms"
AGENT_ROOM_AGENTS = "agent_room_agents"
AGENT_ROOM_MEMBERS = "agent_room_members"
AGENT_ROOM_MESSAGES = "agent_room_messages"


class AgentRoom(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One meeting room."""

    __tablename__ = AGENT_ROOMS
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="title_not_empty"),
        Index("ix_agent_rooms_owner_last", "owner_user_id", "last_message_at"),
    )

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The agent who opens and closes each round (one of the room's agents).
    chair_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{AGENT_PROFILES}.id", ondelete=RESTRICT), nullable=True
    )
    #: Rounds run so far (the next one is ``rounds + 1``).
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRoomAgent(Base, UUIDPrimaryKeyMixin):
    """One agent in a room, in its speaking order, with its own Hermes transcript."""

    __tablename__ = AGENT_ROOM_AGENTS
    __table_args__ = (UniqueConstraint("room_id", "agent_id"),)

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{AGENT_ROOMS}.id", ondelete=CASCADE), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{AGENT_PROFILES}.id", ondelete=RESTRICT), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: What the agent has already been shown: messages after this are new to it.
    seen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AgentRoomMember(Base, UUIDPrimaryKeyMixin):
    """A person the owner invited (the owner is not listed here)."""

    __tablename__ = AGENT_ROOM_MEMBERS
    __table_args__ = (
        UniqueConstraint("room_id", "user_id"),
        Index("ix_agent_room_members_user", "user_id"),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{AGENT_ROOMS}.id", ondelete=CASCADE), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    added_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AgentRoomMessage(Base, UUIDPrimaryKeyMixin):
    """One message: a person's, or an agent's turn in a round."""

    __tablename__ = AGENT_ROOM_MESSAGES
    __table_args__ = (
        CheckConstraint(
            "(speaker = 'PERSON' AND user_id IS NOT NULL AND agent_id IS NULL)"
            " OR (speaker = 'AGENT' AND agent_id IS NOT NULL AND user_id IS NULL)",
            name="one_speaker",
        ),
        Index("ix_agent_room_messages_room_created", "room_id", "created_at"),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{AGENT_ROOMS}.id", ondelete=CASCADE), nullable=False
    )
    speaker: Mapped[RoomSpeaker] = mapped_column(
        value_enum(RoomSpeaker, name="agent_room_speaker", length=8), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{AGENT_PROFILES}.id", ondelete=RESTRICT), nullable=True
    )
    #: Who spoke, as the others read it ("Lê Tuấn Anh - Trưởng phòng Ads", "CMO").
    speaker_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: The round an agent's turn belongs to (a person's message: the round running, else None).
    round_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step: Mapped[RoomStep | None] = mapped_column(
        value_enum(RoomStep, name="agent_room_step", length=8), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[MessageStatus] = mapped_column(
        value_enum(MessageStatus, name="agent_message_status", length=12), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tools_used: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Python default (append-only ordering, see CLAUDE.md gotchas).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AGENT_ROOMS",
    "AGENT_ROOM_AGENTS",
    "AGENT_ROOM_MEMBERS",
    "AGENT_ROOM_MESSAGES",
    "AgentRoom",
    "AgentRoomAgent",
    "AgentRoomMember",
    "AgentRoomMessage",
]
