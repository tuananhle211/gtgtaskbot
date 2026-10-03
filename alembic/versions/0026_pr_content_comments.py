"""Step 1F.2.3g: the conversation around a piece of content.

One new table. Nothing existing is altered or dropped, and 0020 through 0025 are
untouched. The other half of this step - opening derivative contribution to
anybody who may view a piece - is an authorization change and needs no schema at
all, which is why this revision is only about comments.

Why a table rather than a column
---------------------------------

Every existing PR table records a *decision*: an approval names who signed, a
transition names who moved it, a submission names which file was judged. None of
them holds the sentence somebody typed on the way to that decision, so *"hook
đoạn đầu hơi dài, cắt còn 3 giây nhé"* was said in a Telegram group and lost, or
wedged into a ``change_note`` on a version it was not about.

It is deliberately none of the things it sits beside. Not an approval -
``pr_approval_events`` has no ``updated_at`` because a signature may not be
edited, and a comment may. Not an audit row - that is what the system recorded,
this is what a person chose to say. Not a content version - commenting writes no
draft and moves no stage.

Threading
---------

``parent_comment_id`` is nullable and self-referencing: ``NULL`` is a root,
anything else is a reply to a root. The **one-level** rule - a reply may not be
replied to - is enforced in ``PrContentCommentService`` and not here, because
"my parent has no parent" needs a subquery no supported database allows in a
``CHECK``. A ``depth`` column was refused for the usual reason: it is derivable
from the link and can disagree with it.

What the table does assert is the half a row-level check can: ``parent_not_self``,
and ``body_not_empty``.

Soft delete, and only here
---------------------------

``deleted_at`` and ``deleted_by_user_id`` make this the one PR table that
soft-deletes. That is not a reversal of revision 0021, which *removed* soft
deletion from ``pr_content_items``: a hidden content item was invisible in every
list while remaining the row every foreign key pointed at, and "deleted" had come
to mean "cancelled, but harder to find". A tombstoned comment is not hidden - it
is a visible gap in a conversation, which is what it is - and hard-deleting a
root would either orphan its replies or take somebody else's answers with it.

The body is **not** blanked on delete. It stops being sent to clients, and the
column keeps the only possible answer to "what was in the comment the lead
removed".

Indexes
-------

``ix_pr_content_comments_content_created`` - *"the root comments of this item,
oldest first"*, the list the detail page draws on every open, with the sort key
in the index so the ordering falls out of it. ``parent_comment_id`` is not in it:
the filter is ``IS NULL`` on a table holding a handful of rows per item, and the
three-column form would be speculative.

``ix_pr_content_comments_parent`` - *"the replies to these roots"*, one ``IN``
for a whole page. This is what makes rendering a thread two queries rather than
one per root.

No index on ``author_user_id``: "every comment by this person" is a question the
*column* makes answerable and nothing asks yet.

Naming
------

Constraint and index names are given explicitly and short, for the reason 0025
gives: the generated form concatenates long table names and runs past
PostgreSQL's 63-byte identifier limit, which is the defect 0021 exists to repair.
The ``CHECK`` names are bare here and on the model; the metadata's
``NAMING_CONVENTION`` adds the ``ck_<table>_`` prefix on both sides.

Downgrade
---------

Drops the two indexes and the table, in that order. What is lost is real and
total: every comment on every piece of content. Nothing else in the schema
references this table, so nothing else changes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CONTENT_ITEMS = "pr_content_items"
COMMENTS = "pr_content_comments"

RESTRICT = "RESTRICT"

CONTENT_INDEX = "ix_pr_content_comments_content_created"
PARENT_INDEX = "ix_pr_content_comments_parent"


def upgrade() -> None:
    op.create_table(
        COMMENTS,
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("author_user_id", sa.Uuid(as_uuid=True), nullable=False),
        # NULL is a root; anything else is a reply. The one-level rule is the
        # service's - see the module docstring.
        sa.Column("parent_comment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_comments"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            [f"{CONTENT_ITEMS}.id"],
            name="fk_content_comment_content",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            [f"{USERS}.id"],
            name="fk_content_comment_author",
            ondelete=RESTRICT,
        ),
        # RESTRICT on the self-reference too, like every other reference in this
        # module. The aggregate delete removes replies before roots because of
        # it - see ``PrContentLifecycleService._plan``.
        sa.ForeignKeyConstraint(
            ["parent_comment_id"],
            [f"{COMMENTS}.id"],
            name="fk_content_comment_parent",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"],
            [f"{USERS}.id"],
            name="fk_content_comment_deleted_by",
            ondelete=RESTRICT,
        ),
        # Bare names - NAMING_CONVENTION adds the ``ck_<table>_`` prefix.
        sa.CheckConstraint("length(trim(body)) > 0", name="body_not_empty"),
        sa.CheckConstraint(
            "parent_comment_id IS NULL OR parent_comment_id <> id", name="parent_not_self"
        ),
    )
    op.create_index(CONTENT_INDEX, COMMENTS, ["content_id", "created_at"])
    op.create_index(PARENT_INDEX, COMMENTS, ["parent_comment_id"])


def downgrade() -> None:
    """The exact inverse, by the names that actually exist."""
    op.drop_index(PARENT_INDEX, table_name=COMMENTS)
    op.drop_index(CONTENT_INDEX, table_name=COMMENTS)
    op.drop_table(COMMENTS)
