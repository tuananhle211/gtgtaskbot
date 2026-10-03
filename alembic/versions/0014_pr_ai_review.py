"""Step 1A1: the AI review record.

One new table, ``pr_ai_reviews``. No existing table gains a column, loses a
column, changes a type or changes a constraint; revisions 0001-0013 are left
exactly as they are. ``users`` is neither referenced nor altered - this table
has no foreign key to it, because an AI review names a model rather than a
person.

What ``AI_REVIEW`` costs this revision: nothing
-----------------------------------------------

The workflow gains a stage - ``AI_REVIEW``, between ``SCRIPTING`` and
``TEAM_LEAD_REVIEW`` - and **no DDL is emitted for it**. That is a consequence
of how this repository stores enums, and it is worth stating precisely rather
than leaving to be rediscovered:

* ``pr_content_items.workflow_stage`` is ``VARCHAR(30)``. There is no
  PostgreSQL ``ENUM`` type for it, so there is no ``ALTER TYPE ... ADD VALUE``
  to run;
* ``sa.Enum(..., native_enum=False)`` has defaulted to
  ``create_constraint=False`` since SQLAlchemy 1.4, so migration 0012 emitted
  no ``CHECK`` on the stage vocabulary either. There is no constraint to widen;
* ``AI_REVIEW`` is nine characters and the column holds thirty, so no widening
  is needed on length grounds.

The honest consequence: the database has never enforced which workflow stages
are legal, and it does not start now. Membership is checked by the ORM
(``validate_strings=True``) and by the type annotations only - a raw ``INSERT``
could always store an out-of-vocabulary string, and still can. That is
repository-wide debt inherited from 0001-0011, documented in
``docs/pr/STEP_1A_PR_CORE_FOUNDATION.md``, not something this revision
introduces or repairs.

What is created
---------------

::

    pr_content_items ──> pr_ai_reviews <── pr_tasks (optional)

Append-only, like ``pr_approval_events``: ``created_at`` and no ``updated_at``.
And deliberately **not** like ``pr_approval_events`` in one respect - there is
no ``reviewer_user_id`` and no ``users`` foreign key. An AI review is advisory
quality control; the human approval record is ``pr_approval_events``, which
this revision does not touch.

No uniqueness over ``(content_id, reviewed_version)``
-----------------------------------------------------

Two indexes cover that pair and neither is unique, on purpose. Reviewing one
version twice - a retry after a timeout, a second review of a different type -
is normal, and a unique index would turn the second attempt into a failure or
an overwrite instead of another row of history.

Downgrade
---------

Drops ``pr_ai_reviews`` and its indexes, and nothing else. **That loses every
AI review ever recorded** - which content was checked, at which version, by
which model and prompt, and what it found. It touches no other table and reads
or writes no data outside this one. No ``DROP TYPE``: the enum columns are
``VARCHAR`` and no PostgreSQL type was created.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: JSONB on PostgreSQL, matching every other migration that stores JSON.
JSONB = postgresql.JSONB(astext_type=sa.Text())

# --- The stored vocabularies -----------------------------------------------
# Written out as literals rather than imported from ``meobot.domain.pr.models``
# on purpose: a migration has to keep meaning what it meant on the day it ran,
# and an enum that gains a member next month must not retroactively change what
# this revision created. ``tests/unit/test_pr_ai_review_schema_parity.py``
# asserts these lists still match the domain enums.
AI_REVIEW_RESULTS = ("PASS", "PASS_WITH_WARNINGS", "REVISION_REQUIRED")
AI_REVIEW_TYPES = ("SCRIPT_QUALITY", "POLICY_COMPLIANCE", "BRAND_TONE", "FULL_REVIEW")

#: The canonical content workflow as of this revision - the order the stages
#: happen in, with ``CANCELLED`` last because it is a terminal alternative
#: rather than a position in the sequence. Recorded here as documentation and
#: as the thing the parity test compares the domain enum against; **no column,
#: constraint or index is generated from it**. Migration 0012's own
#: ``WORKFLOW_STAGES`` literal is the historical list and stays without
#: ``AI_REVIEW``, because that is what 0012 created.
WORKFLOW_STAGES = (
    "IDEA",
    "BRIEFING",
    "SCRIPTING",
    "AI_REVIEW",
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

#: Nothing in this module cascades. See the module docstring.
RESTRICT = "RESTRICT"


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def _not_empty(column: str) -> str:
    """The same non-empty test the models declare."""
    return f"length(trim({column})) > 0"


def upgrade() -> None:
    # --- pr_ai_reviews ----------------------------------------------------
    # Append-only. No ``updated_at``, no ``reviewer_user_id`` and no ``users``
    # foreign key - see the module docstring for why both absences are load
    # bearing rather than oversights.
    op.create_table(
        "pr_ai_reviews",
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("review_type", _enum(AI_REVIEW_TYPES, "pr_ai_review_type", 30), nullable=False),
        sa.Column("reviewed_version", sa.Integer(), nullable=False),
        sa.Column("result", _enum(AI_REVIEW_RESULTS, "pr_ai_review_result", 30), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("issues", JSONB, nullable=True),
        sa.Column("suggestions", JSONB, nullable=True),
        sa.Column("policy_flags", JSONB, nullable=True),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_ai_reviews"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name="fk_pr_ai_reviews_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["pr_tasks.id"],
            name="fk_pr_ai_reviews_task_id_pr_tasks",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            "reviewed_version >= 1", name="ck_pr_ai_reviews_reviewed_version_positive"
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="ck_pr_ai_reviews_score_in_range",
        ),
        sa.CheckConstraint(_not_empty("model_name"), name="ck_pr_ai_reviews_model_name_not_empty"),
        sa.CheckConstraint(
            _not_empty("prompt_version"), name="ck_pr_ai_reviews_prompt_version_not_empty"
        ),
    )
    # Neither pair index is unique: repeated reviews of one version are history,
    # not a collision.
    op.create_index(
        "ix_pr_ai_reviews_content_reviewed_at", "pr_ai_reviews", ["content_id", "reviewed_at"]
    )
    op.create_index(
        "ix_pr_ai_reviews_content_version", "pr_ai_reviews", ["content_id", "reviewed_version"]
    )
    op.create_index("ix_pr_ai_reviews_task_id", "pr_ai_reviews", ["task_id"])
    op.create_index("ix_pr_ai_reviews_result", "pr_ai_reviews", ["result"])
    op.create_index("ix_pr_ai_reviews_review_type", "pr_ai_reviews", ["review_type"])
    op.create_index("ix_pr_ai_reviews_reviewed_at", "pr_ai_reviews", ["reviewed_at"])
    op.create_index("ix_pr_ai_reviews_model_name", "pr_ai_reviews", ["model_name"])


def downgrade() -> None:
    """Drop ``pr_ai_reviews`` and its indexes.

    Read the data-loss note in the module docstring first. Indexes are dropped
    explicitly before the table, matching how 0012 and 0013 reverse themselves.
    Nothing else is removed: ``pr_content_items``, ``pr_tasks`` and
    ``pr_approval_events`` are untouched, and ``pr_content_items.workflow_stage``
    is not rewritten - any row already sitting in ``AI_REVIEW`` keeps that
    value, because this revision never constrained the column and reversing it
    must not start.
    """
    op.drop_index("ix_pr_ai_reviews_model_name", table_name="pr_ai_reviews")
    op.drop_index("ix_pr_ai_reviews_reviewed_at", table_name="pr_ai_reviews")
    op.drop_index("ix_pr_ai_reviews_review_type", table_name="pr_ai_reviews")
    op.drop_index("ix_pr_ai_reviews_result", table_name="pr_ai_reviews")
    op.drop_index("ix_pr_ai_reviews_task_id", table_name="pr_ai_reviews")
    op.drop_index("ix_pr_ai_reviews_content_version", table_name="pr_ai_reviews")
    op.drop_index("ix_pr_ai_reviews_content_reviewed_at", table_name="pr_ai_reviews")
    op.drop_table("pr_ai_reviews")
