"""Milestone 5: group access control, Guest access and the daily chat quota.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-30

Notes:
* Strictly additive. No column is dropped, no row is deleted, no enum value is
  renamed. ``users.role`` keeps its four values (``OWNER``, ``ADMIN``,
  ``TEAM_LEAD``, ``EMPLOYEE``) exactly as ``0001`` created them.
* ``users`` gains a lifecycle (``status`` and its audit columns) rather than a
  second identity table: a suspended member must remain the same row their
  scripts, approvals and audit entries already point at. Existing rows are
  backfilled from the older ``active`` flag, so nobody's access changes when
  this runs.
* ``observed_telegram_users`` records people MeoBot has *seen*. Being seen is
  not being registered - there is no role column here, and no foreign key into
  ``users``.
* ``group_member_response_policies`` is per (bot, chat, user). No row means
  "inherit", which is the behaviour that existed before this table, so the
  migration changes nothing for anybody until an owner sets a policy.
* ``daily_ai_usage.quota_date`` is a **local** calendar date
  (``Asia/Ho_Chi_Minh``), already converted by the application. That is what
  makes the midnight reset need no scheduled task.
* The self-referencing foreign keys on ``users`` (``suspended_by_user_id`` and
  friends) use ``ON DELETE SET NULL``, but nothing in the application deletes a
  user - revocation is a status change.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

USER_STATUS_VALUES = ("pending", "active", "suspended", "revoked")
GROUP_POLICY_MODES = ("inherit", "allow", "guest", "ignore", "mute_until")
PENDING_STATUS_VALUES = ("open", "approved", "rejected", "expired")
ACCESS_ACTION_VALUES = ("ao", "gg", "am", "io", "ig", "cm")
QUOTA_ACTION_VALUES = ("q1", "qr", "qs", "qd")


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR + CHECK, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    # --- users: lifecycle -------------------------------------------------
    op.add_column(
        "users",
        sa.Column(
            "status",
            _enum(USER_STATUS_VALUES, "user_status", 20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("suspended_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("users", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("users", sa.Column("status_reason", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("last_status_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("added_by_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_users_suspended_by_user_id_users",
        "users",
        "users",
        ["suspended_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_users_revoked_by_user_id_users",
        "users",
        "users",
        ["revoked_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_users_added_by_user_id_users",
        "users",
        "users",
        ["added_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_status", "users", ["status"])
    # Backfill from the pre-existing flag so no live account changes state.
    op.execute("UPDATE users SET status = 'suspended' WHERE active = false")

    # --- observed Telegram users -----------------------------------------
    op.create_table(
        "observed_telegram_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("latest_username", sa.String(length=100), nullable=True),
        sa.Column("latest_display_name", sa.String(length=300), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("extra_metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_observed_telegram_users"),
        sa.UniqueConstraint(
            "telegram_user_id", name="uq_observed_telegram_users_telegram_user_id"
        ),
    )
    op.create_index(
        "ix_observed_telegram_users_telegram_user_id",
        "observed_telegram_users",
        ["telegram_user_id"],
    )

    # --- per-group response policy ---------------------------------------
    op.create_table(
        "group_member_response_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "mode",
            _enum(GROUP_POLICY_MODES, "group_policy_mode", 20),
            nullable=False,
            server_default="inherit",
        ),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_question_limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("guest_questions_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("guest_reserved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("guest_exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_group_member_response_policies_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name="fk_group_member_response_policies_revoked_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_group_member_response_policies"),
        sa.UniqueConstraint(
            "bot_id",
            "telegram_chat_id",
            "telegram_user_id",
            name="uq_group_member_response_policies_bot_chat_user",
        ),
    )
    op.create_index(
        "ix_group_member_response_policies_telegram_chat_id",
        "group_member_response_policies",
        ["telegram_chat_id"],
    )
    op.create_index(
        "ix_group_member_response_policies_telegram_user_id",
        "group_member_response_policies",
        ["telegram_user_id"],
    )
    op.create_index(
        "ix_group_member_response_policies_chat_mode",
        "group_member_response_policies",
        ["telegram_chat_id", "mode"],
    )

    # --- pending access requests -----------------------------------------
    op.create_table(
        "pending_guest_access_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_title", sa.String(length=300), nullable=True),
        sa.Column("requester_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("requester_username", sa.String(length=100), nullable=True),
        sa.Column("requester_display_name", sa.String(length=300), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("question_preview", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "status",
            _enum(PENDING_STATUS_VALUES, "pending_request_status", 20),
            nullable=False,
            server_default="open",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_owner_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("notify_cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_action", _enum(ACCESS_ACTION_VALUES, "access_action", 10), nullable=True
        ),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name="fk_pending_guest_access_requests_resolved_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pending_guest_access_requests"),
    )
    op.create_index(
        "ix_pending_guest_access_requests_telegram_chat_id",
        "pending_guest_access_requests",
        ["telegram_chat_id"],
    )
    op.create_index(
        "ix_pending_guest_access_requests_requester_telegram_id",
        "pending_guest_access_requests",
        ["requester_telegram_id"],
    )
    op.create_index(
        "ix_pending_guest_access_requests_open_key",
        "pending_guest_access_requests",
        ["bot_id", "telegram_chat_id", "requester_telegram_id", "status"],
    )

    # --- daily chat quota -------------------------------------------------
    op.create_table(
        "daily_ai_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("base_limit", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("temporary_bonus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("persistent_override", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("oldest_reservation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_daily_ai_usage_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_daily_ai_usage"),
        sa.UniqueConstraint("user_id", "quota_date", name="uq_daily_ai_usage_user_id_quota_date"),
    )
    op.create_index("ix_daily_ai_usage_user_id", "daily_ai_usage", ["user_id"])
    op.create_index("ix_daily_ai_usage_quota_date", "daily_ai_usage", ["quota_date"])

    op.create_table(
        "user_quota_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_quota_overrides_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_user_quota_overrides_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_quota_overrides"),
        sa.UniqueConstraint("user_id", name="uq_user_quota_overrides_user_id"),
    )
    op.create_index("ix_user_quota_overrides_user_id", "user_quota_overrides", ["user_id"])

    op.create_table(
        "quota_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("requester_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("requester_display_name", sa.String(length=300), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("limit_at_request", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            _enum(PENDING_STATUS_VALUES, "pending_request_status", 20),
            nullable=False,
            server_default="open",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_action", _enum(QUOTA_ACTION_VALUES, "quota_action", 10), nullable=True),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_quota_requests_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name="fk_quota_requests_resolved_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quota_requests"),
        sa.UniqueConstraint("user_id", "quota_date", name="uq_quota_requests_user_id_quota_date"),
    )
    op.create_index("ix_quota_requests_user_id", "quota_requests", ["user_id"])
    op.create_index("ix_quota_requests_status", "quota_requests", ["status"])


def downgrade() -> None:
    op.drop_table("quota_requests")
    op.drop_table("user_quota_overrides")
    op.drop_table("daily_ai_usage")
    op.drop_table("pending_guest_access_requests")
    op.drop_table("group_member_response_policies")
    op.drop_table("observed_telegram_users")

    op.drop_index("ix_users_status", table_name="users")
    op.drop_constraint("fk_users_added_by_user_id_users", "users", type_="foreignkey")
    op.drop_constraint("fk_users_revoked_by_user_id_users", "users", type_="foreignkey")
    op.drop_constraint("fk_users_suspended_by_user_id_users", "users", type_="foreignkey")
    op.drop_column("users", "added_by_user_id")
    op.drop_column("users", "last_status_changed_at")
    op.drop_column("users", "status_reason")
    op.drop_column("users", "revoked_by_user_id")
    op.drop_column("users", "revoked_at")
    op.drop_column("users", "suspended_by_user_id")
    op.drop_column("users", "suspended_at")
    op.drop_column("users", "status")
