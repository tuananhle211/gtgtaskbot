"""Ads: platforms and durations on orders.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-09

Additive. Nothing PR reads or writes changes.

Platforms ("Nền tảng")
----------------------

``unit_platforms`` - the unit's catalogue of target platforms (TikTok,
Facebook, YouTube…). Same lifecycle as video kinds: never deleted, only
deactivated; the order snapshots the name.

No points: a platform does not affect KPI scoring.

Durations ("Thời lượng")
------------------------

``unit_durations`` - 30s, 1p, 1p30s… with configurable points. Same
lifecycle as video kinds.

``orders`` gains six columns: ``platform_id`` / ``platform_name``,
``duration_id`` / ``duration_name`` / ``duration_points`` (snapshots),
and ``note`` (free-text requirement note from the orderer).

The three platforms and three durations the Ads department starts with
are seeded for the ``ADS`` unit, with ``uuid5`` ids from fixed
namespaces.

Downgrade
---------

Drops the six order columns, the two catalogue tables and their seeds.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDERS = "orders"
ORG_UNITS = "org_units"
UNIT_PLATFORMS = "unit_platforms"
UNIT_DURATIONS = "unit_durations"
RESTRICT = "RESTRICT"

UNIT_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004200420042")
PLATFORM_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004900490049")
DURATION_SEED_NAMESPACE = uuid.UUID("5a9f3b0e-6c1d-4f8a-9b2e-004900490d49")

ADS_PLATFORMS = ("TikTok", "Facebook", "YouTube")
ADS_DURATIONS = (
    ("30s", 1),
    ("1p", 1),
    ("1p30s", 1),
)


def platform_seed_id(name: str) -> uuid.UUID:
    return uuid.uuid5(PLATFORM_SEED_NAMESPACE, f"ADS:{name}")


def duration_seed_id(name: str) -> uuid.UUID:
    return uuid.uuid5(DURATION_SEED_NAMESPACE, f"ADS:{name}")


def upgrade() -> None:
    # --- unit_platforms -------------------------------------------------------
    op.create_table(
        UNIT_PLATFORMS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name=op.f("ck_unit_platforms_name_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_unit_platforms_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit_platforms")),
    )
    op.create_index(
        "uq_unit_platforms_unit_name",
        UNIT_PLATFORMS,
        ["unit_id", sa.text("lower(name)")],
        unique=True,
    )

    # --- unit_durations -------------------------------------------------------
    op.create_table(
        UNIT_DURATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "points", sa.Numeric(precision=6, scale=2), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0", name=op.f("ck_unit_durations_name_not_empty")
        ),
        sa.CheckConstraint("points >= 0", name=op.f("ck_unit_durations_points_not_negative")),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            [f"{ORG_UNITS}.id"],
            name=op.f("fk_unit_durations_unit_id_org_units"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit_durations")),
    )
    op.create_index(
        "uq_unit_durations_unit_name",
        UNIT_DURATIONS,
        ["unit_id", sa.text("lower(name)")],
        unique=True,
    )

    # --- orders: new columns --------------------------------------------------
    op.add_column(ORDERS, sa.Column("platform_id", sa.Uuid(), nullable=True))
    op.add_column(ORDERS, sa.Column("platform_name", sa.String(length=120), nullable=True))
    op.add_column(ORDERS, sa.Column("duration_id", sa.Uuid(), nullable=True))
    op.add_column(ORDERS, sa.Column("duration_name", sa.String(length=120), nullable=True))
    op.add_column(
        ORDERS, sa.Column("duration_points", sa.Numeric(precision=6, scale=2), nullable=True)
    )
    op.add_column(ORDERS, sa.Column("note", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_orders_platform_id_unit_platforms"),
        ORDERS,
        UNIT_PLATFORMS,
        ["platform_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_index(op.f("ix_orders_platform_id"), ORDERS, ["platform_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_orders_duration_id_unit_durations"),
        ORDERS,
        UNIT_DURATIONS,
        ["duration_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_index(op.f("ix_orders_duration_id"), ORDERS, ["duration_id"], unique=False)

    # --- seed -----------------------------------------------------------------
    bind = op.get_bind()
    units = sa.table(ORG_UNITS, sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    ads_unit_id = bind.execute(
        sa.select(units.c.id).where(units.c.code == "ADS")
    ).scalar_one_or_none()
    if ads_unit_id is None:
        return

    platforms = sa.table(
        UNIT_PLATFORMS,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    bind.execute(
        sa.insert(platforms),
        [
            {
                "id": platform_seed_id(name),
                "unit_id": ads_unit_id,
                "name": name,
                "active": True,
                "sort_order": index,
            }
            for index, name in enumerate(ADS_PLATFORMS)
        ],
    )

    durations = sa.table(
        UNIT_DURATIONS,
        sa.column("id", sa.Uuid()),
        sa.column("unit_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("points", sa.Numeric()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    bind.execute(
        sa.insert(durations),
        [
            {
                "id": duration_seed_id(name),
                "unit_id": ads_unit_id,
                "name": name,
                "points": points,
                "active": True,
                "sort_order": index,
            }
            for index, (name, points) in enumerate(ADS_DURATIONS)
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_duration_id"), table_name=ORDERS)
    op.drop_constraint(op.f("fk_orders_duration_id_unit_durations"), ORDERS, type_="foreignkey")
    op.drop_index(op.f("ix_orders_platform_id"), table_name=ORDERS)
    op.drop_constraint(op.f("fk_orders_platform_id_unit_platforms"), ORDERS, type_="foreignkey")
    op.drop_column(ORDERS, "note")
    op.drop_column(ORDERS, "duration_points")
    op.drop_column(ORDERS, "duration_name")
    op.drop_column(ORDERS, "duration_id")
    op.drop_column(ORDERS, "platform_name")
    op.drop_column(ORDERS, "platform_id")
    op.drop_index("uq_unit_durations_unit_name", table_name=UNIT_DURATIONS)
    op.drop_table(UNIT_DURATIONS)
    op.drop_index("uq_unit_platforms_unit_name", table_name=UNIT_PLATFORMS)
    op.drop_table(UNIT_PLATFORMS)
