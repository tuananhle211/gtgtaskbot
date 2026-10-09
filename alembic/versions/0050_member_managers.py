"""ORD: each member's own Leader ("trưởng quản lý").

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-09

Additive. Nothing PR reads or writes changes.

A ban can have several Leaders. ``org_unit_members.manager_user_id`` names the
one a member reports to: a staff member of Biên kịch / Design / Dựng points at
a Leader of the same function, an orderer at a Trưởng phòng ORD. Their hand-ins
(and an orderer's orders) then wait on that one person only; with no manager
set they wait on every Leader (head) of the ban, as before.

Downgrade
---------

Drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("org_unit_members", sa.Column("manager_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_org_unit_members_manager_user_id_users"),
        "org_unit_members",
        "users",
        ["manager_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_org_unit_members_manager_user_id_users"), "org_unit_members", type_="foreignkey"
    )
    op.drop_column("org_unit_members", "manager_user_id")
