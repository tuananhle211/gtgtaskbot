"""M4B: recurring work templates, the people they name, and the scheduler's ledger.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-04

Three tables, all new, and **nothing existing is touched.** M4A required no
schema change at all - manual work was already M1's - so everything here is
recurring work and nothing else. No column on ``pr_work_items`` changes meaning
because these exist: a generated item is an ordinary work item whose
``source_type`` is ``RECURRING``, which M1 declared in ``0032`` precisely so this
migration would not have to widen an enum on a populated table.

Why three tables rather than one
---------------------------------

* ``pr_work_recurring_templates`` - *the routine, and management's standing
  authorization for it*. Long-lived, edited in place, versioned by
  ``revision_no``;
* ``pr_work_recurring_template_contributors`` - *who it is for*. A list, not a
  column, because "one job each" needs to iterate it and a JSON array of user
  ids would be a foreign key the database could not enforce;
* ``pr_work_recurring_occurrences`` - **the durable scheduler ledger**. One row
  per scheduled firing, written before the work exists and settled in the same
  transaction as the work. It is what makes "why is there no work for Tuesday" a
  question with a stored answer, and its unique constraint over
  ``(template_id, occurrence_key)`` is what makes two beat workers sweeping the
  same second produce one occurrence rather than two.

The occurrence table is deliberately not a log. Dropping it would not lose
history that exists elsewhere; it would remove the only structure that stops
duplicate generation under concurrency.

No backfill, anywhere
----------------------

Not one occurrence is created here, and no existing work item is re-keyed. A
template that has never been activated has no cursor, and the activation
boundary in the generator is what stops the first sweep of a template whose
``start_date`` is six weeks old from filing six weeks of work.

Downgrade
----------

Drops the three tables in dependency order and nothing else. It loses the
recurring configuration and the scheduler's provenance; the work items already
generated survive untouched, still carrying their ``recurring:`` source keys -
which then name occurrence rows that no longer exist. That is deliberate: the
alternative is a downgrade that deletes real, validated, possibly counted work,
and no schema rollback should be able to do that.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEMPLATES = "pr_work_recurring_templates"
CONTRIBUTORS = "pr_work_recurring_template_contributors"
OCCURRENCES = "pr_work_recurring_occurrences"

WORK_TYPES = "pr_work_types"
PERIODS = "pr_reporting_periods"
USERS = "users"
RESTRICT = "RESTRICT"
CASCADE = "CASCADE"

#: Enum columns are ``VARCHAR`` + CHECK, never a PostgreSQL ``ENUM`` - the
#: repository-wide rule, so adding a frequency is a code change rather than an
#: ``ALTER TYPE`` and the values stay greppable in the database.
TEMPLATE_STATUS_VALUES = ("DRAFT", "ACTIVE", "PAUSED", "ENDED")
FREQUENCY_VALUES = ("DAILY", "WEEKLY", "MONTHLY")
OCCURRENCE_STATE_VALUES = (
    "PENDING",
    "GENERATED",
    "SKIPPED_CLOSED_PERIOD",
    "FAILED_RETRYABLE",
)
#: M4A's mode, stored for the first time. It is not a fact about a work item -
#: an item is fully described by its contributions - but it *is* a fact about a
#: template, because it has to survive until the next occurrence.
ASSIGNMENT_MODE_VALUES = ("SHARED_WORK", "SEPARATE_PER_ASSIGNEE")
PRIORITY_VALUES = ("LOW", "NORMAL", "HIGH", "URGENT")

#: Mirrors ``meobot.db.base.JSONColumn`` exactly - ``JSONB`` on PostgreSQL,
#: plain ``JSON`` elsewhere. Spelled out rather than imported because a
#: migration must not depend on application code that may have moved on.
JSON_COLUMN = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

#: The same precision ``pr_work_items.quantity`` uses. A template's number
#: becomes an item's number unchanged, and two precisions would make that
#: "unchanged" a rounding.
QUANTITY = sa.Numeric(12, 2)


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
    # --- the routine ------------------------------------------------------
    op.create_table(
        TEMPLATES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assignment_mode",
            sa.Enum(
                *ASSIGNMENT_MODE_VALUES,
                name="pr_work_assignment_mode",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        # Null exactly when the work type is not measured by quantity. The rule
        # is a service rule rather than a CHECK because it depends on a column
        # in another table - see ``require_quantity_for_basis``.
        sa.Column("quantity", QUANTITY, nullable=True),
        sa.Column(
            "priority",
            sa.Enum(*PRIORITY_VALUES, name="pr_priority", native_enum=False, length=20),
            nullable=False,
            server_default="NORMAL",
        ),
        sa.Column(
            "frequency",
            sa.Enum(*FREQUENCY_VALUES, name="pr_recurring_frequency", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("weekdays", JSON_COLUMN, nullable=False, server_default="[]"),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        # A local wall clock, not an instant: 09:00 means 09:00 in Ho Chi Minh
        # City in June and in December alike.
        sa.Column("run_time", sa.Time(), nullable=False),
        sa.Column("due_after_hours", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                *TEMPLATE_STATUS_VALUES,
                name="pr_recurring_template_status",
                native_enum=False,
                length=20,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        # **The cursor.** Not ``last_generated_at``: an occurrence declined
        # because its month was closed has been evaluated without being
        # generated, and a cursor that only moved on success would re-walk
        # settled ground for ever.
        sa.Column("last_evaluated_occurrence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("activated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_recurring_templates")),
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_pr_work_recurring_templates_work_type_id_pr_work_types"),
            ondelete=RESTRICT,
        ),
        # **Every column naming a person is ``RESTRICT``** - the repository-wide
        # PR rule. ``activated_by_user_id`` is the standing authorization every
        # generated job is filed under; ``SET NULL`` would let deleting an
        # account erase who asked for a year of somebody's routine work.
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_recurring_templates_created_by"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_recurring_templates_activated_by"),
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name=op.f("ck_pr_work_recurring_templates_name_not_empty"),
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name=op.f("ck_pr_work_recurring_templates_quantity_positive"),
        ),
        sa.CheckConstraint(
            "revision_no >= 1", name=op.f("ck_pr_work_recurring_templates_revision_positive")
        ),
        sa.CheckConstraint(
            "due_after_hours IS NULL OR (due_after_hours > 0 AND due_after_hours <= 8760)",
            name=op.f("ck_pr_work_recurring_templates_due_after_hours_sane"),
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name=op.f("ck_pr_work_recurring_templates_date_range_ordered"),
        ),
        # A WEEKLY template with no weekdays can never fire and a MONTHLY one
        # with no day fires whenever the reader guesses. Both are refused in the
        # domain too; this is the floor under it.
        sa.CheckConstraint(
            "(frequency = 'MONTHLY' AND day_of_month IS NOT NULL "
            "  AND day_of_month BETWEEN 1 AND 31) "
            "OR (frequency <> 'MONTHLY' AND day_of_month IS NULL)",
            name=op.f("ck_pr_work_recurring_templates_day_of_month_matches_frequency"),
        ),
    )
    op.create_index(
        "ix_pr_work_recurring_templates_sweep",
        TEMPLATES,
        ["status", "last_evaluated_occurrence_at"],
    )
    op.create_index("ix_pr_work_recurring_templates_work_type", TEMPLATES, ["work_type_id"])
    op.create_index(op.f("ix_pr_work_recurring_templates_status"), TEMPLATES, ["status"])

    # --- who it is for ----------------------------------------------------
    op.create_table(
        CONTRIBUTORS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_recurring_template_contributors")),
        # ``CASCADE`` towards the template and ``RESTRICT`` towards the person,
        # the same pairing ``pr_user_capability_channels`` uses: these rows are
        # parts of the template rather than facts of their own, and a template
        # that may be deleted at all is one that has generated nothing.
        sa.ForeignKeyConstraint(
            ["template_id"],
            [f"{TEMPLATES}.id"],
            name=op.f("fk_pr_work_recurring_template_contributors_template"),
            ondelete=CASCADE,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_recurring_template_contributors_user"),
            ondelete=RESTRICT,
        ),
        sa.UniqueConstraint("template_id", "user_id", name="uq_template_contributor"),
        sa.CheckConstraint(
            "display_order >= 0",
            name=op.f("ck_pr_work_recurring_template_contributors_display_order_not_negative"),
        ),
    )
    op.create_index("ix_pr_recurring_contributors_user", CONTRIBUTORS, ["user_id"])
    op.create_index(
        op.f("ix_pr_work_recurring_template_contributors_template_id"),
        CONTRIBUTORS,
        ["template_id"],
    )

    # --- the scheduler's ledger -------------------------------------------
    op.create_table(
        OCCURRENCES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=40), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("template_revision_no", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                *OCCURRENCE_STATE_VALUES,
                name="pr_recurring_occurrence_state",
                native_enum=False,
                length=30,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("work_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reporting_period_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_recurring_occurrences")),
        # ``RESTRICT``, unlike the contributor list: an occurrence is provenance
        # for work that exists, and a template with occurrences is not deletable.
        sa.ForeignKeyConstraint(
            ["template_id"],
            [f"{TEMPLATES}.id"],
            name=op.f("fk_pr_work_recurring_occurrences_template"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id"],
            [f"{PERIODS}.id"],
            name=op.f("fk_pr_work_recurring_occurrences_period"),
            ondelete=RESTRICT,
        ),
        # **The idempotency guarantee.** Two workers sweeping the same template
        # in the same second insert the same key; one loses, and losing is the
        # mechanism working rather than an error.
        sa.UniqueConstraint("template_id", "occurrence_key", name="uq_template_occurrence"),
        sa.CheckConstraint(
            "length(trim(occurrence_key)) > 0",
            name=op.f("ck_pr_work_recurring_occurrences_occurrence_key_not_empty"),
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_pr_work_recurring_occurrences_attempts_not_negative")
        ),
        sa.CheckConstraint(
            "work_item_count >= 0",
            name=op.f("ck_pr_work_recurring_occurrences_work_item_count_not_negative"),
        ),
        sa.CheckConstraint(
            "template_revision_no >= 1",
            name=op.f("ck_pr_work_recurring_occurrences_revision_positive"),
        ),
        # Work exists exactly when the state says it does.
        sa.CheckConstraint(
            "(state = 'GENERATED') = (generated_at IS NOT NULL)",
            name=op.f("ck_pr_work_recurring_occurrences_generated_state_has_timestamp"),
        ),
        sa.CheckConstraint(
            "state = 'GENERATED' OR work_item_count = 0",
            name=op.f("ck_pr_work_recurring_occurrences_only_generated_has_work"),
        ),
    )
    op.create_index(
        "ix_pr_recurring_occurrences_state",
        OCCURRENCES,
        ["template_id", "state", "scheduled_for"],
    )
    op.create_index(
        op.f("ix_pr_work_recurring_occurrences_template_id"), OCCURRENCES, ["template_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pr_work_recurring_occurrences_template_id"), table_name=OCCURRENCES)
    op.drop_index("ix_pr_recurring_occurrences_state", table_name=OCCURRENCES)
    op.drop_table(OCCURRENCES)
    op.drop_index(
        op.f("ix_pr_work_recurring_template_contributors_template_id"), table_name=CONTRIBUTORS
    )
    op.drop_index("ix_pr_recurring_contributors_user", table_name=CONTRIBUTORS)
    op.drop_table(CONTRIBUTORS)
    op.drop_index(op.f("ix_pr_work_recurring_templates_status"), table_name=TEMPLATES)
    op.drop_index("ix_pr_work_recurring_templates_work_type", table_name=TEMPLATES)
    op.drop_index("ix_pr_work_recurring_templates_sweep", table_name=TEMPLATES)
    op.drop_table(TEMPLATES)
