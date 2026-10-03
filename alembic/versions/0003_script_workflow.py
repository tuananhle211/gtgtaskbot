"""Milestone 2: scripts, versions, reviews, approvals, invites, conversations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-28

Notes:
* ``sheet_profiles`` gains the milestone-2 columns (state, write-back mapping,
  provenance, sync bookkeeping). Existing rows are backfilled to ``active``
  so a milestone-1 profile keeps working.
* ``scripts.current_version_id`` and ``script_versions.script_id`` reference
  each other, so the former FK is added after both tables exist.
* Enum-like columns stay VARCHAR + CHECK, matching migration 0001.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

SCRIPT_STATUS_VALUES = (
    "draft",
    "imported",
    "submitted_for_review",
    "reviewing",
    "ai_reviewed",
    "waiting_for_script_approval",
    "revision_required",
    "approved_for_production",
    "in_production",
    "archived",
)
SHEET_PROFILE_STATE_VALUES = ("needs_mapping", "active", "schema_changed", "inactive")
REVIEW_VERDICT_VALUES = ("approve", "minor_revision", "major_revision", "reject")
APPROVAL_ACTION_VALUES = ("approve_for_production", "request_revision")
ROLE_VALUES = ("OWNER", "ADMIN", "TEAM_LEAD", "EMPLOYEE")


def _script_status(name: str) -> sa.Enum:
    return sa.Enum(*SCRIPT_STATUS_VALUES, name=name, native_enum=False, length=40)


def upgrade() -> None:
    # --- sheet_profiles: milestone-2 columns ------------------------------
    op.add_column("sheet_profiles", sa.Column("spreadsheet_url", sa.String(length=500), nullable=True))
    op.add_column(
        "sheet_profiles",
        sa.Column(
            "state",
            sa.Enum(
                *SHEET_PROFILE_STATE_VALUES,
                name="sheet_profile_state",
                native_enum=False,
                length=30,
            ),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "sheet_profiles",
        sa.Column("write_back_mapping", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "sheet_profiles",
        sa.Column("last_headers", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("sheet_profiles", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.add_column("sheet_profiles", sa.Column("created_by_telegram_id", sa.Integer(), nullable=True))
    op.add_column(
        "sheet_profiles", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sheet_profiles", sa.Column("last_sync_status", sa.String(length=30), nullable=True))
    op.add_column("sheet_profiles", sa.Column("last_sync_error", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_sheet_profiles_created_by_users",
        "sheet_profiles",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- scripts ----------------------------------------------------------
    op.create_table(
        "scripts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sheet_profile_id", sa.Uuid(), nullable=True),
        sa.Column("external_script_id", sa.String(length=200), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("script_type_id", sa.Uuid(), nullable=True),
        sa.Column("status", _script_status("script_status"), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("channel", sa.String(length=100), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_scripts"),
        sa.ForeignKeyConstraint(
            ["sheet_profile_id"],
            ["sheet_profiles.id"],
            name="fk_scripts_sheet_profile_id_sheet_profiles",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["script_type_id"],
            ["script_types.id"],
            name="fk_scripts_script_type_id_script_types",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "sheet_profile_id", "external_script_id", name="uq_scripts_profile_external_id"
        ),
    )
    op.create_index("ix_scripts_sheet_profile_id", "scripts", ["sheet_profile_id"])
    op.create_index("ix_scripts_script_type_id", "scripts", ["script_type_id"])
    op.create_index("ix_scripts_status", "scripts", ["status"])
    op.create_index("ix_scripts_status_updated_at", "scripts", ["status", "updated_at"])

    op.create_table(
        "script_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("script_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("hook", sa.Text(), nullable=True),
        sa.Column("script_body", sa.Text(), nullable=False),
        sa.Column("production_notes", sa.Text(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("raw_source_data", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_script_versions"),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
            name="fk_script_versions_script_id_scripts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "script_id", "version_number", name="uq_script_versions_script_version"
        ),
    )
    op.create_index("ix_script_versions_script_id", "script_versions", ["script_id"])
    op.create_index("ix_script_versions_source_hash", "script_versions", ["script_id", "source_hash"])

    # Circular reference: added once both tables exist.
    op.create_foreign_key(
        "fk_scripts_current_version_id_script_versions",
        "scripts",
        "script_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )

    # --- reviews ----------------------------------------------------------
    op.create_table(
        "script_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("script_id", sa.Uuid(), nullable=False),
        sa.Column("script_version_id", sa.Uuid(), nullable=False),
        sa.Column("script_type_version_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("rubric_snapshot", JSONB, nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum(*REVIEW_VERDICT_VALUES, name="review_verdict", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("strengths", JSONB, nullable=False),
        sa.Column("critical_issues", JSONB, nullable=False),
        sa.Column("recommendations", JSONB, nullable=False),
        sa.Column("revised_hook_suggestion", sa.Text(), nullable=True),
        sa.Column("revised_script_suggestion", sa.Text(), nullable=True),
        sa.Column("structured_response", JSONB, nullable=False),
        sa.Column("usage_metadata", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_script_reviews"),
        sa.ForeignKeyConstraint(
            ["script_id"], ["scripts.id"], name="fk_script_reviews_script_id_scripts", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["script_version_id"],
            ["script_versions.id"],
            name="fk_script_reviews_script_version_id_script_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["script_type_version_id"],
            ["script_type_versions.id"],
            name="fk_script_reviews_script_type_version_id_script_type_versions",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_script_reviews_script_id", "script_reviews", ["script_id"])
    op.create_index("ix_script_reviews_script_version_id", "script_reviews", ["script_version_id"])
    op.create_index("ix_script_reviews_created_at", "script_reviews", ["created_at"])

    # --- approvals --------------------------------------------------------
    op.create_table(
        "script_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("script_id", sa.Uuid(), nullable=False),
        sa.Column("script_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(*APPROVAL_ACTION_VALUES, name="approval_action", native_enum=False, length=40),
            nullable=False,
        ),
        sa.Column("status_before", _script_status("script_status"), nullable=False),
        sa.Column("status_after", _script_status("script_status"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_script_approvals"),
        sa.ForeignKeyConstraint(
            ["script_id"], ["scripts.id"], name="fk_script_approvals_script_id_scripts", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["script_version_id"],
            ["script_versions.id"],
            name="fk_script_approvals_script_version_id_script_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_script_approvals_actor_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_script_approvals_script_id", "script_approvals", ["script_id"])
    op.create_index(
        "ix_script_approvals_script_version_id", "script_approvals", ["script_version_id"]
    )
    op.create_index("ix_script_approvals_created_at", "script_approvals", ["created_at"])

    # --- invites ----------------------------------------------------------
    op.create_table(
        "invite_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "role",
            sa.Enum(*ROLE_VALUES, name="role", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_by_telegram_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invite_codes"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_invite_codes_created_by_users", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("code_hash", name="uq_invite_codes_code_hash"),
    )
    op.create_index("ix_invite_codes_code_hash", "invite_codes", ["code_hash"])

    # --- conversation states ---------------------------------------------
    op.create_table(
        "conversation_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("destiny", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=200), nullable=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_states"),
        sa.UniqueConstraint(
            "bot_id", "chat_id", "telegram_user_id", "destiny", name="uq_conversation_states_key"
        ),
    )
    op.create_index("ix_conversation_states_chat_id", "conversation_states", ["chat_id"])
    op.create_index(
        "ix_conversation_states_telegram_user_id", "conversation_states", ["telegram_user_id"]
    )
    op.create_index("ix_conversation_states_expires_at", "conversation_states", ["expires_at"])


def downgrade() -> None:
    op.drop_table("conversation_states")
    op.drop_table("invite_codes")
    op.drop_table("script_approvals")
    op.drop_table("script_reviews")
    op.drop_constraint(
        "fk_scripts_current_version_id_script_versions", "scripts", type_="foreignkey"
    )
    op.drop_table("script_versions")
    op.drop_table("scripts")

    op.drop_constraint("fk_sheet_profiles_created_by_users", "sheet_profiles", type_="foreignkey")
    for column in (
        "last_sync_error",
        "last_sync_status",
        "last_synced_at",
        "created_by_telegram_id",
        "created_by",
        "last_headers",
        "write_back_mapping",
        "state",
        "spreadsheet_url",
    ):
        op.drop_column("sheet_profiles", column)
