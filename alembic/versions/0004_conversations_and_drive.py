"""Milestone 3: persistent chat memory, Drive folders, sheet templates.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29

Notes:
* ``conversation_threads`` / ``conversation_messages`` / ``conversation_summaries``
  are separate from ``conversation_states``. The latter holds the FSM state of
  one guided workflow and expires; overloading it with chat history would mean
  an abandoned /add_sheet takes the conversation with it.
* ``drive_folders`` is the allow-list that makes Drive writes acceptable:
  MeoBot can only create files inside a folder an OWNER/ADMIN registered.
* ``created_spreadsheets.idempotency_key`` is UNIQUE and is written *before*
  Google is called - that constraint is what stops a Celery retry or a
  double-tapped inline button producing a second file.
* Enum-like columns stay VARCHAR (no CHECK here): the vocabularies are still
  settling, and a new validation status must not need an ALTER TYPE.
* No table here is ever deleted from by application code; there is no Drive
  deletion path at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # --- Conversation memory ---------------------------------------------
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_threads"),
    )
    op.create_index("ix_conversation_threads_chat_id", "conversation_threads", ["chat_id"])
    op.create_index(
        "ix_conversation_threads_telegram_user_id", "conversation_threads", ["telegram_user_id"]
    )
    op.create_index(
        "ix_conversation_threads_active_key",
        "conversation_threads",
        ["bot_id", "chat_id", "telegram_user_id", "active"],
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("related_tool_name", sa.String(length=100), nullable=True),
        sa.Column("related_entity_type", sa.String(length=50), nullable=True),
        sa.Column("related_entity_id", sa.String(length=200), nullable=True),
        sa.Column("token_count_estimate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["conversation_threads.id"],
            name="fk_conversation_messages_thread_id_conversation_threads",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_messages"),
    )
    op.create_index("ix_conversation_messages_thread_id", "conversation_messages", ["thread_id"])
    op.create_index("ix_conversation_messages_created_at", "conversation_messages", ["created_at"])
    op.create_index(
        "ix_conversation_messages_thread_created",
        "conversation_messages",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["conversation_threads.id"],
            name="fk_conversation_summaries_thread_id_conversation_threads",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_summaries"),
        sa.UniqueConstraint("thread_id", name="uq_conversation_summaries_thread_id"),
    )
    op.create_index("ix_conversation_summaries_thread_id", "conversation_summaries", ["thread_id"])

    # --- Drive folders ----------------------------------------------------
    op.create_table(
        "drive_folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("drive_folder_id", sa.String(length=200), nullable=False),
        sa.Column("shared_drive_id", sa.String(length=200), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("path_label", sa.String(length=500), nullable=True),
        sa.Column("purpose", sa.String(length=300), nullable=True),
        sa.Column("team_scope", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "validation_status",
            sa.String(length=30),
            nullable=False,
            server_default="unvalidated",
        ),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_drive_folders_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_drive_folders"),
        sa.UniqueConstraint("drive_folder_id", name="uq_drive_folders_drive_folder_id"),
    )
    op.create_index("ix_drive_folders_drive_folder_id", "drive_folders", ["drive_folder_id"])

    # --- Sheet templates --------------------------------------------------
    op.create_table(
        "sheet_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("source_file_id", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_folder_id", sa.Uuid(), nullable=True),
        sa.Column("default_worksheet_name", sa.String(length=200), nullable=False),
        sa.Column("expected_tabs", JSONB, nullable=False, server_default="{}"),
        sa.Column("default_field_mapping", JSONB, nullable=False, server_default="{}"),
        sa.Column("default_write_back_mapping", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["default_folder_id"],
            ["drive_folders.id"],
            name="fk_sheet_templates_default_folder_id_drive_folders",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_sheet_templates_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sheet_templates"),
        sa.UniqueConstraint("code", "version", name="uq_sheet_templates_code_version"),
    )
    op.create_index("ix_sheet_templates_code", "sheet_templates", ["code"])
    op.create_index("ix_sheet_templates_kind", "sheet_templates", ["kind"])

    # --- Created spreadsheets --------------------------------------------
    op.create_table(
        "created_spreadsheets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("drive_file_id", sa.String(length=200), nullable=True),
        sa.Column("spreadsheet_id", sa.String(length=200), nullable=True),
        sa.Column("spreadsheet_url", sa.String(length=500), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("parent_folder_id", sa.String(length=200), nullable=True),
        sa.Column("shared_drive_id", sa.String(length=200), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False, server_default="work_management"),
        sa.Column("template_code", sa.String(length=100), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("creation_method", sa.String(length=30), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("sheet_profile_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("creation_status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("creation_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["sheet_templates.id"],
            name="fk_created_spreadsheets_template_id_sheet_templates",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_created_spreadsheets_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sheet_profile_id"],
            ["sheet_profiles.id"],
            name="fk_created_spreadsheets_sheet_profile_id_sheet_profiles",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_created_spreadsheets"),
        # The duplicate-prevention constraints. Both are load-bearing.
        sa.UniqueConstraint("idempotency_key", name="uq_created_spreadsheets_idempotency_key"),
        sa.UniqueConstraint("drive_file_id", name="uq_created_spreadsheets_drive_file_id"),
    )
    op.create_index("ix_created_spreadsheets_template_id", "created_spreadsheets", ["template_id"])
    op.create_index(
        "ix_created_spreadsheets_status_created",
        "created_spreadsheets",
        ["creation_status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("created_spreadsheets")
    op.drop_table("sheet_templates")
    op.drop_table("drive_folders")
    op.drop_table("conversation_summaries")
    op.drop_table("conversation_messages")
    op.drop_table("conversation_threads")
