"""One member code per person: ``users.member_code``.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-14

The member code ("mã nhân viên", the ``TUAN`` of ``TUAN-D-261014-01``) was
kept on each stream tag (``org_unit_members.member_code``): somebody in both
streams could carry two codes, and somebody in no stream had nowhere to keep
one. It is now the person's (``users.member_code``), edited by the OWNER and
the ADMIN only, and copied onto every open tag of theirs so whatever reads
the tag (order codes, rosters) reads the same code.

Backfill: the code of the person's ORD tag (open first, then the latest),
else of their PR tag. Then every open tag of a person with a code is aligned
to it - the ORD tag already carries it, so no future order code changes.

Downgrade
---------

Drops the column. The tags keep the codes they were aligned to.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("member_code", sa.String(20), nullable=True))
    op.execute(
        """
        UPDATE users AS u
        SET member_code = (
            SELECT m.member_code
            FROM org_unit_members AS m
            JOIN org_units AS o ON o.id = m.unit_id
            WHERE m.user_id = u.id
              AND m.member_code IS NOT NULL
              AND length(trim(m.member_code)) > 0
            ORDER BY (o.code = 'ADS') DESC, (m.left_at IS NULL) DESC, m.updated_at DESC
            LIMIT 1
        )
        """
    )
    op.execute(
        """
        UPDATE org_unit_members AS m
        SET member_code = u.member_code
        FROM users AS u
        WHERE u.id = m.user_id
          AND u.member_code IS NOT NULL
          AND m.left_at IS NULL
          AND m.member_code IS DISTINCT FROM u.member_code
        """
    )


def downgrade() -> None:
    op.drop_column("users", "member_code")
