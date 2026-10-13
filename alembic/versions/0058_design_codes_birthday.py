"""ORD: design codes ("mã video") and the Design team's birthday orders.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-12

Additive. Nothing PR reads or writes changes.

Design codes
------------

``unit_design_codes`` - the Design Leader's catalogue of what the Design team
produces (``TKBTS`` "SOCIAL - Thiết kế Banner, Thumb Video, Avatar"...). Same
lifecycle as video kinds: never deleted, only deactivated. The 23 codes the
Design team works with today are seeded for the ``ADS`` unit, with ``uuid5``
ids from a fixed namespace.

``orders.design_items`` - the codes an order needs, each with a quantity, as
JSON snapshots (id, code, name, quantity). Picked by the script step when it
hands in to Design. ``order_nodes.proposed_design_items`` - on the Design
node, the different list a designer handed in with; it waits for the Design
Leader, whose approval copies it onto the order.

Birthday orders
---------------

``orders.category`` (``STANDARD`` / ``BIRTHDAY``; every existing order is
``STANDARD``) and three columns only a birthday order fills:
``birthday_branch``, ``birthday_gender``, ``birthday_on``.

Downgrade
---------

Drops the six columns and the catalogue with its seeds. Birthday orders stay
as ordinary process ``T`` orders.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDERS = "orders"
ORDER_NODES = "order_nodes"
ORG_UNITS = "org_units"
UNIT_DESIGN_CODES = "unit_design_codes"
RESTRICT = "RESTRICT"

DESIGN_CODE_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-005800580058")

#: (name, code), in the Design team's own order.
ADS_DESIGN_CODES: tuple[tuple[str, str], ...] = (
    ("SOCIAL - Resize Banner, Thumb Video, Keyvisual,frame", "RBTS"),
    ("SOCIAL - Thiết kế Banner, Thumb Video, Avatar", "TKBTS"),
    ("SOCIAL - Adapt banner, thumb, cover", "ABTS"),
    ("SOCIAL - Thiết kế ảnh đại diện", "TAPE"),
    ("SOCIAL - Thiết kế cover page (làm mới)", "TKC"),
    ("SOCIAL - Retouch ảnh phức tạp", "CA"),
    ("SOCIAL - Retouch ảnh đơn giản", "CAD"),
    ("VIDEO - Adapt text, popup,barname (có mẫu sẵn)", "ATV"),
    ("VIDEO - Thiết kế popup barname (làm mới)", "TKTV"),
    ("VIDEO - Thiết kế frame (làm mới)", "TKKV"),
    ("VIDEO - Adapt frame (có mẫu sẵn)", "AKV"),
    ("VIDEO - Bộ bài ADS - Basic (dùng phần lớn form có sẵn)", "BVB"),
    ("VIDEO - Bộ bài ADS - Advance ( làm mới, sáng tạo)", "BVA"),
    ("BRAND - Thiết kế Keyvisual", "TKV"),
    ("BRAND - Thiết kế 1 page brand Guideline", "TKBGL"),
    ("BRAND - Thiết kế 1 slide thuyết trình (làm mới)", "TKVO-TST"),
    ("BRAND - Adapt 1 slide thuyết trình (theo mẫu)", "AVO-TST"),
    ("BRAND - Thiết kế Logo phức tạp", "TKLG"),
    ("BRAND - Thiết kế Logo đơn giản", "TKLGDG"),
    ("OFFLINE - Standee/ Poster/ Billboard", "O-SPB"),
    ("OFFLINE - Thiết kế Flyer", "O-TF"),
    ("OFFLINE - Thiết kế Brochure", "O-TB"),
    ("OFFLINE - Thiết kế Backdrop", "O-TD"),
)

#: Every ``order_events.kind`` value after this revision (0053 declared the
#: first twenty; the column is a plain VARCHAR, so the three new ones need no
#: DDL).
EVENT_KIND_VALUES = (
    "SUBMITTED",
    "RESUBMITTED",
    "ORDER_APPROVED",
    "ORDER_RETURNED",
    "NODE_ACTIVATED",
    "NODE_ASSIGNED",
    "NODE_ACCEPTED",
    "WORK_SUBMITTED",
    "NODE_APPROVED",
    "NODE_RETURNED",
    "LINK_ATTACHED",
    "VIDEO_APPROVED",
    "VIDEO_RETURNED",
    "FINAL_APPROVED",
    "FINAL_RETURNED",
    "PRIORITY_SET",
    "PRIORITY_CLEARED",
    "CANCELLED",
    "PLAN_SET",
    "DEADLINE_EXCEEDED",
    "DESIGN_CODES_SET",
    "DESIGN_CODES_PROPOSED",
    "BIRTHDAY_EDITED",
)

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def design_code_seed_id(code: str) -> uuid.UUID:
    return uuid.uuid5(DESIGN_CODE_SEED_NAMESPACE, f"ADS:{code}")


def upgrade() -> None:
    # --- unit_design_codes ----------------------------------------------------
    op.create_table(
        UNIT_DESIGN_CODES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
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
        sa.CheckConstraint(
            "length(trim(code)) > 0", name=op.f("ck_unit_design_codes_code_not_empty")
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name=op.f("ck_unit_design_codes_name_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_unit_design_codes_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit_design_codes")),
    )
    op.create_index(
        "uq_unit_design_codes_unit_code",
        UNIT_DESIGN_CODES,
        ["unit_id", sa.text("lower(code)")],
        unique=True,
    )

    # --- orders / order_nodes -------------------------------------------------
    op.add_column(
        ORDERS,
        sa.Column("category", sa.String(length=20), server_default="STANDARD", nullable=False),
    )
    op.create_index(op.f("ix_orders_category"), ORDERS, ["category"], unique=False)
    op.add_column(ORDERS, sa.Column("design_items", _JSON, nullable=True))
    op.add_column(ORDERS, sa.Column("birthday_branch", sa.String(length=120), nullable=True))
    op.add_column(ORDERS, sa.Column("birthday_gender", sa.String(length=10), nullable=True))
    op.add_column(ORDERS, sa.Column("birthday_on", sa.Date(), nullable=True))
    op.add_column(ORDER_NODES, sa.Column("proposed_design_items", _JSON, nullable=True))

    # --- seed -----------------------------------------------------------------
    bind = op.get_bind()
    units = sa.table(ORG_UNITS, sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    ads_unit_id = bind.execute(
        sa.select(units.c.id).where(units.c.code == "ADS")
    ).scalar_one_or_none()
    if ads_unit_id is None:
        return
    codes = sa.table(
        UNIT_DESIGN_CODES,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    bind.execute(
        sa.insert(codes),
        [
            {
                "id": design_code_seed_id(code),
                "unit_id": ads_unit_id,
                "code": code,
                "name": name,
                "active": True,
                "sort_order": index,
            }
            for index, (name, code) in enumerate(ADS_DESIGN_CODES)
        ],
    )


def downgrade() -> None:
    op.drop_column(ORDER_NODES, "proposed_design_items")
    op.drop_column(ORDERS, "birthday_on")
    op.drop_column(ORDERS, "birthday_gender")
    op.drop_column(ORDERS, "birthday_branch")
    op.drop_column(ORDERS, "design_items")
    op.drop_index(op.f("ix_orders_category"), table_name=ORDERS)
    op.drop_column(ORDERS, "category")
    op.drop_index("uq_unit_design_codes_unit_code", table_name=UNIT_DESIGN_CODES)
    op.drop_table(UNIT_DESIGN_CODES)
