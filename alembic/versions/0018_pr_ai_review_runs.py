"""Step 1F: durable execution records for automated PR reviews.

One new table, ``pr_ai_review_runs``. **No existing table gains a column, loses
a column, changes a type or changes a constraint.** ``pr_ai_reviews``,
``pr_content_items``, ``pr_content_versions`` and ``users`` are referenced and
never altered; revisions 0001-0017 are left exactly as they are.

Why a migration was needed
--------------------------

Step 1F was always going to need one, and the reason is the shape of what
already exists rather than a preference.

``pr_ai_reviews`` is append-only by design (Step 1A1): a verdict about a draft
is history, no row is ever updated, and there is deliberately no
``updated_at``. An *execution* is the opposite - it is queued, it runs, it
fails, it is retried, its status changes several times before it settles. There
were exactly two ways to hold that:

1. add ``status``/``attempt_count``/``error_code`` to ``pr_ai_reviews`` and
   start updating rows in it. That makes the append-only table mutable, which
   removes the one property that lets a stored verdict be trusted;
2. a second table whose rows are *meant* to change, pointing at the immutable
   one it produced.

This is the second. The append-only table keeps its shape and its meaning.

What the table holds
--------------------

One row per attempt to have a model review one draft:

* ``content_version_id`` pins the exact immutable draft, fixed when the run was
  queued. A worker that starts ten minutes later still reviews *that* text, and
  a run whose draft has since been rewritten becomes ``SUPERSEDED`` rather than
  transitioning content nobody has read;
* ``review_id`` points at the appended ``pr_ai_reviews`` row on success.
  Nothing points back - reviews recorded before this table existed have no run,
  and must stay readable;
* ``requested_by_user_id`` is **who asked**, never who reviewed. An automatic
  run has none. ``pr_ai_reviews`` still has no user column at all, and
  ``pr_approval_events`` remains the only record of a human decision;
* ``error_code`` is a stable machine string. No provider message, no traceback,
  no prompt text, no credential - the web panel reads this table.

The partial unique index
------------------------

``uq_pr_ai_review_runs_active`` covers ``(content_id, content_version_id,
review_type)`` **only where the status is QUEUED or RUNNING**. That is the
idempotency rule in the database rather than in application code: two callers
entering ``AI_REVIEW``, a double-tapped retry and a duplicated Celery delivery
all collide on it. Terminal rows are excluded on purpose, so a failed attempt
may legitimately be followed by another at the same draft and both are kept.

Delete behaviour
----------------

Every foreign key is ``ON DELETE RESTRICT``, as everywhere else in this schema.
Deleting a content item, a version or a user that an execution refers to fails
rather than quietly discarding the record of what was reviewed and why.

Downgrade
---------

Drops ``pr_ai_review_runs`` and its indexes, and nothing else. Every recorded
review survives - they live in ``pr_ai_reviews``, which this revision does not
touch. What is lost is the execution history: which attempts failed, how often
they were retried and which were superseded. Automatic review stops happening
until the table exists again; content entering ``AI_REVIEW`` simply waits there,
which is the pre-Step-1F behaviour and is safe.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Tables this revision points at and never alters.
CONTENT_ITEMS = "pr_content_items"
CONTENT_VERSIONS = "pr_content_versions"
AI_REVIEWS = "pr_ai_reviews"
USERS = "users"

#: Nothing in this module cascades.
RESTRICT = "RESTRICT"

# --- The stored vocabulary --------------------------------------------------
# Literals rather than imports from ``meobot.domain.pr.models``: a migration has
# to keep meaning what it meant on the day it ran. ``tests/unit`` asserts each
# of these still matches its enum.
REVIEW_TYPES = ("SCRIPT_QUALITY", "POLICY_COMPLIANCE", "BRAND_TONE", "FULL_REVIEW")
REVIEW_RESULTS = ("PASS", "PASS_WITH_WARNINGS", "REVISION_REQUIRED")
RUN_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "SUPERSEDED")
RUN_TRIGGERS = ("AUTO", "MANUAL_RETRY")

#: The predicate behind the idempotency index. The two statuses that hold the
#: single active slot for one draft, written out because an index cannot import
#: Python.
ACTIVE_PREDICATE = "status IN ('QUEUED', 'RUNNING')"


def upgrade() -> None:
    op.create_table(
        "pr_ai_review_runs",
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "review_type",
            sa.Enum(*REVIEW_TYPES, name="pr_ai_review_type", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column(
            "trigger",
            sa.Enum(*RUN_TRIGGERS, name="pr_ai_review_trigger", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(*RUN_STATUSES, name="pr_ai_review_run_status", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(*REVIEW_RESULTS, name="pr_ai_review_result", native_enum=False, length=30),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_ai_review_runs"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            [f"{CONTENT_ITEMS}.id"],
            name="fk_pr_ai_review_runs_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["content_version_id"],
            [f"{CONTENT_VERSIONS}.id"],
            name="fk_pr_ai_review_runs_content_version_id_pr_content_versions",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            [f"{AI_REVIEWS}.id"],
            name="fk_pr_ai_review_runs_review_id_pr_ai_reviews",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_ai_review_runs_requested_by_user_id_users",
            ondelete=RESTRICT,
        ),
        # Bare names. ``NAMING_CONVENTION`` prefixes ``ck_pr_ai_review_runs_``,
        # so a name written with the prefix already on it comes out doubled -
        # and then no longer matches what the ORM metadata would generate.
        sa.CheckConstraint("attempt_count >= 0", name="attempt_count_not_negative"),
        sa.CheckConstraint("length(trim(prompt_version)) > 0", name="prompt_version_not_empty"),
        sa.CheckConstraint(
            "model_name IS NULL OR length(trim(model_name)) > 0", name="model_name_not_blank"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL", name="finished_implies_started"
        ),
    )
    op.create_index("ix_pr_ai_review_runs_status", "pr_ai_review_runs", ["status"])
    op.create_index(
        "ix_pr_ai_review_runs_content_created", "pr_ai_review_runs", ["content_id", "created_at"]
    )
    op.create_index(
        "ix_pr_ai_review_runs_status_created", "pr_ai_review_runs", ["status", "created_at"]
    )
    # The idempotency rule. Partial, so terminal rows do not occupy the slot.
    op.create_index(
        "uq_pr_ai_review_runs_active",
        "pr_ai_review_runs",
        ["content_id", "content_version_id", "review_type"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_PREDICATE),
        sqlite_where=sa.text(ACTIVE_PREDICATE),
    )


def downgrade() -> None:
    """Drop ``pr_ai_review_runs`` and its indexes.

    Read the module docstring on what this loses: execution history, not
    reviews. ``pr_ai_reviews`` is untouched by both directions of this
    revision.
    """
    op.drop_index("uq_pr_ai_review_runs_active", table_name="pr_ai_review_runs")
    op.drop_index("ix_pr_ai_review_runs_status_created", table_name="pr_ai_review_runs")
    op.drop_index("ix_pr_ai_review_runs_content_created", table_name="pr_ai_review_runs")
    op.drop_index("ix_pr_ai_review_runs_status", table_name="pr_ai_review_runs")
    op.drop_table("pr_ai_review_runs")
