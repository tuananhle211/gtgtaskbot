"""0.6.0A: leave and late-arrival requests, work calendar, Member list context.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-30

Notes:
* Strictly additive. No table is dropped, no column is removed, no enum value is
  renamed, and nothing in ``users``, ``scripts``, ``audit_logs`` or the social
  tables is touched.
* ``hr_requests.version`` is what makes an approval button safe: the button is
  signed against the version it was rendered for, so approving a request that
  has been amended since fails rather than approving text the owner never read.
* ``hr_request_events`` is append-only. Nothing in the application deletes an HR
  request; withdrawal and cancellation are status changes with an event row.
* ``work_schedules`` is intentionally *empty* after this migration. MeoBot must
  not assume when an office opens, so lateness cannot be computed until the
  owner configures it - and MeoBot says so instead of guessing.
* ``member_list_contexts`` is short-lived UI state ("việc số 2"), keyed per
  (bot, chat, person, kind) with a version and an expiry so a stale reference
  resolves to nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

HR_REQUEST_TYPES = (
    "FULL_DAY_LEAVE",
    "MORNING_LEAVE",
    "AFTERNOON_LEAVE",
    "HOURLY_LEAVE",
    "MULTI_DAY_LEAVE",
    "LATE_ARRIVAL",
)
HR_REQUEST_STATUSES = (
    "DRAFT",
    "PENDING",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
    "WITHDRAWN",
    "EXPIRED",
    "CHANGE_REQUESTED",
)
HR_EVENT_TYPES = (
    "CREATED",
    "SUBMITTED",
    "APPROVED",
    "REJECTED",
    "CHANGE_REQUESTED",
    "AMENDED",
    "WITHDRAWN",
    "CANCELLED",
    "EXPIRED",
)


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    op.create_table(
        "hr_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "request_type", _enum(HR_REQUEST_TYPES, "hr_request_type", 30), nullable=False
        ),
        sa.Column(
            "status",
            _enum(HR_REQUEST_STATUSES, "hr_request_status", 20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("expected_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("late_minutes", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("private_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"],
            ["users.id"],
            name="fk_hr_requests_requester_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name="fk_hr_requests_decided_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hr_requests"),
    )
    op.create_index("ix_hr_requests_requester_user_id", "hr_requests", ["requester_user_id"])
    op.create_index("ix_hr_requests_status", "hr_requests", ["status"])
    op.create_index("ix_hr_requests_work_date", "hr_requests", ["work_date"])
    op.create_index(
        "ix_hr_requests_requester_status", "hr_requests", ["requester_user_id", "status"]
    )
    op.create_index("ix_hr_requests_work_date_status", "hr_requests", ["work_date", "status"])

    op.create_table(
        "hr_request_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", _enum(HR_EVENT_TYPES, "hr_event_type", 30), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("state_before", sa.String(length=30), nullable=True),
        sa.Column("state_after", sa.String(length=30), nullable=True),
        sa.Column("event_metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["hr_requests.id"],
            name="fk_hr_request_events_request_id_hr_requests",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_hr_request_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hr_request_events"),
    )
    op.create_index("ix_hr_request_events_request_id", "hr_request_events", ["request_id"])
    op.create_index("ix_hr_request_events_created_at", "hr_request_events", ["created_at"])
    op.create_index(
        "ix_hr_request_events_request_created", "hr_request_events", ["request_id", "created_at"]
    )

    op.create_table(
        "work_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False, server_default="Mặc định"),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="Asia/Ho_Chi_Minh"
        ),
        sa.Column("working_days", JSONB, nullable=False, server_default="[]"),
        sa.Column("morning_start", sa.Time(), nullable=False),
        sa.Column("morning_end", sa.Time(), nullable=False),
        sa.Column("afternoon_start", sa.Time(), nullable=False),
        sa.Column("afternoon_end", sa.Time(), nullable=False),
        sa.Column("active_from", sa.Date(), nullable=True),
        sa.Column("active_until", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_work_schedules_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_schedules"),
    )
    op.create_index("ix_work_schedules_is_active", "work_schedules", ["is_active"])

    op.create_table(
        "organization_holidays",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_organization_holidays_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_holidays"),
        sa.UniqueConstraint("holiday_date", name="uq_organization_holidays_date"),
    )
    op.create_index("ix_organization_holidays_holiday_date", "organization_holidays", ["holiday_date"])

    op.create_table(
        "member_list_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("item_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_member_list_contexts"),
        sa.UniqueConstraint(
            "bot_id", "chat_id", "telegram_user_id", "kind", name="uq_member_list_contexts_key"
        ),
    )
    op.create_index("ix_member_list_contexts_chat_id", "member_list_contexts", ["chat_id"])
    op.create_index(
        "ix_member_list_contexts_telegram_user_id", "member_list_contexts", ["telegram_user_id"]
    )
    op.create_index("ix_member_list_contexts_expires_at", "member_list_contexts", ["expires_at"])


def downgrade() -> None:
    op.drop_table("member_list_contexts")
    op.drop_table("organization_holidays")
    op.drop_table("work_schedules")
    op.drop_table("hr_request_events")
    op.drop_table("hr_requests")
