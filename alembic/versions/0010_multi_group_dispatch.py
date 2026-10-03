"""0.6.0a3: multi-group dispatch, durable drafts, long-message parts and aliases.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-31

Why this migration exists
-------------------------

Three things 0.6.0a2 could not represent, each of which produced a reported
defect rather than a missing feature:

* **A set of chosen recipients.** ``announcements.destination_chat_id`` is one
  nullable foreign key. "Gửi cho ba group" had nowhere to be recorded, so
  "Tất cả" expanded into nothing and the conversation asked again.
* **A confirmed decision with several independent outcomes.** One announcement
  row carried one status, so one group refusing the bot and eleven succeeding
  had no honest representation at all.
* **A long announcement.** Content was capped at 3000 characters and the rest
  was dropped silently.

Six new tables and four new ``telegram_chats`` columns follow from that, and
nothing else changes.

Strictly additive
-----------------

No column is dropped, no column is retyped, no enum value is renamed and no
previous migration file is touched. ``users``, ``hr_requests``, ``reminders``,
``telegram_chats``, ``outbound_messages``, ``announcements`` and every
historical announcement keep every row and every column they had. The new
``telegram_chats`` columns are all nullable, so existing registrations are valid
the moment this runs and no backfill is required.

``uq_dispatch_recipients_dispatch_chat`` is the load-bearing constraint. It is
what makes a double-tapped confirmation, a redelivered Telegram update and a
typed "Xác nhận" arriving alongside a pressed button produce **one** row per
destination rather than three - and, with
``uq_outbound_messages_idempotency_key`` from 0008, one outbox intent per
(dispatch, destination, version, part).

Downgrade
---------

Reverses exactly this revision: the six tables are dropped and the four columns
removed. **That loses data:**

* every open draft, so anybody mid-compose has to write their announcement
  again (nothing was sent, so nothing is inconsistent);
* every dispatch record and its per-destination outcome, so "gửi tới đâu, nơi
  nào lỗi" loses its history - the ``outbound_messages`` rows and their
  delivery attempts survive, so *what was sent where* is still reconstructable
  from the outbox, but the grouping into one logical announcement is not;
* every alias, tag and brand somebody entered, so natural naming falls back to
  display name and Telegram title.

Announcements, acknowledgements, outbox rows, delivery attempts, reminders, HR
and access control are **not** touched by the downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIVACY_CLASSIFICATIONS = (
    "PUBLIC_OPERATIONAL",
    "TEAM_OPERATIONAL",
    "MANAGEMENT_ONLY",
    "PERSONAL_PRIVATE",
    "SECRET",
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
DRAFT_STATUSES = ("CHOOSING", "PREVIEW", "CONFIRMED", "CANCELLED", "EXPIRED")
DISPATCH_STATUSES = ("QUEUED", "IN_PROGRESS", "COMPLETED", "PARTIALLY_FAILED", "FAILED")
DISPATCH_RECIPIENT_STATUSES = ("QUEUED", "DELIVERED", "PARTIAL_FAILURE", "FAILED", "SKIPPED")
DISPATCH_PART_STATUSES = ("QUEUED", "DELIVERED", "FAILED")
SELECTION_SOURCES = ("NAMED", "INFERRED", "ALL_REGISTERED", "BUTTON", "LIST_REFERENCE")


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    # --- telegram_chats: the metadata natural naming resolves against ------
    # All nullable. A group registered before this release keeps working and
    # simply answers to fewer names until somebody adds aliases.
    op.add_column("telegram_chats", sa.Column("aliases_text", sa.Text(), nullable=True))
    op.add_column("telegram_chats", sa.Column("tags_text", sa.Text(), nullable=True))
    op.add_column("telegram_chats", sa.Column("brand", sa.String(length=200), nullable=True))
    op.add_column(
        "telegram_chats", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
    )

    # --- message_dispatch_drafts ------------------------------------------
    op.create_table(
        "message_dispatch_drafts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("bot_identity", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column(
            "privacy_classification",
            _enum(PRIVACY_CLASSIFICATIONS, "privacy_classification", 30),
            nullable=False,
            server_default="PUBLIC_OPERATIONAL",
        ),
        sa.Column("unresolved_phrases", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(DRAFT_STATUSES, "dispatch_draft_status", 20),
            nullable=False,
            server_default="CHOOSING",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_message_dispatch_drafts_status", "message_dispatch_drafts", ["status"], unique=False
    )
    # The lookup every incoming message makes: "is there an open draft for this
    # person, in this chat, on this bot?"
    op.create_index(
        "ix_message_dispatch_drafts_open",
        "message_dispatch_drafts",
        ["bot_identity", "source_chat_id", "created_by_telegram_id", "status"],
        unique=False,
    )

    # --- message_dispatch_draft_recipients --------------------------------
    op.create_table(
        "message_dispatch_draft_recipients",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("message_dispatch_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_chat_row_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "selection_source",
            _enum(SELECTION_SOURCES, "dispatch_selection_source", 20),
            nullable=False,
            server_default="NAMED",
        ),
        sa.Column("permitted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_id", "recipient_chat_row_id", name="uq_dispatch_draft_recipients_draft_chat"
        ),
    )
    op.create_index(
        "ix_message_dispatch_draft_recipients_draft_id",
        "message_dispatch_draft_recipients",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        "ix_dispatch_draft_recipients_draft",
        "message_dispatch_draft_recipients",
        ["draft_id", "position"],
        unique=False,
    )

    # --- message_dispatches ------------------------------------------------
    op.create_table(
        "message_dispatches",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("bot_identity", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "privacy_classification",
            _enum(PRIVACY_CLASSIFICATIONS, "privacy_classification", 30),
            nullable=False,
            server_default="PUBLIC_OPERATIONAL",
        ),
        sa.Column(
            "status",
            _enum(DISPATCH_STATUSES, "dispatch_status", 20),
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retrying_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_parts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_message_dispatches_status", "message_dispatches", ["status"], unique=False)
    op.create_index(
        "ix_message_dispatches_status_created",
        "message_dispatches",
        ["status", "created_at"],
        unique=False,
    )

    # --- message_dispatch_parts -------------------------------------------
    op.create_table(
        "message_dispatch_parts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "dispatch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("message_dispatches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("total_parts", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "dispatch_id", "part_number", name="uq_dispatch_parts_dispatch_number"
        ),
    )
    op.create_index(
        "ix_message_dispatch_parts_dispatch_id",
        "message_dispatch_parts",
        ["dispatch_id"],
        unique=False,
    )

    # --- message_dispatch_recipients --------------------------------------
    op.create_table(
        "message_dispatch_recipients",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "dispatch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("message_dispatches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "telegram_chat_row_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("telegram_chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("destination_display_name", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            _enum(DISPATCH_RECIPIENT_STATUSES, "dispatch_recipient_status", 20),
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column("delivered_parts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_parts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failure_category",
            _enum(FAILURE_CATEGORIES, "failure_category", 40),
            nullable=False,
            server_default="NONE",
        ),
        sa.Column("attempt_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # One row per (dispatch, destination), whatever confirms it and however
        # often. This is what makes a duplicate confirmation harmless.
        sa.UniqueConstraint(
            "dispatch_id", "telegram_chat_row_id", name="uq_dispatch_recipients_dispatch_chat"
        ),
    )
    op.create_index(
        "ix_message_dispatch_recipients_dispatch_id",
        "message_dispatch_recipients",
        ["dispatch_id"],
        unique=False,
    )
    op.create_index(
        "ix_dispatch_recipients_status",
        "message_dispatch_recipients",
        ["dispatch_id", "status"],
        unique=False,
    )

    # --- message_dispatch_recipient_parts ----------------------------------
    op.create_table(
        "message_dispatch_recipient_parts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "recipient_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("message_dispatch_recipients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dispatch_part_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("message_dispatch_parts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("part_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "outbound_message_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("outbound_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            _enum(DISPATCH_PART_STATUSES, "dispatch_part_status", 20),
            nullable=False,
            server_default="QUEUED",
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "recipient_id",
            "dispatch_part_id",
            name="uq_dispatch_recipient_parts_recipient_part",
        ),
    )
    op.create_index(
        "ix_message_dispatch_recipient_parts_recipient_id",
        "message_dispatch_recipient_parts",
        ["recipient_id"],
        unique=False,
    )
    # The worker's settle lookup: "which business row does this delivered
    # outbox message belong to?"
    op.create_index(
        "ix_dispatch_recipient_parts_outbound",
        "message_dispatch_recipient_parts",
        ["outbound_message_id"],
        unique=False,
    )


def downgrade() -> None:
    """Reverse 0010. Read the data-loss note in the module docstring first."""
    op.drop_index(
        "ix_dispatch_recipient_parts_outbound", table_name="message_dispatch_recipient_parts"
    )
    op.drop_index(
        "ix_message_dispatch_recipient_parts_recipient_id",
        table_name="message_dispatch_recipient_parts",
    )
    op.drop_table("message_dispatch_recipient_parts")

    op.drop_index("ix_dispatch_recipients_status", table_name="message_dispatch_recipients")
    op.drop_index(
        "ix_message_dispatch_recipients_dispatch_id", table_name="message_dispatch_recipients"
    )
    op.drop_table("message_dispatch_recipients")

    op.drop_index("ix_message_dispatch_parts_dispatch_id", table_name="message_dispatch_parts")
    op.drop_table("message_dispatch_parts")

    op.drop_index("ix_message_dispatches_status_created", table_name="message_dispatches")
    op.drop_index("ix_message_dispatches_status", table_name="message_dispatches")
    op.drop_table("message_dispatches")

    op.drop_index(
        "ix_dispatch_draft_recipients_draft", table_name="message_dispatch_draft_recipients"
    )
    op.drop_index(
        "ix_message_dispatch_draft_recipients_draft_id",
        table_name="message_dispatch_draft_recipients",
    )
    op.drop_table("message_dispatch_draft_recipients")

    op.drop_index("ix_message_dispatch_drafts_open", table_name="message_dispatch_drafts")
    op.drop_index("ix_message_dispatch_drafts_status", table_name="message_dispatch_drafts")
    op.drop_table("message_dispatch_drafts")

    op.drop_column("telegram_chats", "last_used_at")
    op.drop_column("telegram_chats", "brand")
    op.drop_column("telegram_chats", "tags_text")
    op.drop_column("telegram_chats", "aliases_text")
