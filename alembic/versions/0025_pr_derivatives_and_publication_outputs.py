"""Step 1F.2.3f: derivative production outputs, destination links, and which file went out.

Two new tables and four added columns. Nothing existing is altered or dropped,
and 0020, 0021, 0022, 0023 and 0024 are untouched.

Why a content item needed re-cuts at all
-----------------------------------------

A piece of content is a reusable asset, not a job that ends when it goes out
once. The 60-second master went to Facebook in August; in October a TikTok
channel that did not exist then wants the same piece, so somebody makes a
25-second cut and posts that. Before this revision there were two ways to record
it and both were wrong: clone the content - two codes, two scripts, and every
"how many pieces did we make" report answering 2 for one idea - or overwrite the
production submission, destroying the exact file an internal reviewer approved,
which is the failure ``pr_production_submissions`` was designed to prevent.

``pr_content_derivatives`` is the third answer: same content row, new file, no
workflow.

``pr_content_derivatives``
---------------------------

``source_submission_id`` is **nullable** and points at
``pr_production_submissions`` and nothing else. Nullable because the lineage is
honestly sometimes unknown - a file re-cut from raw footage on somebody's laptop
came from no tracked submission, and requiring a link there would mean storing a
guess. Pointing only at a submission is what makes derivative-of-derivative
unrepresentable rather than merely refused: recursive lineage needs cycle
prevention, depth limits and a display that can draw a tree, and none of that is
worth carrying for a link nobody has asked to follow.

``pr_content_destinations``
----------------------------

The landing page, the booking page, the product listing - where the content
sends a customer. Its own table because it is none of the three things it sits
near: not a review resource (material read *in order to* write), not a production
output (nobody produced it and it cannot be published), and not a publication URL
(that is where this piece ended up, one row per posting; a destination is the
same link across every channel and every repost).

``label`` and ``url``, and nothing else. **This is not a product catalogue** - no
SKU, no price, no inventory, no status. A team that needs those needs a product
table content links to, which is a different design and a different step.

``pr_publications``: which output was published
------------------------------------------------

Four columns, two of which carry the load. ``production_submission_id`` and
``derivative_id`` say *which file* went out, so "this was on TikTok in October"
becomes "the 25-second cutdown was on TikTok in October" - the question a person
actually has. ``note`` records the circumstance. Nothing here duplicates an
existing column: ``url`` is already the public post link, ``published_at`` is
already the business instant, ``publisher_user_id`` is already the actor, and
``channel_id`` is already the canonical channel.

**The constraint, and the legacy rows.** The rule the product wants is "exactly
one output reference", and it is enforced across two layers rather than one:

* this migration adds ``ck_pr_publications_output_not_both``, which refuses a row
  naming **both** a submission and a derivative. That is meaningless under every
  reading, past and future, so it is safe to assert about existing rows;
* the *"and not neither"* half lives in
  :meth:`~meobot.application.pr_publication_service.PrPublicationService.register_publication`
  and applies to new writes only.

A strict XOR ``CHECK`` was considered and refused. Existing publication rows
reference no output, and the only way to make them satisfy XOR is to invent a
submission or a derivative for each - fabricating production lineage in durable
business state to satisfy a constraint. That is a far worse outcome than a
nullable pair, and it is exactly the kind of plausible-looking fiction 0024's
docstring refused to write for ``content_type``. Legacy rows therefore keep both
references ``NULL``, permanently and legitimately.

Indexes
-------

``ix_pr_content_derivatives_content_created`` - the only query the table has,
*"the derivatives of this item, oldest first"*, with the sort key in the index so
the ordering falls out of it. No index on ``derivative_type``: "which content has
cutdowns" is a question the *column* makes answerable and nothing asks yet.

``ix_pr_content_destinations_content`` - same shape, one column, because the list
is ordered by nothing more interesting than insertion.

``ix_pr_publications_content_published`` - *"the publication history of this
piece, newest first"*, which the detail page now draws on every open. ``content_id``
alone was already indexed and would serve the lookup; the second column is the
sort key. No index on either new foreign key: "which publications used this
derivative" is asked once, per derivative, at delete time, against a handful of
rows per content item.

Naming
------

Constraint and index names are given explicitly and short. The generated form
concatenates long table names and runs past PostgreSQL's 63-byte identifier
limit - the defect 0021 exists to repair - so ``fk_content_derivative_content``
rather than whatever ``fk_pr_content_derivatives_content_id_pr_content_items``
truncates to. The ``CHECK`` names are bare here and on the models; the metadata's
``NAMING_CONVENTION`` adds the ``ck_<table>_`` prefix on both sides, so they
agree without being written twice.

Downgrade
---------

The exact inverse, by the names that actually exist: drop the added columns and
their constraints, then the two tables and their indexes. Tested by upgrading,
downgrading and upgrading again on a real PostgreSQL.

What is lost on downgrade is real: every derivative, every destination link, and
every record of which file a publication used. The publication rows themselves
survive - the columns go, the history does not.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CONTENT_ITEMS = "pr_content_items"
SUBMISSIONS = "pr_production_submissions"
DERIVATIVES = "pr_content_derivatives"
DESTINATIONS = "pr_content_destinations"
PUBLICATIONS = "pr_publications"

RESTRICT = "RESTRICT"

#: Literals rather than imports from ``meobot.domain.pr``: a migration has to
#: keep meaning what it meant on the day it ran. A unit test asserts this list
#: still matches ``PrContentDerivativeType``.
DERIVATIVE_TYPES = (
    "REMIX",
    "CUTDOWN",
    "RECUT",
    "REFORMAT",
    "CAPTION_VARIANT",
    "OTHER",
)

DERIVATIVES_INDEX = "ix_pr_content_derivatives_content_created"
DESTINATIONS_INDEX = "ix_pr_content_destinations_content"
PUBLICATIONS_INDEX = "ix_pr_publications_content_published"

#: Bare name. ``NAMING_CONVENTION`` prefixes it with ``ck_pr_publications_`` on
#: the model side, and ``op.f()`` below produces the same final identifier here -
#: which is what lets ``downgrade`` drop it by a name that exists.
OUTPUT_NOT_BOTH = "output_not_both"

FK_PUBLICATION_SUBMISSION = "fk_publication_submission"
FK_PUBLICATION_DERIVATIVE = "fk_publication_derivative"


def _derivative_type() -> sa.Enum:
    return sa.Enum(
        *DERIVATIVE_TYPES, name="pr_content_derivative_type", native_enum=False, length=20
    )


def upgrade() -> None:
    op.create_table(
        DERIVATIVES,
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        # Nullable, and pointing only at a submission. See the module docstring.
        sa.Column("source_submission_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("derivative_type", _derivative_type(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_derivatives"),
        # Short explicit names: the generated ones run past PostgreSQL's 63-byte
        # identifier limit, which is the defect 0021 exists to repair.
        sa.ForeignKeyConstraint(
            ["content_id"],
            [f"{CONTENT_ITEMS}.id"],
            name="fk_content_derivative_content",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["source_submission_id"],
            [f"{SUBMISSIONS}.id"],
            name="fk_content_derivative_source",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            [f"{USERS}.id"],
            name="fk_content_derivative_author",
            ondelete=RESTRICT,
        ),
        # Bare names - NAMING_CONVENTION adds the ``ck_<table>_`` prefix.
        sa.CheckConstraint("length(trim(label)) > 0", name="label_not_empty"),
        sa.CheckConstraint("length(trim(location)) > 0", name="location_not_empty"),
    )
    op.create_index(DERIVATIVES_INDEX, DERIVATIVES, ["content_id", "created_at"])

    op.create_table(
        DESTINATIONS,
        sa.Column("content_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("added_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_content_destinations"),
        sa.ForeignKeyConstraint(
            ["content_id"],
            [f"{CONTENT_ITEMS}.id"],
            name="fk_content_destination_content",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"],
            [f"{USERS}.id"],
            name="fk_content_destination_added_by",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint("length(trim(label)) > 0", name="label_not_empty"),
        sa.CheckConstraint("length(trim(url)) > 0", name="url_not_empty"),
    )
    op.create_index(DESTINATIONS_INDEX, DESTINATIONS, ["content_id"])

    # Which file went out. Nullable on both, and no backfill: see the module
    # docstring on why inventing lineage for legacy rows was refused.
    op.add_column(
        PUBLICATIONS, sa.Column("production_submission_id", sa.Uuid(as_uuid=True), nullable=True)
    )
    op.add_column(PUBLICATIONS, sa.Column("derivative_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column(PUBLICATIONS, sa.Column("note", sa.Text(), nullable=True))
    op.create_foreign_key(
        FK_PUBLICATION_SUBMISSION,
        PUBLICATIONS,
        SUBMISSIONS,
        ["production_submission_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_foreign_key(
        FK_PUBLICATION_DERIVATIVE,
        PUBLICATIONS,
        DERIVATIVES,
        ["derivative_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    # Never both. "And not neither" is the service's, on new writes only.
    op.create_check_constraint(
        OUTPUT_NOT_BOTH,
        PUBLICATIONS,
        "NOT (production_submission_id IS NOT NULL AND derivative_id IS NOT NULL)",
    )
    op.create_index(PUBLICATIONS_INDEX, PUBLICATIONS, ["content_id", "published_at"])


def downgrade() -> None:
    """The exact inverse, by the names that actually exist."""
    op.drop_index(PUBLICATIONS_INDEX, table_name=PUBLICATIONS)
    op.drop_constraint(op.f(f"ck_{PUBLICATIONS}_{OUTPUT_NOT_BOTH}"), PUBLICATIONS, type_="check")
    op.drop_constraint(FK_PUBLICATION_DERIVATIVE, PUBLICATIONS, type_="foreignkey")
    op.drop_constraint(FK_PUBLICATION_SUBMISSION, PUBLICATIONS, type_="foreignkey")
    op.drop_column(PUBLICATIONS, "note")
    op.drop_column(PUBLICATIONS, "derivative_id")
    op.drop_column(PUBLICATIONS, "production_submission_id")

    op.drop_index(DESTINATIONS_INDEX, table_name=DESTINATIONS)
    op.drop_table(DESTINATIONS)
    op.drop_index(DERIVATIVES_INDEX, table_name=DERIVATIVES)
    op.drop_table(DERIVATIVES)
