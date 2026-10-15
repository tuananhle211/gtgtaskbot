"""AI connections ("Kết nối AI"): ``ai_connections`` and ``ai_routes``.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-15

Until now the app talked to one AI, named in the environment (``LLM_*``). The
OWNER can now register several - ChatGPT (OpenAI), Gemini, DeepSeek, or any
OpenAI-compatible gateway such as a self-hosted 9router - and choose, per
purpose (default, AI script review of an order, PR review, the Telegram
assistant), which one answers and which take over when it fails.

* ``ai_connections`` - one AI: kind, endpoint, model, the API key encrypted
  with the deployment's secret key (only ``••••abcd`` in clear), on/off and
  the last connection test;
* ``ai_routes`` - per purpose, the ordered list of connection ids.

Empty tables change nothing: the environment provider keeps answering.

Downgrade
---------

Drops both tables (the keys stored in them are lost).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONNECTIONS = "ai_connections"
ROUTES = "ai_routes"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        CONNECTIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("api_key_hint", sa.String(length=12), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_ai_connections_name_not_empty")),
        sa.CheckConstraint(
            "length(trim(model)) > 0", name=op.f("ck_ai_connections_model_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_connections_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_connections")),
    )
    op.create_table(
        ROUTES,
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("connection_ids", _JSON, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("purpose", name=op.f("pk_ai_routes")),
    )


def downgrade() -> None:
    op.drop_table(ROUTES)
    op.drop_table(CONNECTIONS)
