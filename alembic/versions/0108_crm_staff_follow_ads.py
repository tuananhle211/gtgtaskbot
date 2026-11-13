"""Tài chính's CRM people follow Phòng Ads' links (data only).

Revision ID: 0108
Revises: 0107
Create Date: 2026-11-13

User 13/11: the CRM accounts linked on Phòng Ads must be linked the same way
on Tài chính. From now on ``application/crm/staff_link.mirror_ads_accounts``
keeps them equal after every Ads edit / import / CRM sync; this copies what is
there today: every ``ads_crm_accounts`` row gets its ``crm_staff`` row (created
when missing, role ``marketing``) with the same ``user_id``.

Downgrade
---------

Nothing: the links stay (they were Phòng Ads' anyway).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0108"
down_revision: str | None = "0107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO crm_staff (id, name, roles, user_id, seen_at, updated_at)
            SELECT a.crm_id, a.name, '["marketing"]'::jsonb, a.user_id, now(), now()
            FROM ads_crm_accounts AS a
            WHERE NOT EXISTS (SELECT 1 FROM crm_staff AS s WHERE s.id = a.crm_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE crm_staff AS s
            SET user_id = a.user_id,
                updated_by_user_id = a.updated_by_user_id,
                updated_at = now()
            FROM ads_crm_accounts AS a
            WHERE s.id = a.crm_id AND s.user_id IS DISTINCT FROM a.user_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE crm_staff AS s
            SET roles = s.roles || '["marketing"]'::jsonb
            FROM ads_crm_accounts AS a
            WHERE s.id = a.crm_id AND NOT (s.roles @> '["marketing"]'::jsonb)
            """
        )
    )


def downgrade() -> None:
    pass
