"""Pancake → agent trả lời khách: ``pancake_pages`` + conversations + replies.

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-09

A Facebook page managed in Pancake posts its customers' messages to APM, which
asks a Hermes agent (``chatpage``) and keeps the answer as a suggestion or
sends it. A page's token is stored encrypted; nothing about the customer is
stored (no name, phone, or words - only Pancake's ids). Deleting a page
deletes its conversations and replies (CASCADE).

Downgrade
---------

Drops the three tables (pages, their tokens and every reply are lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAGES = "pancake_pages"
CONVERSATIONS = "pancake_conversations"
REPLIES = "pancake_replies"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        PAGES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("agent_slug", sa.String(length=40), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("access_token_hint", sa.String(length=12), nullable=False),
        sa.Column("webhook_key", sa.String(length=64), nullable=False),
        sa.Column("handoff_tag_ids", _JSON, nullable=False),
        sa.Column(
            "stop_when_assigned", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_ok", sa.Boolean(), nullable=True),
        sa.Column("last_check_message", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "length(trim(page_id)) > 0", name=op.f("ck_pancake_pages_page_id_not_empty")
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_pancake_pages_name_not_empty")),
        _fk(PAGES, "created_by_user_id", "users", "RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pancake_pages")),
        sa.UniqueConstraint("page_id", name=op.f("uq_pancake_pages_page_id")),
        sa.UniqueConstraint("webhook_key", name=op.f("uq_pancake_pages_webhook_key")),
    )

    op.create_table(
        CONVERSATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_row_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.String(length=160), nullable=False),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.String(length=40), nullable=True),
        sa.Column("tag_ids", _JSON, nullable=False),
        sa.Column("assignee_ids", _JSON, nullable=False),
        sa.Column("seen_message_id", sa.String(length=160), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_ids", _JSON, nullable=False),
        *_stamps(),
        _fk(CONVERSATIONS, "page_row_id", PAGES, "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pancake_conversations")),
        sa.UniqueConstraint(
            "page_row_id",
            "conversation_id",
            name=op.f("uq_pancake_conversations_page_row_id_conversation_id"),
        ),
    )

    op.create_table(
        REPLIES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_row_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_row_id", sa.Uuid(), nullable=False),
        sa.Column("customer_message_id", sa.String(length=160), nullable=False),
        sa.Column("customer_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("suggested_text", sa.Text(), nullable=True),
        sa.Column("sent_text", sa.Text(), nullable=True),
        sa.Column("handoff", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("handoff_reason", sa.String(length=200), nullable=True),
        sa.Column("violations", _JSON, nullable=False),
        sa.Column("review_passed", sa.Boolean(), nullable=True),
        sa.Column("review_note", sa.String(length=300), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_id", sa.String(length=160), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        *_stamps(),
        _fk(REPLIES, "page_row_id", PAGES, "CASCADE"),
        _fk(REPLIES, "conversation_row_id", CONVERSATIONS, "CASCADE"),
        _fk(REPLIES, "decided_by_user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pancake_replies")),
        sa.UniqueConstraint(
            "page_row_id",
            "customer_message_id",
            name=op.f("uq_pancake_replies_page_row_id_customer_message_id"),
        ),
    )
    op.create_index("ix_pancake_replies_status_created", REPLIES, ["status", "created_at"])
    op.create_index(
        "ix_pancake_replies_conversation", REPLIES, ["conversation_row_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_pancake_replies_conversation", table_name=REPLIES)
    op.drop_index("ix_pancake_replies_status_created", table_name=REPLIES)
    op.drop_table(REPLIES)
    op.drop_table(CONVERSATIONS)
    op.drop_table(PAGES)
