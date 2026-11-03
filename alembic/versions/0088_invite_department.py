"""Invites carry the inviter's department: a Trưởng phòng's code seats its
redeemer in that department, reporting to them.

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-02

Additive. Until now only stream leads placed their invitees (0051: a ban's
Leader, the Trưởng phòng ORD, a PR team lead); the head of any other
department (Tài chính-Kế toán, Hành chính nhân sự…) minted codes whose
redeemer joined in no department and had to be seated by hand. A code from a
department head now names that department; redeeming it seats the newcomer as
a member there with the inviter as their manager (the order / PR tags follow
from the seat, as everywhere since 0061).

``invite_codes`` gains ``department_id`` (FK ``departments``, RESTRICT, like
``unit_id``). Old codes keep their 0051 stream placement.

Downgrade
---------

Drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVITES = "invite_codes"


def upgrade() -> None:
    op.add_column(INVITES, sa.Column("department_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_invite_codes_department_id_departments"),
        INVITES,
        "departments",
        ["department_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_invite_codes_department_id_departments"), INVITES, type_="foreignkey"
    )
    op.drop_column(INVITES, "department_id")
