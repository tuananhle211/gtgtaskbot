"""Step 1F.2.3a: remove the soft-delete columns. Deletion is permanent now.

Revision 0020 added ``deleted_at``, ``deleted_by_user_id`` and
``deleted_reason`` to ``pr_content_items`` for a soft delete that shipped and was
withdrawn one step later: "Xóa nội dung" now removes the whole aggregate, so
there is no hidden row to flag and nothing left to attribute the flag to. Three
columns, one index and one check constraint go with it.

Why remove rather than leave them
---------------------------------

Because a nullable column nothing writes is a question every future reader has
to answer. Somebody reading ``pr_content_items`` in a year would find a
``deleted_at`` and reasonably conclude that a row could be soft-deleted, and the
first query they wrote against it would be silently wrong - it is *always* null
now, so ``WHERE deleted_at IS NULL`` reads as a safety filter and filters
nothing. Dead schema that looks live is worse than no schema.

**Nothing is edited in 0020.** It stays as the record of what was true between
the two deployments.

Why every dropped name is wrapped in ``op.f()``
------------------------------------------------

The first attempt at this revision failed on PostgreSQL, rolled back, and left
production on 0020:

```
constraint "ck_pr_content_items_ck_pr_content_items_deletion_is_attributed"
of relation "pr_content_items" does not exist
```

The doubled prefix is the whole story. ``Base.metadata`` carries a naming
convention whose check-constraint entry is
``ck_%(table_name)s_%(constraint_name)s``, and Alembic applies it to the name
handed to ``op.drop_constraint`` unless that name is marked as already final.
0020 created the constraint from the **bare** name ``deletion_is_attributed``,
so the convention produced ``ck_pr_content_items_deletion_is_attributed`` - the
right name, and the one in production. This revision then passed that *final*
name back, the convention treated it as bare a second time, and the prefix was
applied twice.

``op.f()`` is the marker that says "this string is the database identifier,
leave it alone". It is used below on all three objects. Only the check
constraint was actually being rewritten - the foreign-key and index conventions
contain no ``%(constraint_name)s`` token, so those two names survived untouched
- but the ``op.f()`` is on them as well, because "this name is final" is the
fact worth stating, and it should not depend on a reader knowing which
convention entries happen to interpolate the name.

The asymmetry with :func:`downgrade` is deliberate and must stay: **dropping
passes the final name through ``op.f()``, creating passes the bare name and
lets the convention build it.** Wrapping the create in ``op.f()`` would produce
a constraint literally called ``deletion_is_attributed``, which is not what
0020 made and not what the next drop would look for.

What happens to rows the withdrawn implementation soft-deleted
--------------------------------------------------------------

**No content row is deleted by this migration.** That is deliberate and worth
being explicit about: a data migration that hard-deleted everything previously
marked would destroy content on the strength of a flag set under different
semantics, without anybody choosing it, inside an ``alembic upgrade``. Nothing in
this file removes a row.

The consequence is that any item soft-deleted by the 1F.2.3 deployment
**reappears** in the lists, because the column that was hiding it is gone. In
practice those items are also ``CANCELLED`` - the withdrawn delete cancelled
whatever it could - so they come back into the ``Đã hủy`` tab rather than into
anybody's queue, which is a truthful place for abandoned work to sit. An operator
who wants one gone for good can now delete it properly, and it will be gone for
good.

A soft-deleted item that was **published** reappears as published and cannot be
permanently deleted at all. That is the new rule working, not a regression:
published operational history is exactly what the 1F.2.3a boundary exists to
keep.

Downgrade
---------

Re-adds the three columns, the index and the constraint, all empty. It restores
the *shape* and not the data: which items had been soft-deleted is not recorded
anywhere else, so downgrading gives back a schema in which nothing is marked
deleted. There is no way to do better, and pretending otherwise by guessing from
``CANCELLED`` would invent a fact.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CONTENT_ITEMS = "pr_content_items"

RESTRICT = "RESTRICT"


def upgrade() -> None:
    # Every name below is wrapped in ``op.f()``: it is the name **as it exists in
    # the database**, and ``op.f`` is how Alembic is told not to put it through
    # the naming convention again. Without it, ``ck_%(table_name)s_%(constraint_name)s``
    # treats the final name as the *bare* part and emits
    # ``ck_pr_content_items_ck_pr_content_items_deletion_is_attributed`` - see the
    # module docstring, which is where this migration failed the first time.
    #
    # The order is the dependency order: the check constraint reads both columns
    # and the foreign key holds one of them, so both go before the columns do.
    # PostgreSQL would drop the pair implicitly with the columns, but doing it
    # explicitly is what makes the intent reviewable and keeps the downgrade a
    # mirror image.
    op.drop_index(op.f("ix_pr_content_items_deleted_at"), table_name=CONTENT_ITEMS)
    op.drop_constraint(
        op.f("ck_pr_content_items_deletion_is_attributed"), CONTENT_ITEMS, type_="check"
    )
    op.drop_constraint(
        op.f("fk_pr_content_items_deleted_by_user_id_users"), CONTENT_ITEMS, type_="foreignkey"
    )
    op.drop_column(CONTENT_ITEMS, "deleted_reason")
    op.drop_column(CONTENT_ITEMS, "deleted_by_user_id")
    op.drop_column(CONTENT_ITEMS, "deleted_at")


def downgrade() -> None:
    """Put the columns back, empty. See the module docstring on what is lost.

    Note the names here are **bare**, unlike the drops above: the convention is
    what builds ``ck_pr_content_items_deletion_is_attributed`` out of
    ``deletion_is_attributed``, exactly as it did in 0020, so this restores the
    same identifiers production already has. See the module docstring on why
    wrapping these in ``op.f()`` would be the opposite of the fix.
    """
    op.add_column(
        CONTENT_ITEMS, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        CONTENT_ITEMS, sa.Column("deleted_by_user_id", sa.Uuid(as_uuid=True), nullable=True)
    )
    op.add_column(CONTENT_ITEMS, sa.Column("deleted_reason", sa.Text(), nullable=True))
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
    op.create_index("ix_pr_content_items_deleted_at", CONTENT_ITEMS, ["deleted_at"])
