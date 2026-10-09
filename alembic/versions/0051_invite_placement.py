"""Invites carry where the invitee lands: stream, role and own Leader.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-09

Additive. A code minted by a stream lead tags its redeemer at once:
a Leader of Biên kịch / Design / Dựng brings in staff of that ban who report
to them, a Trưởng phòng ORD brings in an orderer who reports to them, a PR
team lead brings in a PR member. A code from the OWNER or an ADMIN carries
none of it - the redeemer joins untagged, as before.

``invite_codes`` gains ``unit_id`` (RESTRICT), ``unit_role`` and
``manager_user_id`` (SET NULL, like ``created_by``).

Downgrade
---------

Drops the three columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVITES = "invite_codes"


def upgrade() -> None:
    op.add_column(INVITES, sa.Column("unit_id", sa.Uuid(), nullable=True))
    op.add_column(INVITES, sa.Column("unit_role", sa.String(length=20), nullable=True))
    op.add_column(INVITES, sa.Column("manager_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_invite_codes_unit_id_org_units"),
        INVITES,
        "org_units",
        ["unit_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_invite_codes_manager_user_id_users"),
        INVITES,
        "users",
        ["manager_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_invite_codes_manager_user_id_users"), INVITES, type_="foreignkey")
    op.drop_constraint(op.f("fk_invite_codes_unit_id_org_units"), INVITES, type_="foreignkey")
    op.drop_column(INVITES, "manager_user_id")
    op.drop_column(INVITES, "unit_role")
    op.drop_column(INVITES, "unit_id")
