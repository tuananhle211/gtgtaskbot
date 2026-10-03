"""Step 1A: the PR and Communications core foundation.

Eleven new tables and nothing else. No existing table gains a column, loses a
column, changes a type or changes a constraint. ``users`` is referenced and
never altered - every PR reference to a person is a foreign key to
``users.id``, because MeoBot has exactly one identity table and this revision
does not add a second.

What is created
---------------

Reference data first, then the things that depend on it::

    pr_brands            pr_platforms      pr_content_formats  pr_content_pillars
        └── pr_channels ─┘                        └── pr_content_items ──┘
                └── pr_channel_assignments ──> users
                                pr_content_targets  (content x channel)
                                pr_tasks ──> users
                                    ├── pr_task_assignments ──> users
                                    └── pr_approval_events ──> users

Enum columns
------------

``VARCHAR`` with a length, matching every enum column already in this schema.
The project stores enum *values* through :func:`~meobot.db.base.value_enum` and
does not emit a ``CHECK`` constraint for them - ``sa.Enum(...,
native_enum=False)`` has not created one since SQLAlchemy 1.4, in the models
and in migrations 0001-0011 alike. Nothing here changes that: a PR enum column
is defined exactly as an HR or dispatch enum column is, so the models and this
migration produce byte-identical DDL. There is no PostgreSQL ``ENUM`` type to
create or drop, which is also why the downgrade has no ``DROP TYPE``.

Server defaults
---------------

Every ``NOT NULL`` column whose model expects the database to fill it carries
its default here: ``created_at``/``updated_at`` (``now()``), the status and
priority columns, ``api_available``, ``is_primary`` and ``allocation_percent``.
That is the lesson of revision 0011, where ten timestamp columns were created
``NOT NULL`` with no default and the first production insert failed. The
integration test for this revision inserts every one of these tables without
mentioning a defaulted column.

Delete behaviour
----------------

Every foreign key is ``ON DELETE RESTRICT``. Deleting a user who owns content,
a brand with channels, a channel with assignments, a content item with targets
or a task with approvals fails at the database. Retiring something is a status
change - ``INACTIVE``, ``ARCHIVED``, ``CANCELLED`` - and approval history is
never removed as a side effect of anything.

Downgrade
---------

Drops the eleven tables in reverse dependency order and nothing else. **That
loses every PR row**: brands, platforms, channels, who owned which channel,
every content item and its per-channel plan, every task and its assignees, and
the whole approval history. It touches no other table, and no data outside
these eleven is read or written.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- The stored vocabularies -----------------------------------------------
# Written out as literals rather than imported from ``meobot.domain.pr.models``
# on purpose: a migration has to keep meaning what it meant on the day it ran,
# and an enum that gains a member next month must not retroactively change what
# this revision created. ``tests/unit/test_pr_core_schema_parity.py`` asserts
# these lists still match the domain enums, so a divergence is caught in
# seconds rather than discovered in a column.
ENTITY_STATUSES = ("ACTIVE", "INACTIVE")
CHANNEL_CATEGORIES = ("SCALE", "OPTIMIZE", "TEST", "MAINTAIN", "STOP")
CHANNEL_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")
CHANNEL_ASSIGNMENT_ROLES = (
    "CHANNEL_OWNER",
    "CONTENT_OWNER",
    "PRODUCTION_OWNER",
    "SEEDING_OWNER",
    "ANALYTICS_OWNER",
    "APPROVER",
)
PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")
WORKFLOW_STAGES = (
    "IDEA",
    "BRIEFING",
    "SCRIPTING",
    "TEAM_LEAD_REVIEW",
    "HEAD_REVIEW",
    "APPROVED",
    "PRODUCTION",
    "INTERNAL_REVIEW",
    "READY_TO_PUBLISH",
    "PUBLISHED",
    "MEASURED",
    "ARCHIVED",
    "CANCELLED",
)
CONTENT_TARGET_STATUSES = ("PLANNED", "READY", "PUBLISHED", "CANCELLED")
TASK_STATUSES = (
    "TODO",
    "IN_PROGRESS",
    "BLOCKED",
    "IN_REVIEW",
    "REVISION_REQUIRED",
    "DONE",
    "CANCELLED",
)
TASK_ASSIGNMENT_ROLES = ("OWNER", "CONTRIBUTOR", "REVIEWER")
APPROVAL_STAGES = ("TEAM_LEAD_REVIEW", "HEAD_REVIEW", "INTERNAL_REVIEW")
APPROVAL_DECISIONS = ("APPROVED", "REVISION_REQUIRED", "REJECTED")

#: The one identity table. Named once so "which users table" is a single
#: decision here as well as in the models.
USERS = "users"

#: Nothing in this module cascades. See the module docstring.
RESTRICT = "RESTRICT"


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _not_empty(column: str) -> str:
    """The same non-empty test the models declare."""
    return f"length(trim({column})) > 0"


def upgrade() -> None:
    # --- pr_brands --------------------------------------------------------
    op.create_table(
        "pr_brands",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            _enum(ENTITY_STATUSES, "pr_entity_status", 20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_brands"),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_brands_code_not_empty"),
        sa.CheckConstraint(_not_empty("name"), name="ck_pr_brands_name_not_empty"),
    )
    op.create_index("ix_pr_brands_code", "pr_brands", ["code"], unique=True)
    op.create_index("ix_pr_brands_status", "pr_brands", ["status"])

    # --- pr_platforms -----------------------------------------------------
    op.create_table(
        "pr_platforms",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("api_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("api_note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(ENTITY_STATUSES, "pr_entity_status", 20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_platforms"),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_platforms_code_not_empty"),
        sa.CheckConstraint(_not_empty("name"), name="ck_pr_platforms_name_not_empty"),
    )
    op.create_index("ix_pr_platforms_code", "pr_platforms", ["code"], unique=True)
    op.create_index("ix_pr_platforms_status", "pr_platforms", ["status"])

    # --- pr_content_formats -----------------------------------------------
    op.create_table(
        "pr_content_formats",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(ENTITY_STATUSES, "pr_entity_status", 20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_formats"),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_content_formats_code_not_empty"),
        sa.CheckConstraint(_not_empty("name"), name="ck_pr_content_formats_name_not_empty"),
    )
    op.create_index("ix_pr_content_formats_code", "pr_content_formats", ["code"], unique=True)

    # --- pr_content_pillars -----------------------------------------------
    op.create_table(
        "pr_content_pillars",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(ENTITY_STATUSES, "pr_entity_status", 20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_pillars"),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_content_pillars_code_not_empty"),
        sa.CheckConstraint(_not_empty("name"), name="ck_pr_content_pillars_name_not_empty"),
    )
    op.create_index("ix_pr_content_pillars_code", "pr_content_pillars", ["code"], unique=True)

    # --- pr_channels ------------------------------------------------------
    # ``url`` is stored and never joined on; ``external_id`` is the platform's
    # own identifier and is what the compound index below is for.
    op.create_table(
        "pr_channels",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("platform_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("brand_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("tier", sa.SmallInteger(), nullable=True),
        sa.Column("category", _enum(CHANNEL_CATEGORIES, "pr_channel_category", 20), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(CHANNEL_STATUSES, "pr_channel_status", 20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("started_at", sa.Date(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_channels"),
        sa.ForeignKeyConstraint(
            ["platform_id"],
            ["pr_platforms.id"],
            name="fk_pr_channels_platform_id_pr_platforms",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["pr_brands.id"],
            name="fk_pr_channels_brand_id_pr_brands",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_channels_code_not_empty"),
        sa.CheckConstraint(_not_empty("name"), name="ck_pr_channels_name_not_empty"),
        sa.CheckConstraint(
            "tier IS NULL OR tier IN (1, 2, 3)", name="ck_pr_channels_tier_in_range"
        ),
    )
    op.create_index("ix_pr_channels_code", "pr_channels", ["code"], unique=True)
    op.create_index("ix_pr_channels_platform_id", "pr_channels", ["platform_id"])
    op.create_index("ix_pr_channels_brand_id", "pr_channels", ["brand_id"])
    op.create_index("ix_pr_channels_status", "pr_channels", ["status"])
    op.create_index(
        "ix_pr_channels_platform_external", "pr_channels", ["platform_id", "external_id"]
    )

    # --- pr_channel_assignments -------------------------------------------
    # The unique index is partial and covers only **open** rows - a row is open
    # when ``effective_to IS NULL``. It refuses a second open row for the same
    # (channel, person, role) and nothing more: overlapping *closed* date
    # ranges, and a closed range overlapping an open one, are accepted here.
    # Full overlap rejection is a Step 1B service rule, not a schema one.
    op.create_table(
        "pr_channel_assignments",
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "assignment_role",
            _enum(CHANNEL_ASSIGNMENT_ROLES, "pr_channel_assignment_role", 30),
            nullable=False,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "allocation_percent",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_channel_assignments"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["pr_channels.id"],
            name="fk_pr_channel_assignments_channel_id_pr_channels",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name="fk_pr_channel_assignments_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            "allocation_percent >= 0 AND allocation_percent <= 100",
            name="ck_pr_channel_assignments_allocation_percent_in_range",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_pr_channel_assignments_effective_period_ordered",
        ),
    )
    op.create_index("ix_pr_channel_assignments_channel_id", "pr_channel_assignments", ["channel_id"])
    op.create_index("ix_pr_channel_assignments_user_id", "pr_channel_assignments", ["user_id"])
    op.create_index(
        "uq_pr_channel_assignments_open_role",
        "pr_channel_assignments",
        ["channel_id", "user_id", "assignment_role"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    # --- pr_content_items -------------------------------------------------
    op.create_table(
        "pr_content_items",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("brand_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("format_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("pillar_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("hook", sa.Text(), nullable=True),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column(
            "priority", _enum(PRIORITIES, "pr_priority", 20), nullable=False, server_default="NORMAL"
        ),
        sa.Column(
            "workflow_stage",
            _enum(WORKFLOW_STAGES, "pr_workflow_stage", 30),
            nullable=False,
            server_default="IDEA",
        ),
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("planned_publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_items"),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["pr_brands.id"],
            name="fk_pr_content_items_brand_id_pr_brands",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["format_id"],
            ["pr_content_formats.id"],
            name="fk_pr_content_items_format_id_pr_content_formats",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["pillar_id"],
            ["pr_content_pillars.id"],
            name="fk_pr_content_items_pillar_id_pr_content_pillars",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_content_items_owner_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_content_items_created_by_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_content_items_code_not_empty"),
        sa.CheckConstraint(_not_empty("title"), name="ck_pr_content_items_title_not_empty"),
    )
    op.create_index("ix_pr_content_items_code", "pr_content_items", ["code"], unique=True)
    op.create_index("ix_pr_content_items_brand_id", "pr_content_items", ["brand_id"])
    op.create_index("ix_pr_content_items_owner_user_id", "pr_content_items", ["owner_user_id"])
    op.create_index("ix_pr_content_items_workflow_stage", "pr_content_items", ["workflow_stage"])
    op.create_index(
        "ix_pr_content_items_planned_publish_at", "pr_content_items", ["planned_publish_at"]
    )
    op.create_index(
        "ix_pr_content_items_brand_stage", "pr_content_items", ["brand_id", "workflow_stage"]
    )

    # --- pr_content_targets -----------------------------------------------
    op.create_table(
        "pr_content_targets",
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("target_publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adaptation_note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(CONTENT_TARGET_STATUSES, "pr_content_target_status", 20),
            nullable=False,
            server_default="PLANNED",
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_targets"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name="fk_pr_content_targets_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["pr_channels.id"],
            name="fk_pr_content_targets_channel_id_pr_channels",
            ondelete=RESTRICT,
        ),
    )
    op.create_index(
        "uq_pr_content_targets_content_channel",
        "pr_content_targets",
        ["content_id", "channel_id"],
        unique=True,
    )
    op.create_index("ix_pr_content_targets_channel_id", "pr_content_targets", ["channel_id"])
    op.create_index(
        "ix_pr_content_targets_target_publish_at", "pr_content_targets", ["target_publish_at"]
    )
    op.create_index("ix_pr_content_targets_status", "pr_content_targets", ["status"])

    # --- pr_tasks ---------------------------------------------------------
    # ``completed_at`` has no trigger and no default: whoever finishes the task
    # writes it. See the model docstring for why the database does not.
    op.create_table(
        "pr_tasks",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "priority", _enum(PRIORITIES, "pr_priority", 20), nullable=False, server_default="NORMAL"
        ),
        sa.Column(
            "status",
            _enum(TASK_STATUSES, "pr_task_status", 30),
            nullable=False,
            server_default="TODO",
        ),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_tasks"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name="fk_pr_tasks_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_tasks_created_by_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_tasks_code_not_empty"),
        sa.CheckConstraint(_not_empty("title"), name="ck_pr_tasks_title_not_empty"),
        sa.CheckConstraint(_not_empty("task_type"), name="ck_pr_tasks_task_type_not_empty"),
    )
    op.create_index("ix_pr_tasks_code", "pr_tasks", ["code"], unique=True)
    op.create_index("ix_pr_tasks_content_id", "pr_tasks", ["content_id"])
    op.create_index("ix_pr_tasks_status", "pr_tasks", ["status"])
    op.create_index("ix_pr_tasks_deadline", "pr_tasks", ["deadline"])
    op.create_index("ix_pr_tasks_status_deadline", "pr_tasks", ["status", "deadline"])

    # --- pr_task_assignments ----------------------------------------------
    op.create_table(
        "pr_task_assignments",
        sa.Column("task_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "assignment_role",
            _enum(TASK_ASSIGNMENT_ROLES, "pr_task_assignment_role", 20),
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_task_assignments"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["pr_tasks.id"],
            name="fk_pr_task_assignments_task_id_pr_tasks",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name="fk_pr_task_assignments_user_id_users",
            ondelete=RESTRICT,
        ),
    )
    op.create_index(
        "uq_pr_task_assignments_task_user_role",
        "pr_task_assignments",
        ["task_id", "user_id", "assignment_role"],
        unique=True,
    )
    op.create_index("ix_pr_task_assignments_task_id", "pr_task_assignments", ["task_id"])
    op.create_index("ix_pr_task_assignments_user_id", "pr_task_assignments", ["user_id"])
    op.create_index(
        "ix_pr_task_assignments_user_completed", "pr_task_assignments", ["user_id", "completed_at"]
    )

    # --- pr_approval_events -----------------------------------------------
    # Append-only. No ``updated_at``, by design - see the module docstring.
    op.create_table(
        "pr_approval_events",
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "approval_stage", _enum(APPROVAL_STAGES, "pr_approval_stage", 30), nullable=False
        ),
        sa.Column("reviewer_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision", _enum(APPROVAL_DECISIONS, "pr_approval_decision", 30), nullable=False),
        sa.Column("version_reviewed", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_approval_events"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name="fk_pr_approval_events_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["pr_tasks.id"],
            name="fk_pr_approval_events_task_id_pr_tasks",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_approval_events_reviewer_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            "version_reviewed >= 1", name="ck_pr_approval_events_version_reviewed_positive"
        ),
    )
    op.create_index(
        "ix_pr_approval_events_content_decided",
        "pr_approval_events",
        ["content_id", "decided_at"],
    )
    op.create_index("ix_pr_approval_events_task_id", "pr_approval_events", ["task_id"])
    op.create_index(
        "ix_pr_approval_events_reviewer_user_id", "pr_approval_events", ["reviewer_user_id"]
    )
    op.create_index(
        "ix_pr_approval_events_stage_decision",
        "pr_approval_events",
        ["approval_stage", "decision"],
    )


def downgrade() -> None:
    """Drop the eleven PR tables in reverse dependency order.

    Read the data-loss note in the module docstring first. Indexes are dropped
    explicitly before their table, matching how 0009 and 0010 reverse
    themselves; there is no ``DROP TYPE`` because the enum columns are VARCHAR
    and no PostgreSQL type was ever created.
    """
    op.drop_index("ix_pr_approval_events_stage_decision", table_name="pr_approval_events")
    op.drop_index("ix_pr_approval_events_reviewer_user_id", table_name="pr_approval_events")
    op.drop_index("ix_pr_approval_events_task_id", table_name="pr_approval_events")
    op.drop_index("ix_pr_approval_events_content_decided", table_name="pr_approval_events")
    op.drop_table("pr_approval_events")

    op.drop_index("ix_pr_task_assignments_user_completed", table_name="pr_task_assignments")
    op.drop_index("ix_pr_task_assignments_user_id", table_name="pr_task_assignments")
    op.drop_index("ix_pr_task_assignments_task_id", table_name="pr_task_assignments")
    op.drop_index("uq_pr_task_assignments_task_user_role", table_name="pr_task_assignments")
    op.drop_table("pr_task_assignments")

    op.drop_index("ix_pr_tasks_status_deadline", table_name="pr_tasks")
    op.drop_index("ix_pr_tasks_deadline", table_name="pr_tasks")
    op.drop_index("ix_pr_tasks_status", table_name="pr_tasks")
    op.drop_index("ix_pr_tasks_content_id", table_name="pr_tasks")
    op.drop_index("ix_pr_tasks_code", table_name="pr_tasks")
    op.drop_table("pr_tasks")

    op.drop_index("ix_pr_content_targets_status", table_name="pr_content_targets")
    op.drop_index("ix_pr_content_targets_target_publish_at", table_name="pr_content_targets")
    op.drop_index("ix_pr_content_targets_channel_id", table_name="pr_content_targets")
    op.drop_index("uq_pr_content_targets_content_channel", table_name="pr_content_targets")
    op.drop_table("pr_content_targets")

    op.drop_index("ix_pr_content_items_brand_stage", table_name="pr_content_items")
    op.drop_index("ix_pr_content_items_planned_publish_at", table_name="pr_content_items")
    op.drop_index("ix_pr_content_items_workflow_stage", table_name="pr_content_items")
    op.drop_index("ix_pr_content_items_owner_user_id", table_name="pr_content_items")
    op.drop_index("ix_pr_content_items_brand_id", table_name="pr_content_items")
    op.drop_index("ix_pr_content_items_code", table_name="pr_content_items")
    op.drop_table("pr_content_items")

    op.drop_index("uq_pr_channel_assignments_open_role", table_name="pr_channel_assignments")
    op.drop_index("ix_pr_channel_assignments_user_id", table_name="pr_channel_assignments")
    op.drop_index("ix_pr_channel_assignments_channel_id", table_name="pr_channel_assignments")
    op.drop_table("pr_channel_assignments")

    op.drop_index("ix_pr_channels_platform_external", table_name="pr_channels")
    op.drop_index("ix_pr_channels_status", table_name="pr_channels")
    op.drop_index("ix_pr_channels_brand_id", table_name="pr_channels")
    op.drop_index("ix_pr_channels_platform_id", table_name="pr_channels")
    op.drop_index("ix_pr_channels_code", table_name="pr_channels")
    op.drop_table("pr_channels")

    op.drop_index("ix_pr_content_pillars_code", table_name="pr_content_pillars")
    op.drop_table("pr_content_pillars")

    op.drop_index("ix_pr_content_formats_code", table_name="pr_content_formats")
    op.drop_table("pr_content_formats")

    op.drop_index("ix_pr_platforms_status", table_name="pr_platforms")
    op.drop_index("ix_pr_platforms_code", table_name="pr_platforms")
    op.drop_table("pr_platforms")

    op.drop_index("ix_pr_brands_status", table_name="pr_brands")
    op.drop_index("ix_pr_brands_code", table_name="pr_brands")
    op.drop_table("pr_brands")
