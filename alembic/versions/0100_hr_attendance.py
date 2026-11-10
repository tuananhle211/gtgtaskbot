"""Nhân sự đợt 3-4: đơn từ, phép năm, ca làm việc, chấm công.

Revision ID: 0100
Revises: 0099
Create Date: 2026-11-10

* ``hr_leave_types`` (seeded: phép năm, ốm, không lương, kết hôn, con kết
  hôn, tang, nghỉ bù);
* ``hr_absence_requests`` + ``hr_absence_steps``: leave / late / early
  requests, approved Trưởng phòng → Giám đốc khối → Giám đốc → HCNS;
* ``hr_shifts`` (seeded: "Hành chính" 08:00-12:00, 13:30-17:30, Saturday
  morning, 5 minutes' grace, the default);
* ``hr_attendance_days``: first punch-in / last punch-out per person per day;
* ``hr_leave_adjustments``: days added to / taken from a year's annual leave;
* permission ``HR`` / ``TIMESHEET`` ("Chấm công & xác nhận đơn từ") ALL for
  both positions of the ``HCNS`` department.

Downgrade drops the tables and the grants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0100"
down_revision: str | None = "0099"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEAVE_TYPES = (
    ("ANNUAL", "Nghỉ phép năm", True, True, None, "P"),
    ("SICK", "Nghỉ ốm (hưởng BHXH)", False, False, None, "Ô"),
    ("UNPAID", "Nghỉ không lương", False, False, None, "KL"),
    ("MARRIAGE", "Nghỉ kết hôn", True, False, 3, "R"),
    ("CHILD_MARRIAGE", "Con kết hôn", True, False, 1, "R"),
    ("BEREAVEMENT", "Nghỉ tang (bố mẹ, vợ/chồng, con)", True, False, 3, "R"),
    ("COMPENSATORY", "Nghỉ bù", True, False, None, "NB"),
)
_FULL = {"morning": ["08:00", "12:00"], "afternoon": ["13:30", "17:30"]}
DEFAULT_WEEK = {**{str(day): _FULL for day in range(5)}, "5": {"morning": ["08:00", "12:00"]}}


def _times() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _user_fk(table: str, column: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}_users"), ondelete=ondelete
    )


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "hr_leave_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("annual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_days", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(6), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_times(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_leave_types")),
        sa.UniqueConstraint("code", name=op.f("uq_hr_leave_types_code")),
    )
    op.create_table(
        "hr_absence_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("leave_type_code", sa.String(30), nullable=True),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("end_on", sa.Date(), nullable=False),
        sa.Column("part", sa.String(10), nullable=False, server_default=sa.text("'FULL'")),
        sa.Column("at_time", sa.Time(), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=True),
        sa.Column("days", sa.Numeric(5, 1), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("cancel_note", sa.Text(), nullable=True),
        *_times(),
        _user_fk("hr_absence_requests", "user_id", "RESTRICT"),
        _user_fk("hr_absence_requests", "cancelled_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_absence_requests")),
    )
    op.create_index(
        "ix_hr_absence_requests_user_start", "hr_absence_requests", ["user_id", "start_on"]
    )
    op.create_index("ix_hr_absence_requests_status", "hr_absence_requests", ["status"])
    op.create_table(
        "hr_absence_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("approver_ids", _json(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_times(),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["hr_absence_requests.id"],
            name=op.f("fk_hr_absence_steps_request_id_hr_absence_requests"),
            ondelete="CASCADE",
        ),
        _user_fk("hr_absence_steps", "decided_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_absence_steps")),
        sa.UniqueConstraint(
            "request_id", "position", name=op.f("uq_hr_absence_steps_request_id_position")
        ),
    )
    op.create_index(op.f("ix_hr_absence_steps_request_id"), "hr_absence_steps", ["request_id"])
    op.create_table(
        "hr_shifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("week", _json(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("grace_minutes", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("branches", _json(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("department_ids", _json(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_times(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_shifts")),
    )
    op.create_table(
        "hr_attendance_days",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("check_in", sa.Time(), nullable=True),
        sa.Column("check_out", sa.Time(), nullable=True),
        sa.Column("machine_code", sa.String(40), nullable=True),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("edited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_times(),
        _user_fk("hr_attendance_days", "user_id", "RESTRICT"),
        _user_fk("hr_attendance_days", "edited_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_attendance_days")),
        sa.UniqueConstraint("user_id", "day", name=op.f("uq_hr_attendance_days_user_id_day")),
    )
    op.create_index("ix_hr_attendance_days_day", "hr_attendance_days", ["day"])
    op.create_table(
        "hr_leave_adjustments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("days", sa.Numeric(5, 1), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_times(),
        _user_fk("hr_leave_adjustments", "user_id", "RESTRICT"),
        _user_fk("hr_leave_adjustments", "created_by_user_id", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hr_leave_adjustments")),
    )
    op.create_index(op.f("ix_hr_leave_adjustments_user_id"), "hr_leave_adjustments", ["user_id"])

    now = datetime.now(UTC)
    types = sa.table(
        "hr_leave_types",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("paid", sa.Boolean()),
        sa.column("annual", sa.Boolean()),
        sa.column("max_days", sa.Integer()),
        sa.column("symbol", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        types,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "paid": paid,
                "annual": annual,
                "max_days": max_days,
                "symbol": symbol,
                "sort_order": index,
                "created_at": now,
                "updated_at": now,
            }
            for index, (code, name, paid, annual, max_days, symbol) in enumerate(LEAVE_TYPES)
        ],
    )
    shifts = sa.table(
        "hr_shifts",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("week", _json()),
        sa.column("grace_minutes", sa.Integer()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        shifts,
        [
            {
                "id": uuid.uuid4(),
                "name": "Hành chính",
                "week": DEFAULT_WEEK,
                "grace_minutes": 5,
                "is_default": True,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    hcns = op.get_bind().execute(sa.text("SELECT id FROM departments WHERE code = 'HCNS'")).all()
    grants = sa.table(
        "department_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("department_id", sa.Uuid()),
        sa.column("position", sa.String()),
        sa.column("module", sa.String()),
        sa.column("permission", sa.String()),
        sa.column("scope", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "department_id": department_id,
            "position": position,
            "module": "HR",
            "permission": "TIMESHEET",
            "scope": "ALL",
            "created_at": now,
            "updated_at": now,
        }
        for (department_id,) in hcns
        for position in ("HEAD", "MEMBER")
    ]
    if rows:
        op.bulk_insert(grants, rows)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM department_permissions WHERE module = 'HR' AND permission = 'TIMESHEET'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE user_permissions SET revoked_at = now() "
            "WHERE module = 'HR' AND permission = 'TIMESHEET' AND revoked_at IS NULL"
        )
    )
    op.drop_index(op.f("ix_hr_leave_adjustments_user_id"), table_name="hr_leave_adjustments")
    op.drop_table("hr_leave_adjustments")
    op.drop_index("ix_hr_attendance_days_day", table_name="hr_attendance_days")
    op.drop_table("hr_attendance_days")
    op.drop_table("hr_shifts")
    op.drop_index(op.f("ix_hr_absence_steps_request_id"), table_name="hr_absence_steps")
    op.drop_table("hr_absence_steps")
    op.drop_index("ix_hr_absence_requests_status", table_name="hr_absence_requests")
    op.drop_index("ix_hr_absence_requests_user_start", table_name="hr_absence_requests")
    op.drop_table("hr_absence_requests")
    op.drop_table("hr_leave_types")
