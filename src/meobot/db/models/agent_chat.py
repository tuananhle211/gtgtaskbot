"""Đội agent (0091): the agents people may chat with, their conversations, messages.

See :mod:`meobot.domain.agents.models`. No key and no token is stored here:
the Hermes keys stay on the agents' VPS (the relay holds them), APM holds only
the relay's address and token in its environment.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.core.time import utcnow
from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.agents.models import AgentGroup, MessageRole, MessageStatus

AGENT_PROFILES = "agent_profiles"
AGENT_CONVERSATIONS = "agent_conversations"
AGENT_MESSAGES = "agent_messages"
CASCADE = "CASCADE"


class AgentProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One Hermes agent as APM shows it, and who may chat with it."""

    __tablename__ = AGENT_PROFILES
    __table_args__ = (
        CheckConstraint("length(trim(slug)) > 0", name="slug_not_empty"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
    )

    #: The Hermes profile's name (``/p/<slug>/`` on the agents' VPS).
    slug: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    #: The short letters drawn in its round badge ("CEO", "PC").
    badge: Mapped[str] = mapped_column(String(6), nullable=False, default="")
    group: Mapped[AgentGroup] = mapped_column(
        "group_code", value_enum(AgentGroup, name="agent_group", length=20), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Off = hidden from everybody (kept for its conversations).
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: Anybody signed in may chat with it (Pháp chế).
    everyone: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Department ids (strings) whose seated people may chat with it.
    allowed_department_ids: Mapped[list[Any]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )
    #: Single people (user ids as strings) on top of the departments.
    allowed_user_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)


class AgentConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person's thread with one agent. Only its owner ever reads it."""

    __tablename__ = AGENT_CONVERSATIONS
    __table_args__ = (
        Index("ix_agent_conversations_user_agent_last", "user_id", "agent_id", "last_message_at"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{AGENT_PROFILES}.id", ondelete=RESTRICT), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: The transcript id on the Hermes side (``apm-<id>``).
    hermes_session_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: "Lưu trữ": hidden from the list, kept.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMessage(Base, UUIDPrimaryKeyMixin):
    """One message: the person's question or the agent's answer."""

    __tablename__ = AGENT_MESSAGES
    __table_args__ = (
        Index("ix_agent_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{AGENT_CONVERSATIONS}.id", ondelete=CASCADE), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        value_enum(MessageRole, name="agent_message_role", length=12), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[MessageStatus] = mapped_column(
        value_enum(MessageStatus, name="agent_message_status", length=12), nullable=False
    )
    #: Why it failed (``agent_unreachable``, ``timeout``...).
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Tool names the agent used while answering (shown as "Đã tra cứu").
    tools_used: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Python default (append-only ordering, see CLAUDE.md gotchas).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AGENT_CONVERSATIONS",
    "AGENT_MESSAGES",
    "AGENT_PROFILES",
    "AgentConversation",
    "AgentMessage",
    "AgentProfile",
]
