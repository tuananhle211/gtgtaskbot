"""Period containers, work results, and the counted amount M6 prices.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-10

The rule this revision carries into the schema, stated once::

    WORK = actual work performed.   KPI = target only.

Four additive changes and one bounded data step. No work item, contribution,
allocation, plan, quota or score row is deleted or rewritten.

``pr_work_items``: the period container
-----------------------------------------

Two nullable columns - ``reporting_period_id`` and ``subject_user_id`` - a
CHECK that they are set together, and a **partial unique index** over
``(work_type_id, reporting_period_id, subject_user_id) WHERE
reporting_period_id IS NOT NULL``. A row with both columns is *one work stream
for one employee for one month*; results accumulate inside it and its
``quantity`` is the sum of the results a validator counted. A row with neither
is a one-off job exactly as ``0032`` shipped it. The index is what makes "27
customers in September" one row rather than twenty, and every creation path is
a get-or-create against it.

``ck_pr_work_items_quantity_positive`` is replaced by a form that allows **zero
on a container and nowhere else**: an empty stream genuinely holds zero, and
has to be able to say so.

``pr_work_results``: what was declared
----------------------------------------

One row per declared or system-contributed result: quantity, the generic
``label`` / ``link`` / ``note``, a source (``MANUAL`` or a contributing module)
with a ``source_key`` for everything but ``MANUAL``, and M1's own
``PENDING / COUNTED / EXCLUDED`` vocabulary with the same *counted-at pairs
with counted* CHECK the contribution table has.

``uq_pr_work_results_source`` over ``(source_type, source_key)``, partial over
``source_key IS NOT NULL``, is the idempotency rule the Content mapping needs:
one content milestone is one result however many times its projection runs.

``pr_work_recurring_templates.accumulate_by_period``
------------------------------------------------------

``false`` for every existing routine, so nothing already running changes shape.
``true`` turns a routine into a stream: each firing ensures the month's
container per assignee rather than filing a job with a fixed quantity.

``pr_work_score_allocations.counted_amount``
----------------------------------------------

The amount M6 now prices - the whole counted amount, inside the cap, over it
or with no quota at all. Backfilled from ``eligible_amount``, which is the
only amount those rows ever priced, so a stored provenance row keeps saying
what it said. ``eligible_amount`` stays as M2's decomposition.

The data step
--------------

``default_unit`` becomes ``CUSTOMER`` on work types that are recognisably
*Tìm khách hàng* - a code containing ``CUSTOMER`` or ``KHACH``, or a name
containing "khách hàng" - so the stream reads "khách hàng" rather than
"sản phẩm". Historical work items keep the unit they were filed with; the
period-container patch makes the unit editable in *Cấu hình* for any type this
does not reach.

Downgrade
----------

Drops the results table, the four column groups and the index, restores the
original quantity CHECK, and maps the three units this patch introduced
(``CUSTOMER``, ``SCRIPT``, ``ORDER``) back to ``ITEM`` wherever they are stored
so the older enum never meets a value it does not know. **Results recorded
under this revision are lost on downgrade** and containers become ordinary
``ACCEPTED`` items; take a dump first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_ITEMS = "pr_work_items"
WORK_RESULTS = "pr_work_results"
TEMPLATES = "pr_work_recurring_templates"
SCORE_ALLOCATIONS = "pr_work_score_allocations"
WORK_TYPES = "pr_work_types"
WORK_QUOTAS = "pr_work_quotas"
QUOTA_ALLOCATIONS = "pr_work_quota_allocations"
PERIODS = "pr_reporting_periods"
USERS = "users"
RESTRICT = "RESTRICT"

RESULT_SOURCE_VALUES = ("MANUAL", "CONTENT", "SEEDING", "CRM", "SYSTEM", "OTHER")
RESULT_STATUS_VALUES = ("PENDING", "COUNTED", "EXCLUDED")
#: The units this revision introduces, and what they fold back to on downgrade.
NEW_UNITS = ("CUSTOMER", "SCRIPT", "ORDER")

PERIOD_CONTAINER = sa.text("reporting_period_id IS NOT NULL")
KEYED_RESULT = sa.text("source_key IS NOT NULL")


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    # --- the period container ---------------------------------------------
    op.add_column(WORK_ITEMS, sa.Column("reporting_period_id", sa.Uuid(), nullable=True))
    op.add_column(WORK_ITEMS, sa.Column("subject_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_pr_work_items_reporting_period_id_pr_reporting_periods"),
        WORK_ITEMS,
        PERIODS,
        ["reporting_period_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_foreign_key(
        op.f("fk_pr_work_items_subject_user_id_users"),
        WORK_ITEMS,
        USERS,
        ["subject_user_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_index(
        op.f("ix_pr_work_items_reporting_period_id"), WORK_ITEMS, ["reporting_period_id"]
    )
    op.create_check_constraint(
        "period_container_has_subject",
        WORK_ITEMS,
        "(reporting_period_id IS NULL) = (subject_user_id IS NULL)",
    )
    # **One stream per employee, work type and month.**
    op.create_index(
        "uq_pr_work_items_period_container",
        WORK_ITEMS,
        ["work_type_id", "reporting_period_id", "subject_user_id"],
        unique=True,
        postgresql_where=PERIOD_CONTAINER,
        sqlite_where=PERIOD_CONTAINER,
    )
    # Zero is a real actual for a stream nobody has validated yet.
    # Bare names: the metadata naming convention prefixes ``ck_<table>_`` on
    # both the create and the drop, exactly as ``0038`` relies on.
    op.drop_constraint("quantity_positive", WORK_ITEMS, type_="check")
    op.create_check_constraint(
        "quantity_positive",
        WORK_ITEMS,
        "quantity IS NULL OR quantity > 0 OR reporting_period_id IS NOT NULL",
    )

    # --- the results ------------------------------------------------------
    op.create_table(
        WORK_RESULTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "source_type",
            sa.Enum(
                *RESULT_SOURCE_VALUES, name="pr_work_result_source", native_enum=False, length=20
            ),
            nullable=False,
            server_default="MANUAL",
        ),
        sa.Column("source_key", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                *RESULT_STATUS_VALUES, name="pr_work_result_status", native_enum=False, length=20
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("reported_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("excluded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("excluded_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_results")),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            [f"{WORK_ITEMS}.id"],
            name=op.f("fk_pr_work_results_work_item_id_pr_work_items"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_results_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reported_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_results_reported_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["counted_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_results_counted_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["excluded_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_results_excluded_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_pr_work_results_quantity_positive")),
        sa.CheckConstraint(
            "(status = 'COUNTED') = (counted_at IS NOT NULL)",
            name=op.f("ck_pr_work_results_counted_at_matches_status"),
        ),
        sa.CheckConstraint(
            "source_type = 'MANUAL' OR source_key IS NOT NULL",
            name=op.f("ck_pr_work_results_derived_result_is_keyed"),
        ),
    )
    op.create_index(op.f("ix_pr_work_results_work_item_id"), WORK_RESULTS, ["work_item_id"])
    op.create_index(op.f("ix_pr_work_results_user_id"), WORK_RESULTS, ["user_id"])
    op.create_index(op.f("ix_pr_work_results_status"), WORK_RESULTS, ["status"])
    op.create_index(
        "ix_pr_work_results_item_status", WORK_RESULTS, ["work_item_id", "status", "reported_at"]
    )
    op.create_index("ix_pr_work_results_user_reported", WORK_RESULTS, ["user_id", "reported_at"])
    # **The idempotency guarantee** for results another module contributes.
    op.create_index(
        "uq_pr_work_results_source",
        WORK_RESULTS,
        ["source_type", "source_key"],
        unique=True,
        postgresql_where=KEYED_RESULT,
        sqlite_where=KEYED_RESULT,
    )

    # --- the routine that accumulates -------------------------------------
    op.add_column(
        TEMPLATES,
        sa.Column(
            "accumulate_by_period",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # --- the amount M6 prices ----------------------------------------------
    op.add_column(
        SCORE_ALLOCATIONS, sa.Column("counted_amount", sa.Numeric(12, 2), nullable=True)
    )
    op.execute(
        sa.text(
            f"UPDATE {SCORE_ALLOCATIONS} SET counted_amount = eligible_amount "
            "WHERE counted_amount IS NULL"
        )
    )

    # --- "Tìm khách hàng" reads in customers ------------------------------
    op.execute(
        sa.text(
            f"UPDATE {WORK_TYPES} SET default_unit = 'CUSTOMER' "
            "WHERE default_unit <> 'CUSTOMER' AND ("
            "  upper(code) LIKE '%CUSTOMER%' OR upper(code) LIKE '%KHACH%' "
            "  OR lower(name) LIKE '%khách hàng%'"
            ")"
        )
    )


def downgrade() -> None:
    # The older enum has no CUSTOMER / SCRIPT / ORDER. Fold them into ITEM
    # everywhere a unit is stored, so no reader under 0038 meets a stranger.
    for table in (WORK_TYPES,):
        op.execute(
            sa.text(
                f"UPDATE {table} SET default_unit = 'ITEM' "
                f"WHERE default_unit IN ({', '.join(repr(one) for one in NEW_UNITS)})"
            )
        )
    for table in (WORK_ITEMS, WORK_QUOTAS, QUOTA_ALLOCATIONS):
        op.execute(
            sa.text(
                f"UPDATE {table} SET unit = 'ITEM' "
                f"WHERE unit IN ({', '.join(repr(one) for one in NEW_UNITS)})"
            )
        )

    op.drop_column(SCORE_ALLOCATIONS, "counted_amount")
    op.drop_column(TEMPLATES, "accumulate_by_period")

    op.drop_index("uq_pr_work_results_source", table_name=WORK_RESULTS)
    op.drop_index("ix_pr_work_results_user_reported", table_name=WORK_RESULTS)
    op.drop_index("ix_pr_work_results_item_status", table_name=WORK_RESULTS)
    op.drop_index(op.f("ix_pr_work_results_status"), table_name=WORK_RESULTS)
    op.drop_index(op.f("ix_pr_work_results_user_id"), table_name=WORK_RESULTS)
    op.drop_index(op.f("ix_pr_work_results_work_item_id"), table_name=WORK_RESULTS)
    op.drop_table(WORK_RESULTS)

    # A container's derived zero is not representable under the old CHECK:
    # give it the null the old schema means by "no quantity".
    op.execute(
        sa.text(
            f"UPDATE {WORK_ITEMS} SET quantity = NULL, unit = NULL "
            "WHERE reporting_period_id IS NOT NULL AND quantity = 0"
        )
    )
    op.drop_constraint("quantity_positive", WORK_ITEMS, type_="check")
    op.create_check_constraint(
        "quantity_positive", WORK_ITEMS, "quantity IS NULL OR quantity > 0"
    )
    op.drop_index("uq_pr_work_items_period_container", table_name=WORK_ITEMS)
    op.drop_constraint("period_container_has_subject", WORK_ITEMS, type_="check")
    op.drop_index(op.f("ix_pr_work_items_reporting_period_id"), table_name=WORK_ITEMS)
    op.drop_constraint(
        op.f("fk_pr_work_items_subject_user_id_users"), WORK_ITEMS, type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_pr_work_items_reporting_period_id_pr_reporting_periods"),
        WORK_ITEMS,
        type_="foreignkey",
    )
    op.drop_column(WORK_ITEMS, "subject_user_id")
    op.drop_column(WORK_ITEMS, "reporting_period_id")
