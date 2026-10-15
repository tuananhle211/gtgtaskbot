"""Resources ("Quản lý tài nguyên", đợt 1) and the employment status.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-15

* ``users.employment_status`` - Đang làm / Thử việc / Tạm nghỉ / Đã nghỉ
  việc, ``WORKING`` for everybody today;
* ``resource_options`` - the pick-lists, seeded with the requirement's
  lists (type, role, status with its group, origin, two-factor, handover);
* ``resources`` and ``resource_code_counters``;
* ``resource_handovers``, ``resource_status_history``, ``resource_changes`` -
  append-only, enforced by a trigger that refuses UPDATE and DELETE;
* every department gets the module's default ``RESOURCES`` grants by its
  function: the Phòng Tài nguyên runs the module, Ban Giám đốc sees all and
  the reports, any other department sees its own (a head their department's
  resources, a member what they hold).

Downgrade
---------

Drops the tables, the trigger function, the grants and the column. The
inventory is lost: back the database up first (the deploy job does).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPTIONS = "resource_options"
RESOURCES = "resources"
COUNTERS = "resource_code_counters"
HANDOVERS = "resource_handovers"
STATUS_HISTORY = "resource_status_history"
CHANGES = "resource_changes"
APPEND_ONLY = (HANDOVERS, STATUS_HISTORY, CHANGES)
RESTRICT = "RESTRICT"
SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-006500650065")

#: (kind, code, name, status group)
SEED_OPTIONS = (
    ("TYPE", "VIA", "Via", None),
    ("TYPE", "PAGE", "Page", None),
    ("TYPE", "GROUP", "Nhóm", None),
    ("TYPE", "AI", "AI", None),
    ("TYPE", "TKQC", "TKQC", None),
    ("TYPE", "BM", "BM", None),
    ("TYPE", "OTHER", "Khác", None),
    ("ROLE", "OWNER_ADMIN", "Chủ sở hữu/Admin", None),
    ("ROLE", "EDITOR", "Biên tập viên", None),
    ("ROLE", "MODERATOR", "Kiểm duyệt viên", None),
    ("ROLE", "ADVERTISER", "Nhà quảng cáo", None),
    ("ROLE", "ANALYST", "Phân tích", None),
    ("ROLE", "MEMBER", "Thành viên", None),
    ("ROLE", "ACCOUNT_OWNER", "Chủ tài khoản", None),
    ("ROLE", "SHARED", "Dùng chung", None),
    ("ROLE", "OTHER", "Khác", None),
    ("STATUS", "ACTIVE", "Hoạt động", "NORMAL"),
    ("STATUS", "RESTRICTED", "Hạn chế", "ATTENTION"),
    ("STATUS", "CHECKPOINT", "Checkpoint", "ATTENTION"),
    ("STATUS", "DIE", "Die/Mất", "LOST"),
    ("STATUS", "RETURNED", "Đã thu hồi", "CLOSED"),
    ("STATUS", "STOPPED", "Ngừng dùng", "CLOSED"),
    ("ORIGIN", "COMPANY", "Công ty cấp", None),
    ("ORIGIN", "SELF", "Tự tạo", None),
    ("ORIGIN", "BOUGHT", "Mua ngoài", None),
    ("ORIGIN", "RENTED", "Thuê", None),
    ("ORIGIN", "INHERITED", "Nhận lại từ người khác", None),
    ("ORIGIN", "UNKNOWN", "Không rõ", None),
    ("TWO_FACTOR", "YES", "Có", None),
    ("TWO_FACTOR", "NO", "Không", None),
    ("TWO_FACTOR", "UNKNOWN", "Không rõ", None),
    ("HANDOVER", "NOT_YET", "Chưa bàn giao", None),
    ("HANDOVER", "IN_PROGRESS", "Đang bàn giao", None),
    ("HANDOVER", "DONE", "Đã bàn giao", None),
)

ALL_PERMISSIONS = ("VIEW", "EDIT", "IMPORT", "SETTINGS", "REPORT", "HR_STATUS")


def default_grants(function: str) -> dict[str, dict[str, str]]:
    if function == "RESOURCES":
        return {
            "HEAD": dict.fromkeys(ALL_PERMISSIONS, "ALL"),
            "MEMBER": {"VIEW": "ALL", "EDIT": "ALL", "REPORT": "ALL"},
        }
    if function == "BOARD":
        return {"HEAD": {"VIEW": "ALL", "REPORT": "ALL"}, "MEMBER": {"VIEW": "ALL", "REPORT": "ALL"}}
    return {"HEAD": {"VIEW": "OWN"}, "MEMBER": {"VIEW": "OWN"}}


IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION resource_history_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'resource history is append-only (%)', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def seed_id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, ":".join(parts))


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=RESTRICT,
    )


def upgrade() -> None:
    # --- the person's standing with the company --------------------------------
    op.add_column(
        "users",
        sa.Column(
            "employment_status",
            sa.String(length=20),
            server_default="WORKING",
            nullable=False,
        ),
    )

    # --- pick-lists ---------------------------------------------------------------
    op.create_table(
        OPTIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status_group", sa.String(length=20), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_resource_options_code_not_empty")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_resource_options_name_not_empty")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_options")),
        sa.UniqueConstraint("kind", "code", name="uq_resource_options_kind_code"),
    )

    # --- resources ----------------------------------------------------------------
    op.create_table(
        RESOURCES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("type_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("holder_user_id", sa.Uuid(), nullable=True),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("two_factor_id", sa.Uuid(), nullable=True),
        sa.Column("two_factor_holder_user_id", sa.Uuid(), nullable=True),
        sa.Column("origin_id", sa.Uuid(), nullable=True),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("handover_id", sa.Uuid(), nullable=False),
        sa.Column("status_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_resources_code_not_empty")),
        sa.CheckConstraint(
            "length(trim(external_id)) > 0", name=op.f("ck_resources_external_id_not_empty")
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_resources_name_not_empty")),
        _fk(RESOURCES, "department_id", "departments"),
        _fk(RESOURCES, "type_id", OPTIONS),
        _fk(RESOURCES, "holder_user_id", "users"),
        _fk(RESOURCES, "role_id", OPTIONS),
        _fk(RESOURCES, "two_factor_id", OPTIONS),
        _fk(RESOURCES, "two_factor_holder_user_id", "users"),
        _fk(RESOURCES, "origin_id", OPTIONS),
        _fk(RESOURCES, "handover_id", OPTIONS),
        _fk(RESOURCES, "status_id", OPTIONS),
        _fk(RESOURCES, "created_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resources")),
        sa.UniqueConstraint("code", name=op.f("uq_resources_code")),
        sa.UniqueConstraint("type_id", "external_id", name="uq_resources_type_id_external_id"),
    )
    op.create_index("ix_resources_department_id", RESOURCES, ["department_id"])
    op.create_index("ix_resources_holder_user_id", RESOURCES, ["holder_user_id"])
    op.create_index("ix_resources_status_id", RESOURCES, ["status_id"])

    op.create_table(
        COUNTERS,
        sa.Column("prefix", sa.String(length=12), nullable=False),
        sa.Column("last_no", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("last_no >= 0", name=op.f("ck_resource_code_counters_last_no_not_negative")),
        sa.PrimaryKeyConstraint("prefix", name=op.f("pk_resource_code_counters")),
    )

    # --- histories (append-only) ----------------------------------------------------
    op.create_table(
        HANDOVERS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("from_user_id", sa.Uuid(), nullable=True),
        sa.Column("to_user_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=12), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _fk(HANDOVERS, "resource_id", RESOURCES),
        _fk(HANDOVERS, "from_user_id", "users"),
        _fk(HANDOVERS, "to_user_id", "users"),
        _fk(HANDOVERS, "actor_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_handovers")),
    )
    op.create_index("ix_resource_handovers_resource_id", HANDOVERS, ["resource_id"])

    op.create_table(
        STATUS_HISTORY,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("from_status_id", sa.Uuid(), nullable=True),
        sa.Column("to_status_id", sa.Uuid(), nullable=False),
        sa.Column("review_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _fk(STATUS_HISTORY, "resource_id", RESOURCES),
        _fk(STATUS_HISTORY, "from_status_id", OPTIONS),
        _fk(STATUS_HISTORY, "to_status_id", OPTIONS),
        _fk(STATUS_HISTORY, "actor_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_status_history")),
    )
    op.create_index("ix_resource_status_history_resource_id", STATUS_HISTORY, ["resource_id"])

    op.create_table(
        CHANGES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(length=40), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _fk(CHANGES, "resource_id", RESOURCES),
        _fk(CHANGES, "actor_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_changes")),
    )
    op.create_index("ix_resource_changes_resource_id", CHANGES, ["resource_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(IMMUTABLE_FUNCTION)
        for table in APPEND_ONLY:
            op.execute(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION resource_history_immutable()"
            )

    # --- seed: the pick-lists ---------------------------------------------------------
    options = sa.table(
        OPTIONS,
        sa.column("id", sa.Uuid()),
        sa.column("kind", sa.String()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("status_group", sa.String()),
        sa.column("sort_order", sa.Integer()),
    )
    rows = []
    order: dict[str, int] = {}
    for kind, code, name, group in SEED_OPTIONS:
        order[kind] = order.get(kind, -1) + 1
        rows.append(
            {
                "id": seed_id("option", kind, code),
                "kind": kind,
                "code": code,
                "name": name,
                "status_group": group,
                "sort_order": order[kind],
            }
        )
    bind.execute(sa.insert(options), rows)

    # --- seed: every department's default grants in the module -------------------------
    departments = bind.execute(sa.text("SELECT id, code, function FROM departments")).all()
    permissions = sa.table(
        "department_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("module", sa.String()),
        sa.column("permission", sa.String()),
        sa.column("scope", sa.String()),
    )
    grant_rows = [
        {
            "id": seed_id("grant", code, position, permission),
            "department_id": department_id,
            "position": position,
            "module": "RESOURCES",
            "permission": permission,
            "scope": scope,
        }
        for department_id, code, function in departments
        for position, cells in default_grants(function).items()
        for permission, scope in cells.items()
    ]
    if grant_rows:
        bind.execute(sa.insert(permissions), grant_rows)


def downgrade() -> None:
    op.execute("DELETE FROM department_permissions WHERE module = 'RESOURCES'")
    bind = op.get_bind()
    for table in (CHANGES, STATUS_HISTORY, HANDOVERS):
        if bind.dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.drop_index("ix_resource_changes_resource_id", table_name=CHANGES)
    op.drop_table(CHANGES)
    op.drop_index("ix_resource_status_history_resource_id", table_name=STATUS_HISTORY)
    op.drop_table(STATUS_HISTORY)
    op.drop_index("ix_resource_handovers_resource_id", table_name=HANDOVERS)
    op.drop_table(HANDOVERS)
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS resource_history_immutable()")
    op.drop_table(COUNTERS)
    op.drop_index("ix_resources_status_id", table_name=RESOURCES)
    op.drop_index("ix_resources_holder_user_id", table_name=RESOURCES)
    op.drop_index("ix_resources_department_id", table_name=RESOURCES)
    op.drop_table(RESOURCES)
    op.drop_table(OPTIONS)
    op.drop_column("users", "employment_status")
