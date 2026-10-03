"""Per-group response policy and pending access requests.

``group_member_response_policies`` answers "does MeoBot talk to this person in
this chat, and on what terms". It is additive: no row means
:attr:`~meobot.domain.access.models.GroupPolicyMode.INHERIT`, which is the
behaviour that existed before this table did.

The Guest counters live on the same row rather than in a separate table. A
Guest *is* a group policy in mode ``guest`` - splitting the grant from the
counters would let the two disagree, and "how many questions are left" would
stop being answerable in the same transaction that decides whether to answer.
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
from meobot.domain.access.models import (
    GUEST_DEFAULT_QUESTION_LIMIT,
    AccessAction,
    GroupPolicyMode,
    PendingRequestStatus,
)


class GroupMemberResponsePolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """How MeoBot treats one Telegram account inside one Telegram chat."""

    __tablename__ = "group_member_response_policies"
    __table_args__ = (
        UniqueConstraint(
            "bot_id",
            "telegram_chat_id",
            "telegram_user_id",
            name="uq_group_member_response_policies_bot_chat_user",
        ),
        Index(
            "ix_group_member_response_policies_chat_mode",
            "telegram_chat_id",
            "mode",
        ),
    )

    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    mode: Mapped[GroupPolicyMode] = mapped_column(
        value_enum(GroupPolicyMode, name="group_policy_mode", length=20),
        nullable=False,
        default=GroupPolicyMode.INHERIT,
    )
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Guest allowance, meaningful only when ``mode`` is GUEST ----------
    guest_granted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    guest_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    guest_question_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=GUEST_DEFAULT_QUESTION_LIMIT
    )
    guest_questions_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: In-flight answers. Held between the allowance check and delivery so two
    #: concurrent messages cannot both take the last slot.
    guest_reserved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    guest_exhausted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PendingGuestAccessRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One unknown person, in one group, waiting for the owner to decide.

    At most one row is *open* per (bot, chat, requester) at a time, which is
    what stops a stranger tagging MeoBot ten times and producing ten owner
    notifications. Repeated tags update this row instead.
    """

    __tablename__ = "pending_guest_access_requests"
    __table_args__ = (
        Index(
            "ix_pending_guest_access_requests_open_key",
            "bot_id",
            "telegram_chat_id",
            "requester_telegram_id",
            "status",
        ),
    )

    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requester_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    requester_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requester_display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: The message that triggered the request. ANSWER_ONCE replies to exactly
    #: this message and nothing else.
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Redacted, truncated preview shown to the owner so they can judge the ask.
    #: Never the raw message, and never stored as conversation memory.
    question_preview: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[PendingRequestStatus] = mapped_column(
        value_enum(PendingRequestStatus, name="pending_request_status", length=20),
        nullable=False,
        default=PendingRequestStatus.OPEN,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set when the owner was told. Kept so repeated tags stay silent.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_owner_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: After an explicit refusal, the owner is not bothered again until this.
    notify_cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_action: Mapped[AccessAction | None] = mapped_column(
        value_enum(AccessAction, name="access_action", length=10),
        nullable=True,
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Number of times this person tagged MeoBot while the request was open.
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
