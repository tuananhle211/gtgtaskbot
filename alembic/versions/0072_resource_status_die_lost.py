"""Split the resource status "Die / Mất" into "Die" and "Mất" (0072).

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-22

Data only. The existing entry (code ``DIE``) is renamed "Die" - so every
resource and history line that was "Die / Mất" is now "Die", as asked - and a
new entry "Mất" (code ``LOST``, group LOST: a loss, red) is added right after
it, the later entries moving down one place.

Downgrade
---------

Moves resources on "Mất" back to "Die", retires "Mất" (inactive - history
lines may point at it) and renames "Die" back to "Die / Mất".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOST_ID = uuid.uuid5(uuid.UUID("6f1c9a52-0b7e-4a59-9d55-0072c0de0072"), "STATUS:LOST")


def upgrade() -> None:
    bind = op.get_bind()
    die = bind.execute(
        sa.text("SELECT sort_order FROM resource_options WHERE kind = 'STATUS' AND code = 'DIE'")
    ).first()
    bind.execute(
        sa.text("UPDATE resource_options SET name = 'Die' WHERE kind = 'STATUS' AND code = 'DIE'")
    )
    exists = bind.execute(
        sa.text("SELECT 1 FROM resource_options WHERE kind = 'STATUS' AND code = 'LOST'")
    ).first()
    if exists:
        return
    if die is None:
        order = (
            bind.execute(
                sa.text(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM resource_options "
                    "WHERE kind = 'STATUS'"
                )
            ).scalar()
            or 0
        )
    else:
        order = die[0] + 1
        bind.execute(
            sa.text(
                "UPDATE resource_options SET sort_order = sort_order + 1 "
                "WHERE kind = 'STATUS' AND sort_order >= :order"
            ),
            {"order": order},
        )
    bind.execute(
        sa.text(
            "INSERT INTO resource_options (id, kind, code, name, status_group, sort_order, active) "
            "VALUES (:id, 'STATUS', 'LOST', 'Mất', 'LOST', :order, true)"
        ),
        {"id": _LOST_ID, "order": order},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE resources SET status_id = (SELECT id FROM resource_options "
            "WHERE kind = 'STATUS' AND code = 'DIE') WHERE status_id = (SELECT id FROM "
            "resource_options WHERE kind = 'STATUS' AND code = 'LOST')"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE resource_options SET active = false WHERE kind = 'STATUS' AND code = 'LOST'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE resource_options SET name = 'Die / Mất' WHERE kind = 'STATUS' AND code = 'DIE'"
        )
    )
