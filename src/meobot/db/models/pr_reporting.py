"""PR reporting: periods, publications, metrics, issues, actions and report runs.

Step 1B of the PR module. Nine tables that let MeoBot build a periodic report
**from PostgreSQL**, and deliberately nothing that builds one - no query, no KPI
formula, no workbook, no delivery. What these tables encode:

* **The database is the source of truth.** A published report is a snapshot
  taken from these rows at a stated ``source_cutoff_at``, not a document that
  numbers are typed into. Re-running a report for the same period against the
  same cutoff has to be able to produce the same numbers, which is why the
  metric tables are append-only and the weekly manual input is versioned.
* **An observation is not a fact about now.** ``pr_post_metric_snapshots`` and
  ``pr_channel_metric_snapshots`` each record *one reading at one instant from
  one source*. They are never updated, so a view count that was 1,000 on Monday
  stays 1,000 on Monday however large it grows afterwards. ``source`` is part of
  the uniqueness key because an API number and a hand-typed number for the same
  instant can disagree, and both being stored is what makes the disagreement
  visible rather than a silent overwrite.
* **Views and reach are different numbers** and have separate columns on both
  snapshot tables. One counts plays, the other counts people. Adding them, or
  storing whichever the platform happened to expose, produces a report that
  cannot be reconciled against the platform's own dashboard.
* **A correction is a new version, not an edit.** ``pr_weekly_manual_inputs``
  is keyed on ``(channel_id, period_id, version_no)``. An approved or locked
  version must never be updated in place - a report that quoted version 2 has
  to keep meaning what it meant. Refusing the in-place update is a Step 1C
  service rule; the database enforces the uniqueness that makes versions
  possible.

Every foreign key here is ``RESTRICT``. Deleting a channel that has been
measured, a period that has been reported, a publication that has metrics, a
report run that produced a file, or a user who entered a week's numbers fails at
the database rather than quietly removing the history that hangs off it.

Nothing in this module interprets a status. Which report-run state may follow
which, whether a ``LOCKED`` period still accepts input, and whether a weekly
row's period is actually a week are all Step 1C service rules; see
``docs/pr/STEP_1B_REPORTING_DATA_FOUNDATION.md``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meobot.db.base import Base, JSONColumn, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.db.models.pr import RESTRICT, USERS_TABLE, _not_empty
from meobot.domain.pr.reporting import (
    PrActionStatus,
    PrArtifactFormat,
    PrIssueSeverity,
    PrIssueStatus,
    PrMetricSource,
    PrPeriodStatus,
    PrPeriodType,
    PrPublicationStatus,
    PrReportRunStatus,
    PrReportTriggerType,
    PrReportType,
    PrWeeklyInputStatus,
)

#: What makes a publication identifiable on its platform: the platform gave it
#: an id. The partial unique index below reads only those rows, so the many
#: publications nobody has looked up yet do not collide with each other on a
#: shared ``NULL``. Written once and used by the model's index and by migration
#: 0013, and spelled in SQL that PostgreSQL and SQLite both accept because the
#: offline suite builds this schema from the models.
KNOWN_PLATFORM_POST = text("platform_post_id IS NOT NULL")

#: What makes a channel snapshot **deduplicable**: a connector computed a
#: fingerprint for it. Manual readings have none - a person typing numbers is
#: not a repeatable fetch - so the partial unique index below reads only the
#: automatic rows and the many manual ones do not collide on a shared ``NULL``.
#: Step 1F.2.4b; spelled in SQL both PostgreSQL and SQLite accept, because the
#: offline suite builds this schema from the models.
KNOWN_READING_KEY = text("provider_reading_key IS NOT NULL")

#: Every metric column on ``pr_post_metric_snapshots``. Named here so the
#: non-negative constraint is generated from one list rather than eleven
#: hand-written clauses that can fall out of step with the columns.
POST_METRIC_COLUMNS: tuple[str, ...] = (
    "views",
    "reach",
    "impressions",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "watch_time_seconds",
    "average_view_duration_seconds",
    "followers_gained",
)

#: Every metric column migration 0013 put on ``pr_channel_metric_snapshots``.
#:
#: Frozen at what 0013 created, because a parity test compares this tuple with
#: the one in that revision and the constraint it generates is that revision's.
#: Step 1F.2.4a's columns are in :data:`CHANNEL_WINDOW_METRIC_COLUMNS` and carry
#: their own constraint, added by 0027.
CHANNEL_METRIC_COLUMNS: tuple[str, ...] = (
    "followers",
    "members",
    "views",
    "reach",
    "impressions",
    "engagements",
    "messages",
)

#: Every metric column migration 0027 added to ``pr_channel_metric_snapshots``.
#:
#: Step 1F.2.4a. Two kinds of number, and the names say which: ``following`` and
#: ``posts_count`` are point-in-time counts like ``followers``, and everything
#: after them carries the window it was measured over. That suffix is the whole
#: reason these are new columns rather than reuses of 0013's ``views`` and
#: ``reach``: a channel-level "views" with no window attached cannot be
#: reconciled against anything, and one row has to hold both the 7-day and the
#: 30-day figure at once, which a single windowless column cannot do.
#:
#: 0013's windowless columns are left in place and left **unwritten** by this
#: step - see :class:`PrChannelMetricSnapshot`.
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

#: Every metric column migration 0030 added to ``pr_channel_metric_snapshots``.
#:
#: Step 1F.2.4d, the Facebook metrics expansion. Its own tuple and its own check
#: constraint for the same reason 0027's columns did not join 0013's: a parity
#: test holds each revision to the list it was written from, and widening a
#: frozen tuple would silently leave a real database's constraint behind.
#:
#: What the six are for:
#:
#: * ``fans`` - Page likes. A *different number* from followers on every Meta
#:   Page since Meta split them, and stored beside rather than instead;
#: * ``posts_count_7d`` / ``posts_count_30d`` - posts published **in the
#:   window**, which is the denominator "trung bình mỗi bài" needs. The
#:   windowless ``posts_count`` above is a lifetime total and cannot be divided
#:   into a 30-day engagement figure;
#: * ``reactions_30d`` - reactions on the window's posts. Not ``likes_30d``: a
#:   Facebook reaction is a like, a love, a haha or an angry, and the total is
#:   not a like count;
#: * ``video_views_7d`` / ``video_views_30d`` - plays of video and Reels.
#:   Deliberately not the ``views_*`` columns, because a Meta account-level view
#:   is a *profile* view and putting the two in one column would make a Page's
#:   card mean something different from a YouTube channel's.
CHANNEL_EXPANSION_METRIC_COLUMNS: tuple[str, ...] = (
    "fans",
    "posts_count_7d",
    "posts_count_30d",
    "reactions_30d",
    "video_views_7d",
    "video_views_30d",
)

#: Every counted or costed column on ``pr_weekly_manual_inputs`` that cannot
#: sensibly be negative. Each of these counts a thing that exists - posts,
#: views, leads, dong, members at the close of the week - and a negative one is
#: a typo rather than a fact.
WEEKLY_INPUT_VALUE_COLUMNS: tuple[str, ...] = (
    "planned_posts",
    "manual_posts",
    "manual_views",
    "manual_reach",
    "manual_engagements",
    "leads",
    "bookings",
    "cost_vnd",
    "members_end",
)

#: Columns on ``pr_weekly_manual_inputs`` that are **signed** and therefore
#: excluded from the non-negative constraint above. Named rather than merely
#: absent, so that leaving a column out reads as a decision and a new column
#: cannot escape the constraint by being forgotten -
#: ``test_every_numeric_weekly_column_is_constrained_or_declared_signed``
#: requires every numeric column to appear in one tuple or the other.
WEEKLY_INPUT_SIGNED_COLUMNS: tuple[str, ...] = ("members_gain",)


def _non_negative(columns: tuple[str, ...]) -> str:
    """SQL for "every one of these columns is either absent or not negative".

    One constraint per table rather than one per column: PostgreSQL truncates
    identifiers at 63 characters, and
    ``ck_pr_post_metric_snapshots_average_view_duration_seconds_not_negative``
    is 71. A single named constraint per table also keeps the migration
    readable. The cost is that a violation names the table's constraint rather
    than the offending column, which is why the column list lives in a named
    tuple that the error message can be read against.
    """
    return " AND ".join(f"({column} IS NULL OR {column} >= 0)" for column in columns)


# ---------------------------------------------------------------------------
# When a report is about
# ---------------------------------------------------------------------------


class PrReportingPeriod(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One reportable week or month - ``2026-W32``, ``2026-08``.

    A table rather than a pair of dates on each report, because a period is a
    thing the team refers to ("we agreed the W32 numbers") and because a report
    comparing this week with last week needs a way to say which week last week
    was. That is ``previous_period_id``: an explicit link rather than a
    computed one, so a period following a public holiday, a reporting gap or a
    calendar change still knows what it is being compared against.

    ``status`` records how far the period has been put beyond change and is
    **not enforced by the database**. Writing a row against a ``LOCKED`` period
    is accepted here; refusing it is a Step 1C service rule.

    Periods are not generated. Step 1B has no calendar, no counter and no job
    that creates next week's row - a caller supplies the code and the dates.
    """

    __tablename__ = "pr_reporting_periods"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint("date_end >= date_start", name="dates_ordered"),
        # A period cannot follow itself. Cheap to state, and the one cycle a
        # single row can create - longer cycles are a Step 1C service rule,
        # because no CHECK constraint can see another row.
        CheckConstraint(
            "previous_period_id IS NULL OR previous_period_id <> id",
            name="previous_is_not_self",
        ),
        Index("ix_pr_reporting_periods_type_start", "period_type", "date_start"),
    )

    #: ``2026-W32``, ``2026-08``. What a person says and a report prints. Not a
    #: key: every relationship in this module is a UUID.
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    period_type: Mapped[PrPeriodType] = mapped_column(
        value_enum(PrPeriodType, name="pr_period_type", length=20), nullable=False
    )
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    #: The period this one is compared against. Nullable - the first period the
    #: team ever reported has nothing behind it.
    previous_period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete=RESTRICT), nullable=True, index=True
    )
    status: Mapped[PrPeriodStatus] = mapped_column(
        value_enum(PrPeriodStatus, name="pr_period_status", length=20),
        nullable=False,
        default=PrPeriodStatus.OPEN,
        server_default=PrPeriodStatus.OPEN.value,
        index=True,
    )
    #: When the numbers were agreed, and when the period was put beyond
    #: correction. Both written by whoever decided; nothing derives either from
    #: ``status``, for the same reason a task's ``completed_at`` is not derived
    #: from its status - a reopened period would lose the original time.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# What was actually published
# ---------------------------------------------------------------------------


class PrPublication(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One actual publication occurrence: this content, on this channel, then.

    Distinct from :class:`~meobot.db.models.pr.PrContentTarget`, which is the
    *plan*. A target says "this goes to TikTok"; a publication says "it went to
    TikTok at 19:04 on the 3rd, and here is the platform's id for it". One
    content item has many publications, and the same content published twice to
    one channel - a repost, a corrected re-upload, a scheduled repeat - is two
    rows, because each has its own metrics.

    That is why the uniqueness rule is on ``(channel_id, platform_post_id)``
    and not on ``(content_id, channel_id)``: what may only exist once is one
    *post on a platform*, not one appearance of an idea. The index is partial
    and covers only rows where the platform id is known, so the publications
    nobody has looked up yet do not collide with each other on a shared
    ``NULL``.

    ``url`` is stored and never joined on, for the same reason
    ``pr_channels.url`` is not a key: it changes when a handle is renamed, when
    a platform migrates a domain, and when somebody pastes a link with tracking
    parameters. ``platform_post_id`` is the identifier a collector matches on.

    Which file went out - Step 1F.2.3f
    -----------------------------------

    A content item is a reusable asset with several produced outputs: the
    60-second master, a 25-second cutdown, a captioned variant. "This content was
    on TikTok in October" was therefore an incomplete record - the useful
    question is *which cut* was on TikTok, and before this step the row could not
    answer it.

    So exactly one of :attr:`production_submission_id` and
    :attr:`derivative_id` names the output. **Exactly one, for new rows**, and
    the enforcement is split deliberately between two layers:

    * the database refuses **both** - ``publication_output_not_both`` - because
      a row naming two different files is meaningless under any reading, past or
      future;
    * the application refuses **neither**, in
      :meth:`~meobot.application.pr_publication_service.PrPublicationService.register_publication`.

    The asymmetry is the whole of the legacy story. Rows written before this
    revision reference no output and there is no honest way to give them one:
    inventing a submission or a derivative to satisfy a ``CHECK`` would be
    fabricating production history, which is a far worse outcome than a nullable
    pair. A strict XOR constraint would have required exactly that fabrication,
    so it is not here - see ``0025`` and
    ``docs/pr/STEP_1F23F_DERIVATIVES_AND_PUBLICATIONS.md``.
    """

    __tablename__ = "pr_publications"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        # Step 1F.2.3f. Never two outputs. Legacy rows carry neither and are
        # untouched by this; the "exactly one" half is the service's, above.
        CheckConstraint(
            "NOT (production_submission_id IS NOT NULL AND derivative_id IS NOT NULL)",
            name="output_not_both",
        ),
        Index(
            "uq_pr_publications_channel_platform_post",
            "channel_id",
            "platform_post_id",
            unique=True,
            postgresql_where=KNOWN_PLATFORM_POST,
            sqlite_where=KNOWN_PLATFORM_POST,
        ),
        # "What went out on this channel, in order" - the query a channel
        # report and a weekly review both run.
        Index("ix_pr_publications_channel_published", "channel_id", "published_at"),
        # Step 1F.2.3f. "The publication history of this piece, newest first" -
        # what the detail page asks on every open. ``content_id`` alone was
        # already indexed and would serve the lookup; the second column is the
        # sort key, so the ordering comes off the index rather than out of a
        # sort now that this list is drawn rather than merely counted.
        Index("ix_pr_publications_content_published", "content_id", "published_at"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False
    )
    #: The platform's own id for this post. Nullable: somebody records that a
    #: piece went out long before anybody fetches the id back.
    platform_post_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: For a person to click. Never a key - see the class docstring.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Step 1F.2.3f. The original production submission that was published, when
    #: it was the master that went out. Mutually exclusive with
    #: :attr:`derivative_id`; ``NULL`` on both means a row that predates the
    #: revision - see the class docstring.
    production_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_production_submissions.id", ondelete=RESTRICT), nullable=True
    )
    #: Step 1F.2.3f. The derivative that was published, when a re-cut went out
    #: instead of the master. ``RESTRICT`` is what stops a derivative being
    #: deleted out from under the record of where it was posted.
    derivative_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_derivatives.id", ondelete=RESTRICT), nullable=True
    )
    #: When it actually went out, which is not when the row was written.
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Step 1F.2.3f. *"đăng lại dịp khai trương"*. Optional, plain text, and
    #: never rendered as markup - the same field a derivative and a resource
    #: carry, for the same reason: the row is a record of something somebody did
    #: and there is always a circumstance worth writing down.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Who pressed publish, when anybody knows. Nullable: an imported back
    #: catalogue has no publisher, and guessing one would be a lie as data.
    publisher_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    status: Mapped[PrPublicationStatus] = mapped_column(
        value_enum(PrPublicationStatus, name="pr_publication_status", length=20),
        nullable=False,
        default=PrPublicationStatus.PUBLISHED,
        server_default=PrPublicationStatus.PUBLISHED.value,
        index=True,
    )


# ---------------------------------------------------------------------------
# What the numbers were, at a stated moment
# ---------------------------------------------------------------------------


class PrPostMetricSnapshot(Base, UUIDPrimaryKeyMixin):
    """One reading of one publication's numbers at one instant. Append-only.

    Never updated, and no ``updated_at``. A snapshot is a claim about a moment:
    "at 09:00 on the 5th, the API said 12,431 views". Editing it would destroy
    the only thing it is good for, which is that a report generated last week
    and the same report regenerated today read the same numbers.

    ``views`` and ``reach`` are separate columns and mean different things -
    plays versus people. ``impressions`` is a third. A platform that exposes
    only one of them leaves the others null, which is honest; copying whichever
    number exists into all three would produce a report nobody can reconcile.

    ``source`` is part of the uniqueness key, so the same publication at the
    same instant may carry an ``API`` reading and a ``MANUAL`` one. When they
    disagree, both are stored and the disagreement is visible. Which one a
    report prefers is a Step 1C rule.

    ``extra_metrics`` is JSONB for the platform-specific numbers that do not
    deserve a column - a per-platform completion rate, a sticker tap count.
    Nothing indexes or parses it; a metric worth reporting on gets a column.
    """

    __tablename__ = "pr_post_metric_snapshots"
    __table_args__ = (
        CheckConstraint(_non_negative(POST_METRIC_COLUMNS), name="metrics_not_negative"),
        Index(
            "uq_pr_post_metric_snapshots_publication_observed_source",
            "publication_id",
            "observed_at",
            "source",
            unique=True,
        ),
        # No separate (publication_id, observed_at) index. The unique index
        # above is a B-tree, so a query filtering on publication_id - with or
        # without an observed_at range - already uses it through its leftmost
        # prefix. A duplicate would cost a second write on every append to a
        # table that only ever appends, and buy nothing.
    )

    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_publications.id", ondelete=RESTRICT), nullable=False
    )
    #: The instant this reading describes, which is not when the row was
    #: written: a backfilled import is created today and observed in March.
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[PrMetricSource] = mapped_column(
        value_enum(PrMetricSource, name="pr_metric_source", length=20), nullable=False, index=True
    )

    #: Plays. Not the same number as ``reach``.
    views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: People who saw it. Not the same number as ``views``.
    reach: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Times it was rendered, which may exceed both of the above.
    impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    likes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comments: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    saves: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    clicks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    watch_time_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Seconds, to the millisecond. ``NUMERIC`` rather than an integer because
    #: an average of whole seconds is not a whole number, and rounding it here
    #: would make a per-channel average of averages drift.
    average_view_duration_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    followers_gained: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Platform-specific numbers with no column of their own. Never parsed.
    extra_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrChannelMetricSnapshot(Base, UUIDPrimaryKeyMixin):
    """One reading of one channel's numbers at one instant. Append-only.

    The channel-level counterpart of :class:`PrPostMetricSnapshot`, and separate
    from it because a channel's follower count is not the sum of anything its
    posts did. Same rules: never updated, no ``updated_at``, ``source`` in the
    uniqueness key, ``views`` and ``reach`` distinct.

    ``followers`` and ``members`` are both here because platforms mean different
    things by them - a page has followers, a group has members, and a channel
    that has both should not have to pick.

    Step 1F.2.4a and the windowless columns
    ----------------------------------------

    Step 1F.2.4a made this table the one a person actually writes to, through
    :class:`~meobot.application.pr_channel_metrics_service.PrChannelMetricsService`,
    and added the columns in :data:`CHANNEL_WINDOW_METRIC_COLUMNS` plus
    ``recorded_by_user_id``.

    It writes **none** of 0013's ``views``, ``reach``, ``impressions``,
    ``engagements`` or ``messages``, and the manual form does not offer them.
    Those are channel-level flow counts with no window attached, and a flow with
    no window is not a measurement - which is precisely why the new columns
    carry ``_7d`` and ``_30d`` in their names. The old columns are kept because
    dropping columns from a Step 1B foundation table to tidy a naming decision
    would be a destructive migration bought with nothing, and because a
    period-scoped report may yet find them useful. Anything reading this table
    should read the suffixed columns.
    """

    __tablename__ = "pr_channel_metric_snapshots"
    __table_args__ = (
        CheckConstraint(_non_negative(CHANNEL_METRIC_COLUMNS), name="metrics_not_negative"),
        # Step 1F.2.4a's columns, in their own constraint rather than a widened
        # version of the one above: that one is migration 0013's and a parity
        # test holds the model and that revision to the same column list.
        CheckConstraint(
            _non_negative(CHANNEL_WINDOW_METRIC_COLUMNS), name="window_metrics_not_negative"
        ),
        # Step 1F.2.4d's columns, in a third constraint rather than a widened
        # second one. Same reasoning again: 0027's constraint belongs to 0027.
        CheckConstraint(
            _non_negative(CHANNEL_EXPANSION_METRIC_COLUMNS), name="expansion_metrics_not_negative"
        ),
        Index(
            "uq_pr_channel_metric_snapshots_channel_observed_source",
            "channel_id",
            "observed_at",
            "source",
            unique=True,
        ),
        # No separate (channel_id, observed_at) index, for the reason given on
        # :class:`PrPostMetricSnapshot`: the unique B-tree's leftmost prefix
        # already serves that lookup.
        #
        # Step 1F.2.4b. One provider reading, one row. Partial, over the rows
        # that have a key at all - manual entries carry none and must not
        # collide with each other on a shared ``NULL``, which is the same reason
        # ``KNOWN_PLATFORM_POST`` above is a partial index rather than a plain
        # unique constraint.
        Index(
            "uq_pr_channel_metric_snapshots_reading_key",
            "channel_id",
            "provider_reading_key",
            unique=True,
            postgresql_where=KNOWN_READING_KEY,
            sqlite_where=KNOWN_READING_KEY,
        ),
    )

    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[PrMetricSource] = mapped_column(
        value_enum(PrMetricSource, name="pr_metric_source", length=20), nullable=False, index=True
    )

    followers: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    members: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Plays. Not the same number as ``reach``.
    views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: People. Not the same number as ``views``.
    reach: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    engagements: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Inbound messages, for the channels where a conversation is the point.
    messages: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- Step 1F.2.4a -----------------------------------------------------
    #: Accounts this one follows. A vanity number on a brand page and a real one
    #: on a seeding account.
    following: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: How many posts the account has published in total, as the platform counts
    #: them. Named ``posts_count`` rather than ``posts`` so it cannot be read as
    #: a collection.
    posts_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    views_7d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    views_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: People, over the window. Still not the same number as views.
    reach_7d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reach_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    impressions_7d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    impressions_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    engagements_7d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    engagements_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    likes_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comments_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shares_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Step 1F.2.4a. Who typed this in, for the ``MANUAL`` rows that have an
    #: answer. Null for a reading no person entered - an ``API`` row, or a
    #: pre-1F.2.4a import - which is why it is nullable rather than defaulted to
    #: whoever happened to be running the job.
    #:
    #: Not derived from the audit trail: "ai nhập chỉ số này" is a question the
    #: history table itself has to answer, in the same query that reads the row,
    #: and an audit log is searched by entity rather than joined to.
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )

    # --- Step 1F.2.4d -----------------------------------------------------
    #: People who liked the account. On a Facebook Page this is ``fan_count``,
    #: which stopped being the same number as ``followers_count`` when Meta
    #: split the two - a fan liked the Page, a follower receives its posts.
    #: Both are stored; neither is derived from the other; platforms without the
    #: distinction leave this ``None``.
    fans: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Posts published **inside** the 7- and 30-day windows, as opposed to
    #: ``posts_count`` above, which is everything the account has ever posted.
    #: This is the one an average-per-post figure may be divided by.
    posts_count_7d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    posts_count_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Reactions on the window's posts. Not ``likes_30d`` - see the tuple above.
    reactions_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Plays of video and Reels over the window. Never written from an
    #: account-level "views" metric: on Meta that is a profile view.
    video_views_7d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_views_30d: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: Step 1F.2.4b. A stable key for *this reading of this account over this
    #: period*, from
    #: :func:`~meobot.domain.pr.channel_connections.reading_fingerprint`.
    #:
    #: The idempotency device for automatic sync, and null for everything else:
    #: a person typing numbers in is not a repeatable fetch and has nothing to
    #: deduplicate against. The partial unique index below is what actually
    #: refuses the duplicate - a scheduler retry, two overlapping sweeps, or a
    #: manager pressing "Đồng bộ ngay" twice - so a time series cannot collect
    #: three identical rows for one provider reading.
    provider_reading_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    extra_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# What a person typed in, and which version of it
# ---------------------------------------------------------------------------


class PrWeeklyManualInput(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One version of one channel's manually entered numbers for one period.

    Versioned rather than editable. ``(channel_id, period_id, version_no)`` is
    unique, so a correction is a new row at a higher version and the row a
    report quoted keeps saying what it said. **An approved or locked version
    must never be updated in place** - that is a Step 1C service rule, and the
    database provides the versioning that makes obeying it possible rather than
    the refusal itself.

    ``manual_*`` columns are named for what they are: numbers a person typed,
    which may disagree with the collected snapshots. They are stored separately
    from :class:`PrChannelMetricSnapshot` precisely so the disagreement is
    visible - ``variance_reason`` is where somebody explains it.

    ``period_id`` may point at a monthly period as far as the database is
    concerned. Requiring ``period_type = 'WEEK'`` needs a lookup into another
    row, which no ``CHECK`` constraint can do; Step 1C services must validate
    it. ``approved_at`` and ``approved_by_user_id`` are the one pair the
    database does keep consistent: an approval with no approver, or an approver
    with no time, is refused.

    Every counted and costed column is constrained non-negative **except
    ``members_gain``**, which is a signed period delta: a group that lost
    members over the week has a negative gain, and refusing to store that would
    make a real week unrecordable.
    """

    __tablename__ = "pr_weekly_manual_inputs"
    __table_args__ = (
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(_non_negative(WEEKLY_INPUT_VALUE_COLUMNS), name="values_not_negative"),
        # An approval is a person and a time, or it is neither. Half of one is
        # a row nobody can act on: "approved, by nobody" and "approved by Anh,
        # at no point" are both unusable in a report that has to say who signed
        # a number off.
        CheckConstraint(
            "(approved_at IS NULL AND approved_by_user_id IS NULL)"
            " OR (approved_at IS NOT NULL AND approved_by_user_id IS NOT NULL)",
            name="approval_pair_consistent",
        ),
        Index(
            "uq_pr_weekly_manual_inputs_channel_period_version",
            "channel_id",
            "period_id",
            "version_no",
            unique=True,
        ),
    )

    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: 1, then 2, then 3. A correction is a new version, never an edit.
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PrWeeklyInputStatus] = mapped_column(
        value_enum(PrWeeklyInputStatus, name="pr_weekly_input_status", length=20),
        nullable=False,
        default=PrWeeklyInputStatus.DRAFT,
        server_default=PrWeeklyInputStatus.DRAFT.value,
    )

    planned_posts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_posts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    manual_reach: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    manual_engagements: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    leads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bookings: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Vietnamese dong, whole units. ``NUMERIC(18, 0)`` rather than a float,
    #: because money that rounds is money that stops reconciling.
    cost_vnd: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    #: The membership count at the close of the period. A count, so never
    #: negative.
    members_end: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: The change in membership over the period, and **signed**: a group that
    #: lost members has a negative gain, and that is a fact the team needs
    #: recorded rather than refused. This is the one numeric column on this
    #: table outside ``ck_pr_weekly_manual_inputs_values_not_negative``.
    members_gain: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Why the typed numbers differ from the collected ones. Prose for a human.
    variance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    entered_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# What went wrong, and what is being done about it
# ---------------------------------------------------------------------------


class PrIssue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One PR management issue - something a report has to explain.

    ``period_id``, ``channel_id`` and ``content_id`` are all nullable and all
    independent. An issue may be about a channel in a week, about a single
    piece, or about none of them: "we still have no analytics access on
    TikTok" belongs to no period and no post, and forcing it into one would
    file it where nobody looks.

    ``severity`` is a judgement recorded at the time, not a number derived from
    a threshold - what counts as ``CRITICAL`` changes with the quarter, and a
    computed value would rewrite history whenever the threshold moved.

    ``needs_management_decision`` defaults to ``false``: an issue is the team's
    to solve until somebody says otherwise. It is the flag a weekly report reads
    to build its "for your decision" section.

    A new issue opens at ``MEDIUM``/``OPEN``. Neither is a claim about the
    issue - they are the values that let a person record one in a hurry and
    triage it afterwards, which is the state most issues are created in.
    """

    __tablename__ = "pr_issues"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("title"), name="title_not_empty"),
        Index("ix_pr_issues_owner_status", "owner_user_id", "status"),
        Index("ix_pr_issues_severity_status", "severity", "status"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete=RESTRICT), nullable=True, index=True
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=True, index=True
    )
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: A free-text bucket - "ANALYTICS", "STAFFING". Not an enum: the list is
    #: still being discovered, and a wrong enum costs a migration while a wrong
    #: string costs an ``UPDATE``.
    issue_group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity: Mapped[PrIssueSeverity] = mapped_column(
        value_enum(PrIssueSeverity, name="pr_issue_severity", length=20),
        nullable=False,
        default=PrIssueSeverity.MEDIUM,
        server_default=PrIssueSeverity.MEDIUM.value,
    )
    #: What it costs. Prose; nothing computes from it.
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PrIssueStatus] = mapped_column(
        value_enum(PrIssueStatus, name="pr_issue_status", length=20),
        nullable=False,
        default=PrIssueStatus.OPEN,
        server_default=PrIssueStatus.OPEN.value,
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    needs_management_decision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: Written by whoever resolved it. Nothing derives it from ``status``.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One action attached to one issue: who does what, by when.

    A separate table rather than columns on the issue, because an issue
    routinely has several actions with different owners and different deadlines,
    and a report that says "3 of 5 actions done" cannot be built from a single
    ``action_taken`` field.

    ``expected_result`` and ``actual_result`` are both stored and both prose.
    Keeping the expectation next to the outcome is the whole point: an action
    marked ``DONE`` that did not achieve what it was for is the failure a review
    exists to notice.

    ``completed_at`` is written by whoever completes the action. **Nothing in
    the database derives it from ``status``** - no trigger, no default, no
    ``onupdate`` - for the same reason ``pr_tasks.completed_at`` is not derived:
    a reopened action would lose the original completion time.
    """

    __tablename__ = "pr_actions"
    __table_args__ = (
        CheckConstraint(_not_empty("description"), name="description_not_empty"),
        Index("ix_pr_actions_owner_status", "owner_user_id", "status"),
        # "What is overdue, by how much" - answered from the index alone.
        Index("ix_pr_actions_status_deadline", "status", "deadline"),
    )

    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_issues.id", ondelete=RESTRICT), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[PrActionStatus] = mapped_column(
        value_enum(PrActionStatus, name="pr_action_status", length=20),
        nullable=False,
        default=PrActionStatus.TODO,
        server_default=PrActionStatus.TODO.value,
    )
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# What was generated, and from which data
# ---------------------------------------------------------------------------


class PrReportRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One attempt to generate one periodic report.

    An attempt, not a report: a run that fails validation is a row here too, and
    that is the point. "How often is the weekly report blocked by missing
    numbers" is a question about the runs, and it is unanswerable if only the
    successes are recorded.

    ``idempotency_key`` is unique and is what stops a retried request, a double
    press and a scheduler firing twice from producing three reports of the same
    week. The database is the collision detector; a caller retries on
    ``IntegrityError`` rather than inventing its own locking.

    ``source_cutoff_at`` is required because a report has to be reproducible.
    It states the instant beyond which data was ignored, so regenerating the
    same run reads the same snapshots however many have arrived since. Without
    it, "the same report" is a moving target.

    ``validation_summary`` is JSONB - what was missing, per channel, in whatever
    shape the validator produces. Nothing indexes or parses it here.
    ``missing_data_count`` is the one number a person needs at a glance, which
    is why it is a column and defaults to 0.

    Step 1B stores any status in any order. **No transition is implemented or
    enforced**; the lifecycle is described in
    ``docs/pr/STEP_1B_REPORTING_DATA_FOUNDATION.md`` and belongs to Step 1C.
    """

    __tablename__ = "pr_report_runs"
    __table_args__ = (
        CheckConstraint(_not_empty("idempotency_key"), name="idempotency_key_not_empty"),
        CheckConstraint(_not_empty("template_version"), name="template_version_not_empty"),
        CheckConstraint("missing_data_count >= 0", name="missing_data_count_not_negative"),
        # A run that finished before it started is a clock problem or a bug,
        # and either way the duration computed from it is meaningless. A run
        # that has finished without a recorded start is allowed: that is what a
        # crashed-and-reconciled run looks like.
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        Index("ix_pr_report_runs_period_type", "period_id", "report_type"),
        Index("ix_pr_report_runs_type_status", "report_type", "status"),
    )

    report_type: Mapped[PrReportType] = mapped_column(
        value_enum(PrReportType, name="pr_report_type", length=30), nullable=False
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_reporting_periods.id", ondelete=RESTRICT), nullable=False
    )
    #: What makes "generate the W32 report" safe to say twice.
    idempotency_key: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True
    )
    #: Which template produced it. A report regenerated after a template change
    #: is a different document, and a reader comparing two weeks needs to know.
    template_version: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger_type: Mapped[PrReportTriggerType] = mapped_column(
        value_enum(PrReportTriggerType, name="pr_report_trigger_type", length=20), nullable=False
    )
    status: Mapped[PrReportRunStatus] = mapped_column(
        value_enum(PrReportRunStatus, name="pr_report_run_status", length=30),
        nullable=False,
        default=PrReportRunStatus.PENDING,
        server_default=PrReportRunStatus.PENDING.value,
        index=True,
    )
    #: Null for a scheduled run - nobody asked, the clock did.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True
    )
    #: The instant beyond which data was ignored. What makes a run repeatable.
    source_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: What the validator found. Never parsed by anything in Step 1B.
    validation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    missing_data_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PrReportArtifact(Base, UUIDPrimaryKeyMixin):
    """One file produced by one report run.

    A separate table because one run legitimately produces more than one file -
    an XLSX for the analyst and a PDF for the meeting - and because a
    regenerated file is a new version rather than an overwrite. That is what
    ``(report_run_id, artifact_format, version_no)`` being unique buys: the
    workbook somebody was sent on Monday is still on disk and still findable
    when version 2 appears on Tuesday.

    ``storage_path`` is where the bytes are, and is **not** a relational key:
    nothing joins on it, nothing is unique on it, and moving a file does not
    orphan a row. ``checksum_sha256`` is what tells you the file at that path is
    still the file this row describes.

    No ``updated_at``. The only mutation is stamping ``delivered_at``, which is
    a dated fact in its own right. Step 1B delivers nothing - that column exists
    so Step 1C has somewhere to record it without a migration.
    """

    __tablename__ = "pr_report_artifacts"
    __table_args__ = (
        CheckConstraint(_not_empty("file_name"), name="file_name_not_empty"),
        CheckConstraint(_not_empty("storage_path"), name="storage_path_not_empty"),
        CheckConstraint(_not_empty("mime_type"), name="mime_type_not_empty"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0", name="file_size_not_negative"
        ),
        Index(
            "uq_pr_report_artifacts_run_format_version",
            "report_run_id",
            "artifact_format",
            "version_no",
            unique=True,
        ),
    )

    report_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_report_runs.id", ondelete=RESTRICT), nullable=False, index=True
    )
    artifact_format: Mapped[PrArtifactFormat] = mapped_column(
        value_enum(PrArtifactFormat, name="pr_artifact_format", length=20), nullable=False
    )
    #: 1, then 2. A regenerated file never displaces the one already sent.
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Where the bytes are. Never a key - see the class docstring.
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(150), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 64 hex characters. What proves the file at ``storage_path`` is this one.
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    #: When it was sent. Nothing in Step 1B sends anything.
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


__all__: list[str] = [
    "PrAction",
    "PrChannelMetricSnapshot",
    "PrIssue",
    "PrPostMetricSnapshot",
    "PrPublication",
    "PrReportArtifact",
    "PrReportRun",
    "PrReportingPeriod",
    "PrWeeklyManualInput",
]
