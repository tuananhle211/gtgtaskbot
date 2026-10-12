"""Telegram ids that did not fit: two creator columns widened to BIGINT.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-12

``invite_codes.created_by_telegram_id`` and
``sheet_profiles.created_by_telegram_id`` were 32-bit. A Telegram account id
above 2 147 483 647 (every newer account) overflowed them: creating an invite
failed with "integer out of range" - silently on Telegram - for anybody but
the holders of an old, small id.

Downgrade
---------

Narrows them back. It fails if a wide id has been stored by then.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("invite_codes", "sheet_profiles")


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "created_by_telegram_id",
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=True,
            )


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "created_by_telegram_id",
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=True,
            )
