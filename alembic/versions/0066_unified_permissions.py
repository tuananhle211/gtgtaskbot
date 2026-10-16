"""One permission model (0066): the PR module joins the departments' grants,
and people get a personal layer.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-16

* ``user_permissions`` - one person's extra right in one module (the
  personal layer; the PR review gates stay in ``pr_user_capabilities``);
* every department whose function is PR Thương Hiệu or Ban Giám đốc gets the
  PR module's default grants by position: a head what a Trưởng nhóm held
  through the role matrix until now, a member what an Employee held - so
  nobody's access changes - plus, for the PR head, ``MANAGE_PERMISSIONS``
  over their own members;
* the order flow's "Admin column" is no longer read: an ADMIN holds every
  permission in every module (the stored ``settings.permissions.ADMIN`` is
  left in place, ignored).

Downgrade
---------

Drops the table and the PR grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "user_permissions"
SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-006600660066")

#: What an EMPLOYEE held through the role matrix (``script.submit``,
#: ``video.submit``), as PR capabilities. The review gates are not cells.
MEMBER_CAPABILITIES = (
    "PR_CONTENT_CREATE",
    "PR_CONTENT_EDIT",
    "PR_CONTENT_TRANSITION",
    "PR_CONTENT_DELETE",
    "PR_PUBLICATION_CREATE",
    "PR_TASK_MANAGE",
    "PR_PRODUCTION_EXECUTE",
    "PR_WORK_EXECUTE",
)
#: What a TEAM_LEAD held on top (``script.approve``, ``video.approve``).
HEAD_CAPABILITIES = (
    *MEMBER_CAPABILITIES,
    "PR_CONTENT_CANCEL",
    "PR_PRODUCTION_ASSIGN",
    "PR_WORK_MANAGE",
    "PR_WORK_VALIDATE",
)


def seed_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, ":".join(parts))


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("module", sa.String(length=20), nullable=False),
        sa.Column("permission", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("scope IN ('OWN', 'ALL')", name=op.f("ck_user_permissions_scope_known")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_permissions_user_id_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name=op.f("fk_user_permissions_granted_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name=op.f("fk_user_permissions_revoked_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_permissions")),
    )
    op.create_index("ix_user_permissions_user_id", TABLE, ["user_id"])
    op.create_index("ix_user_permissions_module_permission", TABLE, ["module", "permission"])

    bind = op.get_bind()
    departments = bind.execute(
        sa.text("SELECT id, code, function FROM departments WHERE function IN ('PR', 'BOARD')")
    ).all()
    permissions = sa.table(
        "department_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("module", sa.String()),
        sa.column("permission", sa.String()),
        sa.column("scope", sa.String()),
    )
    rows = []
    for department_id, code, function in departments:
        cells = (
            {"HEAD": {**dict.fromkeys(HEAD_CAPABILITIES, "ALL"), "MANAGE_PERMISSIONS": "OWN"}, "MEMBER": dict.fromkeys(MEMBER_CAPABILITIES, "ALL")}
            if function == "PR"
            else {"HEAD": dict.fromkeys(MEMBER_CAPABILITIES, "ALL"), "MEMBER": dict.fromkeys(MEMBER_CAPABILITIES, "ALL")}
        )
        for position, line in cells.items():
            for permission, scope in line.items():
                rows.append(
                    {
                        "id": seed_id("grant", code, position, permission),
                        "department_id": department_id,
                        "position": position,
                        "module": "PR",
                        "permission": permission,
                        "scope": scope,
                    }
                )
    if rows:
        bind.execute(sa.insert(permissions), rows)


def downgrade() -> None:
    op.execute("DELETE FROM department_permissions WHERE module = 'PR'")
    op.drop_index("ix_user_permissions_module_permission", table_name=TABLE)
    op.drop_index("ix_user_permissions_user_id", table_name=TABLE)
    op.drop_table(TABLE)
