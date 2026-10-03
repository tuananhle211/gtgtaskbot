"""The one message a stranger sent before anybody decided about them.

Why this exists at all: Telegram's Bot API has no "fetch message 42 from chat
-100" call. A bot can only see a message as it arrives. So answering "the
question they asked before you approved them" is possible only if MeoBot stored
the question at the moment it arrived - and storing a non-user's words is
exactly the kind of thing that needs a reason, a boundary and an expiry rather
than a convenient column on an existing table.

The boundary this table draws: **holding is not processing.** Nothing here has
been through the LLM, produced conversation memory, created an
:class:`~meobot.domain.identity.models.Actor` or run a tool. It is text, an
address to reply to, a hash and a clock. Approval is what turns it into work.

Retention is explicit. ``expires_at`` bounds how long an undecided question is
kept; rejection and expiry blank ``sanitized_text`` and keep only the hash, so
an audit can still prove *which* message was refused without retaining what it
said.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.deferred.models import AuthorizationMode, DeferredMessageStatus


class DeferredGuestMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One held question, and everything needed to answer it later."""

    __tablename__ = "deferred_guest_messages"
    __table_args__ = (
        # One held question per access request. A stranger who sends five
        # messages while waiting does not get five answers when approved - the
        # owner read one question and approved that one.
        UniqueConstraint(
            "pending_access_request_id",
            name="uq_deferred_guest_messages_request",
        ),
        Index("ix_deferred_guest_messages_status_expiry", "status", "expires_at"),
    )

    pending_access_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pending_guest_access_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Which bot held it, so two deployments sharing a database cannot answer
    #: into each other's groups.
    bot_identity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: The question, after secret redaction, bounded in length. Blanked once
    #: the request is refused or expires.
    sanitized_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Survives purging. Lets an audit answer "was this the message that was
    #: refused" without keeping the message.
    original_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The message to reply to, so the answer lands under the question.
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[DeferredMessageStatus] = mapped_column(
        value_enum(DeferredMessageStatus, name="deferred_message_status", length=30),
        nullable=False,
        default=DeferredMessageStatus.PENDING_APPROVAL,
        index=True,
    )
    authorization_mode: Mapped[AuthorizationMode | None] = mapped_column(
        value_enum(AuthorizationMode, name="deferred_authorization_mode", length=20),
        nullable=True,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: After this, the question is stale: the person has moved on, and
    #: answering a day-old message is worse than not answering.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: The outbox row carrying the answer, once one exists.
    outbox_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outbound_messages.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
