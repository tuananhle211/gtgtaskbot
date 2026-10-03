"""Persistent chat memory: threads, messages and rolling summaries.

Deliberately separate from ``conversation_states``. That table holds the FSM
state of one *guided workflow* (adding a sheet, typing a revision comment), it
expires, and a nightly task deletes it. Overloading it with chat history would
mean an abandoned ``/add_sheet`` takes the conversation with it.

What keeps this from becoming unbounded memory:

* only ``CHAT_HISTORY_MAX_MESSAGES`` recent messages are ever replayed to a
  provider - older content survives only as a rolling summary;
* every stored ``content`` has been through
  :func:`meobot.domain.conversations.redaction.prepare_for_storage`;
* a thread can be archived (``/clear_chat``), which stops it being read
  without destroying the audit-relevant history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin


class ConversationThread(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One continuous conversation between a person and MeoBot.

    At most one thread per (bot, chat, user) is ``active``; ``/new_chat``
    archives the current one and opens a fresh thread, which is how a user
    starts a new topic without the old one bleeding into it.
    """

    __tablename__ = "conversation_threads"
    __table_args__ = (
        Index(
            "ix_conversation_threads_active_key",
            "bot_id",
            "chat_id",
            "telegram_user_id",
            "active",
        ),
    )

    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Bounded list of the entities this thread has been talking about, so
    #: "kịch bản này" or "Sheet đó" can be resolved on the next turn. Capped at
    #: ``MAX_RECENT_REFERENCES`` on write - this is a pointer list, not memory.
    recent_references: Mapped[list[dict[str, object]]] = mapped_column(
        JSONColumn, nullable=False, default=list
    )


class ConversationMessage(Base, UUIDPrimaryKeyMixin):
    """One stored turn.

    ``role`` is ``user``, ``assistant`` or ``tool``. A ``tool`` row records
    that a tool ran and what it was about - never the tool's full output, which
    can be large and is already in the audit log.
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (Index("ix_conversation_messages_thread_created", "thread_id", "created_at"),)

    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    related_tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    token_count_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class ConversationSummary(Base, UUIDPrimaryKeyMixin):
    """The rolling summary of everything older than the recent window.

    One row per thread: summarising replaces the previous summary rather than
    appending, which is what keeps the prompt bounded.
    """

    __tablename__ = "conversation_summaries"
    __table_args__ = (UniqueConstraint("thread_id", name="uq_conversation_summaries_thread_id"),)

    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
