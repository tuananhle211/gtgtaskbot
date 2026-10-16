"""The PR head keeps the roster (0067): two PR capabilities for the head.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-16

Data only. Every department whose function is PR Thương Hiệu grants its
heads ``PR_MEMBERS_VIEW`` and ``PR_MEMBERS_MANAGE`` (scope ``ALL``): seeing
the PR roster, the roles and permissions tabs, and adding people to PR. The
system half - changing a role, deactivating, revoking an account - stays
with the OWNER / ADMIN.

Downgrade
---------

Removes the two cells.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-006700670067")
CAPABILITIES = ("PR_MEMBERS_VIEW", "PR_MEMBERS_MANAGE")


def upgrade() -> None:
    bind = op.get_bind()
    departments = bind.execute(
        sa.text("SELECT id, code FROM departments WHERE function = 'PR'")
    ).all()
    existing = {
        (department_id, permission)
        for department_id, permission in bind.execute(
            sa.text(
                "SELECT department_id, permission FROM department_permissions "
                "WHERE module = 'PR' AND position = 'HEAD'"
            )
        ).all()
    }
    permissions = sa.table(
        "department_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("module", sa.String()),
        sa.column("permission", sa.String()),
        sa.column("scope", sa.String()),
    )
    rows = [
        {
            "id": uuid.uuid5(SEED_NAMESPACE, f"grant:{code}:HEAD:{permission}"),
            "department_id": department_id,
            "position": "HEAD",
            "module": "PR",
            "permission": permission,
            "scope": "ALL",
        }
        for department_id, code in departments
        for permission in CAPABILITIES
        if (department_id, permission) not in existing
    ]
    if rows:
        bind.execute(sa.insert(permissions), rows)


def downgrade() -> None:
    op.execute(
        "DELETE FROM department_permissions WHERE module = 'PR' "
        "AND permission IN ('PR_MEMBERS_VIEW', 'PR_MEMBERS_MANAGE')"
    )
