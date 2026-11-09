"""Pancake → agent trả lời khách (0094). See :mod:`meobot.domain.pancake.models`.

* ``pancake_pages``: one Facebook page wired to an agent. Its page token is
  stored encrypted (AAD ``pancake_page:<id>``); ``webhook_key`` is the secret
  part of the webhook address the OWNER pastes into Pancake.
* ``pancake_conversations``: one customer thread of a page = one Hermes
  transcript (``apm-pk-<id>``); its pause and the tags / assignment Pancake last
  reported. No customer name, phone or words.
* ``pancake_replies``: one customer message APM answered (or not), unique per
  page + Pancake message id so a webhook delivered twice is answered once.
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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.agent_chat import CASCADE
from meobot.db.models.pr import RESTRICT, USERS_TABLE
from meobot.domain.pancake.models import ReplyMode, ReplyStatus

PANCAKE_PAGES = "pancake_pages"
PANCAKE_CONVERSATIONS = "pancake_conversations"
PANCAKE_REPLIES = "pancake_replies"
SET_NULL = "SET NULL"


class PancakePage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = PANCAKE_PAGES
    __table_args__ = (
        CheckConstraint("length(trim(page_id)) > 0", name="page_id_not_empty"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
    )

    #: Pancake's page id (the Facebook page id, ``tt_...`` for TikTok...).
    page_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Off = webhooks are taken and dropped, nothing answered.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    mode: Mapped[ReplyMode] = mapped_column(
        value_enum(ReplyMode, name="pancake_reply_mode", length=10),
        nullable=False,
        default=ReplyMode.SUGGEST,
    )
    agent_slug: Mapped[str] = mapped_column(String(40), nullable=False, default="chatpage")
    #: ``v1:<key id>:<nonce>:<ciphertext>``; None = not typed yet.
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    webhook_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: Pancake tag ids (strings): a conversation wearing one = a person handles it.
    handoff_tag_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: An assigned conversation = a person handles it.
    stop_when_assigned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )


class PancakeConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = PANCAKE_CONVERSATIONS
    __table_args__ = (UniqueConstraint("page_row_id", "conversation_id"),)

    page_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{PANCAKE_PAGES}.id", ondelete=CASCADE), nullable=False
    )
    #: Pancake's conversation id (for an inbox: the customer's PSID).
    conversation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    #: The bot keeps quiet until then (a staff member answered, the agent asked for a person).
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: What Pancake last reported (ids only).
    tag_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    assignee_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: The newest message the agent was already shown (its transcript has it).
    seen_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Messages APM itself sent (their webhook echo is not a staff reply).
    sent_message_ids: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)


class PancakeReply(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = PANCAKE_REPLIES
    __table_args__ = (
        UniqueConstraint("page_row_id", "customer_message_id"),
        Index("ix_pancake_replies_status_created", "status", "created_at"),
        Index("ix_pancake_replies_conversation", "conversation_row_id", "created_at"),
    )

    page_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{PANCAKE_PAGES}.id", ondelete=CASCADE), nullable=False
    )
    conversation_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{PANCAKE_CONVERSATIONS}.id", ondelete=CASCADE), nullable=False
    )
    customer_message_id: Mapped[str] = mapped_column(String(160), nullable=False)
    customer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ReplyStatus] = mapped_column(
        value_enum(ReplyStatus, name="pancake_reply_status", length=12),
        nullable=False,
        default=ReplyStatus.WAITING,
    )
    #: Why it was skipped / failed / held (``REASON_LABELS``).
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: What the agent wrote (the hand-off marker taken out).
    suggested_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What went to the customer (a person may have edited it).
    sent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    handoff_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The script rules the answer breaks ``[{rule, quote, reason, suggestion}]``.
    violations: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: The AI check: True passed, False broke a rule, None could not run.
    review_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Who pressed "Gửi" / "Bỏ qua"; None on a SENT row = sent automatically.
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=SET_NULL), nullable=True
    )


__all__ = [
    "PANCAKE_CONVERSATIONS",
    "PANCAKE_PAGES",
    "PANCAKE_REPLIES",
    "PancakeConversation",
    "PancakePage",
    "PancakeReply",
]
