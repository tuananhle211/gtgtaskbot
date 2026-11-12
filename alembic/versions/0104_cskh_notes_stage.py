"""Hộp chat CSKH: Pancake notes + customer codes, a conversation's stage, a page's look.

Revision ID: 0104
Revises: 0103
Create Date: 2026-11-12

From the prototype's 12/11 update (``CAP_NHAT_MA_KH_GHI_CHU.md``) and the inbox
redesign (``THIET_KE_HOP_CHAT.md``):

* ``cskh_conversations.notes`` (JSON ``[{id, text, by, at}]``) = Pancake's customer
  notes, read with every poll; ``cskh_conversation_codes`` (conversation, code) = the
  customer codes (``KH`` + digits) those notes hold, indexed by code;
* ``cskh_conversations.stage`` NEW / CONSULTING / CALLBACK / CLOSED ("Mới / Đang tư
  vấn / Chờ gọi lại / Đã chốt"): a conversation waiting for a call back is CALLBACK,
  one with a confirmed order CLOSED, one the page answered CONSULTING, else NEW;
* ``cskh_pages.short_name`` / ``color``: the page's short coloured label.

Downgrade
---------

Drops the three columns' data, the codes table and the page look.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "cskh_conversations",
        sa.Column("notes", _JSON, nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "cskh_conversations",
        sa.Column("stage", sa.String(12), nullable=False, server_default="NEW"),
    )
    op.add_column(
        "cskh_pages",
        sa.Column("short_name", sa.String(40), nullable=False, server_default=""),
    )
    op.add_column(
        "cskh_pages", sa.Column("color", sa.String(12), nullable=False, server_default="")
    )
    op.create_table(
        "cskh_conversation_codes",
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("cskh_conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cskh_conversation_codes_code", "cskh_conversation_codes", ["code"])
    # Existing rows got "[]"; new ones get it from the model, as every other JSON column.
    op.alter_column("cskh_conversations", "notes", server_default=None)
    op.execute(
        """
        UPDATE cskh_conversations SET stage = CASE
            WHEN callback THEN 'CALLBACK'
            WHEN EXISTS (
                SELECT 1 FROM cskh_orders o WHERE o.conversation_id = cskh_conversations.id
            ) THEN 'CLOSED'
            WHEN EXISTS (
                SELECT 1 FROM cskh_messages m
                WHERE m.conversation_id = cskh_conversations.id AND m.direction = 'out'
            ) THEN 'CONSULTING'
            ELSE 'NEW'
        END
        """
    )


def downgrade() -> None:
    op.drop_index("ix_cskh_conversation_codes_code", table_name="cskh_conversation_codes")
    op.drop_table("cskh_conversation_codes")
    op.drop_column("cskh_pages", "color")
    op.drop_column("cskh_pages", "short_name")
    op.drop_column("cskh_conversations", "stage")
    op.drop_column("cskh_conversations", "notes")
