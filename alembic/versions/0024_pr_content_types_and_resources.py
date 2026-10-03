"""Step 1F.2.3e: what a piece of content *is*, and what to read while judging it.

One added column and one new table. Nothing existing is altered or dropped, and
0020, 0021, 0022 and 0023 are untouched.

``pr_content_items.content_type`` - and why it is nullable
-----------------------------------------------------------

Six canonical formats: a short-video script, a Facebook post, a TVC. The
application **requires** one for every item created from now on.

The column is nevertheless ``NULL``-able, and this is the load-bearing decision
of the revision. Rows that predate it have no type, and there are only three
things to do about that:

* **guess.** Derive it from the target platform or the title - a piece on a
  TikTok channel is *probably* a short-video script. Refused: it is right most of
  the time, and a wrong classification is indistinguishable from a right one
  once it is a value in a column people filter and report on. Writing a
  plausible answer nobody chose into durable business state is the failure this
  revision most needed to avoid;
* **invent a seventh value** - ``LEGACY``, ``UNSPECIFIED`` - and backfill it.
  That is a vocabulary change to serve a data problem: every dropdown then has
  to hide the value, every report has to special-case it, and the enum stops
  meaning "the formats we make" and starts meaning "the formats we make, plus a
  bookkeeping marker";
* **leave it absent**, which is what ``NULL`` already means, and which is what
  the panel renders as *Chưa phân loại*.

The third is the honest one. Absence needs no backfill, no new enum member and
no guess, and anybody authorised can correct a row in one click. **This
migration therefore writes no data at all.**

``VARCHAR(30)`` with no ``CHECK``, matching every other enum column here - see
0023's docstring for why ``sa.Enum(native_enum=False)`` emits no constraint, and
:func:`~meobot.db.base.value_enum` for where the vocabulary is actually enforced.

``pr_content_resources`` - the input side of a content item
------------------------------------------------------------

The brief, the moodboard, the reference video, the study behind a health claim:
the material somebody reads *in order to* write and to review. Zero or many per
content item, mutable in place, deleted outright.

**Not ``pr_production_submissions``**, which is the output side - the finished
cut being judged. Reusing that table would have meant making its
``submission_no``, ``content_version_id`` and ``producer_user_id`` nullable to
fit a moodboard, and an internal-review screen that could no longer tell which
rows it was meant to be judging. See
``src/meobot/db/models/pr_content_resource.py``.

Foreign keys are ``RESTRICT``, like every other reference in this module: the
aggregate is deleted explicitly and in order by
:class:`~meobot.application.pr_lifecycle_service.PrContentLifecycleService`, and
``RESTRICT`` is the backstop that turns a forgotten table into a failed
transaction rather than a half-deleted item. Step 1F.2.3e adds this table to that
plan; a test walks the metadata and fails if it had not been.

Constraint names are given explicitly and short. The generated form concatenates
two long table names and runs past PostgreSQL's 63-byte identifier limit - the
defect 0021 was written to repair - so ``fk_content_resource_content`` rather
than whatever ``fk_pr_content_resources_content_id_pr_content_items`` truncates
to.

Indexes
-------

**One**, ``(content_id, required_for_review)``.

``content_id`` alone would serve the only query this table has - *"the resources
of this item"*, asked by the detail page and by nothing else. The second column
is there because it is also the first **sort** key and it is two values wide, so
the ordering falls out of the index rather than out of a sort, at no extra cost
on a table holding a handful of rows per item.

**No index on ``resource_type``**: nothing filters by it, and this step
deliberately does not add a filter that would want one. **No index on
``content_type``** either - the board's content-type filter is one equality
composed with a scope, a workflow group and a date range that are all far more
selective, on a table of a few thousand rows. Both decisions are recorded rather
than deferred silently; revisit either when a real query plan asks for it.

Downgrade
---------

Drops the table, its index and the column - the exact inverse, and it works,
which is not automatic: the index is dropped by the name it was created with, and
the constraint names above are the ones that actually exist. Tested by upgrading,
downgrading and upgrading again on a real PostgreSQL.

What is lost on downgrade is real: every review resource, and every content type
anybody has recorded. Nothing else is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CONTENT_ITEMS = "pr_content_items"
RESOURCES = "pr_content_resources"

RESTRICT = "RESTRICT"

#: Literals rather than imports from ``meobot.domain.pr``: a migration has to keep
#: meaning what it meant on the day it ran. Unit tests assert both lists still
#: match their enums.
CONTENT_TYPES = (
    "ULTRA_SHORT_SCRIPT",
    "SHORT_VIDEO_SCRIPT",
    "FACEBOOK_POST",
    "LONG_YOUTUBE_SCRIPT",
    "PRESS_ARTICLE",
    "CORPORATE_TVC",
)

RESOURCE_TYPES = (
    "REFERENCE",
    "IMAGE",
    "VIDEO",
    "DRIVE_FILE",
    "SOURCE",
    "BRAND_ASSET",
    "OTHER",
)

RESOURCES_INDEX = "ix_pr_content_resources_content_required"


def _content_type() -> sa.Enum:
    return sa.Enum(*CONTENT_TYPES, name="pr_content_type", native_enum=False, length=30)


def _resource_type() -> sa.Enum:
    return sa.Enum(*RESOURCE_TYPES, name="pr_content_resource_type", native_enum=False, length=20)


def upgrade() -> None:
    # Nullable, and no backfill. See the module docstring: historical rows have
    # no type because nobody chose one, and that is what ``NULL`` says.
    op.add_column(CONTENT_ITEMS, sa.Column("content_type", _content_type(), nullable=True))

    op.create_table(
        RESOURCES,
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource_type", _resource_type(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "required_for_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("added_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_resources"),
        # Short explicit names: the generated ones run past PostgreSQL's 63-byte
        # identifier limit, which is the defect 0021 exists to repair.
        sa.ForeignKeyConstraint(
            ["content_id"],
            [f"{CONTENT_ITEMS}.id"],
            name="fk_content_resource_content",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"],
            [f"{USERS}.id"],
            name="fk_content_resource_added_by",
            ondelete=RESTRICT,
        ),
        # Bare names - NAMING_CONVENTION adds the ``ck_<table>_`` prefix.
        sa.CheckConstraint("length(trim(label)) > 0", name="label_not_empty"),
        sa.CheckConstraint("length(trim(location)) > 0", name="location_not_empty"),
    )
    op.create_index(RESOURCES_INDEX, RESOURCES, ["content_id", "required_for_review"])


def downgrade() -> None:
    """The exact inverse, by the names that actually exist."""
    op.drop_index(RESOURCES_INDEX, table_name=RESOURCES)
    op.drop_table(RESOURCES)
    op.drop_column(CONTENT_ITEMS, "content_type")
