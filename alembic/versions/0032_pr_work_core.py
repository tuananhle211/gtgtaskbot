"""M1: the Work Ledger - work, whose it is, and whether it counts.

Five new tables and **nothing else**. No column on any existing table changes,
no existing row is read or written, and there is no backfill: ``pr_tasks``,
``pr_content_items`` and every approval, transition and publication row come
through this revision byte for byte. That is the whole risk profile of M1, and
it is deliberate - the ledger is additive so that a mistake in it cannot reach
the workflow that the department already runs on.

What the five tables are for
-----------------------------

* ``pr_work_types`` - the taxonomy. A **row** per kind of work, with an enum
  category beside it: the category is a small closed set code groups by, the
  sub-type is a long open list the business edits without a deploy;
* ``pr_work_items`` - **one real job**;
* ``pr_work_contributions`` - **one person's share of one job**, and the row a
  KPI reads;
* ``pr_work_evidence`` - links proving a job was done;
* ``pr_work_history`` - the user-facing timeline, append-only.

Why the item and the contribution are separate tables
------------------------------------------------------

A shoot with three people is one job and three people's workload. The
department's count is one *item*; each person's count is one *contribution*. A
single table would force reporting to divide by the headcount or multiply by
it, and both are wrong. It is also where the anti-gaming rule lives: an item
becoming ``APPROVED`` is what lets its contributions become ``COUNTED``, and
the approval is refused when the actor has a row in
``pr_work_contributions`` for that item - a join, not a policy somebody has to
remember to apply.

The three constraints that carry rules rather than tidiness
------------------------------------------------------------

``uq_pr_work_items_source`` is **partial**, over ``source_key IS NOT NULL``.
Work derived from a source - the content projector M3 adds, the recurring
generator M4 adds - must exist exactly once however many times its event is
replayed, and manual work has no key to be unique on. A non-partial index would
either forbid a second manual row or rely on how one database happens to treat
``NULL``.

``ck_pr_work_contributions_counted_at_matches_status`` refuses a half-write.
``count_status = 'COUNTED'`` and ``counted_at`` are set together by one method
inside one transaction, and the database is what says so - a counted row with
no time is a row no reporting period can claim.

``uq_pr_work_contributions_item_user_role`` makes crediting one person twice for
one job in one capacity unrepresentable, which is the same guarantee
``uq_pr_task_assignments_task_user_role`` already gives assignments.

No scoring, anywhere
---------------------

There is no ``base_score``, no multiplier, no quality grade, no quota, no
``score_cap`` and no ``score_status`` in this revision. M1 records that work
happened and was independently validated; what it is *worth* is M6's question,
and a column for it now would be an unapproved rate sitting in the schema.

Downgrade
---------

Drops the five tables in dependency order and nothing else. It loses the work
ledger, which exists nowhere else - and it cannot corrupt anything, because
nothing outside these five tables references them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_TYPES = "pr_work_types"
WORK_ITEMS = "pr_work_items"
WORK_CONTRIBUTIONS = "pr_work_contributions"
WORK_EVIDENCE = "pr_work_evidence"
WORK_HISTORY = "pr_work_history"

USERS = "users"
RESTRICT = "RESTRICT"

#: Enum columns are ``VARCHAR`` + CHECK, never a PostgreSQL ``ENUM`` type - the
#: repository-wide rule, so that adding a status is a code change rather than an
#: ``ALTER TYPE`` migration and the values stay greppable in the database.
CATEGORY = sa.Enum(
    "CONTENT",
    "PRODUCTION",
    "DISTRIBUTION",
    "COMMUNITY",
    "PR_EVENT",
    "OPERATIONS",
    "RESEARCH",
    "OTHER",
    name="pr_work_category",
    native_enum=False,
    length=20,
)
UNIT_VALUES = (
    "ITEM",
    "VIDEO",
    "POST",
    "ARTICLE",
    "COMMENT",
    "MESSAGE",
    "SESSION",
    "HOUR",
    "DAY",
)
SOURCE_TYPE = sa.Enum(
    "MANUAL",
    "CONTENT",
    "TASK",
    "RECURRING",
    "SYSTEM",
    name="pr_work_source_type",
    native_enum=False,
    length=20,
)
STATUS_VALUES = (
    "PROPOSED",
    "ACCEPTED",
    "IN_PROGRESS",
    "COMPLETED",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
)
PRIORITY = sa.Enum(
    "NORMAL", "HIGH", "URGENT", "CRITICAL", name="pr_priority", native_enum=False, length=20
)
CONTRIBUTION_ROLE = sa.Enum(
    "PRIMARY",
    "CONTRIBUTOR",
    "SUPPORT",
    name="pr_work_contribution_role",
    native_enum=False,
    length=20,
)
COUNT_STATUS = sa.Enum(
    "PENDING", "COUNTED", "EXCLUDED", name="pr_work_count_status", native_enum=False, length=20
)
EVENT_TYPE = sa.Enum(
    "CREATED",
    "PROPOSED",
    "ACCEPTED",
    "REJECTED",
    "ASSIGNED",
    "CONTRIBUTOR_ADDED",
    "CONTRIBUTOR_REMOVED",
    "STARTED",
    "COMPLETED",
    "REOPENED",
    "APPROVED",
    "COUNTED",
    "EXCLUDED",
    "DEADLINE_CHANGED",
    "PRIORITY_CHANGED",
    "EVIDENCE_ADDED",
    "EVIDENCE_REMOVED",
    "CANCELLED",
    name="pr_work_event_type",
    native_enum=False,
    length=30,
)


def _unit(name: str) -> sa.Enum:
    """A fresh ``PrWorkUnit`` enum object.

    One per column rather than a shared instance: two columns in one revision
    sharing an ``sa.Enum`` makes SQLAlchemy try to emit the same CHECK
    constraint name twice on some backends.
    """
    return sa.Enum(*UNIT_VALUES, name=name, native_enum=False, length=20)


def _status(name: str) -> sa.Enum:
    """A fresh ``PrWorkStatus`` enum object. See :func:`_unit`."""
    return sa.Enum(*STATUS_VALUES, name=name, native_enum=False, length=20)


def _json() -> sa.types.TypeEngine[object]:
    """``JSONB`` on PostgreSQL, plain ``JSON`` elsewhere. The repository default."""
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # --- The taxonomy -----------------------------------------------------
    op.create_table(
        WORK_TYPES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", CATEGORY, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_unit", _unit("pr_work_unit"), server_default="ITEM", nullable=False),
        sa.Column(
            "requires_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_pr_work_types_code_not_empty")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_pr_work_types_name_not_empty")),
        sa.CheckConstraint(
            "display_order >= 0", name=op.f("ck_pr_work_types_display_order_not_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_types")),
    )
    op.create_index(op.f("ix_pr_work_types_code"), WORK_TYPES, ["code"], unique=True)
    op.create_index(op.f("ix_pr_work_types_category"), WORK_TYPES, ["category"])
    op.create_index(op.f("ix_pr_work_types_is_active"), WORK_TYPES, ["is_active"])
    # What the type picker reads: the active types, grouped, in the order
    # somebody arranged them.
    op.create_index("ix_pr_work_types_category_order", WORK_TYPES, ["category", "display_order"])

    # --- One real job -----------------------------------------------------
    op.create_table(
        WORK_ITEMS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", SOURCE_TYPE, server_default="MANUAL", nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=True),
        sa.Column("status", _status("pr_work_status"), server_default="PROPOSED", nullable=False),
        sa.Column("priority", PRIORITY, server_default="NORMAL", nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("unit", _unit("pr_work_unit_item"), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.Uuid(), nullable=True),
        # Reserved for M3 and M4. Nullable, unwritten by M1, and created now
        # rather than later because a foreign key added to a populated table is
        # a migration with a backfill decision attached.
        sa.Column("content_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("length(trim(code)) > 0", name=op.f("ck_pr_work_items_code_not_empty")),
        sa.CheckConstraint(
            "length(trim(title)) > 0", name=op.f("ck_pr_work_items_title_not_empty")
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0", name=op.f("ck_pr_work_items_quantity_positive")
        ),
        # "100" of nothing and "comments" of no number are both unreadable.
        sa.CheckConstraint(
            "(quantity IS NULL) = (unit IS NULL)",
            name=op.f("ck_pr_work_items_quantity_and_unit_together"),
        ),
        # Manual work has no key and everything derived must have one, or the
        # partial unique index below protects nothing.
        sa.CheckConstraint(
            "source_type = 'MANUAL' OR source_key IS NOT NULL",
            name=op.f("ck_pr_work_items_derived_work_is_keyed"),
        ),
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_pr_work_items_work_type_id_pr_work_types"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_items_created_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_items_assigned_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_items_completed_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_items_approved_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_items_cancelled_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["pr_channels.id"],
            name=op.f("fk_pr_work_items_channel_id_pr_channels"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name=op.f("fk_pr_work_items_content_id_pr_content_items"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["pr_tasks.id"],
            name=op.f("fk_pr_work_items_task_id_pr_tasks"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_items")),
    )
    op.create_index(op.f("ix_pr_work_items_code"), WORK_ITEMS, ["code"], unique=True)
    op.create_index(op.f("ix_pr_work_items_work_type_id"), WORK_ITEMS, ["work_type_id"])
    op.create_index(op.f("ix_pr_work_items_status"), WORK_ITEMS, ["status"])
    op.create_index(op.f("ix_pr_work_items_due_at"), WORK_ITEMS, ["due_at"])
    op.create_index(op.f("ix_pr_work_items_created_by_user_id"), WORK_ITEMS, ["created_by_user_id"])
    op.create_index(op.f("ix_pr_work_items_channel_id"), WORK_ITEMS, ["channel_id"])
    op.create_index(op.f("ix_pr_work_items_content_id"), WORK_ITEMS, ["content_id"])
    op.create_index(op.f("ix_pr_work_items_task_id"), WORK_ITEMS, ["task_id"])
    # "What is open and when is it due" - the operational list and the overdue
    # query, both answered from this one.
    op.create_index("ix_pr_work_items_status_due", WORK_ITEMS, ["status", "due_at"])
    op.create_index("ix_pr_work_items_type_status", WORK_ITEMS, ["work_type_id", "status"])
    # The manager's two queues, keyed on a person rather than on a team - which
    # this system does not model and M1 declined to invent.
    op.create_index(
        "ix_pr_work_items_created_by_status", WORK_ITEMS, ["created_by_user_id", "status"]
    )
    op.create_index("ix_pr_work_items_assigned_by", WORK_ITEMS, ["assigned_by_user_id"])
    # **The idempotency guarantee.** Partial: manual work has no key, derived
    # work has exactly one row per semantic event however often it is replayed.
    op.create_index(
        "uq_pr_work_items_source",
        WORK_ITEMS,
        ["source_type", "source_key"],
        unique=True,
        postgresql_where=sa.text("source_key IS NOT NULL"),
        sqlite_where=sa.text("source_key IS NOT NULL"),
    )

    # --- One person's share ------------------------------------------------
    op.create_table(
        WORK_CONTRIBUTIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("contribution_role", CONTRIBUTION_ROLE, server_default="PRIMARY", nullable=False),
        sa.Column(
            "credit_weight",
            sa.Numeric(precision=5, scale=4),
            server_default=sa.text("1.0"),
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count_status", COUNT_STATUS, server_default="PENDING", nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("excluded_reason", sa.Text(), nullable=True),
        sa.Column("excluded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=True),
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
        # A share may be reduced to record a minor role and may never be raised
        # above a whole unit: nobody's contribution is worth two people's.
        sa.CheckConstraint(
            "credit_weight > 0 AND credit_weight <= 1",
            name=op.f("ck_pr_work_contributions_credit_weight_in_range"),
        ),
        # Counted work has a time and uncounted work does not. The database
        # refuses the half-write that would leave a counted row no period can
        # claim.
        sa.CheckConstraint(
            "(count_status = 'COUNTED') = (counted_at IS NOT NULL)",
            name=op.f("ck_pr_work_contributions_counted_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            [f"{WORK_ITEMS}.id"],
            name=op.f("fk_pr_work_contributions_work_item_id_pr_work_items"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_contributions_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["excluded_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_contributions_excluded_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_contributions")),
    )
    op.create_index(
        op.f("ix_pr_work_contributions_work_item_id"), WORK_CONTRIBUTIONS, ["work_item_id"]
    )
    op.create_index(op.f("ix_pr_work_contributions_user_id"), WORK_CONTRIBUTIONS, ["user_id"])
    op.create_index(
        op.f("ix_pr_work_contributions_count_status"), WORK_CONTRIBUTIONS, ["count_status"]
    )
    # One person, one capacity, one job.
    op.create_index(
        "uq_pr_work_contributions_item_user_role",
        WORK_CONTRIBUTIONS,
        ["work_item_id", "user_id", "contribution_role"],
        unique=True,
    )
    # **The KPI index.** "What has this person had counted, and when."
    op.create_index(
        "ix_pr_work_contributions_user_count",
        WORK_CONTRIBUTIONS,
        ["user_id", "count_status", "counted_at"],
    )
    op.create_index(
        "ix_pr_work_contributions_user_item", WORK_CONTRIBUTIONS, ["user_id", "work_item_id"]
    )

    # --- Evidence ----------------------------------------------------------
    op.create_table(
        WORK_EVIDENCE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=False),
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
            "length(trim(label)) > 0", name=op.f("ck_pr_work_evidence_label_not_empty")
        ),
        sa.CheckConstraint(
            "length(trim(location)) > 0", name=op.f("ck_pr_work_evidence_location_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            [f"{WORK_ITEMS}.id"],
            name=op.f("fk_pr_work_evidence_work_item_id_pr_work_items"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_evidence_added_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_evidence")),
    )
    op.create_index(op.f("ix_pr_work_evidence_work_item_id"), WORK_EVIDENCE, ["work_item_id"])
    op.create_index(
        "ix_pr_work_evidence_item_created", WORK_EVIDENCE, ["work_item_id", "created_at"]
    )

    # --- The timeline ------------------------------------------------------
    op.create_table(
        WORK_HISTORY,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("contribution_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", EVENT_TYPE, nullable=False),
        sa.Column("from_status", _status("pr_work_status_from"), nullable=True),
        sa.Column("to_status", _status("pr_work_status_to"), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata", _json(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            [f"{WORK_ITEMS}.id"],
            name=op.f("fk_pr_work_history_work_item_id_pr_work_items"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["contribution_id"],
            [f"{WORK_CONTRIBUTIONS}.id"],
            name=op.f("fk_pr_work_history_contribution_id_pr_work_contributions"),
            ondelete=RESTRICT,
        ),
        # ``RESTRICT``, like every other foreign key in the PR module, and
        # deliberately not ``audit_logs``' ``SET NULL``: a MeoBot user row is
        # never hard-deleted - suspension is a status and a timestamp - so
        # nothing needs the escape hatch, and forgetting who moved a piece of
        # work would lose the answer this table exists to give.
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_work_history_actor_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_work_history")),
    )
    op.create_index(op.f("ix_pr_work_history_work_item_id"), WORK_HISTORY, ["work_item_id"])
    op.create_index("ix_pr_work_history_item_created", WORK_HISTORY, ["work_item_id", "created_at"])


def downgrade() -> None:
    """Drop the five tables, newest dependency first. Nothing else is touched."""
    op.drop_table(WORK_HISTORY)
    op.drop_table(WORK_EVIDENCE)
    op.drop_table(WORK_CONTRIBUTIONS)
    op.drop_table(WORK_ITEMS)
    op.drop_table(WORK_TYPES)
