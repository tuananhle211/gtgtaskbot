"""Registered destinations, announcements, and the transactional outbox.

``outbound_messages`` is the load-bearing table. It is written **in the same
transaction as the business change it announces**, which is what makes the
guarantee possible: an approved leave request and the intent to tell somebody
about it either both exist or neither does. Telegram is then a separate,
retryable problem, and a failure there can no longer roll back an approval.

``idempotency_key`` is unique. That single constraint is what stops a
double-tapped button, a redelivered Telegram update and a retried service call
from producing three copies of one notification - the second insert simply
fails, and the caller treats that as "already queued".

``safe_payload_json`` holds only the structured fields a template declared. No
token, no credential, no provider response body.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.notifications.models import (
    AnnouncementStatus,
    AssignmentRole,
    ChatPurpose,
    DeliveryOutcome,
    DestinationHealth,
    FailureCategory,
    OutboxStatus,
    PrivacyClassification,
    RecipientType,
)


class TelegramChat(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A Telegram group MeoBot is allowed to send to.

    Identity is ``telegram_chat_id``, never the title: a group name is
    something any admin can change on a whim, and routing that broke when
    somebody renamed a group would be worse than no routing.

    Registration is deliberate. The bot being *in* a group does not register
    it - somebody with authority has to say so, in that group, and confirm.
    """

    __tablename__ = "telegram_chats"
    __table_args__ = (
        UniqueConstraint("bot_identity", "telegram_chat_id", name="uq_telegram_chats_bot_chat"),
        Index("ix_telegram_chats_purpose_active", "purpose", "is_active"),
    )

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    #: Which bot registered it, so two deployments sharing a database cannot
    #: deliver into each other's groups.
    bot_identity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_type: Mapped[str] = mapped_column(String(30), nullable=False, default="supergroup")
    #: Whatever Telegram called it when it was registered. Refreshed
    #: opportunistically; never used to find the row.
    telegram_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: What people here call it. This is what "gửi vào group Content" matches.
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Accent-free, lower case, for matching a typed alias.
    normalized_alias: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    team: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purpose: Mapped[ChatPurpose] = mapped_column(
        value_enum(ChatPurpose, name="chat_purpose", length=40),
        nullable=False,
        default=ChatPurpose.GENERAL,
    )
    #: The ceiling on what may be delivered here.
    privacy_level: Mapped[PrivacyClassification] = mapped_column(
        value_enum(PrivacyClassification, name="privacy_classification", length=30),
        nullable=False,
        default=PrivacyClassification.PUBLIC_OPERATIONAL,
    )
    allow_automated_delivery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Health, learned from delivery failures rather than assumed.
    bot_can_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bot_is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    registered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Bumped on every change, and bound into buttons rendered against it.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # --- Proactive health, added in 0.6.0a2 -------------------------------
    # Until this release, a destination was only discovered to be broken by a
    # real business message failing against it - so the first person to learn
    # the bot had been removed from a group was whoever's announcement did not
    # arrive. These columns hold what a ``getChat``/``getChatMember`` probe
    # found, which is checked on a schedule and costs nobody a message.
    health_status: Mapped[DestinationHealth] = mapped_column(
        value_enum(DestinationHealth, name="destination_health", length=30),
        nullable=False,
        default=DestinationHealth.UNKNOWN,
        server_default=DestinationHealth.UNKNOWN.value,
    )
    last_healthy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_unhealthy_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_error_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: A transient Telegram error must not disable a working group, so an
    #: unhealthy verdict has to repeat before it is believed.
    consecutive_health_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Bumped on every health transition. Part of the alert idempotency key, so
    #: one transition produces one alert however often the sweep runs.
    health_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # --- Natural naming, added in 0.6.0a3 ---------------------------------
    # Until this release a destination answered to its display name, its
    # Telegram title, and nothing else - so reaching "Saykeng - Vựa Idea"
    # meant typing "Saykeng - Vựa Idea". People do not do that. They say
    # "group idea", "nhóm sáng tạo", "các group content", and every one of
    # those is a fact about the group that simply had nowhere to be stored.
    #
    # These columns are that place. They are matched deterministically and
    # they are *authoritative*: a phrase the model extracted is only ever a
    # lookup key, and the chat id still comes from this table.
    #: Extra names this group answers to, one per line. Stored folded-on-read
    #: rather than folded-on-write so the owner sees what they typed.
    aliases_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Free labels - "brainstorm", "nội dung", "sáng tạo" - one per line.
    #: What makes "các group content" resolve to a set rather than a name.
    tags_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A brand or client this group belongs to, for "các group Apexmed".
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: When something was last sent here. Used only for ordering the registry
    #: list; never for deciding where a message goes.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TelegramChatAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person's explicit relationship to one registered group.

    This is what makes "Trưởng nhóm may broadcast to their team" a fact in the
    database rather than an inference from a job title. Before it existed, a
    team lead's set of addressable groups was computed from the group's
    *purpose* and was, in practice, always empty.

    An assignment never changes a global role. Assigning somebody as a manager
    of one group grants exactly that: authority over that resource.
    """

    __tablename__ = "telegram_chat_assignments"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_row_id",
            "user_id",
            name="uq_telegram_chat_assignments_chat_user",
        ),
        Index("ix_telegram_chat_assignments_user_active", "user_id", "is_active"),
    )

    telegram_chat_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assignment_role: Mapped[AssignmentRole] = mapped_column(
        value_enum(AssignmentRole, name="assignment_role", length=20),
        nullable=False,
        default=AssignmentRole.MEMBER,
    )
    can_broadcast: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_view_read_receipts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_manage_audience: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Revocation is a flag, not a delete: the audit trail of who could post
    #: where, and when, is the point of having this table.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AnnouncementRecipient(Base, UUIDPrimaryKeyMixin):
    """One person who was expected to read one announcement.

    Snapshotted at publication time, and that timing is the whole point.
    "Ai chưa đọc?" needs a denominator, and computing it later would answer a
    different question every time somebody joins or leaves - a person hired
    after the announcement was sent has not failed to read it.

    Telegram will not enumerate a group's members for a bot, so where MeoBot
    has no configured audience it says so rather than inventing one.
    """

    __tablename__ = "announcement_recipients"
    __table_args__ = (
        UniqueConstraint(
            "announcement_id", "user_id", name="uq_announcement_recipients_announcement_user"
        ),
    )

    announcement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: When the snapshot was taken, so "expected at the time" stays answerable.
    expected_at_publish_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Announcement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Something a person wrote, to be delivered somewhere else.

    Kept as its own row rather than only an outbox entry because it is
    *authored content*: it has a draft state, it is confirmed before it goes
    anywhere, and acknowledgements point at it.
    """

    __tablename__ = "announcements"

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Where it was written - a private chat. Audited alongside the destination.
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    destination_chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[AnnouncementStatus] = mapped_column(
        value_enum(AnnouncementStatus, name="announcement_status", length=20),
        nullable=False,
        default=AnnouncementStatus.DRAFT,
        index=True,
    )
    request_read_receipt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnnouncementAcknowledgement(Base, UUIDPrimaryKeyMixin):
    """One person confirming they read one announcement.

    Unique per (announcement, person) so pressing "Đã đọc" twice records once,
    and so nobody can acknowledge on somebody else's behalf by pressing again.
    """

    __tablename__ = "announcement_acknowledgements"
    __table_args__ = (
        UniqueConstraint(
            "announcement_id",
            "telegram_user_id",
            name="uq_announcement_acknowledgements_announcement_user",
        ),
    )

    announcement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    needs_followup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboundMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One durable intent to send one message to one destination."""

    __tablename__ = "outbound_messages"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbound_messages_idempotency_key"),
        # The worker's claim query: due, claimable, oldest first.
        Index("ix_outbound_messages_claim", "status", "available_at"),
        Index("ix_outbound_messages_aggregate", "aggregate_type", "aggregate_id"),
    )

    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    recipient_type: Mapped[RecipientType] = mapped_column(
        value_enum(RecipientType, name="recipient_type", length=30), nullable=False
    )
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    #: Resolved at creation time. A destination that moves later does not
    #: silently redirect a message somebody already confirmed.
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    registered_chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="SET NULL"), nullable=True
    )

    template_key: Mapped[str] = mapped_column(String(60), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    privacy_classification: Mapped[PrivacyClassification] = mapped_column(
        value_enum(PrivacyClassification, name="privacy_classification", length=30),
        nullable=False,
    )
    #: Only fields the template declared. Never a token or a provider body.
    safe_payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, default=dict
    )

    status: Mapped[OutboxStatus] = mapped_column(
        value_enum(OutboxStatus, name="outbox_status", length=30),
        nullable=False,
        default=OutboxStatus.PENDING,
        index=True,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    last_error_category: Mapped[FailureCategory] = mapped_column(
        value_enum(FailureCategory, name="failure_category", length=40),
        nullable=False,
        default=FailureCategory.NONE,
    )
    #: Where the action that produced this message was taken. Audited so the
    #: pair (source, destination) is always answerable.
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # --- Proactive failure reporting, added in 0.6.0a2 --------------------
    # Before this, a permanently failed message sat in the outbox and the only
    # way to find out was to ask "tin nào chưa gửi được?". Somebody who does
    # not know a message failed does not know to ask.
    #
    # ``failure_alert_sent`` is set inside the same transaction that settles
    # the failure, so the "notify once" guarantee does not depend on the alert
    # itself succeeding - a second sweep will not re-alert.
    failure_alert_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    failure_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: What to call the destination when telling somebody it failed. Resolved
    #: at creation time: a numeric chat id is never shown to a user, and the
    #: registration may be gone by the time the alert is written.
    destination_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: A short human description of the business event, for the same reason.
    business_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: True for messages that are themselves alerts. A failed alert must never
    #: produce an alert about the alert.
    is_alert: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class DeliveryAttempt(Base, UUIDPrimaryKeyMixin):
    """One try at delivering one outbound message.

    Append-only. Keeping every attempt is what makes "why did this take four
    hours" answerable without turning on debug logging after the fact.
    """

    __tablename__ = "delivery_attempts"
    __table_args__ = (
        Index("ix_delivery_attempts_message_number", "outbound_message_id", "attempt_number"),
    )

    outbound_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outbound_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[DeliveryOutcome] = mapped_column(
        value_enum(DeliveryOutcome, name="delivery_outcome", length=30), nullable=False
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_category: Mapped[FailureCategory] = mapped_column(
        value_enum(FailureCategory, name="failure_category", length=40),
        nullable=False,
        default=FailureCategory.NONE,
    )
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
