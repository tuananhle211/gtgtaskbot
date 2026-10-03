"""Step 1F.2.7: approval grants get a scope, and stop depending on the role.

Six added columns on ``pr_user_capabilities``, two new association tables, one
dropped index, and **one deliberate data statement** over the rows that already
exist. 0012-0030 are untouched.

What an approval grant was
--------------------------

One row: *this person may decide at this gate, between these two dates*.
Authorization was a conjunction - the role had to carry the capability's
baseline permission **and** a grant had to exist - so a grant could only ever
narrow who could act, never widen it, and it said nothing about *which* content
it applied to.

Both halves are what this revision changes, and they change independently.

Scope: two axes, two association tables
---------------------------------------

``content_type_scope`` and ``channel_scope`` are each ``ALL`` or ``SELECTED``.
``SELECTED`` reads its values from ``pr_user_capability_content_types`` and
``pr_user_capability_channels`` - a row per value, with a unique index per
grant, and a real foreign key from the channel table into ``pr_channels``.

**Not a comma-separated column.** Both axes are joined against, counted and
written into the audit trail, and a string of ids supports none of that; the
channel axis additionally needs the foreign key, so that a channel somebody has
been given approval rights over cannot be deleted out from under the grant.

``ALL`` is a **mode**, not a materialised list of today's ids. A grant written
as "the eleven channels that exist right now" silently fails to cover the
twelfth, and the person who wrote it never finds out. It also covers the
*missing* case - an item with no classification, an item with no channel -
because "every channel including none" is the only reading of *all* without a
hole in it. ``include_unclassified_content`` and ``include_unassigned_channel``
give a ``SELECTED`` scope the same reach explicitly, and both default to
``false``: absent is not a wildcard, and a scope-restricted grant that silently
swallowed every historical unclassified row would be the largest quiet
widening this module could produce.

Server defaults are ``SELECTED`` / ``false`` - **fail closed**. A row inserted
without a scope covers nothing, which is the direction a bug should point.

``requires_role_baseline``: how the existing grants are preserved
-----------------------------------------------------------------

The column says whether a grant still needs the holder's role to carry the
capability's permission. Everything created from now on sets it ``false`` - an
explicit grant authorises on its own, which is the point of the step.

**The rows that already exist are set to ``true``**, by the ``UPDATE`` at the
end of :func:`upgrade`, and this is the security-relevant decision of the
revision. Those grants were given under the conjunctive rule, and their holders
were entitled *only because their role already carried the permission*. Reading
them as standalone would hand approval rights to anybody who holds a grant and
lacks the role - a real possibility, because ``PrCapabilityService.grant`` has
never checked the grantee's role - and nobody would have decided to do that.
Together with the ``ALL``/``ALL`` scope the same statement writes, the effect on
every pre-existing grant is: **exactly the authority it had yesterday, and not
one item more.**

The statement touches only rows that exist when the migration runs, sets
columns this migration just created, and is what "preserve existing grants"
means here. It is not a data repair: it writes no business data, corrects
nothing, and cannot run twice to different effect.

Revocation becomes immediate
----------------------------

``revoked_at`` is a timestamp and ``revoked_by_user_id`` names who set it. A
grant with ``revoked_at`` is inactive from that instant.

Before this, revoking meant setting ``effective_to`` to today - and the date
interval is **closed**, so the grant stayed usable for the rest of the day.
Whether that was ever exploited is unknowable and beside the point: "revoked"
has to mean revoked. Both columns are still written on a revocation;
``revoked_at`` is what authorization reads and ``effective_to`` is what a person
reading the history sees. No existing row is back-filled, because no existing
row is revoked in the new sense - a closed grant is closed by its date, which
still works.

The dropped index
-----------------

``uq_pr_user_capabilities_open_grant`` was ``UNIQUE (user_id, capability)`` over
open rows. It has to go: two scoped grants of the same capability to the same
person - *Facebook posts on the two Facebook channels*, and *short-video scripts
everywhere* - are two different rights, and calling them a conflict would force
one grant per combination, which is exactly what the multi-select exists to
avoid. What replaces it is a plain ``(user_id, capability)`` lookup index and an
application-level refusal of an **identical** scope.

Downgrade
---------

Drops the two tables, the six columns and the lookup index, and restores the
partial unique index. It works only if no person currently holds two open grants
of one capability - which is the state the restored index describes - so the
downgrade will fail loudly rather than silently discard a right. What is lost is
every scope: after a downgrade each surviving grant is once again "this person,
this gate", conjunctive with the role. That is a genuine loss of information and
is why the downgrade is a recovery path, not a routine one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS = "pr_user_capabilities"
GRANT_CONTENT_TYPES = "pr_user_capability_content_types"
GRANT_CHANNELS = "pr_user_capability_channels"
CHANNELS = "pr_channels"
USERS = "users"

RESTRICT = "RESTRICT"
CASCADE = "CASCADE"

OPEN_GRANT_INDEX = "uq_pr_user_capabilities_open_grant"
LOOKUP_INDEX = "ix_pr_user_capabilities_user_capability"

#: Literals rather than imports from ``meobot.domain.pr``: a migration has to
#: keep meaning what it meant on the day it ran. A unit test asserts both lists
#: still match their enums.
SCOPE_MODES = ("ALL", "SELECTED")

CONTENT_TYPES = (
    "ULTRA_SHORT_SCRIPT",
    "SHORT_VIDEO_SCRIPT",
    "FACEBOOK_POST",
    "LONG_YOUTUBE_SCRIPT",
    "PRESS_ARTICLE",
    "CORPORATE_TVC",
)

#: The added columns, in creation order. Named once so the downgrade cannot
#: drift from the upgrade.
ADDED_COLUMNS = (
    "content_type_scope",
    "include_unclassified_content",
    "channel_scope",
    "include_unassigned_channel",
    "requires_role_baseline",
    "revoked_at",
    "revoked_by_user_id",
)


def _scope_mode() -> sa.Enum:
    return sa.Enum(*SCOPE_MODES, name="pr_grant_scope_mode", native_enum=False, length=20)


def _content_type() -> sa.Enum:
    return sa.Enum(*CONTENT_TYPES, name="pr_content_type", native_enum=False, length=30)


def upgrade() -> None:
    # --- Scope, revocation and the role question, on the grant itself ------
    # Every default fails closed: SELECTED with nothing selected covers nothing,
    # and requires_role_baseline=false is the additive reading only *new* rows
    # get - the UPDATE below puts the existing ones back on the old rule.
    op.add_column(
        GRANTS,
        sa.Column(
            "content_type_scope",
            _scope_mode(),
            server_default="SELECTED",
            nullable=False,
        ),
    )
    op.add_column(
        GRANTS,
        sa.Column(
            "include_unclassified_content",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        GRANTS,
        sa.Column("channel_scope", _scope_mode(), server_default="SELECTED", nullable=False),
    )
    op.add_column(
        GRANTS,
        sa.Column(
            "include_unassigned_channel",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        GRANTS,
        sa.Column(
            "requires_role_baseline",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(GRANTS, sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(GRANTS, sa.Column("revoked_by_user_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_pr_user_capability_revoked_by",
        GRANTS,
        USERS,
        ["revoked_by_user_id"],
        ["id"],
        ondelete=RESTRICT,
    )

    # --- The two scope tables ---------------------------------------------
    op.create_table(
        GRANT_CONTENT_TYPES,
        sa.Column("capability_grant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_type", _content_type(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_user_capability_content_types"),
        # Short explicit names: the generated form runs past PostgreSQL's
        # 63-byte identifier limit, which is the defect 0021 exists to repair.
        sa.ForeignKeyConstraint(
            ["capability_grant_id"],
            [f"{GRANTS}.id"],
            name="fk_grant_content_type_grant",
            ondelete=CASCADE,
        ),
    )
    op.create_index(
        "ix_pr_user_capability_content_types_capability_grant_id",
        GRANT_CONTENT_TYPES,
        ["capability_grant_id"],
    )
    op.create_index(
        "uq_pr_user_capability_content_types_grant_type",
        GRANT_CONTENT_TYPES,
        ["capability_grant_id", "content_type"],
        unique=True,
    )

    op.create_table(
        GRANT_CHANNELS,
        sa.Column("capability_grant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pr_user_capability_channels"),
        sa.ForeignKeyConstraint(
            ["capability_grant_id"],
            [f"{GRANTS}.id"],
            name="fk_grant_channel_grant",
            ondelete=CASCADE,
        ),
        # RESTRICT towards the channel: a channel a grant names must not vanish
        # under it. CASCADE towards the grant: these rows are parts of it.
        sa.ForeignKeyConstraint(
            ["channel_id"],
            [f"{CHANNELS}.id"],
            name="fk_grant_channel_channel",
            ondelete=RESTRICT,
        ),
    )
    op.create_index(
        "ix_pr_user_capability_channels_capability_grant_id",
        GRANT_CHANNELS,
        ["capability_grant_id"],
    )
    op.create_index("ix_pr_user_capability_channels_channel_id", GRANT_CHANNELS, ["channel_id"])
    op.create_index(
        "uq_pr_user_capability_channels_grant_channel",
        GRANT_CHANNELS,
        ["capability_grant_id", "channel_id"],
        unique=True,
    )

    # --- One grant per (person, gate) is no longer true --------------------
    op.drop_index(OPEN_GRANT_INDEX, table_name=GRANTS)
    op.create_index(LOOKUP_INDEX, GRANTS, ["user_id", "capability"])

    # --- Existing grants keep exactly the authority they had ---------------
    # ALL/ALL scope, because an unscoped grant applied to everything; and the
    # conjunctive role rule, because that is what made its holder entitled. See
    # the module docstring: this preserves, it does not widen, and it is the
    # answer to "how are existing unscoped grants migrated".
    # Written out rather than interpolated: a migration's data statement is the
    # thing a reviewer greps for, and it should read as the SQL it is.
    op.execute(
        sa.text(
            """
            UPDATE pr_user_capabilities
               SET content_type_scope = 'ALL',
                   channel_scope = 'ALL',
                   include_unclassified_content = true,
                   include_unassigned_channel = true,
                   requires_role_baseline = true
            """
        )
    )


def downgrade() -> None:
    op.drop_index("uq_pr_user_capability_channels_grant_channel", table_name=GRANT_CHANNELS)
    op.drop_index("ix_pr_user_capability_channels_channel_id", table_name=GRANT_CHANNELS)
    op.drop_index("ix_pr_user_capability_channels_capability_grant_id", table_name=GRANT_CHANNELS)
    op.drop_table(GRANT_CHANNELS)

    op.drop_index("uq_pr_user_capability_content_types_grant_type", table_name=GRANT_CONTENT_TYPES)
    op.drop_index(
        "ix_pr_user_capability_content_types_capability_grant_id",
        table_name=GRANT_CONTENT_TYPES,
    )
    op.drop_table(GRANT_CONTENT_TYPES)

    op.drop_index(LOOKUP_INDEX, table_name=GRANTS)
    # Restored exactly as 0016 created it. Fails if two open grants of one
    # capability now exist for one person - loudly, which is the right outcome:
    # the pre-1F.2.7 schema genuinely cannot hold them.
    op.create_index(
        OPEN_GRANT_INDEX,
        GRANTS,
        ["user_id", "capability"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL"),
    )

    op.drop_constraint("fk_pr_user_capability_revoked_by", GRANTS, type_="foreignkey")
    for column in reversed(ADDED_COLUMNS):
        op.drop_column(GRANTS, column)
