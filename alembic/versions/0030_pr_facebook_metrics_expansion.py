"""Step 1F.2.4d: the six numbers a Facebook Page can actually answer for.

Six columns on ``pr_channel_metric_snapshots``, one check constraint, and
**nothing else**. No new table, no index, no backfill, no data movement.

Why six columns and not one JSON blob
--------------------------------------

``extra_metrics`` already exists and already holds ``facebook_page_fan_count``.
It would have been less work to leave the expansion there and read it from the
panel. The rule this module has followed since 0027 is the reason it is not:
*a metric worth a card gets a column.* A figure inside ``extra_metrics`` cannot
be filtered on, cannot be constrained non-negative, cannot be compared across
rows in SQL, and has no name the schema will hold anybody to - which is exactly
what a JSON bag is good at and exactly wrong for the six numbers a manager
reads first every Monday.

The six
-------

``fans``
    Page likes. **Not** a second copy of ``followers``. Meta split the two and
    they have diverged permanently: a fan liked the Page, a follower receives
    its posts. Reports quote both, so both are stored, and neither is computed
    from the other.

``posts_count_7d`` / ``posts_count_30d``
    Posts published inside the window. 0027's ``posts_count`` is a *lifetime*
    total and cannot be the denominator of a 30-day average - dividing a
    monthly engagement figure by every post an account ever made produces a
    number that is wrong by orders of magnitude and entirely plausible on a
    card, which is the failure mode 0027's own docstring warned about.

``reactions_30d``
    Reactions on the window's posts, kept apart from 0027's ``likes_30d``. A
    Facebook reaction is a like, a love, a haha, a wow, a sad or an angry, and
    a total of all six labelled "Likes" would be a number saying something it
    is not. A platform that really does report plain likes still writes
    ``likes_30d``; nothing has to choose.

``video_views_7d`` / ``video_views_30d``
    Plays of video and Reels. Deliberately **not** 0027's ``views_7d`` /
    ``views_30d``: on a Meta account the account-level view metric counts
    *profile* views, and merging the two would make a Facebook card and a
    YouTube card in the same list mean different things under one heading.

What this revision does **not** add
------------------------------------

**No reach and no impressions.** Graph v23 retired ``page_impressions`` and
``page_impressions_unique`` from Page Insights, there is no semantically
equivalent replacement, and 0027's four columns for them stay ``NULL`` for a
Facebook Page. Adding a column here that summed daily uniques into a "reach"
figure would count one person on three days as three people. The honest answer
is a blank card, and the blank card is what the panel shows.

**No ``top_post_30d`` column.** The best-performing post is an object - an id, a
permalink, a time, three counts and an excerpt - not a count, and a foreign key
to a post MeoBot does not store rows for would be a reference to nothing. It
lives in ``extra_metrics`` under ``facebook_top_post_30d``, which is what that
column is for: platform-specific values with no canonical shape.

Nullable, all six
------------------

For the reason 0027 wrote down and this step re-proves on a second platform:
``NULL`` means *the platform does not report this, or nobody looked*, and ``0``
means the number is zero. A Page with no video posts last month has
``video_views_30d = 0``; a Page whose Graph version no longer serves
``page_video_views`` has ``NULL``. A migration that defaulted these to zero
would erase that difference on every row it touched.

Indexes
-------

None. The lookups are still *"the latest readings for this channel"* and
*"this channel's readings since a date"*, both served by 0013's unique B-tree on
``(channel_id, observed_at, source)`` through its leftmost prefix. Six more
columns change nothing about which rows are read.

Downgrade
----------

Drops the constraint and the six columns. What is lost is the Step 1F.2.4d half
of every snapshot; the rows themselves and every column 0013 and 0027 created
survive untouched. Nothing in the schema references any of it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAPSHOTS = "pr_channel_metric_snapshots"

#: The **bare** check-constraint name, as handed to ``op.create_check_constraint``.
#: ``Base.metadata``'s naming convention expands it into ``ck_<table>_<name>``,
#: and :func:`downgrade` passes that expansion back through ``op.f()`` so the
#: convention does not apply itself twice. Revision 0021 exists because that
#: asymmetry was got wrong once.
EXPANSION_CHECK = "expansion_metrics_not_negative"

#: The columns this revision adds, in the order they are created.
#:
#: Must equal ``CHANNEL_EXPANSION_METRIC_COLUMNS`` in
#: ``meobot.db.models.pr_reporting``; ``tests/unit/test_pr_reporting_schema_parity``
#: asserts it, so a column added to the model without widening this tuple fails
#: a test rather than shipping unconstrained on a real database.
CHANNEL_EXPANSION_METRIC_COLUMNS: tuple[str, ...] = (
    "fans",
    "posts_count_7d",
    "posts_count_30d",
    "reactions_30d",
    "video_views_7d",
    "video_views_30d",
)


def _non_negative(columns: Sequence[str]) -> str:
    """*"each of these is absent, or is not a negative count"*, as one clause.

    Written the way 0013 and 0027 write theirs, so all three constraints on this
    table read identically in ``\\d`` output.
    """
    return " AND ".join(f"({column} IS NULL OR {column} >= 0)" for column in columns)


def upgrade() -> None:
    for column in CHANNEL_EXPANSION_METRIC_COLUMNS:
        op.add_column(SNAPSHOTS, sa.Column(column, sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        EXPANSION_CHECK, SNAPSHOTS, _non_negative(CHANNEL_EXPANSION_METRIC_COLUMNS)
    )


def downgrade() -> None:
    """The exact inverse, by the name that actually exists."""
    op.drop_constraint(op.f(f"ck_{SNAPSHOTS}_{EXPANSION_CHECK}"), SNAPSHOTS, type_="check")
    for column in reversed(CHANNEL_EXPANSION_METRIC_COLUMNS):
        op.drop_column(SNAPSHOTS, column)
