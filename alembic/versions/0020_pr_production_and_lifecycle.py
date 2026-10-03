"""Step 1F.2.3: production ownership, production submissions, and soft delete.

One new table, six added columns, and no change to anything 0001-0019 created.
Every added column is nullable or carries a server default, so the revision is
safe to apply while the previous application version is still serving traffic:
an older API writing a row simply leaves the new columns unset, which is what
they mean.

Why each column exists
----------------------

``pr_content_items.producer_user_id``
    Who is cutting the video. **Nullable, and that is the feature** - approved
    work enters ``PRODUCTION`` before anybody has been handed the edit, and
    ``NULL`` there is the "chưa có người nhận" state the panel renders. Not
    reused from ``owner_user_id``: the owner answers for the piece, the producer
    for one file, and on some pieces they are two people.

``pr_content_items.production_started_at``
    When the item **first** entered production, stamped once and never cleared.
    This is the durable answer to "has this ever been produced", which the
    member-delete rule turns on. The current stage cannot answer it: an internal
    reviewer sending a cut back moves the item to a stage it has been at before,
    and a rule reading the stage would hand a member the delete button for a
    piece that already has a producer's afternoon in it.

    **Deliberately not backfilled.** Rows sitting at ``PRODUCTION`` or later
    today did enter production, at a moment nobody recorded; writing
    ``updated_at`` or ``now()`` into this column would invent a date and make it
    indistinguishable from a measured one. Instead
    :func:`~meobot.domain.pr.lifecycle.has_reached_production` reads the stamp
    **or** the current stage, so legacy rows answer correctly with no fiction in
    the table. A unit test pins that behaviour.

``pr_content_items.deleted_at`` / ``deleted_by_user_id`` / ``deleted_reason``
    Soft delete. A hard ``DELETE`` is not available here and should not be made
    available: every PR foreign key is ``ON DELETE RESTRICT``, so the statement
    would fail against the item's own versions, AI verdicts, approvals, tasks and
    publications - and loosening those to make it succeed would mean a delete
    could destroy an approval somebody signed. The paired check constraint
    ``ck_pr_content_items_deletion_is_attributed`` makes "deleted by nobody"
    unrepresentable.

``pr_approval_events.production_submission_id``
    Which cut an internal review was about. Nullable because the other two gates
    judge a script - ``version_reviewed`` already identifies that - and because
    every row written before this revision predates the concept. Adding a
    nullable column to an append-only table updates nothing.

``pr_production_submissions``
    The cut itself, one row per handover, append-only. See the model docstring
    in ``meobot/db/models/pr_production.py`` for why this is a table and not a
    ``production_url`` column: a piece that is sent back and re-cut has two
    submissions against one script version, and a column would erase the file
    the first internal-review decision was about.

Indexes
-------

No index is added speculatively. Three are created and each answers a query this
step actually issues:

* ``ix_pr_content_items_producer_user_id`` - "what am I producing", which is the
  new ``MY_ACTIONS`` branch and runs on every board load for a producer;
* ``ix_pr_content_items_deleted_at`` - every content list now filters
  ``deleted_at IS NULL``;
* ``uq_pr_production_submissions_content_no`` - unique, and therefore also the
  index behind "the latest submission for this item". It is the constraint that
  makes two concurrent submits fail one of themselves rather than both becoming
  "the latest".

Concurrency
-----------

Nothing here enforces the one-winner rule for claiming production; that is a
conditional ``UPDATE ... WHERE producer_user_id IS NULL`` in
``PrProductionService.claim_production``, which is correct on both PostgreSQL and
the offline SQLite suite. A unique index cannot express "may be set once from
null" without inventing a second table, and a table whose only job is to stop a
race would be more machinery than the race deserves.

Downgrade
---------

Drops the table and the six columns. **What is lost is real**: every production
file reference, every producer assignment, and the record of which items were
deleted - after which deleted content reappears in every list, because the column
that hid it is gone. Approvals survive in ``pr_approval_events`` and simply stop
being able to name the cut they judged. Content, versions, verdicts and tasks are
untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CONTENT_ITEMS = "pr_content_items"
CONTENT_VERSIONS = "pr_content_versions"
APPROVAL_EVENTS = "pr_approval_events"
SUBMISSIONS = "pr_production_submissions"

RESTRICT = "RESTRICT"

# Literals rather than imports from ``meobot.domain.pr.models``: a migration has
# to keep meaning what it meant on the day it ran. A unit test asserts this list
# still matches ``PrProductionArtifactType``.
ARTIFACT_TYPES = ("DRIVE_LINK", "NAS_LINK", "NAS_PATH", "EXTERNAL_LINK")


def _artifact_type() -> sa.Enum:
    return sa.Enum(
        *ARTIFACT_TYPES, name="pr_production_artifact_type", native_enum=False, length=20
    )


def upgrade() -> None:
    # --- Production ownership and the lifecycle stamps -----------------------
    op.add_column(
        CONTENT_ITEMS, sa.Column("producer_user_id", sa.Uuid(as_uuid=True), nullable=True)
    )
    op.add_column(
        CONTENT_ITEMS,
        sa.Column("production_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        CONTENT_ITEMS, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        CONTENT_ITEMS, sa.Column("deleted_by_user_id", sa.Uuid(as_uuid=True), nullable=True)
    )
    op.add_column(CONTENT_ITEMS, sa.Column("deleted_reason", sa.Text(), nullable=True))

    op.create_foreign_key(
        "fk_pr_content_items_producer_user_id_users",
        CONTENT_ITEMS,
        USERS,
        ["producer_user_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_foreign_key(
        "fk_pr_content_items_deleted_by_user_id_users",
        CONTENT_ITEMS,
        USERS,
        ["deleted_by_user_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_check_constraint(
        "deletion_is_attributed",
        CONTENT_ITEMS,
        "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
    )
    op.create_index("ix_pr_content_items_producer_user_id", CONTENT_ITEMS, ["producer_user_id"])
    op.create_index("ix_pr_content_items_deleted_at", CONTENT_ITEMS, ["deleted_at"])

    # --- The cut ------------------------------------------------------------
    op.create_table(
        SUBMISSIONS,
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("submission_no", sa.Integer(), nullable=False),
        sa.Column("producer_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("artifact_type", _artifact_type(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_production_submissions"),
        # Short explicit names: the generated ones concatenate two long table
        # names and run past PostgreSQL's 63-byte identifier limit, which is the
        # failure 0019's docstring records.
        sa.ForeignKeyConstraint(
            ["content_id"], [f"{CONTENT_ITEMS}.id"], name="fk_prod_sub_content", ondelete=RESTRICT
        ),
        sa.ForeignKeyConstraint(
            ["content_version_id"],
            [f"{CONTENT_VERSIONS}.id"],
            name="fk_prod_sub_version",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["producer_user_id"], [f"{USERS}.id"], name="fk_prod_sub_producer", ondelete=RESTRICT
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            [f"{USERS}.id"],
            name="fk_prod_sub_submitter",
            ondelete=RESTRICT,
        ),
        # Bare names - NAMING_CONVENTION adds the ``ck_<table>_`` prefix.
        sa.CheckConstraint("submission_no >= 1", name="submission_no_positive"),
        sa.CheckConstraint("length(trim(location)) > 0", name="location_not_empty"),
    )
    op.create_index(
        "ix_pr_production_submissions_producer_user_id", SUBMISSIONS, ["producer_user_id"]
    )
    op.create_index("ix_pr_production_submissions_created_at", SUBMISSIONS, ["created_at"])
    op.create_index(
        "uq_pr_production_submissions_content_no",
        SUBMISSIONS,
        ["content_id", "submission_no"],
        unique=True,
    )

    # --- Which cut an internal review judged ---------------------------------
    op.add_column(
        APPROVAL_EVENTS,
        sa.Column("production_submission_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_approval_event_prod_sub",
        APPROVAL_EVENTS,
        SUBMISSIONS,
        ["production_submission_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_index(
        "ix_pr_approval_events_production_submission_id",
        APPROVAL_EVENTS,
        ["production_submission_id"],
    )


def downgrade() -> None:
    """Drop the submissions table and the six columns.

    Read the module docstring on what this loses. The one consequence worth
    repeating: deleted content **reappears** in every list, because the column
    that hid it no longer exists.
    """
    op.drop_index(
        "ix_pr_approval_events_production_submission_id", table_name=APPROVAL_EVENTS
    )
    op.drop_constraint("fk_approval_event_prod_sub", APPROVAL_EVENTS, type_="foreignkey")
    op.drop_column(APPROVAL_EVENTS, "production_submission_id")

    op.drop_index("uq_pr_production_submissions_content_no", table_name=SUBMISSIONS)
    op.drop_index("ix_pr_production_submissions_created_at", table_name=SUBMISSIONS)
    op.drop_index("ix_pr_production_submissions_producer_user_id", table_name=SUBMISSIONS)
    op.drop_table(SUBMISSIONS)

    op.drop_index("ix_pr_content_items_deleted_at", table_name=CONTENT_ITEMS)
    op.drop_index("ix_pr_content_items_producer_user_id", table_name=CONTENT_ITEMS)
    # ``op.f`` marks the name as the final database identifier. Without it the
    # metadata's ``ck_%(table_name)s_%(constraint_name)s`` convention applies the
    # prefix a second time and emits
    # ``ck_pr_content_items_ck_pr_content_items_deletion_is_attributed``, which
    # exists nowhere - the failure 0021 hit on its first deployment. **Downgrade
    # only.** Nothing about what this revision does on the way up has changed.
    op.drop_constraint(
        op.f("ck_pr_content_items_deletion_is_attributed"), CONTENT_ITEMS, type_="check"
    )
    op.drop_constraint(
        "fk_pr_content_items_deleted_by_user_id_users", CONTENT_ITEMS, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_pr_content_items_producer_user_id_users", CONTENT_ITEMS, type_="foreignkey"
    )
    op.drop_column(CONTENT_ITEMS, "deleted_reason")
    op.drop_column(CONTENT_ITEMS, "deleted_by_user_id")
    op.drop_column(CONTENT_ITEMS, "deleted_at")
    op.drop_column(CONTENT_ITEMS, "production_started_at")
    op.drop_column(CONTENT_ITEMS, "producer_user_id")
