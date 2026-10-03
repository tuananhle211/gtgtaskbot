"""Step 1C.1: PR review grants, and the counters behind PR codes.

Two new tables and nothing else. No existing table gains a column, loses a
column, changes a type or changes a constraint; revisions 0001-0015 are left
exactly as they are, and every ``code`` column keeps the shape and the unique
index it has had since 0012.

``pr_user_capabilities``
------------------------

Step 1C reported one thing its authorization could not express: **MeoBot's four
roles cannot tell a Team Lead from a Head.** ``TEAM_LEAD`` holds both
``script.review`` and ``script.approve``, so whichever permissions the two
review gates map to, one person satisfies both - and "the Head must be somebody
other than the Team Lead" was a rule with nowhere to live.

This table is where it lives. It is not a second identity system: the subject
is a ``users.id``, authority still comes from the role and the existing
permission matrix, and a grant is required *in addition to* the permission, so
it can only ever narrow who may act. Only the three review capabilities are
grant-backed; the other seven are decided by permissions alone.

Dated with closed intervals and ``NULL`` for unbounded, matching
``pr_channel_assignments``. A partial unique index refuses a second **open**
grant of one capability to one person; closed rows may overlap, because a
re-grant after a gap is history repeating rather than a conflict. Revoking sets
``effective_to``; nothing is ever deleted, because an approval recorded under a
grant that has since been withdrawn was still legitimate when it happened.

``pr_code_counters``
--------------------

Step 1C left codes as caller inputs because no safe allocator existed and
``MAX(code) + 1`` is a race, not an allocator. This is the allocator: one row
per namespace (per year, where the namespace resets annually), holding *the
next number to hand out*, incremented by a single
``INSERT … ON CONFLICT … DO UPDATE … RETURNING`` so the read and the write
cannot be separated by another transaction.

**Two partial unique indexes, not one.** ``year`` is nullable and PostgreSQL
treats two ``NULL``s as distinct, so a plain ``UNIQUE (namespace, year)`` would
neither prevent a second ``('CHANNEL', NULL)`` row nor give ``ON CONFLICT``
anything to match - the channel counter would silently restart at 1 forever.
The two indexes cover disjoint halves of the table: ``(namespace, year)`` where
the year is present, ``(namespace)`` where it is not. Each allocation names the
one that applies to it.

Numbers are **not** skipped by a rollback: this is a table row, not a
PostgreSQL sequence, so the increment is transactional and a failed command
gives its number back. The cost is that the counter row is locked until the
command commits, so creations in one namespace serialise on it - milliseconds,
for commands this short.

Delete behaviour
----------------

Both foreign keys on ``pr_user_capabilities`` are ``ON DELETE RESTRICT``, as
every foreign key in this module is. Deleting a person who has been granted a
review right, or who granted one to somebody else, fails rather than quietly
removing the record of who was allowed to approve what.
``pr_code_counters`` has no foreign keys at all: a counter belongs to a
namespace, not to a row.

Downgrade
---------

Drops both tables and their indexes, and nothing else. **That loses every
review grant** - after which nobody can pass a review gate until grants are
issued again - **and every counter**, after which the next allocation in each
namespace restarts at 1 and will collide with codes already on rows. Neither
table is touched by any other revision, and no data outside the two is read or
written.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The one identity table. Named once, as in 0012, 0013 and 0015.
USERS = "users"

#: Nothing in this module cascades.
RESTRICT = "RESTRICT"

# --- The stored vocabulary --------------------------------------------------
# Written out as a literal rather than imported from ``meobot.domain.pr.policy``
# on purpose: a migration has to keep meaning what it meant on the day it ran.
# ``tests/unit/test_pr_authorization_schema_parity.py`` asserts this list still
# matches the domain enum.
PR_CAPABILITIES = (
    "PR_CONTENT_CREATE",
    "PR_CONTENT_EDIT",
    "PR_TEAM_LEAD_REVIEW",
    "PR_HEAD_REVIEW",
    "PR_INTERNAL_REVIEW",
    "PR_TASK_MANAGE",
    "PR_CHANNEL_MANAGE",
    "PR_PUBLICATION_REGISTER",
    "PR_CONTENT_CANCEL",
    "PR_CONTENT_TRANSITION",
)


def _enum(values: tuple[str, ...], name: str, length: int) -> sa.Enum:
    """VARCHAR, matching ``meobot.db.base``'s convention."""
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def _not_empty(column: str) -> str:
    """The same non-empty test the models declare."""
    return f"length(trim({column})) > 0"


def upgrade() -> None:
    # --- pr_user_capabilities ---------------------------------------------
    op.create_table(
        "pr_user_capabilities",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("capability", _enum(PR_CAPABILITIES, "pr_capability", 40), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("granted_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_user_capabilities"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{USERS}.id"],
            name="fk_pr_user_capabilities_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            [f"{USERS}.id"],
            name="fk_pr_user_capabilities_granted_by_user_id_users",
            ondelete=RESTRICT,
        ),
        sa.CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
            name="ck_pr_user_capabilities_effective_period_ordered",
        ),
    )
    op.create_index("ix_pr_user_capabilities_user_id", "pr_user_capabilities", ["user_id"])
    # At most one *open* grant per (person, capability). Closed rows may
    # overlap - see the module docstring.
    op.create_index(
        "uq_pr_user_capabilities_open_grant",
        "pr_user_capabilities",
        ["user_id", "capability"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "ix_pr_user_capabilities_capability_to",
        "pr_user_capabilities",
        ["capability", "effective_to"],
    )

    # --- pr_code_counters --------------------------------------------------
    # ``next_value`` is the number the *next* allocation returns, so a row
    # created by the first allocation already holds 2.
    op.create_table(
        "pr_code_counters",
        sa.Column("namespace", sa.String(length=40), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("next_value", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_code_counters"),
        sa.CheckConstraint(_not_empty("namespace"), name="ck_pr_code_counters_namespace_not_empty"),
        sa.CheckConstraint("next_value >= 1", name="ck_pr_code_counters_next_value_positive"),
        sa.CheckConstraint("year IS NULL OR year >= 2000", name="ck_pr_code_counters_year_plausible"),
    )
    # Two partial unique indexes covering disjoint halves of the table. See
    # the module docstring for why one constraint would not work.
    op.create_index(
        "uq_pr_code_counters_namespace_year",
        "pr_code_counters",
        ["namespace", "year"],
        unique=True,
        postgresql_where=sa.text("year IS NOT NULL"),
    )
    op.create_index(
        "uq_pr_code_counters_namespace_global",
        "pr_code_counters",
        ["namespace"],
        unique=True,
        postgresql_where=sa.text("year IS NULL"),
    )


def downgrade() -> None:
    """Drop both tables and their indexes.

    Read the data-loss note in the module docstring first: this removes every
    review grant and every counter, and a counter that restarts at 1 will
    collide with codes already written on rows. Indexes are dropped explicitly
    before their table, matching how 0012-0015 reverse themselves. No
    ``DROP TYPE``: the enum column is ``VARCHAR`` and no PostgreSQL type was
    created.
    """
    op.drop_index("uq_pr_code_counters_namespace_global", table_name="pr_code_counters")
    op.drop_index("uq_pr_code_counters_namespace_year", table_name="pr_code_counters")
    op.drop_table("pr_code_counters")

    op.drop_index("ix_pr_user_capabilities_capability_to", table_name="pr_user_capabilities")
    op.drop_index("uq_pr_user_capabilities_open_grant", table_name="pr_user_capabilities")
    op.drop_index("ix_pr_user_capabilities_user_id", table_name="pr_user_capabilities")
    op.drop_table("pr_user_capabilities")
