"""Step 1F.2.4a: channel identity, and numbers a person can actually write down.

One new column on ``pr_channels``, fourteen on ``pr_channel_metric_snapshots``,
and **no new table**. 0020 through 0026 are untouched, and so is 0013 - which is
the revision that created the snapshot table in the first place.

Why there is no ``pr_channel_metrics`` table
---------------------------------------------

Because ``pr_channel_metric_snapshots`` already existed. Step 1B built it as
part of the reporting foundation: append-only, no ``updated_at``, unique on
``(channel_id, observed_at, source)`` so an API reading and a hand-typed one at
the same instant can both be kept and can visibly disagree, and
``PrMetricSource`` already spelling ``API``/``MANUAL``/``IMPORT``. Every rule
Step 1F.2.4a needed was already asserted by a migration test against a real
PostgreSQL. A second table would have been a second answer to "what were this
channel's numbers on the 20th".

So this revision widens that table rather than replacing it, and keeps its
vocabulary: the instant is ``observed_at``, not ``captured_at``; the free-form
half is ``extra_metrics``, not ``raw_metrics_json``.

The windowed columns
---------------------

0013's ``views``, ``reach``, ``impressions`` and ``engagements`` on this table
carry no window. At publication level that is fine - a post has a lifetime and
a snapshot reads its total. At *channel* level it is not: "views" for an account
is a flow, it means nothing without "over what period", and one reading has to
carry both the 7-day and the 30-day figure at once, which one column cannot do.

Hence ``views_7d``/``views_30d`` and their four siblings, plus ``likes_30d``,
``comments_30d`` and ``shares_30d`` where only the monthly figure is worth
typing, plus the two stock counts ``following`` and ``posts_count``. The old
columns are left alone and left unwritten - dropping columns from a foundation
table to tidy a naming decision is a destructive migration bought with nothing.

Every one of them is nullable, and that is the design rather than laxity. TikTok
does not report reach the way Facebook does, YouTube does not report either the
way Instagram does, and forcing four platforms into one non-null shape would
mean writing zeros where the honest value is "the platform does not say".
``NULL`` means *not recorded*; ``0`` means *zero*.

``recorded_by_user_id``
-----------------------

Nullable, ``RESTRICT``, pointing at ``users`` like every other person reference
in this module. Nullable because an ``API`` reading has no author and neither
does a row written before this revision; ``RESTRICT`` because deleting a person
who typed the numbers must not quietly remove who typed them.

``pr_channels.handle``
----------------------

Nullable text, plus a check that a handle is not whitespace. Nothing else on
``pr_channels`` changes, and specifically **no platform column is added**: the
canonical platform a screen shows is derived from the existing ``platform_id``
through ``pr_platforms.code``, which is already the token the Step 1F.1 policy
system matches on. Adding a platform enum beside that foreign key would be two
answers to one question, and no migration can decide which one is right when
they disagree.

No backfill, and no guessing
-----------------------------

Nothing is written to an existing row. Every channel that existed at 0026 is
byte for byte what it was, with ``handle`` null. In particular this revision
does **not** read ``url`` or ``name`` and conclude "this one is Facebook":
inferring a platform - which decides which policy pack an AI review is grounded
in - from a display string would manufacture authoritative data out of a guess.
Channels whose platform is outside the canonical six are shown as *Chưa xác
định* until somebody sets one, and go on working in the meantime.

Indexes
-------

None added. The lookup this step performs is *"the latest readings for this
channel"* - ``WHERE channel_id = ? ORDER BY observed_at DESC`` - and 0013's
unique B-tree on ``(channel_id, observed_at, source)`` already serves it through
its leftmost prefix. That is the same reasoning 0013 recorded when it declined a
separate ``(channel_id, observed_at)`` index, and a redundant one would cost a
write on every append and buy nothing. ``recorded_by_user_id`` gets no index
either: nothing asks "every reading this person entered".

Downgrade
---------

Drops the check constraint, the foreign key, the fourteen columns and the
handle, in that order. What is lost is the Step 1F.2.4a half of every snapshot -
the rows themselves survive with their 0013 columns - and every handle. Nothing
else in the schema references any of it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = "users"
CHANNELS = "pr_channels"
SNAPSHOTS = "pr_channel_metric_snapshots"

RESTRICT = "RESTRICT"

#: The **bare** check-constraint names, as handed to ``op.create_check_constraint``.
#: ``Base.metadata``'s naming convention expands each into ``ck_<table>_<name>``,
#: and :func:`downgrade` passes that expansion back through ``op.f()`` so the
#: convention does not apply itself a second time. Revision 0021 exists because
#: that asymmetry was got wrong once; see its module docstring.
HANDLE_CHECK = "handle_not_empty"
WINDOW_CHECK = "window_metrics_not_negative"
#: The foreign-key convention interpolates no constraint name, so this one is
#: written out in full and passed unchanged to both ``create`` and ``drop``.
RECORDER_FK = "fk_pr_channel_metric_snapshots_recorded_by_user_id_users"

#: The columns this revision adds to the snapshot table, in the order they are
#: created. Kept as one tuple so the non-negative constraint below is generated
#: from the same list the columns are, and a column added later without widening
#: the constraint fails the parity test rather than shipping unconstrained.
#:
#: Must equal ``CHANNEL_WINDOW_METRIC_COLUMNS`` in
#: ``meobot.db.models.pr_reporting``; ``tests/unit/test_pr_reporting_schema_parity``
#: asserts it.
CHANNEL_WINDOW_METRIC_COLUMNS: tuple[str, ...] = (
    "following",
    "posts_count",
    "views_7d",
    "views_30d",
    "reach_7d",
    "reach_30d",
    "impressions_7d",
    "impressions_30d",
    "engagements_7d",
    "engagements_30d",
    "likes_30d",
    "comments_30d",
    "shares_30d",
)


def _non_negative(columns: Sequence[str]) -> str:
    """*"each of these is absent, or is not a negative count"*, as one clause.

    Written the same way 0013 writes its own, so the two constraints on this
    table read identically in ``\\d`` output.
    """
    return " AND ".join(f"({column} IS NULL OR {column} >= 0)" for column in columns)


def upgrade() -> None:
    op.add_column(CHANNELS, sa.Column("handle", sa.String(length=200), nullable=True))
    op.create_check_constraint(
        HANDLE_CHECK, CHANNELS, "handle IS NULL OR length(trim(handle)) > 0"
    )

    for column in CHANNEL_WINDOW_METRIC_COLUMNS:
        op.add_column(SNAPSHOTS, sa.Column(column, sa.BigInteger(), nullable=True))
    op.add_column(SNAPSHOTS, sa.Column("recorded_by_user_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        RECORDER_FK,
        SNAPSHOTS,
        USERS,
        ["recorded_by_user_id"],
        ["id"],
        ondelete=RESTRICT,
    )
    op.create_check_constraint(
        WINDOW_CHECK, SNAPSHOTS, _non_negative(CHANNEL_WINDOW_METRIC_COLUMNS)
    )


def downgrade() -> None:
    """The exact inverse, by the names that actually exist."""
    op.drop_constraint(op.f(f"ck_{SNAPSHOTS}_{WINDOW_CHECK}"), SNAPSHOTS, type_="check")
    op.drop_constraint(RECORDER_FK, SNAPSHOTS, type_="foreignkey")
    op.drop_column(SNAPSHOTS, "recorded_by_user_id")
    for column in reversed(CHANNEL_WINDOW_METRIC_COLUMNS):
        op.drop_column(SNAPSHOTS, column)

    op.drop_constraint(op.f(f"ck_{CHANNELS}_{HANDLE_CHECK}"), CHANNELS, type_="check")
    op.drop_column(CHANNELS, "handle")
