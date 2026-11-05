"""Đội agent: ``agent_attachments`` - pictures, documents, skills sent to an agent.

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-05

A question to an agent may carry files (``application/agents/files.py``):
a picture goes to the agent as a picture; a PDF / Word / Excel / PowerPoint /
text file or a skill is read by APM and its words go with the question. The
bytes stay here (the person can open the file again), with the words the agent
got. ``message_id`` NULL = uploaded, not sent yet. Deleting a conversation
deletes its files (CASCADE).

Downgrade
---------

Drops the table (every attached file is lost; the messages stay).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_attachments"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_agent_attachments_size_positive")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name=op.f("fk_agent_attachments_conversation_id_agent_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["agent_messages.id"],
            name=op.f("fk_agent_attachments_message_id_agent_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_agent_attachments_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_attachments")),
    )
    op.create_index(
        "ix_agent_attachments_conversation_message", TABLE, ["conversation_id", "message_id"]
    )
    op.create_index("ix_agent_attachments_user_created", TABLE, ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_attachments_user_created", table_name=TABLE)
    op.drop_index("ix_agent_attachments_conversation_message", table_name=TABLE)
    op.drop_table(TABLE)
