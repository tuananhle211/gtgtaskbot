"""Step 1C: immutable content versions.

One new table, ``pr_content_versions``. No existing table gains a column, loses
a column, changes a type or changes a constraint; revisions 0001-0014 are left
exactly as they are. In particular ``pr_content_items`` keeps every mutable
column it has - ``title``, ``topic``, ``hook``, ``brief`` stay, and stay
meaning what they meant, as the *current projection* of the newest version.

Why this table
--------------

``pr_approval_events.version_reviewed`` and ``pr_ai_reviews.reviewed_version``
have recorded which draft a reviewer judged since 0012 and 0014 respectively,
but nothing stored what that draft said. This is the row those integers point
at. ``UNIQUE (content_id, version_no)`` is what makes "version 4" name exactly
one thing, forever.

Append-only, like ``pr_approval_events`` and ``pr_ai_reviews``: ``created_at``
and no ``updated_at``. Correcting a draft is version N+1.

Downgrade
---------

Drops ``pr_content_versions`` and its indexes, and nothing else. **That loses
every draft ever written** - the text every review was about, leaving the
version numbers on the review tables pointing at nothing again. It touches no
other table and reads or writes no data outside this one. No ``DROP TYPE``:
this revision creates no enum column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The one identity table. Named once, as in 0012 and 0013.
USERS = "users"

#: Nothing in this module cascades. Deleting a content item or a person who
#: wrote a draft fails while the draft still refers to them.
RESTRICT = "RESTRICT"


def _not_empty(column: str) -> str:
    """The same non-empty test the models declare."""
    return f"length(trim({column})) > 0"


def upgrade() -> None:
    op.create_table(
        "pr_content_versions",
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("hook", sa.Text(), nullable=True),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("script_text", sa.Text(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_versions"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["pr_content_items.id"],
            name="fk_pr_content_versions_content_id_pr_content_items",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_content_versions_created_by_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_pr_content_versions_version_no_positive"),
        sa.CheckConstraint(_not_empty("title"), name="ck_pr_content_versions_title_not_empty"),
    )
    # Unique, and therefore also the index on ``(content_id, version_no)`` the
    # specification asks for: a B-tree unique index serves both. A second
    # non-unique index on the same pair would cost a write per append.
    op.create_index(
        "uq_pr_content_versions_content_version",
        "pr_content_versions",
        ["content_id", "version_no"],
        unique=True,
    )
    op.create_index(
        "ix_pr_content_versions_created_by_user_id",
        "pr_content_versions",
        ["created_by_user_id"],
    )
    op.create_index("ix_pr_content_versions_created_at", "pr_content_versions", ["created_at"])


def downgrade() -> None:
    """Drop ``pr_content_versions`` and its indexes.

    Read the data-loss note in the module docstring first. Indexes are dropped
    explicitly before the table, matching how 0012, 0013 and 0014 reverse
    themselves. ``pr_content_items`` is untouched: its projection columns still
    hold the newest values, which is exactly the state the schema was in before
    this revision.
    """
    op.drop_index("ix_pr_content_versions_created_at", table_name="pr_content_versions")
    op.drop_index("ix_pr_content_versions_created_by_user_id", table_name="pr_content_versions")
    op.drop_index("uq_pr_content_versions_content_version", table_name="pr_content_versions")
    op.drop_table("pr_content_versions")
