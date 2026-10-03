"""M3: Content → Work projection - the mapping, and the work list.

Two new tables and **nothing else**. No column on any existing table changes, no
existing row is read or written, and there is **no backfill of any kind** -
``pr_content_items``, ``pr_approval_events``, ``pr_content_transition_events``,
``pr_publications``, ``pr_tasks`` and every M1/M2 work table come through this
revision byte for byte.

That is the whole risk profile of M3, and it is the same one M1 and M2 chose:
the projector *reads* the content workflow and writes into the Work Ledger only
through the Work services, so a mistake in it cannot reach the workflow the
department already runs on.

**Deliberately no historical backfill.** Projecting years of past content would
mean deciding, in a migration with no operator watching, who wrote a script two
Augusts ago and whether the reporting period it lands in may still be written
to. Both answers are sometimes "we cannot know", and a migration has nowhere to
put that. Catch-up is an explicit, bounded, audited request instead - see
``PrContentWorkProjector.reconcile``.

What the two tables are for
----------------------------

* ``pr_content_work_rules`` - **which work type a content milestone counts as.**
  The one administratively configurable part of M3. The milestones themselves,
  the contributor rules and the independent-validation boundary stay in code,
  because those are the department's anti-gaming rules rather than its taxonomy;
* ``pr_content_work_projections`` - **one row per content item, for ever**,
  acting as both the work list and the diagnostic. Written inside the content
  transaction that changed something, so a request cannot be lost the way a
  timer-based scan loses everything it was asleep for.

The constraints that carry rules rather than tidiness
------------------------------------------------------

``uq_pr_content_work_rules_kind_type`` and
``uq_pr_content_work_rules_kind_default`` are **two partial indexes** where one
plain unique would look sufficient. It would not be: PostgreSQL treats every
``NULL`` as distinct, so a unique on ``(contribution_kind, content_type)`` would
accept five competing default rules for one kind and the projector would pick
whichever came back first. Splitting the index says the thing that is true - one
rule per (kind, type), and **one** default per kind.

``uq_pr_content_work_projections_content`` is what makes "request projection"
idempotent. Projection is convergent, so two outstanding requests for one piece
are the same request; a content item that moves five times before the sweeper
wakes writes one row and touches it four times.

No scoring, and no eligibility
-------------------------------

There is no ``base_score``, no ``points``, no quota, no cap, no allocation and no
``quota_status`` in this revision. M3 decides whether the content workflow
produced trustworthy ``COUNTED`` work; M2 owns what that work is worth, and a
column here caching any of it would be a second authority on the question M2
exists to be the only authority on.

Downgrade
---------

Drops the two tables and nothing else. It loses the mapping configuration and
the projection work list, both of which exist nowhere else - and it cannot
corrupt anything, because nothing outside these two tables references them and
the work items the projector wrote are ordinary ``pr_work_items`` rows that
stand on their own.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTENT_WORK_RULES = "pr_content_work_rules"
CONTENT_WORK_PROJECTIONS = "pr_content_work_projections"
CONTENT_ITEMS = "pr_content_items"
WORK_TYPES = "pr_work_types"

USERS = "users"
RESTRICT = "RESTRICT"

#: Enum columns are ``VARCHAR`` + CHECK, never a PostgreSQL ``ENUM`` type - the
#: repository-wide rule, so adding a kind is a code change rather than an
#: ``ALTER TYPE`` migration and the values stay greppable in the database.
KIND_VALUES = ("CONTENT_CREATION", "PRODUCTION", "PUBLICATION")
PROJECTION_STATUS_VALUES = ("PENDING", "RUNNING", "SETTLED", "FAILED")
OUTCOME_VALUES = (
    "PROJECTED",
    "UNCHANGED",
    "PENDING_VALIDATION",
    "REVERSED",
    "NOT_QUALIFIED",
    "NO_MAPPING",
    "UNRESOLVED_CONTRIBUTOR",
    "BLOCKED_BY_PERIOD",
)
CONTENT_TYPE_VALUES = (
    "ULTRA_SHORT_SCRIPT",
    "SHORT_VIDEO_SCRIPT",
    "FACEBOOK_POST",
    "LONG_YOUTUBE_SCRIPT",
    "PRESS_ARTICLE",
    "CORPORATE_TVC",
)


def _content_type(name: str) -> sa.Enum:
    """A fresh ``PrContentType`` enum object.

    One per column rather than a shared instance: two columns in one revision
    sharing an ``sa.Enum`` makes SQLAlchemy try to emit the same CHECK
    constraint name twice on some backends. The same reason 0032's ``_unit`` and
    0033's ``_basis`` exist.
    """
    return sa.Enum(*CONTENT_TYPE_VALUES, name=name, native_enum=False, length=30)


def upgrade() -> None:
    # --- Which work type a content milestone counts as ---------------------
    op.create_table(
        CONTENT_WORK_RULES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "contribution_kind",
            sa.Enum(*KIND_VALUES, name="pr_content_work_kind", native_enum=False, length=30),
            nullable=False,
        ),
        # NULL is **the default for the kind**, not "unclassified content" - see
        # the two partial indexes below, which is where that distinction is
        # actually held.
        sa.Column("content_type", _content_type("pr_content_work_rule_type"), nullable=True),
        sa.Column("work_type_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["work_type_id"],
            [f"{WORK_TYPES}.id"],
            name=op.f("fk_pr_content_work_rules_work_type_id_pr_work_types"),
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name=op.f("fk_pr_content_work_rules_created_by_user_id_users"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_content_work_rules")),
    )
    op.create_index(
        op.f("ix_pr_content_work_rules_work_type_id"), CONTENT_WORK_RULES, ["work_type_id"]
    )
    # **One rule per (kind, content type)**, and **one default per kind**. Two
    # partial indexes rather than one plain unique - see the module docstring on
    # why a nullable column cannot carry the second meaning.
    op.create_index(
        "uq_pr_content_work_rules_kind_type",
        CONTENT_WORK_RULES,
        ["contribution_kind", "content_type"],
        unique=True,
        postgresql_where=sa.text("content_type IS NOT NULL"),
        sqlite_where=sa.text("content_type IS NOT NULL"),
    )
    op.create_index(
        "uq_pr_content_work_rules_kind_default",
        CONTENT_WORK_RULES,
        ["contribution_kind"],
        unique=True,
        postgresql_where=sa.text("content_type IS NULL"),
        sqlite_where=sa.text("content_type IS NULL"),
    )
    op.create_index(
        "ix_pr_content_work_rules_kind_active",
        CONTENT_WORK_RULES,
        ["contribution_kind", "is_active"],
    )

    # --- One content item's standing with the projector -------------------
    op.create_table(
        CONTENT_WORK_PROJECTIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                *PROJECTION_STATUS_VALUES,
                name="pr_content_work_projection_status",
                native_enum=False,
                length=20,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "last_outcome",
            sa.Enum(
                *OUTCOME_VALUES, name="pr_content_work_outcome", native_enum=False, length=30
            ),
            nullable=True,
        ),
        # A short stable code, never an exception message: an error string is an
        # implementation detail, and storing one as operational state puts a
        # stack trace on an admin screen.
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
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
            "attempts >= 0", name=op.f("ck_pr_content_work_projections_attempts_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            [f"{CONTENT_ITEMS}.id"],
            name=op.f("fk_pr_content_work_projections_content_id_pr_content_items"),
            ondelete=RESTRICT,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pr_content_work_projections")),
    )
    op.create_index(
        op.f("ix_pr_content_work_projections_status"), CONTENT_WORK_PROJECTIONS, ["status"]
    )
    # **One standing per content item.** What makes "request projection"
    # idempotent, and what a repeated content transition collapses onto.
    op.create_index(
        "uq_pr_content_work_projections_content",
        CONTENT_WORK_PROJECTIONS,
        ["content_id"],
        unique=True,
    )
    # "What is waiting, oldest first" - the sweeper's only query.
    op.create_index(
        "ix_pr_content_work_projections_status_requested",
        CONTENT_WORK_PROJECTIONS,
        ["status", "requested_at"],
    )


def downgrade() -> None:
    """Drop the two tables. Nothing else is touched.

    No content row moves, no work item is removed and no ``counted_at``
    changes - the work the projector wrote is ordinary ``pr_work_items`` with a
    ``source_key``, and it stands on its own without the mapping that produced
    it.
    """
    op.drop_table(CONTENT_WORK_PROJECTIONS)
    op.drop_table(CONTENT_WORK_RULES)
