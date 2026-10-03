"""Step 1B: the PR reporting data foundation.

Nine new tables and nothing else. No existing table gains a column, loses a
column, changes a type or changes a constraint; revisions 0001-0012 are left
exactly as they are. ``users`` is referenced and never altered - every reference
to a person here is a foreign key to ``users.id``, because MeoBot has one
identity table and this revision does not add a second.

What is created
---------------

Periods first, then what is published, then what was measured, then what a
person typed, then what went wrong, then what was generated::

    pr_reporting_periods (self-referencing: previous_period_id)
        ├── pr_weekly_manual_inputs ──> pr_channels, users
        ├── pr_issues ──> pr_channels, pr_content_items, users
        │       └── pr_actions ──> users
        └── pr_report_runs ──> users
                └── pr_report_artifacts

    pr_content_items ──┐
                       ├──> pr_publications ──> users
    pr_channels ───────┘        └── pr_post_metric_snapshots
            └── pr_channel_metric_snapshots

Enum columns
------------

``VARCHAR`` with a length, matching every enum column already in this schema.
The project stores enum *values* through :func:`~meobot.db.base.value_enum` and
does not emit a ``CHECK`` constraint for them - ``sa.Enum(...,
native_enum=False)`` has not created one since SQLAlchemy 1.4, in the models and
in migrations 0001-0012 alike. Nothing here changes that, so there is no
PostgreSQL ``ENUM`` type to create and the downgrade has no ``DROP TYPE``.
Membership is checked by the ORM, not by the database; a raw ``INSERT`` can
still store an out-of-vocabulary string. That is repository-wide debt inherited
from 0001-0012, not something this revision introduces.

Append-only tables
------------------

``pr_post_metric_snapshots`` and ``pr_channel_metric_snapshots`` have
``created_at`` and **no** ``updated_at``, deliberately. A snapshot is a claim
about a moment - "at 09:00 on the 5th the API said 12,431 views" - and a claim
that can be edited afterwards is not one. ``pr_report_artifacts`` likewise has
no ``updated_at``: its only mutation is stamping ``delivered_at``, which is a
dated fact in its own right.

Server defaults
---------------

Every ``NOT NULL`` column whose model expects the database to fill it carries
its default here: ``created_at``/``updated_at`` (``now()``), every status column
(``OPEN``, ``PUBLISHED``, ``DRAFT``, ``OPEN``, ``TODO``, ``PENDING``),
``pr_issues.severity`` (``MEDIUM``), ``needs_management_decision`` (``false``)
and ``missing_data_count`` (``0``). That is the lesson of revision 0011, where
ten timestamp columns were created ``NOT NULL`` with no default and the first
production insert failed.

``pr_report_runs.report_type`` and ``pr_report_runs.trigger_type`` deliberately
have **no** default. Neither has a value that is right more often than it is
wrong - a run is weekly or monthly, manual or scheduled or a retry, and guessing
either would file a run under a heading nobody chose. The caller states both.

Delete behaviour
----------------

Every foreign key is ``ON DELETE RESTRICT``. Deleting a user who entered a
week's numbers, a channel that has been measured, a period that has been
reported, a publication that has metrics, an issue with actions or a report run
that produced a file fails at the database. Nothing cascades and nothing is set
to null.

Downgrade
---------

Drops the nine tables in exact reverse dependency order and nothing else.
**That loses every PR reporting row**: every reporting period, every publication
and its whole metric history, every channel measurement, every version of every
week's manual input, every issue and action, and the entire record of which
reports were generated and delivered. It touches no other table, and no data
outside these nine is read or written.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSONB on PostgreSQL, matching every other migration that stores JSON.
JSONB = postgresql.JSONB(astext_type=sa.Text())

# --- The stored vocabularies -----------------------------------------------
# Written out as literals rather than imported from
# ``meobot.domain.pr.reporting`` on purpose: a migration has to keep meaning
# what it meant on the day it ran, and an enum that gains a member next month
# must not retroactively change what this revision created.
# ``tests/unit/test_pr_reporting_schema_parity.py`` asserts these lists still
# match the domain enums, so a divergence is caught in seconds rather than
# discovered in a column.
PERIOD_TYPES = ("WEEK", "MONTH")
PERIOD_STATUSES = ("OPEN", "CLOSED", "LOCKED")
PUBLICATION_STATUSES = ("PUBLISHED", "REMOVED", "UNAVAILABLE")
METRIC_SOURCES = ("API", "MANUAL", "IMPORT")
WEEKLY_INPUT_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "LOCKED")
ISSUE_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
ISSUE_STATUSES = ("OPEN", "IN_PROGRESS", "RESOLVED", "CANCELLED")
ACTION_STATUSES = ("TODO", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED")
REPORT_TYPES = ("WEEKLY_MANAGEMENT", "MONTHLY_MANAGEMENT")
REPORT_TRIGGER_TYPES = ("MANUAL", "SCHEDULED", "RETRY")
REPORT_RUN_STATUSES = (
    "PENDING",
    "VALIDATING",
    "VALIDATION_FAILED",
    "GENERATING",
    "GENERATED",
    "DELIVERING",
    "DELIVERED",
    "FAILED",
)
ARTIFACT_FORMATS = ("XLSX", "PDF")

# --- The measured columns ---------------------------------------------------
# Frozen copies of the tuples the models declare, for the same reason as the
# enums above. The non-negative CHECK on each table is generated from these, so
# adding a metric column to a model without widening the constraint here is a
# parity-test failure rather than a silently unconstrained column.
POST_METRIC_COLUMNS = (
    "views",
    "reach",
    "impressions",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "watch_time_seconds",
    "average_view_duration_seconds",
    "followers_gained",
)
CHANNEL_METRIC_COLUMNS = (
    "followers",
    "members",
    "views",
    "reach",
    "impressions",
    "engagements",
    "messages",
)
WEEKLY_INPUT_VALUE_COLUMNS = (
    "planned_posts",
    "manual_posts",
    "manual_views",
    "manual_reach",
    "manual_engagements",
    "leads",
    "bookings",
    "cost_vnd",
    "members_end",
)

#: Numeric columns on ``pr_weekly_manual_inputs`` deliberately left out of the
#: non-negative constraint. ``members_gain`` is a signed period delta: a group
#: that lost members has a negative gain, and refusing it would make a real
#: week unrecordable. Listed rather than merely omitted so the exclusion reads
#: as a decision, and so the parity test can require every numeric column to
#: appear in exactly one of these two tuples.
WEEKLY_INPUT_SIGNED_COLUMNS = ("members_gain",)

#: The one identity table. Named once, as in 0012.
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


def _non_negative(columns: tuple[str, ...]) -> str:
    """The same non-negative test the models declare."""
    return " AND ".join(f"({column} IS NULL OR {column} >= 0)" for column in columns)


def upgrade() -> None:
    # --- pr_reporting_periods ---------------------------------------------
    # The self-referencing foreign key is declared inline: PostgreSQL accepts a
    # table referring to itself in ``CREATE TABLE``, so no ``use_alter`` and no
    # follow-up ``ALTER TABLE`` is needed. Periods are not generated by this
    # revision - a caller supplies the code and the dates.
    op.create_table(
        "pr_reporting_periods",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("period_type", _enum(PERIOD_TYPES, "pr_period_type", 20), nullable=False),
        sa.Column("date_start", sa.Date(), nullable=False),
        sa.Column("date_end", sa.Date(), nullable=False),
        sa.Column("previous_period_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            _enum(PERIOD_STATUSES, "pr_period_status", 20),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_reporting_periods"),
        sa.ForeignKeyConstraint(
            ["previous_period_id"],
            ["pr_reporting_periods.id"],
            name="fk_pr_reporting_periods_previous_period_id_pr_reporting_periods",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_reporting_periods_code_not_empty"),
        sa.CheckConstraint("date_end >= date_start", name="ck_pr_reporting_periods_dates_ordered"),
        sa.CheckConstraint(
            "previous_period_id IS NULL OR previous_period_id <> id",
            name="ck_pr_reporting_periods_previous_is_not_self",
        ),
    )
    op.create_index("ix_pr_reporting_periods_code", "pr_reporting_periods", ["code"], unique=True)
    op.create_index(
        "ix_pr_reporting_periods_type_start",
        "pr_reporting_periods",
        ["period_type", "date_start"],
    )
    op.create_index("ix_pr_reporting_periods_status", "pr_reporting_periods", ["status"])
    op.create_index(
        "ix_pr_reporting_periods_previous_period_id",
        "pr_reporting_periods",
        ["previous_period_id"],
    )

    # --- pr_publications ---------------------------------------------------
    # The uniqueness rule is on (channel, platform post id) and is **partial**:
    # it covers only rows where the platform id is known, so the publications
    # nobody has looked up yet do not collide with each other on a shared NULL.
    # It is deliberately not (content_id, channel_id) - the same content really
    # can be published to one channel more than once, and each occurrence has
    # its own metrics. ``url`` carries no key, no unique index and no index.
    op.create_table(
        "pr_publications",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("platform_post_id", sa.String(length=200), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publisher_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            _enum(PUBLICATION_STATUSES, "pr_publication_status", 20),
            nullable=False,
            server_default="PUBLISHED",
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_publications"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name="fk_pr_publications_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["pr_channels.id"],
            name="fk_pr_publications_channel_id_pr_channels",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["publisher_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_publications_publisher_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_publications_code_not_empty"),
    )
    op.create_index("ix_pr_publications_code", "pr_publications", ["code"], unique=True)
    op.create_index(
        "uq_pr_publications_channel_platform_post",
        "pr_publications",
        ["channel_id", "platform_post_id"],
        unique=True,
        postgresql_where=sa.text("platform_post_id IS NOT NULL"),
    )
    op.create_index("ix_pr_publications_content_id", "pr_publications", ["content_id"])
    op.create_index(
        "ix_pr_publications_channel_published", "pr_publications", ["channel_id", "published_at"]
    )
    op.create_index("ix_pr_publications_status", "pr_publications", ["status"])

    # --- pr_post_metric_snapshots ------------------------------------------
    # Append-only: created_at, no updated_at. ``views`` and ``reach`` are
    # separate columns and mean different things - plays versus people.
    # ``source`` is part of the uniqueness key so an API reading and a
    # hand-typed reading for the same instant can both be stored and disagree.
    op.create_table(
        "pr_post_metric_snapshots",
        sa.Column("publication_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", _enum(METRIC_SOURCES, "pr_metric_source", 20), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("reach", sa.BigInteger(), nullable=True),
        sa.Column("impressions", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("saves", sa.BigInteger(), nullable=True),
        sa.Column("clicks", sa.BigInteger(), nullable=True),
        sa.Column("watch_time_seconds", sa.BigInteger(), nullable=True),
        sa.Column(
            "average_view_duration_seconds", sa.Numeric(precision=12, scale=3), nullable=True
        ),
        sa.Column("followers_gained", sa.BigInteger(), nullable=True),
        sa.Column("extra_metrics", JSONB, nullable=True),
        _created_at(),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_post_metric_snapshots"),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["pr_publications.id"],
            name="fk_pr_post_metric_snapshots_publication_id_pr_publications",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            _non_negative(POST_METRIC_COLUMNS),
            name="ck_pr_post_metric_snapshots_metrics_not_negative",
        ),
    )
    # No separate (publication_id, observed_at) index: the unique index below
    # is a B-tree, so a lookup on publication_id - with or without an
    # observed_at range - is already served by its leftmost prefix. A duplicate
    # would cost a second write on every append and buy nothing.
    op.create_index(
        "uq_pr_post_metric_snapshots_publication_observed_source",
        "pr_post_metric_snapshots",
        ["publication_id", "observed_at", "source"],
        unique=True,
    )
    op.create_index(
        "ix_pr_post_metric_snapshots_observed_at", "pr_post_metric_snapshots", ["observed_at"]
    )
    op.create_index("ix_pr_post_metric_snapshots_source", "pr_post_metric_snapshots", ["source"])

    # --- pr_channel_metric_snapshots ---------------------------------------
    # The channel-level counterpart, and separate from the post table because a
    # follower count is not the sum of anything the posts did.
    op.create_table(
        "pr_channel_metric_snapshots",
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", _enum(METRIC_SOURCES, "pr_metric_source", 20), nullable=False),
        sa.Column("followers", sa.BigInteger(), nullable=True),
        sa.Column("members", sa.BigInteger(), nullable=True),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("reach", sa.BigInteger(), nullable=True),
        sa.Column("impressions", sa.BigInteger(), nullable=True),
        sa.Column("engagements", sa.BigInteger(), nullable=True),
        sa.Column("messages", sa.BigInteger(), nullable=True),
        sa.Column("extra_metrics", JSONB, nullable=True),
        _created_at(),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_channel_metric_snapshots"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["pr_channels.id"],
            name="fk_pr_channel_metric_snapshots_channel_id_pr_channels",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            _non_negative(CHANNEL_METRIC_COLUMNS),
            name="ck_pr_channel_metric_snapshots_metrics_not_negative",
        ),
    )
    op.create_index(
        "uq_pr_channel_metric_snapshots_channel_observed_source",
        "pr_channel_metric_snapshots",
        ["channel_id", "observed_at", "source"],
        unique=True,
    )
    # No separate (channel_id, observed_at) index, for the same reason.
    op.create_index(
        "ix_pr_channel_metric_snapshots_observed_at", "pr_channel_metric_snapshots", ["observed_at"]
    )
    op.create_index(
        "ix_pr_channel_metric_snapshots_source", "pr_channel_metric_snapshots", ["source"]
    )

    # --- pr_weekly_manual_inputs -------------------------------------------
    # Versioned, never edited: (channel, period, version) is unique, so a
    # correction is a new row and the version a report quoted keeps saying what
    # it said. Refusing an in-place update of an APPROVED or LOCKED row is a
    # Step 1C service rule; so is checking that ``period_id`` names a WEEK,
    # which needs a lookup into another row that no CHECK constraint can do.
    op.create_table(
        "pr_weekly_manual_inputs",
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("period_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum(WEEKLY_INPUT_STATUSES, "pr_weekly_input_status", 20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("planned_posts", sa.Integer(), nullable=True),
        sa.Column("manual_posts", sa.Integer(), nullable=True),
        sa.Column("manual_views", sa.BigInteger(), nullable=True),
        sa.Column("manual_reach", sa.BigInteger(), nullable=True),
        sa.Column("manual_engagements", sa.BigInteger(), nullable=True),
        sa.Column("leads", sa.Integer(), nullable=True),
        sa.Column("bookings", sa.Integer(), nullable=True),
        sa.Column("cost_vnd", sa.Numeric(precision=18, scale=0), nullable=True),
        sa.Column("members_end", sa.BigInteger(), nullable=True),
        sa.Column("members_gain", sa.BigInteger(), nullable=True),
        sa.Column("variance_reason", sa.Text(), nullable=True),
        sa.Column("entered_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_weekly_manual_inputs"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["pr_channels.id"],
            name="fk_pr_weekly_manual_inputs_channel_id_pr_channels",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["pr_reporting_periods.id"],
            name="fk_pr_weekly_manual_inputs_period_id_pr_reporting_periods",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["entered_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_weekly_manual_inputs_entered_by_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_weekly_manual_inputs_approved_by_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            "version_no >= 1", name="ck_pr_weekly_manual_inputs_version_no_positive"
        ),
        sa.CheckConstraint(
            _non_negative(WEEKLY_INPUT_VALUE_COLUMNS),
            name="ck_pr_weekly_manual_inputs_values_not_negative",
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL AND approved_by_user_id IS NULL)"
            " OR (approved_at IS NOT NULL AND approved_by_user_id IS NOT NULL)",
            name="ck_pr_weekly_manual_inputs_approval_pair_consistent",
        ),
    )
    op.create_index(
        "uq_pr_weekly_manual_inputs_channel_period_version",
        "pr_weekly_manual_inputs",
        ["channel_id", "period_id", "version_no"],
        unique=True,
    )
    op.create_index(
        "ix_pr_weekly_manual_inputs_period_id", "pr_weekly_manual_inputs", ["period_id"]
    )

    # --- pr_issues ---------------------------------------------------------
    # period, channel and content are all nullable and all independent: an
    # issue may belong to a week, to a channel, to a piece, or to none of them.
    op.create_table(
        "pr_issues",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("period_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("issue_group", sa.String(length=100), nullable=True),
        sa.Column(
            "severity",
            _enum(ISSUE_SEVERITIES, "pr_issue_severity", 20),
            nullable=False,
            server_default="MEDIUM",
        ),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum(ISSUE_STATUSES, "pr_issue_status", 20),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "needs_management_decision",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_issues"),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["pr_reporting_periods.id"],
            name="fk_pr_issues_period_id_pr_reporting_periods",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["pr_channels.id"],
            name="fk_pr_issues_channel_id_pr_channels",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name="fk_pr_issues_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_issues_owner_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(_not_empty("code"), name="ck_pr_issues_code_not_empty"),
        sa.CheckConstraint(_not_empty("title"), name="ck_pr_issues_title_not_empty"),
    )
    op.create_index("ix_pr_issues_code", "pr_issues", ["code"], unique=True)
    op.create_index("ix_pr_issues_period_id", "pr_issues", ["period_id"])
    op.create_index("ix_pr_issues_owner_status", "pr_issues", ["owner_user_id", "status"])
    op.create_index("ix_pr_issues_severity_status", "pr_issues", ["severity", "status"])
    op.create_index("ix_pr_issues_channel_id", "pr_issues", ["channel_id"])
    op.create_index("ix_pr_issues_content_id", "pr_issues", ["content_id"])

    # --- pr_actions --------------------------------------------------------
    # ``completed_at`` has no trigger and no default: whoever finishes the
    # action writes it. See the model docstring for why the database does not.
    op.create_table(
        "pr_actions",
        sa.Column("issue_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            _enum(ACTION_STATUSES, "pr_action_status", 20),
            nullable=False,
            server_default="TODO",
        ),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("actual_result", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_actions"),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["pr_issues.id"],
            name="fk_pr_actions_issue_id_pr_issues",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_actions_owner_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            _not_empty("description"), name="ck_pr_actions_description_not_empty"
        ),
    )
    op.create_index("ix_pr_actions_issue_id", "pr_actions", ["issue_id"])
    op.create_index("ix_pr_actions_owner_status", "pr_actions", ["owner_user_id", "status"])
    op.create_index("ix_pr_actions_deadline", "pr_actions", ["deadline"])
    op.create_index("ix_pr_actions_status_deadline", "pr_actions", ["status", "deadline"])

    # --- pr_report_runs ----------------------------------------------------
    # An attempt, not a report: a run that fails validation is a row here too.
    # ``idempotency_key`` is unique and is what stops a retry, a double press
    # and a scheduler firing twice from producing three reports of one week.
    # No state transition is implemented or enforced by this revision.
    op.create_table(
        "pr_report_runs",
        sa.Column("report_type", _enum(REPORT_TYPES, "pr_report_type", 30), nullable=False),
        sa.Column("period_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("template_version", sa.String(length=50), nullable=False),
        sa.Column(
            "trigger_type",
            _enum(REPORT_TRIGGER_TYPES, "pr_report_trigger_type", 20),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(REPORT_RUN_STATUSES, "pr_report_run_status", 30),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validation_summary", JSONB, nullable=True),
        sa.Column("missing_data_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pr_report_runs"),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["pr_reporting_periods.id"],
            name="fk_pr_report_runs_period_id_pr_reporting_periods",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_report_runs_requested_by_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            _not_empty("idempotency_key"), name="ck_pr_report_runs_idempotency_key_not_empty"
        ),
        sa.CheckConstraint(
            _not_empty("template_version"), name="ck_pr_report_runs_template_version_not_empty"
        ),
        sa.CheckConstraint(
            "missing_data_count >= 0", name="ck_pr_report_runs_missing_data_count_not_negative"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_pr_report_runs_finished_after_started",
        ),
    )
    op.create_index(
        "ix_pr_report_runs_idempotency_key", "pr_report_runs", ["idempotency_key"], unique=True
    )
    op.create_index("ix_pr_report_runs_period_type", "pr_report_runs", ["period_id", "report_type"])
    op.create_index("ix_pr_report_runs_status", "pr_report_runs", ["status"])
    op.create_index("ix_pr_report_runs_created_at", "pr_report_runs", ["created_at"])
    op.create_index("ix_pr_report_runs_type_status", "pr_report_runs", ["report_type", "status"])

    # --- pr_report_artifacts -----------------------------------------------
    # One run may produce an XLSX and a PDF, and a regenerated file is a new
    # version rather than an overwrite - which is what the uniqueness on
    # (run, format, version) buys. ``storage_path`` is where the bytes are and
    # is not a relational key: nothing joins on it and moving a file orphans
    # nothing. No ``updated_at``; the only mutation is stamping delivered_at.
    op.create_table(
        "pr_report_artifacts",
        sa.Column("report_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "artifact_format", _enum(ARTIFACT_FORMATS, "pr_artifact_format", 20), nullable=False
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=300), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=150), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_report_artifacts"),
        sa.ForeignKeyConstraint(
            ["report_run_id"],
            ["pr_report_runs.id"],
            name="fk_pr_report_artifacts_report_run_id_pr_report_runs",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            _not_empty("file_name"), name="ck_pr_report_artifacts_file_name_not_empty"
        ),
        sa.CheckConstraint(
            _not_empty("storage_path"), name="ck_pr_report_artifacts_storage_path_not_empty"
        ),
        sa.CheckConstraint(
            _not_empty("mime_type"), name="ck_pr_report_artifacts_mime_type_not_empty"
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_pr_report_artifacts_version_no_positive"),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_pr_report_artifacts_file_size_not_negative",
        ),
    )
    op.create_index(
        "uq_pr_report_artifacts_run_format_version",
        "pr_report_artifacts",
        ["report_run_id", "artifact_format", "version_no"],
        unique=True,
    )
    op.create_index("ix_pr_report_artifacts_report_run_id", "pr_report_artifacts", ["report_run_id"])
    op.create_index("ix_pr_report_artifacts_created_at", "pr_report_artifacts", ["created_at"])
    op.create_index("ix_pr_report_artifacts_delivered_at", "pr_report_artifacts", ["delivered_at"])


def downgrade() -> None:
    """Drop the nine Step 1B tables in exact reverse dependency order.

    Read the data-loss note in the module docstring first. Indexes are dropped
    explicitly before their table, matching how 0009, 0010 and 0012 reverse
    themselves; there is no ``DROP TYPE`` because the enum columns are VARCHAR
    and no PostgreSQL type was ever created. Nothing outside these nine tables
    is touched - the Step 1A tables, ``users`` and every other table survive.
    """
    op.drop_index("ix_pr_report_artifacts_delivered_at", table_name="pr_report_artifacts")
    op.drop_index("ix_pr_report_artifacts_created_at", table_name="pr_report_artifacts")
    op.drop_index("ix_pr_report_artifacts_report_run_id", table_name="pr_report_artifacts")
    op.drop_index("uq_pr_report_artifacts_run_format_version", table_name="pr_report_artifacts")
    op.drop_table("pr_report_artifacts")

    op.drop_index("ix_pr_report_runs_type_status", table_name="pr_report_runs")
    op.drop_index("ix_pr_report_runs_created_at", table_name="pr_report_runs")
    op.drop_index("ix_pr_report_runs_status", table_name="pr_report_runs")
    op.drop_index("ix_pr_report_runs_period_type", table_name="pr_report_runs")
    op.drop_index("ix_pr_report_runs_idempotency_key", table_name="pr_report_runs")
    op.drop_table("pr_report_runs")

    op.drop_index("ix_pr_actions_status_deadline", table_name="pr_actions")
    op.drop_index("ix_pr_actions_deadline", table_name="pr_actions")
    op.drop_index("ix_pr_actions_owner_status", table_name="pr_actions")
    op.drop_index("ix_pr_actions_issue_id", table_name="pr_actions")
    op.drop_table("pr_actions")

    op.drop_index("ix_pr_issues_content_id", table_name="pr_issues")
    op.drop_index("ix_pr_issues_channel_id", table_name="pr_issues")
    op.drop_index("ix_pr_issues_severity_status", table_name="pr_issues")
    op.drop_index("ix_pr_issues_owner_status", table_name="pr_issues")
    op.drop_index("ix_pr_issues_period_id", table_name="pr_issues")
    op.drop_index("ix_pr_issues_code", table_name="pr_issues")
    op.drop_table("pr_issues")

    op.drop_index("ix_pr_weekly_manual_inputs_period_id", table_name="pr_weekly_manual_inputs")
    op.drop_index(
        "uq_pr_weekly_manual_inputs_channel_period_version", table_name="pr_weekly_manual_inputs"
    )
    op.drop_table("pr_weekly_manual_inputs")

    op.drop_index("ix_pr_channel_metric_snapshots_source", table_name="pr_channel_metric_snapshots")
    op.drop_index(
        "ix_pr_channel_metric_snapshots_observed_at", table_name="pr_channel_metric_snapshots"
    )
    op.drop_index(
        "uq_pr_channel_metric_snapshots_channel_observed_source",
        table_name="pr_channel_metric_snapshots",
    )
    op.drop_table("pr_channel_metric_snapshots")

    op.drop_index("ix_pr_post_metric_snapshots_source", table_name="pr_post_metric_snapshots")
    op.drop_index("ix_pr_post_metric_snapshots_observed_at", table_name="pr_post_metric_snapshots")
    op.drop_index(
        "uq_pr_post_metric_snapshots_publication_observed_source",
        table_name="pr_post_metric_snapshots",
    )
    op.drop_table("pr_post_metric_snapshots")

    op.drop_index("ix_pr_publications_status", table_name="pr_publications")
    op.drop_index("ix_pr_publications_channel_published", table_name="pr_publications")
    op.drop_index("ix_pr_publications_content_id", table_name="pr_publications")
    op.drop_index("uq_pr_publications_channel_platform_post", table_name="pr_publications")
    op.drop_index("ix_pr_publications_code", table_name="pr_publications")
    op.drop_table("pr_publications")

    op.drop_index(
        "ix_pr_reporting_periods_previous_period_id", table_name="pr_reporting_periods"
    )
    op.drop_index("ix_pr_reporting_periods_status", table_name="pr_reporting_periods")
    op.drop_index("ix_pr_reporting_periods_type_start", table_name="pr_reporting_periods")
    op.drop_index("ix_pr_reporting_periods_code", table_name="pr_reporting_periods")
    op.drop_table("pr_reporting_periods")
