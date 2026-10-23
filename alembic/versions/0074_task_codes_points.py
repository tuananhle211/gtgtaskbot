"""Task codes with points on every production phase (0074).

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-23

The design-code catalogue (``unit_design_codes``, 0058) becomes the "mã task
công việc" of every production phase: Biên kịch N1-N5, Design T1-T5, Dựng
D1-D7 and one "Việc khác" per phase (points typed by the person), each with
the points one unit scores. See ``docs/perf/TASK_CODES_PLAN.md``.

* ``unit_design_codes`` + ``phase``, ``points``, ``unit_label``, ``examples``,
  ``note``, ``free_points``; ``name`` may be empty (Design codes show their
  examples instead).
* ``order_nodes.task_items`` (Biên kịch / Dựng: the step's one code),
  ``returns_fault`` / ``returns_other`` (sends back by reason).
* ``orders.suggested_task_items``: the suggestion as placed, never rewritten.
* ``order_events.return_reason``.
* ``order_node_points``: what one person earned on one step.

Data: the ADS unit gets the 20 new codes; the 23 old design codes are switched
off and every stored list (orders, pending changes, designers' lists and their
history) is converted to T1-T5 by the department's table - only the code
changes, two old codes landing on one new code add up.

Downgrade
---------
Drops the new columns and table and the new catalogue rows. The converted
lists are **not** turned back into the old codes (the deploy backed the DB up).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_UNITS = "org_units"
CODES = "unit_design_codes"
ORDERS = "orders"
ORDER_NODES = "order_nodes"
ORDER_EVENTS = "order_events"
ASSIGNEES = "order_node_assignees"
ASSIGNEE_EVENTS = "order_node_assignee_events"
POINTS = "order_node_points"
USERS = "users"
RESTRICT = "RESTRICT"

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

SEED_NAMESPACE = uuid.UUID("7c1e2a40-0074-4d6b-9f3e-007400740074")

_T_NOTE = "Adapt = 50%, Resize = 20% (chọn ở Hình thức)."
_FREE_EXAMPLES = "Việc ngoài danh mục: ghi rõ làm gì và tự điền số điểm (1 giờ công = 60 điểm)."

#: (phase, code, name, examples, unit, points, note, free) - the department's
#: sheet "Danh mục điểm" (2026-10-22), kept here so the migration stands alone.
SEED: tuple[tuple[str, str, str | None, str, str | None, int, str | None, bool], ...] = (
    (
        "BIEN_TAP",
        "N1",
        "Kịch bản ngắn / nội dung ngắn",
        "Kịch bản video trend, đổi hook, caption, linetext, content thư mời, brief order thiết kế",
        "bản",
        15,
        None,
        False,
    ),
    (
        "BIEN_TAP",
        "N2",
        "Nội dung ngắn",
        "Caption, đổi hook, linetext, content thư mời, tin nhắn/email gửi khách, "
        "brief order thiết kế",
        "bản",
        60,
        None,
        False,
    ),
    (
        "BIEN_TAP",
        "N3",
        "Kịch bản video đến 3 phút",
        "Kịch bản ads, talk bác sĩ, feedback/review, video gửi khách",
        "kịch bản",
        120,
        None,
        False,
    ),
    (
        "BIEN_TAP",
        "N4",
        "Bài viết dài / kịch bản trên 3 phút",
        "Bài SEO, bài dịch vụ, trang thông tin website, kịch bản video tuyển dụng/đào tạo",
        "bài",
        120,
        None,
        False,
    ),
    (
        "BIEN_TAP",
        "N5",
        "Kế hoạch / kịch bản tổng",
        "Kế hoạch nội dung dự án hoặc tháng, kịch bản KOL/KOC, chương trình sale, sự kiện lớn, YEP",
        "bản",
        480,
        None,
        False,
    ),
    (
        "BIEN_TAP",
        "N0",
        "Việc khác",
        _FREE_EXAMPLES,
        None,
        0,
        None,
        True,
    ),
    (
        "THIET_KE",
        "T1",
        None,
        "Ảnh post, ảnh ads 1 khung, banner, thumbnail, avatar, popup, barname, thiệp sinh nhật",
        "mẫu",
        90,
        _T_NOTE,
        False,
    ),
    (
        "THIET_KE",
        "T2",
        None,
        "Cover page, standee, poster, flyer, backdrop, billboard, trang bìa brochure, "
        "bộ ảnh ads nhiều khung",
        "mẫu",
        360,
        _T_NOTE,
        False,
    ),
    (
        "THIET_KE",
        "T3",
        None,
        "Key visual chương trình, logo, concept nhận diện",
        "phương án",
        480,
        _T_NOTE,
        False,
    ),
    (
        "THIET_KE",
        "T4",
        None,
        "Trang trong brochure, slide thuyết trình, catalogue",
        "trang",
        90,
        _T_NOTE,
        False,
    ),
    (
        "THIET_KE",
        "T5",
        None,
        "Thiết kế trang con, 1 màn mobile, dựng Ladipage/Webcake",
        "trang",
        480,
        _T_NOTE,
        False,
    ),
    ("THIET_KE", "T0", "Việc khác", _FREE_EXAMPLES, None, 0, None, True),
    (
        "DUNG",
        "D1",
        "Edit video trend",
        "Video bắt trend làm theo format/âm thanh có sẵn, đến 60 giây",
        "video",
        45,
        None,
        False,
    ),
    (
        "DUNG",
        "D2",
        "Video đến 90 giây",
        "Video ads, video social, video sinh nhật, thư mời",
        "video",
        120,
        "Đã gồm: cắt ghép, nhạc, text cơ bản, phụ đề, thumbnail lấy từ khung hình, "
        "chỉnh màu cơ bản.",
        False,
    ),
    (
        "DUNG",
        "D3",
        "Video trên 90 giây đến 3 phút",
        "Video hỏi đáp bác sĩ, trailer sự kiện nhỏ, video tuyển dụng",
        "video",
        240,
        "Có diễn hoạt: chọn hình thức 'Video có diễn hoạt' (+25%). "
        "Có After Effects: chọn 'Video có After Effects' (+50%).",
        False,
    ),
    (
        "DUNG",
        "D4",
        "Video trên 3 đến 5 phút",
        "Video quy trình, trailer sự kiện lớn, video cơ sở",
        "video",
        480,
        None,
        False,
    ),
    (
        "DUNG",
        "D5",
        "Video trên 5 đến 15 phút",
        "Talk bác sĩ dài, video đào tạo",
        "video",
        480,
        None,
        False,
    ),
    (
        "DUNG",
        "D6",
        "Video trên 15 phút",
        "Ghi hình hội thảo, đào tạo trọn buổi",
        "video",
        480,
        None,
        False,
    ),
    (
        "DUNG",
        "D7",
        "Bản phái sinh từ video đã dựng",
        "Cắt bản ngắn, đổi tỷ lệ khung hình, đổi hook, ghép lại intro/outro",
        "bản",
        30,
        None,
        False,
    ),
    ("DUNG", "D0", "Việc khác", _FREE_EXAMPLES, None, 0, None, True),
)

#: The department's conversion of the old design codes (only the code changes).
LEGACY: dict[str, str] = {
    "RBTS": "T1",
    "TKBTS": "T1",
    "ABTS": "T1",
    "TAPE": "T2",
    "TKC": "T2",
    "CA": "T1",
    "CAD": "T1",
    "ATV": "T1",
    "TKTV": "T1",
    "TKKV": "T1",
    "AKV": "T1",
    "BVB": "T2",
    "BVA": "T3",
    "TKV": "T3",
    "TKBGL": "T4",
    "TKVO-TST": "T4",
    "AVO-TST": "T4",
    "TKLG": "T3",
    "TKLGDG": "T1",
    "O-SPB": "T2",
    "O-TF": "T2",
    "O-TB": "T3",
    "O-TD": "T3",
}


def seed_id(code: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"ADS:{code}")


def upgrade() -> None:
    # --- the catalogue ----------------------------------------------------------
    op.add_column(
        CODES,
        sa.Column("phase", sa.String(length=20), server_default="THIET_KE", nullable=False),
    )
    op.add_column(
        CODES, sa.Column("points", sa.Integer(), server_default=sa.text("0"), nullable=False)
    )
    op.add_column(CODES, sa.Column("unit_label", sa.String(length=40), nullable=True))
    op.add_column(CODES, sa.Column("examples", sa.Text(), nullable=True))
    op.add_column(CODES, sa.Column("note", sa.Text(), nullable=True))
    op.add_column(
        CODES,
        sa.Column("free_points", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.drop_constraint(op.f("ck_unit_design_codes_name_not_empty"), CODES, type_="check")
    op.alter_column(CODES, "name", existing_type=sa.String(length=200), nullable=True)
    op.create_check_constraint(
        op.f("ck_unit_design_codes_name_not_blank"),
        CODES,
        "name IS NULL OR length(trim(name)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_unit_design_codes_points_not_negative"), CODES, "points >= 0"
    )

    # --- orders / nodes / events -------------------------------------------------
    op.add_column(ORDERS, sa.Column("suggested_task_items", _JSON, nullable=True))
    op.add_column(ORDER_NODES, sa.Column("task_items", _JSON, nullable=True))
    op.add_column(
        ORDER_NODES,
        sa.Column("returns_fault", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        ORDER_NODES,
        sa.Column("returns_other", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(ORDER_EVENTS, sa.Column("return_reason", sa.String(length=20), nullable=True))

    op.create_table(
        POINTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.String(length=10), nullable=False),
        sa.Column("items", _JSON, nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("first_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            [f"{ORDER_NODES}.id"],
            name=op.f("fk_order_node_points_node_id_order_nodes"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{ORDERS}.id"],
            name=op.f("fk_order_node_points_order_id_orders"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_order_node_points_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_node_points")),
        sa.UniqueConstraint("node_id", "user_id", name="uq_order_node_points_node_user"),
    )
    op.create_index(
        "ix_order_node_points_user_completed", POINTS, ["user_id", "first_completed_at"]
    )

    # --- data ---------------------------------------------------------------------
    bind = op.get_bind()
    units = sa.table(ORG_UNITS, sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    unit_id = bind.execute(sa.select(units.c.id).where(units.c.code == "ADS")).scalar_one_or_none()
    if unit_id is None:
        return
    new_rows = _seed(bind, unit_id)
    _convert(bind, new_rows)


def _codes_table() -> sa.TableClause:
    return sa.table(
        CODES,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("phase", sa.String()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("points", sa.Integer()),
        sa.column("unit_label", sa.String()),
        sa.column("examples", sa.Text()),
        sa.column("note", sa.Text()),
        sa.column("free_points", sa.Boolean()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )


def _seed(bind: sa.Connection, unit_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    """Insert (or refresh) the 20 new codes; switch every other code off.
    Returns the new rows by code."""
    codes = _codes_table()
    existing = {
        str(code).lower(): row_id
        for row_id, code in bind.execute(
            sa.select(codes.c.id, codes.c.code).where(codes.c.unit_id == unit_id)
        ).all()
    }
    new_rows: dict[str, dict[str, Any]] = {}
    for order, (phase, code, name, examples, unit, points, note, free) in enumerate(SEED):
        values = {
            "phase": phase,
            "code": code,
            "name": name,
            "points": points,
            "unit_label": unit,
            "examples": examples,
            "note": note,
            "free_points": free,
            "active": True,
            "sort_order": order,
        }
        row_id = existing.get(code.lower())
        if row_id is None:
            row_id = seed_id(code)
            bind.execute(sa.insert(codes).values(id=row_id, unit_id=unit_id, **values))
        else:
            bind.execute(sa.update(codes).where(codes.c.id == row_id).values(**values))
        new_rows[code] = {"id": row_id, **values}
    keep = [row["id"] for row in new_rows.values()]
    bind.execute(
        sa.update(codes)
        .where(codes.c.unit_id == unit_id, codes.c.id.not_in(keep))
        .values(active=False)
    )
    return new_rows


def _convert_list(raw: Any, new_rows: dict[str, dict[str, Any]]) -> list[Any] | None:
    """One stored list with every old code replaced; None when nothing changed."""
    if not isinstance(raw, list):
        return None
    changed = False
    merged: dict[str, dict[str, Any]] = {}
    out: list[Any] = []
    for entry in raw:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        target = LEGACY.get(str(entry.get("code", "")).upper())
        if target is None or target not in new_rows:
            out.append(entry)
            continue
        changed = True
        row = new_rows[target]
        quantity = int(entry.get("quantity") or 1)
        if target in merged:
            merged[target]["quantity"] += quantity
            continue
        item = {
            "code_id": str(row["id"]),
            "code": target,
            "name": row["name"] or "",
            "quantity": quantity,
            "form": "NEW",
            "form_label": "Làm mới",
            "form_pct": 100,
            "unit_points": row["points"],
            "note": None,
            "free": False,
            "examples": row["examples"],
            "unit_label": row["unit_label"],
        }
        merged[target] = item
        out.append(item)
    if not changed:
        return None
    for item in merged.values():
        item["points"] = item["unit_points"] * item["quantity"]
    return out


def _convert(bind: sa.Connection, new_rows: dict[str, dict[str, Any]]) -> None:
    for table_name, column in (
        (ORDERS, "design_items"),
        (ORDER_NODES, "proposed_design_items"),
        (ASSIGNEES, "work_items"),
        (ASSIGNEE_EVENTS, "work_items"),
    ):
        table = sa.table(table_name, sa.column("id", sa.Uuid()), sa.column(column, _JSON))
        rows = bind.execute(
            sa.select(table.c.id, table.c[column]).where(table.c[column].is_not(None))
        ).all()
        for row_id, raw in rows:
            converted = _convert_list(raw, new_rows)
            if converted is not None:
                bind.execute(
                    sa.update(table).where(table.c.id == row_id).values({column: converted})
                )


def downgrade() -> None:
    bind = op.get_bind()
    codes = _codes_table()
    bind.execute(sa.delete(codes).where(codes.c.id.in_([seed_id(row[1]) for row in SEED])))
    bind.execute(sa.update(codes).values(active=True).where(codes.c.phase == "THIET_KE"))
    op.drop_index("ix_order_node_points_user_completed", table_name=POINTS)
    op.drop_table(POINTS)
    op.drop_column(ORDER_EVENTS, "return_reason")
    op.drop_column(ORDER_NODES, "returns_other")
    op.drop_column(ORDER_NODES, "returns_fault")
    op.drop_column(ORDER_NODES, "task_items")
    op.drop_column(ORDERS, "suggested_task_items")
    op.drop_constraint(op.f("ck_unit_design_codes_points_not_negative"), CODES, type_="check")
    op.drop_constraint(op.f("ck_unit_design_codes_name_not_blank"), CODES, type_="check")
    bind.execute(sa.update(codes).where(codes.c.name.is_(None)).values(name=codes.c.code))
    op.alter_column(CODES, "name", existing_type=sa.String(length=200), nullable=False)
    op.create_check_constraint(
        op.f("ck_unit_design_codes_name_not_empty"), CODES, "length(trim(name)) > 0"
    )
    op.drop_column(CODES, "free_points")
    op.drop_column(CODES, "note")
    op.drop_column(CODES, "examples")
    op.drop_column(CODES, "unit_label")
    op.drop_column(CODES, "points")
    op.drop_column(CODES, "phase")
