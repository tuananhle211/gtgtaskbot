"""0.6.0a1: chat registry, announcements and the transactional outbox.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-30

Notes:
* Strictly additive. No table is dropped, no column removed, no enum value
  renamed. ``users``, ``hr_requests``, the script/Sheet/Drive tables and the
  access-control tables from 0006 are untouched apart from six new nullable
  columns on ``users``.
* ``outbound_messages.idempotency_key`` is **unique**, and that single
  constraint is the anti-duplication mechanism for the whole release: a
  double-tapped button, a redelivered Telegram update and a retried service
  call all collide on it, and the second insert simply fails.
* ``ix_outbound_messages_claim`` backs the worker's ``FOR UPDATE SKIP LOCKED``
  claim query, which is what keeps two workers off the same row.
* The new ``users`` columns record whether a private chat is reachable.
  ``private_chat_available`` defaults to **false**: Telegram does not let a bot
  open a conversation, so reachability has to be learned, never assumed.
* ``telegram_chats`` is empty after this migration. The bot being in a group
  does not register it - somebody with authority has to, in that group.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

CHAT_PURPOSES = (
    "DEPARTMENT_ANNOUNCEMENTS",
    "CONTENT_TEAM",
    "PRODUCTION_TEAM",
    "SEEDING_TEAM",
    "REPORTING",
    "ATTENDANCE",
    "MANAGEMENT",
    "GENERAL",
)
PRIVACY_LEVELS = (
    "PUBLIC_OPERATIONAL",
    "TEAM_OPERATIONAL",
    "MANAGEMENT_ONLY",
    "PERSONAL_PRIVATE",
    "SECRET",
)
RECIPIENT_TYPES = ("USER_PRIVATE", "REGISTERED_CHAT")
OUTBOX_STATUSES = (
    "PENDING",
    "PROCESSING",
    "DELIVERED",
    "RETRY_WAIT",
    "PERMANENT_FAILURE",
    "CANCELLED",
)
DELIVERY_OUTCOMES = (
    "DELIVERED",
    "TEMPORARY_FAILURE",
    "RECIPIENT_UNAVAILABLE",
    "PERMANENT_FAILURE",
)
FAILURE_CATEGORIES = (
    "NONE",
    "NETWORK",
    "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "PRIVATE_CHAT_UNAVAILABLE",
    "BOT_NOT_IN_CHAT",
    "BOT_CANNOT_SEND",
    "CHAT_NOT_FOUND",
    "DESTINATION_REFUSED",
    "UNKNOWN",
)
ANNOUNCEMENT_STATUSES = ("DRAFT", "PUBLISHED", "CANCELLED")


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    # --- users: private-chat reachability ---------------------------------
    op.add_column("users", sa.Column("telegram_private_chat_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "private_chat_available", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "users", sa.Column("last_private_interaction_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("last_private_delivery_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column("private_delivery_failure_category", sa.String(length=40), nullable=True),
    )
    op.add_column("users", sa.Column("bot_blocked_at", sa.DateTime(timezone=True), nullable=True))

    # --- registered destinations ------------------------------------------
    op.create_table(
        "telegram_chats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_identity", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(length=30), nullable=False, server_default="supergroup"),
        sa.Column("telegram_title", sa.String(length=300), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_alias", sa.String(length=200), nullable=False),
        sa.Column("department", sa.String(length=200), nullable=True),
        sa.Column("team", sa.String(length=200), nullable=True),
        sa.Column(
            "purpose",
            _enum(CHAT_PURPOSES, "chat_purpose", 40),
            nullable=False,
            server_default="GENERAL",
        ),
        sa.Column(
            "privacy_level",
            _enum(PRIVACY_LEVELS, "privacy_classification", 30),
            nullable=False,
            server_default="PUBLIC_OPERATIONAL",
        ),
        sa.Column(
            "allow_automated_delivery", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("bot_can_send", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("bot_is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("registered_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["registered_by_user_id"],
            ["users.id"],
            name="fk_telegram_chats_registered_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_chats"),
        sa.UniqueConstraint("bot_identity", "telegram_chat_id", name="uq_telegram_chats_bot_chat"),
    )
    op.create_index("ix_telegram_chats_telegram_chat_id", "telegram_chats", ["telegram_chat_id"])
    op.create_index("ix_telegram_chats_normalized_alias", "telegram_chats", ["normalized_alias"])
    op.create_index("ix_telegram_chats_is_active", "telegram_chats", ["is_active"])
    op.create_index("ix_telegram_chats_purpose_active", "telegram_chats", ["purpose", "is_active"])

    # --- announcements ----------------------------------------------------
    op.create_table(
        "announcements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("destination_chat_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            _enum(ANNOUNCEMENT_STATUSES, "announcement_status", 20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column(
            "request_read_receipt", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_announcements_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["destination_chat_id"],
            ["telegram_chats.id"],
            name="fk_announcements_destination_chat_id_telegram_chats",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_announcements"),
    )
    op.create_index("ix_announcements_status", "announcements", ["status"])

    op.create_table(
        "announcement_acknowledgements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("display_name", sa.String(length=300), nullable=True),
        sa.Column("needs_followup", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["announcement_id"],
            ["announcements.id"],
            name="fk_announcement_acknowledgements_announcement_id_announcements",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_announcement_acknowledgements_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_announcement_acknowledgements"),
        sa.UniqueConstraint(
            "announcement_id",
            "telegram_user_id",
            name="uq_announcement_acknowledgements_announcement_user",
        ),
    )
    op.create_index(
        "ix_announcement_acknowledgements_announcement_id",
        "announcement_acknowledgements",
        ["announcement_id"],
    )

    # --- the transactional outbox -----------------------------------------
    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("aggregate_type", sa.String(length=40), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_type", _enum(RECIPIENT_TYPES, "recipient_type", 30), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("registered_chat_id", sa.Uuid(), nullable=True),
        sa.Column("template_key", sa.String(length=60), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "privacy_classification",
            _enum(PRIVACY_LEVELS, "privacy_classification", 30),
            nullable=False,
        ),
        sa.Column("safe_payload_json", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "status",
            _enum(OUTBOX_STATUSES, "outbox_status", 30),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "last_error_category",
            _enum(FAILURE_CATEGORIES, "failure_category", 40),
            nullable=False,
            server_default="NONE",
        ),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name="fk_outbound_messages_recipient_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["registered_chat_id"],
            ["telegram_chats.id"],
            name="fk_outbound_messages_registered_chat_id_telegram_chats",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_outbound_messages_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbound_messages"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbound_messages_idempotency_key"),
    )
    op.create_index("ix_outbound_messages_status", "outbound_messages", ["status"])
    op.create_index("ix_outbound_messages_available_at", "outbound_messages", ["available_at"])
    op.create_index("ix_outbound_messages_claim", "outbound_messages", ["status", "available_at"])
    op.create_index(
        "ix_outbound_messages_aggregate", "outbound_messages", ["aggregate_type", "aggregate_id"]
    )

    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("outbound_message_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", _enum(DELIVERY_OUTCOMES, "delivery_outcome", 30), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "error_category",
            _enum(FAILURE_CATEGORIES, "failure_category", 40),
            nullable=False,
            server_default="NONE",
        ),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["outbound_message_id"],
            ["outbound_messages.id"],
            name="fk_delivery_attempts_outbound_message_id_outbound_messages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_delivery_attempts"),
    )
    op.create_index(
        "ix_delivery_attempts_outbound_message_id", "delivery_attempts", ["outbound_message_id"]
    )
    op.create_index(
        "ix_delivery_attempts_message_number",
        "delivery_attempts",
        ["outbound_message_id", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_table("delivery_attempts")
    op.drop_table("outbound_messages")
    op.drop_table("announcement_acknowledgements")
    op.drop_table("announcements")
    op.drop_table("telegram_chats")

    op.drop_column("users", "bot_blocked_at")
    op.drop_column("users", "private_delivery_failure_category")
    op.drop_column("users", "last_private_delivery_at")
    op.drop_column("users", "last_private_interaction_at")
    op.drop_column("users", "private_chat_available")
    op.drop_column("users", "telegram_private_chat_id")
