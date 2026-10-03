"""Step 1F.2.3b: durable content-transition history, and its reversal links.

One new table, ``pr_content_transition_events``. Nothing existing is altered:
no column is added to a Step 1A-1F table, no constraint changes, and 0020 and
0021 are untouched.

Why this needs a table
----------------------

Undo has to answer a **business** question - *"what was the last reversible
action, and is it still the thing that put this content where it is"* - in SQL,
under the content row's lock, before it reverses anything. Every fact it needs
already happened, and every one of them was recorded only in the audit trail, as
a ``pr.content.stage_changed`` row with the stages in a JSON payload.

That is not a foundation to build authority on. ``audit_logs.after_data`` is
free-form by design, nothing constrains its keys, and it is written by every
module in the system; an undo that parsed it would be one refactor away from
reversing the wrong action, and no test would notice because the payload would
still be *a* payload. The specification says so directly: durable business state
does not live in audit text.

So the transitions become rows with columns. The audit trail is unchanged and
still records every move - the two are written in the same transaction by
``PrContentWorkflowService.apply`` - and this is the half that can be queried.

The reversal pair, and the one mutable column
----------------------------------------------

An undo appends a second row, the backward edge, with ``trigger = 'UNDO'`` and
``reverses_event_id`` pointing at the row it takes back. The original row's
``reversed_by_event_id`` is then set, once, to point forward at it.

``reversed_by_event_id`` is the only column in this table that is ever updated,
and it is write-once. It exists so that "is this transition still in force" -
and therefore "is the approval it recorded still effective" - is an index lookup
rather than a correlated search for a reversal that usually does not exist. The
check constraint refuses a row that reverses itself.

Note what this makes possible without touching ``pr_approval_events``: that
table stays append-only, with no ``updated_at`` and no "cancelled" flag, and an
approval's *effectiveness* is a property of the transition that recorded it.

Indexes
-------

Three, each answering a query this step issues:

* ``(content_id, created_at)`` - the history a detail page renders, and the
  ordering undo scans to find the latest effective transition;
* ``approval_event_id`` - "is the approval behind this transition reversed",
  asked by ``_require_prior_team_lead_approval`` on every Head approval;
* ``actor_user_id`` - "who moved this", and the undo rule's own-action check.

No index on ``reversed_by_event_id``: it is read from a row already fetched by
one of the above, never searched.

Backfill
--------

**None, deliberately.** Content that moved before this revision has no rows
here, so nothing about it is undoable - which is the correct answer rather than
a limitation to work around. Reconstructing history from ``audit_logs`` was
considered and rejected for the reason the table exists: a reversal decision
taken on a parsed payload is exactly the failure mode this replaces. The first
transition after deployment starts the record.

Downgrade
---------

Drops the table. Every recorded transition goes with it, and after that nothing
is undoable until new history accumulates. No other data is affected: approvals,
submissions, versions and the audit trail are all untouched by this revision in
either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CONTENT_ITEMS = "pr_content_items"
CONTENT_VERSIONS = "pr_content_versions"
APPROVAL_EVENTS = "pr_approval_events"
SUBMISSIONS = "pr_production_submissions"
TRANSITIONS = "pr_content_transition_events"

RESTRICT = "RESTRICT"

# Literals rather than imports from ``meobot.domain.pr``: a migration has to keep
# meaning what it meant on the day it ran. Unit tests assert both lists still
# match their enums.
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
TRIGGERS = ("MANUAL", "AI_REVIEW", "HUMAN_APPROVAL", "UNDO")


def _stage() -> sa.Enum:
    return sa.Enum(*WORKFLOW_STAGES, name="pr_workflow_stage", native_enum=False, length=30)


def _trigger() -> sa.Enum:
    return sa.Enum(*TRIGGERS, name="pr_transition_trigger", native_enum=False, length=20)


def upgrade() -> None:
    op.create_table(
        TRANSITIONS,
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("from_stage", _stage(), nullable=False),
        sa.Column("to_stage", _stage(), nullable=False),
        sa.Column("trigger", _trigger(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approval_event_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("production_submission_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reverses_event_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reversed_by_event_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_transition_events"),
        # Short explicit names: the generated ones concatenate two long table
        # names and run past PostgreSQL's 63-byte identifier limit - the failure
        # 0019's docstring records.
        sa.ForeignKeyConstraint(
            ["content_id"], [f"{CONTENT_ITEMS}.id"], name="fk_transition_content", ondelete=RESTRICT
        ),
        sa.ForeignKeyConstraint(
            ["content_version_id"],
            [f"{CONTENT_VERSIONS}.id"],
            name="fk_transition_version",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], [f"{USERS}.id"], name="fk_transition_actor", ondelete=RESTRICT
        ),
        sa.ForeignKeyConstraint(
            ["approval_event_id"],
            [f"{APPROVAL_EVENTS}.id"],
            name="fk_transition_approval",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["production_submission_id"],
            [f"{SUBMISSIONS}.id"],
            name="fk_transition_submission",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reverses_event_id"],
            [f"{TRANSITIONS}.id"],
            name="fk_transition_reverses",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by_event_id"],
            [f"{TRANSITIONS}.id"],
            name="fk_transition_reversed_by",
            ondelete=RESTRICT,
        ),
        # Bare name - NAMING_CONVENTION adds the ``ck_<table>_`` prefix.
        sa.CheckConstraint("reverses_event_id IS NULL OR reverses_event_id <> id", name="no_self_reversal"),
    )
    op.create_index(
        "ix_pr_content_transition_events_content_created",
        TRANSITIONS,
        ["content_id", "created_at"],
    )
    op.create_index(
        "ix_pr_content_transition_events_approval", TRANSITIONS, ["approval_event_id"]
    )
    op.create_index(
        "ix_pr_content_transition_events_actor_user_id", TRANSITIONS, ["actor_user_id"]
    )
    op.create_index("ix_pr_content_transition_events_created_at", TRANSITIONS, ["created_at"])


def downgrade() -> None:
    """Drop the history. Nothing else is touched - see the module docstring."""
    op.drop_index("ix_pr_content_transition_events_created_at", table_name=TRANSITIONS)
    op.drop_index("ix_pr_content_transition_events_actor_user_id", table_name=TRANSITIONS)
    op.drop_index("ix_pr_content_transition_events_approval", table_name=TRANSITIONS)
    op.drop_index("ix_pr_content_transition_events_content_created", table_name=TRANSITIONS)
    op.drop_table(TRANSITIONS)
