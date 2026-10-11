"""Owner controls: deleted accounts, and Design / Dựng hand-ins without review.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-11

Additive, plus one data change. Nothing PR reads or writes changes.

* ``users.deleted_at`` - the OWNER deleted the account for good ("Xoá thành
  viên"): it cannot sign in and is gone from every list; the row stays so the
  orders and work it left keep their history, named "<tên> (đã xoá)".
* ORD's stored settings: ``review_thiet_ke`` and ``review_dung`` are switched
  off, so a Design or Dựng hand-in finishes the step and the next one starts
  (the new default too; the two switches stay on the settings page).

Downgrade
---------

Drops the column. The review switches are left as they are: which way they
were before is not recorded.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

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
        updated["review_thiet_ke"] = False
        updated["review_dung"] = False
        bind.execute(sa.update(units).where(units.c.id == unit_id).values(settings=updated))


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
