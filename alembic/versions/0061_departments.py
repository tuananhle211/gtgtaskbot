"""Departments ("Phòng ban"): who sits where, and what each department grants.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-14

Additive. Three tables:

* ``departments`` - ADS, Biên Tập, Thiết Kế, Dựng, PR Thương Hiệu, Tài nguyên,
  Ban Giám đốc to start with; more are added on the departments screen;
* ``department_members`` - a person's seat in a department: head or member,
  and the head they report to. One person may sit in several departments;
* ``department_permissions`` - what a department grants its heads and its
  members in a module (the order flow, ``ORDER``, today).

Seeded from what exists, so nobody's access changes:

* every open stream tag becomes a seat: an ORD head / orderer in ADS (head /
  member), a Leader / staff of a production function in its department (head
  / member), a PR member in PR Thương Hiệu (a head when their system role is
  TEAM_LEAD). The "own Leader" links (0050) carry over;
* the order flow's permission matrix becomes the grants: the head / orderer
  columns for ADS, the Leader / staff columns for the production departments.
  A head's view and approval are granted ``OWN`` - their own team, which is
  what the head saw and decided before (team scope, 0055/0056).

Downgrade
---------

Drops the three tables. The stream tags were never touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEPARTMENTS = "departments"
MEMBERS = "department_members"
PERMISSIONS = "department_permissions"
RESTRICT = "RESTRICT"
SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-006100610061")

#: (code, name, function, order)
START = (
    ("ADS", "ADS", "NONE", 0),
    ("BT", "Biên Tập", "BIEN_TAP", 1),
    ("TK", "Thiết Kế", "THIET_KE", 2),
    ("D", "Dựng", "DUNG", 3),
    ("PR", "PR Thương Hiệu", "PR", 4),
    ("TN", "Tài nguyên", "RESOURCES", 5),
    ("BGD", "Ban Giám đốc", "BOARD", 6),
)
FUNCTION_DEPARTMENT = {"BIEN_TAP": "BT", "THIET_KE": "TK", "DUNG": "D"}
ORDER_PERMISSIONS = (
    "VIEW",
    "ORDER_CREATE",
    "ORDER_APPROVE",
    "NODE_ASSIGN",
    "NODE_REVIEW",
    "VIDEO_REVIEW",
    "FINAL_REVIEW",
    "PRIORITY",
    "CANCEL",
)
#: The order flow's defaults before 0061 (``DEFAULT_MATRIX``), per column.
OLD_DEFAULTS = {
    "HEAD": {
        "VIEW": "ALL",
        "ORDER_CREATE": "ALL",
        "ORDER_APPROVE": "ALL",
        "FINAL_REVIEW": "ALL",
        "PRIORITY": "ALL",
        "CANCEL": "ALL",
    },
    "LEAD": {"VIEW": "OWN", "NODE_ASSIGN": "OWN", "NODE_REVIEW": "OWN", "VIDEO_REVIEW": "OWN"},
    "STAFF": {"VIEW": "OWN"},
    "ORDERER": {"VIEW": "OWN", "ORDER_CREATE": "ALL", "CANCEL": "OWN"},
}
#: A head saw and approved their own team's orders only: granted as OWN.
HEAD_TEAM_ONLY = ("VIEW", "ORDER_APPROVE")


def seed_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, ":".join(parts))


def _column(stored: dict | None, column: str) -> dict[str, str]:
    cells = dict(OLD_DEFAULTS[column])
    for permission, scope in ((stored or {}).get(column) or {}).items():
        if permission in ORDER_PERMISSIONS:
            if scope == "NONE":
                cells.pop(permission, None)
            else:
                cells[permission] = scope
    return cells


def upgrade() -> None:
    op.create_table(
        DEPARTMENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("function", sa.String(length=20), server_default="NONE", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_departments_code_not_empty")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_departments_name_not_empty")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("code", name=op.f("uq_departments_code")),
    )
    op.create_table(
        MEMBERS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.String(length=10), server_default="MEMBER", nullable=False),
        sa.Column("manager_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            [f"{DEPARTMENTS}.id"],
            name=op.f("fk_department_members_department_id_departments"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_department_members_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["manager_user_id"],
            ["users.id"],
            name=op.f("fk_department_members_manager_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department_members")),
    )
    op.create_index(
        "uq_department_members_open_seat",
        MEMBERS,
        ["department_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )
    op.create_index("ix_department_members_user_id", MEMBERS, ["user_id"])
    op.create_index("ix_department_members_manager_user_id", MEMBERS, ["manager_user_id"])
    op.create_table(
        PERMISSIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.String(length=10), nullable=False),
        sa.Column("module", sa.String(length=20), nullable=False),
        sa.Column("permission", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            [f"{DEPARTMENTS}.id"],
            name=op.f("fk_department_permissions_department_id_departments"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department_permissions")),
        sa.UniqueConstraint(
            "department_id",
            "position",
            "module",
            "permission",
            name="uq_department_permissions_cell",
        ),
    )
    op.create_index(op.f("ix_department_permissions_department_id"), PERMISSIONS, ["department_id"])

    # --- seed ------------------------------------------------------------------
    bind = op.get_bind()
    departments = sa.table(
        DEPARTMENTS,
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("function", sa.String()),
        sa.column("sort_order", sa.Integer()),
    )
    ids = {code: seed_id("department", code) for code, *_ in START}
    bind.execute(
        sa.insert(departments),
        [
            {"id": ids[code], "code": code, "name": name, "function": function, "sort_order": n}
            for code, name, function, n in START
        ],
    )

    units = sa.table(
        "org_units",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("settings", sa.JSON()),
    )
    stored = bind.execute(
        sa.select(units.c.settings).where(units.c.code == "ADS")
    ).scalar_one_or_none()
    matrix = (stored or {}).get("permissions") if isinstance(stored, dict) else None
    grants = {
        ("ADS", "HEAD"): _column(matrix, "HEAD"),
        ("ADS", "MEMBER"): _column(matrix, "ORDERER"),
        **{(code, "HEAD"): _column(matrix, "LEAD") for code in ("BT", "TK", "D")},
        **{(code, "MEMBER"): _column(matrix, "STAFF") for code in ("BT", "TK", "D")},
    }
    for permission in HEAD_TEAM_ONLY:
        if grants[("ADS", "HEAD")].get(permission) == "ALL":
            grants[("ADS", "HEAD")][permission] = "OWN"
    permissions = sa.table(
        PERMISSIONS,
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("module", sa.String()),
        sa.column("permission", sa.String()),
        sa.column("scope", sa.String()),
    )
    rows = [
        {
            "id": seed_id("grant", code, position, permission),
            "department_id": ids[code],
            "position": position,
            "module": "ORDER",
            "permission": permission,
            "scope": scope,
        }
        for (code, position), cells in grants.items()
        for permission, scope in cells.items()
    ]
    if rows:
        bind.execute(sa.insert(permissions), rows)

    # Seats from the open stream tags.
    tags = bind.execute(
        sa.text(
            "SELECT m.user_id, u.code AS unit, m.role, m.is_lead, m.manager_user_id, "
            "       usr.role AS system_role "
            "FROM org_unit_members m "
            "JOIN org_units u ON u.id = m.unit_id "
            "JOIN users usr ON usr.id = m.user_id "
            "WHERE m.left_at IS NULL"
        )
    ).all()
    seats: dict[tuple[str, uuid.UUID], dict] = {}
    for user_id, unit, role, is_lead, manager, system_role in tags:
        if unit == "ADS":
            if role in FUNCTION_DEPARTMENT:
                code, position = FUNCTION_DEPARTMENT[role], "HEAD" if is_lead else "MEMBER"
            elif role == "HEAD":
                code, position = "ADS", "HEAD"
            elif role == "ORDERER":
                code, position = "ADS", "MEMBER"
            else:
                continue
        elif unit == "PR":
            code, position = "PR", "HEAD" if system_role == "TEAM_LEAD" else "MEMBER"
        else:
            continue
        seats[(code, user_id)] = {
            "code": code,
            "user_id": user_id,
            "position": position,
            "manager": manager if unit == "ADS" else None,
        }
    members = sa.table(
        MEMBERS,
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("manager_user_id", sa.Uuid()),
    )
    rows = []
    for (code, user_id), seat in seats.items():
        boss = seat["manager"]
        # The link carries over only to a head of the same department.
        boss_seat = None if boss is None else seats.get((code, boss))
        rows.append(
            {
                "id": seed_id("seat", code, str(user_id)),
                "department_id": ids[code],
                "user_id": user_id,
                "position": seat["position"],
                "manager_user_id": boss
                if boss_seat is not None and boss_seat["position"] == "HEAD" and boss != user_id
                else None,
            }
        )
    if rows:
        bind.execute(sa.insert(members), rows)


def downgrade() -> None:
    op.drop_index(op.f("ix_department_permissions_department_id"), table_name=PERMISSIONS)
    op.drop_table(PERMISSIONS)
    op.drop_index("ix_department_members_manager_user_id", table_name=MEMBERS)
    op.drop_index("ix_department_members_user_id", table_name=MEMBERS)
    op.drop_index("uq_department_members_open_seat", table_name=MEMBERS)
    op.drop_table(MEMBERS)
    op.drop_table(DEPARTMENTS)
