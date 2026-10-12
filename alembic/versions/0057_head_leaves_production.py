"""ORD: a Trưởng phòng ORD no longer runs the production steps.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-12

A data change only. In ORD's stored permission matrix the HEAD column loses
"Giao việc" (``NODE_ASSIGN``), "Duyệt / trả bài nộp" (``NODE_REVIEW``) and
"Duyệt video" (``VIDEO_REVIEW``): the head decides their own gate - the order -
and the final product; Biên kịch, Design and Dựng are their Leaders' to hand
out and review. The new default says the same; a unit that never saved its
matrix needs nothing. The matrix stays editable on the settings page.

Downgrade
---------

Gives the three permissions back to the HEAD column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRODUCTION = ("NODE_ASSIGN", "NODE_REVIEW", "VIDEO_REVIEW")


def _set_head(scope: str) -> None:
    bind = op.get_bind()
    settings_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    units = sa.table(
        "org_units",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("settings", settings_type),
    )
    rows = bind.execute(sa.select(units.c.id, units.c.settings).where(units.c.code == "ADS")).all()
    for unit_id, settings in rows:
        updated = dict(settings or {})
        matrix = dict(updated.get("permissions") or {})
        if "HEAD" not in matrix:
            # Never saved: the default matrix applies.
            continue
        head = dict(matrix["HEAD"])
        for permission in PRODUCTION:
            head[permission] = scope
        matrix["HEAD"] = head
        updated["permissions"] = matrix
        bind.execute(sa.update(units).where(units.c.id == unit_id).values(settings=updated))


def upgrade() -> None:
    _set_head("NONE")


def downgrade() -> None:
    _set_head("ALL")
