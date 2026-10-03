"""0.6.0a2: reminders, deferred Guest questions, group assignments and health.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-30

Notes:
* Strictly additive. Nothing is dropped, no column removed, no enum value
  renamed, and no previous migration file is touched. ``users``,
  ``hr_requests``, the access-control tables from 0006 and the outbox from 0008
  keep every row and every column they had.
* Five new tables: ``reminders``, ``reminder_occurrences``,
  ``deferred_guest_messages``, ``telegram_chat_assignments`` and
  ``announcement_recipients``.
* ``uq_reminder_occurrences_reminder_moment`` is the load-bearing constraint of
  the release. The reminder sweep runs every 60 seconds; two Beat processes, an
  overlapping sweep, a retried task and a restarted worker all try to insert the
  same ``(reminder, instant)`` pair, and this is what makes exactly one of them
  win. Without it, "remind me every Thursday" eventually means twice.
* ``uq_deferred_guest_messages_request`` allows one held question per access
  request. A stranger who sends five messages while waiting does not receive
  five answers when approved - the owner read one question and approved that.
* ``telegram_chats`` gains six health columns and ``outbound_messages`` gains
  five failure-reporting columns. All have server defaults, so existing rows are
  valid immediately and no backfill is needed.

Downgrade drops the five new tables and the eleven new columns. **That loses
data, and the data it loses is not recoverable from anywhere else:**

* every reminder anybody has created, and its firing history;
* every held Guest question not yet answered;
* every Trưởng nhóm group assignment (broadcast permission reverts to none);
* every announcement audience snapshot, so "ai chưa đọc" loses its denominator
  for announcements already published;
* learned destination health, which rebuilds itself on the next sweep.

Acknowledgements, announcements, outbox rows and delivery history are **not**
touched by the downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTINATION_HEALTH = (
    "UNKNOWN",
    "HEALTHY",
    "CANNOT_SEND",
    "BOT_REMOVED",
    "NOT_FOUND",
    "PROVIDER_ERROR",
)
ASSIGNMENT_ROLES = ("MANAGER", "MEMBER")
DEFERRED_STATUSES = (
    "PENDING_APPROVAL",
    "AUTHORIZED_ONCE",
    "AUTHORIZED_GUEST",
    "PROCESSING",
    "RESPONSE_QUEUED",
    "ANSWERED",
    "REJECTED",
    "EXPIRED",
    "FAILED",
)
AUTHORIZATION_MODES = ("ANSWER_ONCE", "GUEST_WINDOW", "MEMBER")
REMINDER_DESTINATION_TYPES = ("USER_PRIVATE", "REGISTERED_CHAT")
SCHEDULE_KINDS = ("ONE_TIME", "DAILY", "WEEKLY")
REMINDER_STATUSES = ("ACTIVE", "PAUSED", "CANCELLED", "COMPLETED")
MISSED_POLICIES = ("DELIVER_LATEST", "SKIP_ALL")
OCCURRENCE_STATUSES = ("SCHEDULED", "QUEUED", "DELIVERED", "SKIPPED", "FAILED")


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    # --- telegram_chats: proactive health ---------------------------------
    op.add_column(
        "telegram_chats",
        sa.Column(
            "health_status",
            _enum(DESTINATION_HEALTH, "destination_health", 30),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column(
        "telegram_chats", sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "telegram_chats", sa.Column("last_unhealthy_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "telegram_chats",
        sa.Column("last_health_error_category", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "telegram_chats",
        sa.Column(
            "consecutive_health_failures", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "telegram_chats",
        sa.Column("health_version", sa.Integer(), nullable=False, server_default="1"),
    )

    # --- outbound_messages: proactive failure reporting -------------------
    op.add_column(
        "outbound_messages",
        sa.Column("failure_alert_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "outbound_messages",
        sa.Column("failure_alert_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbound_messages", sa.Column("destination_label", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "outbound_messages", sa.Column("business_summary", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "outbound_messages",
        sa.Column("is_alert", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # The failure sweep's query: terminal, not yet alerted, not itself an alert.
    op.create_index(
        "ix_outbound_messages_failure_alert",
        "outbound_messages",
        ["status", "failure_alert_sent"],
    )

    # --- group assignments ------------------------------------------------
    op.create_table(
        "telegram_chat_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_chat_row_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assignment_role",
            _enum(ASSIGNMENT_ROLES, "assignment_role", 20),
            nullable=False,
            server_default="MEMBER",
        ),
        sa.Column("can_broadcast", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "can_view_read_receipts", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("can_manage_audience", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["telegram_chat_row_id"],
            ["telegram_chats.id"],
            name="fk_telegram_chat_assignments_chat_telegram_chats",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_telegram_chat_assignments_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["users.id"],
            name="fk_telegram_chat_assignments_assigned_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_chat_assignments"),
        sa.UniqueConstraint(
            "telegram_chat_row_id", "user_id", name="uq_telegram_chat_assignments_chat_user"
        ),
    )
    op.create_index(
        "ix_telegram_chat_assignments_telegram_chat_row_id",
        "telegram_chat_assignments",
        ["telegram_chat_row_id"],
    )
    op.create_index(
        "ix_telegram_chat_assignments_user_id", "telegram_chat_assignments", ["user_id"]
    )
    op.create_index(
        "ix_telegram_chat_assignments_is_active", "telegram_chat_assignments", ["is_active"]
    )
    op.create_index(
        "ix_telegram_chat_assignments_user_active",
        "telegram_chat_assignments",
        ["user_id", "is_active"],
    )

    # --- announcement audience snapshot -----------------------------------
    op.create_table(
        "announcement_recipients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("display_name", sa.String(length=300), nullable=True),
        sa.Column("expected_at_publish_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["announcement_id"],
            ["announcements.id"],
            name="fk_announcement_recipients_announcement_announcements",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_announcement_recipients_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_announcement_recipients"),
        sa.UniqueConstraint(
            "announcement_id", "user_id", name="uq_announcement_recipients_announcement_user"
        ),
    )
    op.create_index(
        "ix_announcement_recipients_announcement_id",
        "announcement_recipients",
        ["announcement_id"],
    )

    # --- deferred Guest questions -----------------------------------------
    op.create_table(
        "deferred_guest_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pending_access_request_id", sa.Uuid(), nullable=False),
        sa.Column("bot_identity", sa.BigInteger(), nullable=False),
        sa.Column("original_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("original_message_id", sa.BigInteger(), nullable=False),
        sa.Column("original_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("sanitized_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_text_hash", sa.String(length=64), nullable=False),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            _enum(DEFERRED_STATUSES, "deferred_message_status", 30),
            nullable=False,
            server_default="PENDING_APPROVAL",
        ),
        sa.Column(
            "authorization_mode",
            _enum(AUTHORIZATION_MODES, "deferred_authorization_mode", 20),
            nullable=True,
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_category", sa.String(length=40), nullable=True),
        sa.Column("outbox_message_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["pending_access_request_id"],
            ["pending_guest_access_requests.id"],
            name="fk_deferred_guest_messages_request_pending",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["outbox_message_id"],
            ["outbound_messages.id"],
            name="fk_deferred_guest_messages_outbox_outbound",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deferred_guest_messages"),
        sa.UniqueConstraint(
            "pending_access_request_id", name="uq_deferred_guest_messages_request"
        ),
    )
    op.create_index(
        "ix_deferred_guest_messages_pending_access_request_id",
        "deferred_guest_messages",
        ["pending_access_request_id"],
    )
    op.create_index("ix_deferred_guest_messages_status", "deferred_guest_messages", ["status"])
    op.create_index(
        "ix_deferred_guest_messages_status_expiry",
        "deferred_guest_messages",
        ["status", "expires_at"],
    )

    # --- reminders --------------------------------------------------------
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "destination_type",
            _enum(REMINDER_DESTINATION_TYPES, "reminder_destination_type", 30),
            nullable=False,
            server_default="USER_PRIVATE",
        ),
        sa.Column("destination_user_id", sa.Uuid(), nullable=True),
        sa.Column("destination_chat_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="Asia/Ho_Chi_Minh"
        ),
        sa.Column(
            "schedule_kind", _enum(SCHEDULE_KINDS, "reminder_schedule_kind", 20), nullable=False
        ),
        sa.Column("recurrence_rule", sa.String(length=60), nullable=False),
        sa.Column("local_time", sa.Time(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            _enum(REMINDER_STATUSES, "reminder_status", 20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column(
            "missed_occurrence_policy",
            _enum(MISSED_POLICIES, "missed_occurrence_policy", 30),
            nullable=False,
            server_default="DELIVER_LATEST",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_reminders_owner_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_reminders_created_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["destination_user_id"],
            ["users.id"],
            name="fk_reminders_destination_user_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["destination_chat_id"],
            ["telegram_chats.id"],
            name="fk_reminders_destination_chat_telegram_chats",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reminders"),
    )
    op.create_index("ix_reminders_owner_user_id", "reminders", ["owner_user_id"])
    op.create_index("ix_reminders_status", "reminders", ["status"])
    op.create_index("ix_reminders_next_run_at", "reminders", ["next_run_at"])
    op.create_index("ix_reminders_due", "reminders", ["status", "next_run_at"])
    op.create_index("ix_reminders_owner_status", "reminders", ["owner_user_id", "status"])

    op.create_table(
        "reminder_occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reminder_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outbox_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            _enum(OCCURRENCE_STATUSES, "occurrence_status", 20),
            nullable=False,
            server_default="SCHEDULED",
        ),
        sa.Column("skip_reason", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["reminder_id"],
            ["reminders.id"],
            name="fk_reminder_occurrences_reminder_reminders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["outbox_message_id"],
            ["outbound_messages.id"],
            name="fk_reminder_occurrences_outbox_outbound",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reminder_occurrences"),
        # The duplicate defence. See the module docstring.
        sa.UniqueConstraint(
            "reminder_id", "scheduled_for", name="uq_reminder_occurrences_reminder_moment"
        ),
    )
    op.create_index("ix_reminder_occurrences_reminder_id", "reminder_occurrences", ["reminder_id"])
    op.create_index("ix_reminder_occurrences_status", "reminder_occurrences", ["status"])


def downgrade() -> None:
    """Reverse 0009. Read the data-loss note in the module docstring first."""
    op.drop_index("ix_reminder_occurrences_status", table_name="reminder_occurrences")
    op.drop_index("ix_reminder_occurrences_reminder_id", table_name="reminder_occurrences")
    op.drop_table("reminder_occurrences")

    op.drop_index("ix_reminders_owner_status", table_name="reminders")
    op.drop_index("ix_reminders_due", table_name="reminders")
    op.drop_index("ix_reminders_next_run_at", table_name="reminders")
    op.drop_index("ix_reminders_status", table_name="reminders")
    op.drop_index("ix_reminders_owner_user_id", table_name="reminders")
    op.drop_table("reminders")

    op.drop_index("ix_deferred_guest_messages_status_expiry", table_name="deferred_guest_messages")
    op.drop_index("ix_deferred_guest_messages_status", table_name="deferred_guest_messages")
    op.drop_index(
        "ix_deferred_guest_messages_pending_access_request_id",
        table_name="deferred_guest_messages",
    )
    op.drop_table("deferred_guest_messages")

    op.drop_index("ix_announcement_recipients_announcement_id", table_name="announcement_recipients")
    op.drop_table("announcement_recipients")

    op.drop_index(
        "ix_telegram_chat_assignments_user_active", table_name="telegram_chat_assignments"
    )
    op.drop_index("ix_telegram_chat_assignments_is_active", table_name="telegram_chat_assignments")
    op.drop_index("ix_telegram_chat_assignments_user_id", table_name="telegram_chat_assignments")
    op.drop_index(
        "ix_telegram_chat_assignments_telegram_chat_row_id",
        table_name="telegram_chat_assignments",
    )
    op.drop_table("telegram_chat_assignments")

    op.drop_index("ix_outbound_messages_failure_alert", table_name="outbound_messages")
    op.drop_column("outbound_messages", "is_alert")
    op.drop_column("outbound_messages", "business_summary")
    op.drop_column("outbound_messages", "destination_label")
    op.drop_column("outbound_messages", "failure_alert_at")
    op.drop_column("outbound_messages", "failure_alert_sent")

    op.drop_column("telegram_chats", "health_version")
    op.drop_column("telegram_chats", "consecutive_health_failures")
    op.drop_column("telegram_chats", "last_health_error_category")
    op.drop_column("telegram_chats", "last_unhealthy_at")
    op.drop_column("telegram_chats", "last_healthy_at")
    op.drop_column("telegram_chats", "health_status")
