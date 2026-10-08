"""Ads: the flexible process and the unit's video kinds.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-08

Additive. Nothing PR reads or writes changes.

The process ("Quy trình")
-------------------------

``orders.video_type`` keeps its name and now holds the **process code**: the
letters of the production nodes the order visits, in pipeline order - ``B``
(Biên kịch), ``T`` (Design), ``D`` (Dựng). The three fixed types ``D``, ``TD``
and ``BTD`` are three of the seven codes; ``B``, ``T``, ``BT`` and ``BD`` are
new. The column (and ``order_work_rules.video_type``) is a ``VARCHAR(5)``
written by a non-native SQLAlchemy enum **without** a ``CHECK`` constraint
(``0042`` created none: ``create_constraint`` is off by default in
SQLAlchemy 2), so admitting the new codes needs no DDL; ``VIDEO_TYPE_VALUES``
below records the full set for the schema-parity test.

The design-link rule generalises: an edit with no design node must bring the
design with it. ``0042``'s ``CHECK (video_type <> 'D' OR design_link IS NOT
NULL)`` is replaced by the same rule over every such process
(``video_type NOT IN ('D', 'BD') OR design_link IS NOT NULL``). Every row
that satisfied the old check satisfies the new one (``BD`` did not exist), so
the swap cannot fail on existing data.

Video kinds ("Loại video")
--------------------------

* ``unit_video_kinds`` - a unit's catalogue: name, points (default 1), active,
  display order. One name per unit regardless of case (a unique index on
  ``(unit_id, lower(name))``). Never deleted, only deactivated.
* ``orders.video_kind_id`` (nullable, ``RESTRICT``) and a snapshot of the
  kind's ``video_kind_name`` / ``video_kind_points`` taken at submission, so
  editing the catalogue never rewrites an order.

The eleven kinds the Ads department starts with are seeded for the ``ADS``
unit (when its ``0042`` row exists), with ``uuid5`` ids from a fixed
namespace so the seed is the same on every database. Like ``0042``'s
registry, this is a bounded exception to "no taxonomy in migrations": the
create form requires a kind as soon as the unit has one, and the department
wants the list on the first day.

Downgrade
---------

Refuses - and changes nothing - while any order (or work rule) uses one of the
four new process codes: ``0043`` cannot represent them. Otherwise it restores
``0042``'s design-link check, drops the three ``orders`` columns (the kind
snapshots go with them) and the catalogue table.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDERS = "orders"
ORDER_WORK_RULES = "order_work_rules"
ORG_UNITS = "org_units"
UNIT_VIDEO_KINDS = "unit_video_kinds"
RESTRICT = "RESTRICT"

#: Every process code, in the domain enum's order (``OrderVideoType``).
VIDEO_TYPE_VALUES = ("D", "TD", "BTD", "B", "T", "BT", "BD")
#: The codes this revision introduces; ``0043`` knows only the first three.
NEW_VIDEO_TYPE_VALUES = ("B", "T", "BT", "BD")

OLD_DESIGN_LINK_CHECK = "design_link_required_for_d"
OLD_DESIGN_LINK_RULE = "video_type <> 'D' OR design_link IS NOT NULL"
NEW_DESIGN_LINK_CHECK = "design_link_required_without_design"
NEW_DESIGN_LINK_RULE = "video_type NOT IN ('D', 'BD') OR design_link IS NOT NULL"

#: ``0042``'s namespace for the unit ids (``unit_seed_id``).
UNIT_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004200420042")
KIND_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004400440044")

#: The Ads department's starting catalogue, in display order, one point each.
ADS_VIDEO_KINDS = (
    "Video full diễn hoạt",
    "Video live full diễn hoạt",
    "Video live 50% diễn hoạt",
    "Sửa khác (source, nhạc, khung,...)",
    "Short video",
    "Quay nửa ngày",
    "Quay cả ngày",
    "Quay khác",
    "Kịch bản quay mới",
    "Kịch bản khác",
    "Tổ chức sản xuất",
)


def kind_seed_id(name: str) -> uuid.UUID:
    return uuid.uuid5(KIND_SEED_NAMESPACE, f"ADS:{name}")


def upgrade() -> None:
    op.create_table(
        UNIT_VIDEO_KINDS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "points", sa.Numeric(precision=6, scale=2), server_default=sa.text("1"), nullable=False
        ),
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
            "length(trim(name)) > 0", name=op.f("ck_unit_video_kinds_name_not_empty")
        ),
        sa.CheckConstraint("points >= 0", name=op.f("ck_unit_video_kinds_points_not_negative")),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_unit_video_kinds_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit_video_kinds")),
    )
    op.create_index(
        "uq_unit_video_kinds_unit_name",
        UNIT_VIDEO_KINDS,
        ["unit_id", sa.text("lower(name)")],
        unique=True,
    )

    op.add_column(ORDERS, sa.Column("video_kind_id", sa.Uuid(), nullable=True))
    op.add_column(ORDERS, sa.Column("video_kind_name", sa.String(length=120), nullable=True))
    op.add_column(
        ORDERS, sa.Column("video_kind_points", sa.Numeric(precision=6, scale=2), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_orders_video_kind_id_unit_video_kinds"),
        ORDERS,
        UNIT_VIDEO_KINDS,
        ["video_kind_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_index(op.f("ix_orders_video_kind_id"), ORDERS, ["video_kind_id"], unique=False)

    op.drop_constraint(op.f(f"ck_orders_{OLD_DESIGN_LINK_CHECK}"), ORDERS, type_="check")
    op.create_check_constraint(
        op.f(f"ck_orders_{NEW_DESIGN_LINK_CHECK}"), ORDERS, NEW_DESIGN_LINK_RULE
    )

    bind = op.get_bind()
    units = sa.table(ORG_UNITS, sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    ads_unit_id = bind.execute(
        sa.select(units.c.id).where(units.c.code == "ADS")
    ).scalar_one_or_none()
    if ads_unit_id is None:
        return
    kinds = sa.table(
        UNIT_VIDEO_KINDS,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("points", sa.Numeric()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    bind.execute(
        sa.insert(kinds),
        [
            {
                "id": kind_seed_id(name),
                "unit_id": ads_unit_id,
                "name": name,
                "points": 1,
                "active": True,
                "sort_order": index,
            }
            for index, name in enumerate(ADS_VIDEO_KINDS)
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    codes = ", ".join(f"'{code}'" for code in NEW_VIDEO_TYPE_VALUES)
    orders = bind.execute(
        sa.text(f"SELECT count(*) FROM {ORDERS} WHERE video_type IN ({codes})")
    ).scalar_one()
    rules = bind.execute(
        sa.text(f"SELECT count(*) FROM {ORDER_WORK_RULES} WHERE video_type IN ({codes})")
    ).scalar_one()
    if orders or rules:
        raise RuntimeError(
            f"0044 downgrade refused: {orders} order(s) and {rules} work rule(s) use a process "
            f"code 0043 cannot represent ({', '.join(NEW_VIDEO_TYPE_VALUES)}). "
            "Cancel and re-create or remap them first."
        )
    op.drop_constraint(op.f(f"ck_orders_{NEW_DESIGN_LINK_CHECK}"), ORDERS, type_="check")
    op.create_check_constraint(
        op.f(f"ck_orders_{OLD_DESIGN_LINK_CHECK}"), ORDERS, OLD_DESIGN_LINK_RULE
    )
    op.drop_index(op.f("ix_orders_video_kind_id"), table_name=ORDERS)
    op.drop_constraint(op.f("fk_orders_video_kind_id_unit_video_kinds"), ORDERS, type_="foreignkey")
    op.drop_column(ORDERS, "video_kind_points")
    op.drop_column(ORDERS, "video_kind_name")
    op.drop_column(ORDERS, "video_kind_id")
    op.drop_index("uq_unit_video_kinds_unit_name", table_name=UNIT_VIDEO_KINDS)
    op.drop_table(UNIT_VIDEO_KINDS)
