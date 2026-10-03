"""Initial schema: users, settings, audit, script types, sheet profiles, confirmations.

Revision ID: 0001
Revises:
Create Date: 2026-07-28

Notes:
* Enum-like columns are VARCHAR + CHECK (``native_enum=False``) so adding a
  workflow state is an ordinary data change, not an ``ALTER TYPE``.
* All timestamps are ``TIMESTAMPTZ`` and written in UTC.
* Constraint names are spelled out to match the metadata naming convention.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_VALUES = ("OWNER", "ADMIN", "TEAM_LEAD", "EMPLOYEE")
AUDIT_RESULT_VALUES = ("success", "denied", "failed", "pending_confirmation")
CONFIRMATION_STATE_VALUES = ("pending", "confirmed", "rejected", "expired")

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_username", sa.String(length=100), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum(*ROLE_VALUES, name="role", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"])

    op.create_table(
        "system_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_system_settings_updated_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_system_settings"),
        sa.UniqueConstraint("key", name="uq_system_settings_key"),
    )
    op.create_index("ix_system_settings_key", "system_settings", ["key"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=True),
        sa.Column("entity_id", sa.String(length=200), nullable=True),
        sa.Column("before_data", JSONB, nullable=True),
        sa.Column("after_data", JSONB, nullable=True),
        sa.Column(
            "result",
            sa.Enum(*AUDIT_RESULT_VALUES, name="audit_result", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_action_created_at", "audit_logs", ["action", "created_at"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])

    op.create_table(
        "script_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_script_types"),
        sa.UniqueConstraint("code", name="uq_script_types_code"),
    )
    op.create_index("ix_script_types_code", "script_types", ["code"])

    op.create_table(
        "script_type_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("script_type_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", JSONB, nullable=False),
        sa.Column("review_rubric", JSONB, nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["script_type_id"],
            ["script_types.id"],
            name="fk_script_type_versions_script_type_id_script_types",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_script_type_versions"),
        sa.UniqueConstraint(
            "script_type_id", "version", name="uq_script_type_versions_type_version"
        ),
    )
    op.create_index(
        "ix_script_type_versions_script_type_id", "script_type_versions", ["script_type_id"]
    )

    op.create_table(
        "sheet_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("spreadsheet_id", sa.String(length=200), nullable=False),
        sa.Column("sheet_name", sa.String(length=200), nullable=False),
        sa.Column("header_row", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=100), nullable=True),
        sa.Column("script_type_id", sa.Uuid(), nullable=True),
        sa.Column("field_mapping", JSONB, nullable=False),
        sa.Column("status_mapping", JSONB, nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["script_type_id"],
            ["script_types.id"],
            name="fk_sheet_profiles_script_type_id_script_types",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sheet_profiles"),
        sa.UniqueConstraint(
            "spreadsheet_id", "sheet_name", name="uq_sheet_profiles_spreadsheet_tab"
        ),
    )
    op.create_index("ix_sheet_profiles_spreadsheet_id", "sheet_profiles", ["spreadsheet_id"])
    op.create_index("ix_sheet_profiles_script_type_id", "sheet_profiles", ["script_type_id"])

    op.create_table(
        "confirmation_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action_plan", JSONB, nullable=False),
        sa.Column("confirmation_token", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                *CONFIRMATION_STATE_VALUES,
                name="confirmation_state",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_confirmation_requests_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_confirmation_requests"),
        sa.UniqueConstraint(
            "confirmation_token", name="uq_confirmation_requests_confirmation_token"
        ),
    )
    op.create_index("ix_confirmation_requests_user_id", "confirmation_requests", ["user_id"])
    op.create_index(
        "ix_confirmation_requests_telegram_user_id", "confirmation_requests", ["telegram_user_id"]
    )
    op.create_index(
        "ix_confirmation_requests_confirmation_token",
        "confirmation_requests",
        ["confirmation_token"],
    )
    op.create_index(
        "ix_confirmation_requests_idempotency_key", "confirmation_requests", ["idempotency_key"]
    )


def downgrade() -> None:
    op.drop_table("confirmation_requests")
    op.drop_table("sheet_profiles")
    op.drop_table("script_type_versions")
    op.drop_table("script_types")
    op.drop_table("audit_logs")
    op.drop_table("system_settings")
    op.drop_table("users")
