"""PR and Communications: brands, channels, content, tasks and approvals.

Step 1A of the PR module. Eleven tables, and deliberately nothing else - no
service reads them yet, no handler writes them. What they encode:

* **Who a channel belongs to** is a row in ``pr_channel_assignments``, not a
  column on the channel. One channel has a person answering for it, another
  editing it and a third measuring it, and those are usually three people. A
  single ``owner_user_id`` would force a choice that the team does not make.
* **Where a piece of content goes** is a row in ``pr_content_targets``, not a
  column on the content item. One script becomes a TikTok cut, a YouTube cut
  and a Facebook post; each of those is published, cancelled or delayed on its
  own schedule, so each needs its own status.
* **What a reviewer decided** is a row in ``pr_approval_events`` and is never
  updated. That table has no ``updated_at`` for the same reason
  ``hr_request_events`` has none: the question it answers is "who decided what,
  when, against which version", and an answer that can be edited afterwards is
  not an answer.

Every foreign key here is ``RESTRICT``. Deleting a brand that has content, or a
user who has reviewed something, fails rather than quietly removing the history
that hangs off it. Retirement is a status change - ``INACTIVE``, ``ARCHIVED`` -
which is why every status enum has a value for it.

Codes (``CH-0001``, ``CNT-2026-000001``, ``TSK-2026-000001``) are what people
say to each other and what a report prints. They are unique and, for a brand,
immutable. They are **not** keys: every relationship in this module is a UUID,
so renaming a code can never orphan a row. Generating them is Step 1B's job;
this step only stores and constrains them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from meobot.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, value_enum
from meobot.domain.pr.models import (
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelAssignmentRole,
    PrChannelCategory,
    PrChannelStatus,
    PrContentTargetStatus,
    PrContentType,
    PrDistributionMode,
    PrEntityStatus,
    PrPriority,
    PrTaskAssignmentRole,
    PrTaskStatus,
    PrWorkflowStage,
)

#: The users table every PR person reference points at. Named once, here, so
#: that "which users table" is a single decision rather than eleven of them.
#: There is exactly one identity table in MeoBot and this module does not add
#: a second - a PR channel owner *is* a MeoBot user.
USERS_TABLE = "users"

#: Deleting a person, a brand, a channel, a content item or a task fails while
#: anything still refers to it. Retire it with a status instead.
RESTRICT = "RESTRICT"

#: What makes a channel assignment **open**: it has not been closed with an end
#: date. Not the same as "in force today" - a row with an ``effective_from`` in
#: the future is open by this definition, and a closed row covering today is
#: not. Written once and used by both the model's partial unique index and the
#: migration that creates it, so the two cannot drift apart. Spelled in SQL that
#: PostgreSQL and SQLite both accept, because the offline suite builds this
#: schema from the models.
OPEN_ASSIGNMENT = text("effective_to IS NULL")


def _not_empty(column: str) -> str:
    """SQL for "this text column holds something other than whitespace".

    ``trim`` rather than a length test on the raw value, because ``" "`` is the
    string a form submits when somebody tabs past a required field, and it is
    exactly as useless as ``""``. Spelled in SQL that both PostgreSQL and
    SQLite understand, so the offline suite builds the same schema.
    """
    return f"length(trim({column})) > 0"


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


class PrBrand(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A brand the team communicates for.

    ``code`` is immutable once set. It is printed on reports, typed into
    Telegram and pasted into spreadsheets that outlive any one release; letting
    it change would silently invalidate every one of those references while the
    rows themselves stayed perfectly consistent.
    """

    __tablename__ = "pr_brands"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[PrEntityStatus] = mapped_column(
        value_enum(PrEntityStatus, name="pr_entity_status", length=20),
        nullable=False,
        default=PrEntityStatus.ACTIVE,
        server_default=PrEntityStatus.ACTIVE.value,
        index=True,
    )

    @validates("code")
    def _code_is_write_once(self, key: str, value: str) -> str:
        """Refuse to change a code that has already been set.

        Enforced in the mapper rather than by a trigger: the database is shared
        with migrations and repair scripts that legitimately need to correct a
        row, and a trigger would block those too. Application writes all go
        through the ORM, and this is where they are.
        """
        current = getattr(self, key, None)
        if current is not None and current != value:
            raise ValueError(
                f"pr_brands.code is immutable: {current!r} cannot become {value!r}",
            )
        return value


class PrPlatform(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A place content can be published - TikTok, YouTube, Facebook, a website.

    ``api_available`` records whether numbers can be collected automatically.
    Step 1A only stores the fact; no collector reads it yet. It exists now
    because the answer is a property of the platform, and recording it late
    would mean backfilling it from memory.
    """

    __tablename__ = "pr_platforms"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: Why the API is or is not usable - a quota, a review requirement, a
    #: missing permission. Prose for a human, never parsed.
    api_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PrEntityStatus] = mapped_column(
        value_enum(PrEntityStatus, name="pr_entity_status", length=20),
        nullable=False,
        default=PrEntityStatus.ACTIVE,
        server_default=PrEntityStatus.ACTIVE.value,
        index=True,
    )


class PrContentFormat(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A shape content takes - a short video, a long video, an article."""

    __tablename__ = "pr_content_formats"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PrEntityStatus] = mapped_column(
        value_enum(PrEntityStatus, name="pr_entity_status", length=20),
        nullable=False,
        default=PrEntityStatus.ACTIVE,
        server_default=PrEntityStatus.ACTIVE.value,
    )


class PrContentPillar(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A recurring theme content is written against."""

    __tablename__ = "pr_content_pillars"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PrEntityStatus] = mapped_column(
        value_enum(PrEntityStatus, name="pr_entity_status", length=20),
        nullable=False,
        default=PrEntityStatus.ACTIVE,
        server_default=PrEntityStatus.ACTIVE.value,
    )


# ---------------------------------------------------------------------------
# Channels and who is responsible for them
# ---------------------------------------------------------------------------


class PrChannel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One account or property the team publishes to.

    ``url`` is stored and never joined on. A channel's URL changes when a
    handle is renamed, when a platform migrates its domain, and when somebody
    pastes a link with tracking parameters; every one of those would break a
    relationship keyed on it. ``external_id`` is the platform's own identifier
    and is what a future collector matches against - it is nullable because
    a channel is registered by a person long before anybody looks it up.

    ``brand_id`` is nullable: a shared corporate account belongs to no single
    brand, and forcing one would be a lie recorded as data.

    Step 1F.2.4a added ``handle`` and nothing else. The canonical *platform*
    identity a channel screen shows is derived from ``platform_id`` through
    ``pr_platforms.code`` - see
    :func:`~meobot.domain.pr.channel_metrics.platform_from_code` - rather than
    stored again here, because a channel carrying its own platform enum beside a
    foreign key to a platform row is two answers to one question.
    """

    __tablename__ = "pr_channels"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("name"), name="name_not_empty"),
        # Three tiers, or none stated. Not a foreign key to a tier table: the
        # values are a fixed editorial scale, not reference data somebody edits.
        CheckConstraint("tier IS NULL OR tier IN (1, 2, 3)", name="tier_in_range"),
        # Step 1F.2.4a. A handle is optional, and a handle of spaces is not a
        # handle - the same rule ``_not_empty`` applies to every required text
        # column here, applied to an optional one.
        CheckConstraint(
            f"handle IS NULL OR {_not_empty('handle')}",
            name="handle_not_empty",
        ),
        Index("ix_pr_channels_platform_external", "platform_id", "external_id"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_platforms.id", ondelete=RESTRICT), nullable=False, index=True
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_brands.id", ondelete=RESTRICT), nullable=True, index=True
    )
    #: 1, 2 or 3 - how much of the team's attention this channel is worth.
    tier: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    category: Mapped[PrChannelCategory] = mapped_column(
        value_enum(PrChannelCategory, name="pr_channel_category", length=20), nullable=False
    )
    #: The platform's own identifier for this channel. Never a URL.
    #:
    #: Step 1F.2.4a calls this the *external account id* on screen and added no
    #: second column for it: a Facebook page id, a YouTube channel id and a
    #: TikTok open id are all exactly what this column was described as holding
    #: in Step 1A, and a future connector is exactly the caller it was written
    #: for. It is metadata; nothing authorizes on it.
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Step 1F.2.4a. The user-facing identifier a person would type or say -
    #: ``@drtien``, ``apexmed``, ``UCxxxxxxxx``. Stored as written, with no ``@``
    #: added or removed, because the platforms disagree about whether it belongs
    #: and the string is for a human to recognise rather than for code to parse.
    #: Never identity: two channels may carry the same handle and nothing here
    #: is looked up by it.
    handle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: For a person to click. Never used as a key - see the class docstring.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PrChannelStatus] = mapped_column(
        value_enum(PrChannelStatus, name="pr_channel_status", length=20),
        nullable=False,
        default=PrChannelStatus.ACTIVE,
        server_default=PrChannelStatus.ACTIVE.value,
        index=True,
    )
    #: The day the channel started operating, if anybody recorded it.
    started_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class PrChannelAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One person, one channel, one responsibility, over one period.

    Dated rather than overwritten. "Who owned this channel in March" is a
    question a report has to answer months later, and a row that is edited in
    place cannot answer it. Handing a channel over means closing the current
    row with an ``effective_to`` and opening a new one.

    The unique index is partial and covers only **open** rows - an assignment
    is open when ``effective_to IS NULL``. Two open rows for the same channel,
    person and role are a data-entry mistake and are refused.

    That is the whole of what the database enforces, and it is narrower than
    "no overlapping assignments". Two *closed* rows whose date ranges overlap -
    2026-01-01→2026-06-30 and 2026-03-01→2026-09-30 for the same triple - are
    accepted here, as is a closed row overlapping an open one. Rejecting those
    is a Step 1B service rule; see ``docs/pr/STEP_1A_PR_CORE_FOUNDATION.md``.
    Nothing in this module should be described as preventing overlap.

    ``allocation_percent`` says how much of that person's time this channel is
    meant to take. It is bounded to 0-100 per row, and deliberately *not*
    constrained to sum to 100 across a person: the database cannot know whether
    a temporarily over-allocated week is an error or a fact, and refusing the
    insert would make it impossible to record what is actually happening.
    Whether the totals are sensible is a Step 1B question.
    """

    __tablename__ = "pr_channel_assignments"
    __table_args__ = (
        CheckConstraint(
            "allocation_percent >= 0 AND allocation_percent <= 100",
            name="allocation_percent_in_range",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_period_ordered",
        ),
        # Named for what it actually covers - open rows - rather than "active",
        # which reads as "currently in force by date" and would overstate it.
        Index(
            "uq_pr_channel_assignments_open_role",
            "channel_id",
            "user_id",
            "assignment_role",
            unique=True,
            postgresql_where=OPEN_ASSIGNMENT,
            sqlite_where=OPEN_ASSIGNMENT,
        ),
    )

    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    assignment_role: Mapped[PrChannelAssignmentRole] = mapped_column(
        value_enum(PrChannelAssignmentRole, name="pr_channel_assignment_role", length=30),
        nullable=False,
    )
    #: Marks the row a report should name first when several people share a
    #: role. Not enforced to be singular: during a handover two people really
    #: are primary for a day, and the database refusing that helps nobody.
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    allocation_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("100"), server_default=text("100")
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: Null while the assignment is open. Setting it closes the row.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class PrContentItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One idea, carried from a brief to a published, measured piece.

    A content item is the unit people talk about ("CNT-2026-000042 is stuck in
    head review"). It is not the unit that gets published - that is a
    :class:`PrContentTarget`, one per channel.

    ``owner_user_id`` and ``created_by_user_id`` are both required and both
    point at ``users``. They are separate because they answer different
    questions: who is accountable for this now, and who started it. An owner
    changes; the person who created the row does not.
    """

    __tablename__ = "pr_content_items"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("title"), name="title_not_empty"),
        Index("ix_pr_content_items_brand_stage", "brand_id", "workflow_stage"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_brands.id", ondelete=RESTRICT), nullable=False, index=True
    )
    format_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_formats.id", ondelete=RESTRICT), nullable=True
    )
    pillar_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_pillars.id", ondelete=RESTRICT), nullable=True
    )
    #: What it is about, in a few words.
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The opening line that has to earn the next three seconds.
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The full brief. Free prose; nothing parses it.
    brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[PrPriority] = mapped_column(
        value_enum(PrPriority, name="pr_priority", length=20),
        nullable=False,
        default=PrPriority.NORMAL,
        server_default=PrPriority.NORMAL.value,
    )
    #: What kind of thing this is - a short-video script, a Facebook post, a TVC.
    #: Step 1F.2.3e, and **nullable on purpose**: the application requires one for
    #: everything created from now on, and the thousands of rows that predate the
    #: column stay ``NULL`` rather than being assigned a type nobody chose.
    #:
    #: Guessing from the title or the target platform was considered and refused.
    #: A piece on a TikTok channel is *usually* a short-video script and
    #: sometimes is not, and a backfill would have written a plausible answer
    #: into a column people will filter and report on - a wrong classification
    #: that looks exactly like a real one. ``NULL`` reads as *Chưa phân loại*,
    #: which is true, and anybody authorised can correct it in one click.
    #:
    #: Deliberately not ``format_id``: see :class:`~meobot.domain.pr.models.PrContentType`.
    content_type: Mapped[PrContentType | None] = mapped_column(
        value_enum(PrContentType, name="pr_content_type", length=30), nullable=True
    )
    workflow_stage: Mapped[PrWorkflowStage] = mapped_column(
        value_enum(PrWorkflowStage, name="pr_workflow_stage", length=30),
        nullable=False,
        default=PrWorkflowStage.IDEA,
        server_default=PrWorkflowStage.IDEA.value,
        index=True,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: Who is cutting the video. Step 1F.2.3, and **nullable on purpose**: the
    #: workflow enters ``PRODUCTION`` when the script is approved, which is
    #: usually before anybody has been given the edit. ``NULL`` at ``PRODUCTION``
    #: is a real, expected state - "chưa có người nhận" - and the alternative,
    #: requiring a producer to enter the stage, would either block approved work
    #: or invite somebody to name a placeholder.
    #:
    #: Deliberately **not** ``owner_user_id`` reused: the owner answers for the
    #: piece from brief to measurement, the producer answers for one file. On a
    #: three-person team they are often the same human and sometimes not, and a
    #: single column could not say which.
    producer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=True, index=True
    )
    #: When the item **first** entered ``PRODUCTION``. Stamped once and never
    #: cleared, so it survives an internal reviewer sending the cut back - which
    #: is what makes it, and not the current stage, the answer to "has this ever
    #: been produced". See :mod:`meobot.domain.pr.lifecycle`.
    production_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the item as a whole is meant to go out. Each channel may differ;
    #: that is what ``pr_content_targets.target_publish_at`` is for.
    planned_publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    #: Set when the item is put away. A stored fact, not a computed one: no
    #: trigger fills it, because "archived" is a decision somebody makes.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrContentTarget(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One content item's planned appearance on one channel.

    The unique constraint on ``(content_id, channel_id)`` is what makes "publish
    this to TikTok" idempotent: pressing the button twice, or a retried request
    arriving alongside the original, produces one row rather than two competing
    schedules for the same channel.
    """

    __tablename__ = "pr_content_targets"
    __table_args__ = (
        Index("uq_pr_content_targets_content_channel", "content_id", "channel_id", unique=True),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_channels.id", ondelete=RESTRICT), nullable=False, index=True
    )
    #: When this channel's version goes out, when it differs from the item's
    #: own plan - a YouTube cut a day behind the TikTok one, say.
    target_publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: What has to change for this channel. Prose for the editor.
    adaptation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Organic or paid, per target. Step 1F.1: which official policy applies
    #: depends on it, so for a policy-grounded platform ``UNSPECIFIED`` blocks
    #: AI review rather than defaulting to the more permissive reading.
    distribution_mode: Mapped[PrDistributionMode] = mapped_column(
        value_enum(PrDistributionMode, name="pr_distribution_mode", length=20),
        nullable=False,
        default=PrDistributionMode.UNSPECIFIED,
        server_default=PrDistributionMode.UNSPECIFIED.value,
    )
    status: Mapped[PrContentTargetStatus] = mapped_column(
        value_enum(PrContentTargetStatus, name="pr_content_target_status", length=20),
        nullable=False,
        default=PrContentTargetStatus.PLANNED,
        server_default=PrContentTargetStatus.PLANNED.value,
        index=True,
    )


# ---------------------------------------------------------------------------
# Work
# ---------------------------------------------------------------------------


class PrTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A unit of work, usually but not always attached to a content item.

    ``content_id`` is nullable because real PR work includes tasks that belong
    to no single piece: renewing an account, writing a style guide, chasing a
    platform's support desk.

    ``completed_at`` is written by whoever completes the task. Nothing in the
    database sets it from ``status`` - a trigger doing that would make the two
    columns impossible to disagree, which sounds desirable until a task is
    reopened and the original completion time is gone.
    """

    __tablename__ = "pr_tasks"
    __table_args__ = (
        CheckConstraint(_not_empty("code"), name="code_not_empty"),
        CheckConstraint(_not_empty("title"), name="title_not_empty"),
        CheckConstraint(_not_empty("task_type"), name="task_type_not_empty"),
        Index("ix_pr_tasks_status_deadline", "status", "deadline"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=True, index=True
    )
    #: What kind of work this is - "SCRIPT", "EDIT", "SEEDING". Free text
    #: rather than an enum: the list is still being discovered, and a wrong
    #: enum costs a migration while a wrong string costs an ``UPDATE``.
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[PrPriority] = mapped_column(
        value_enum(PrPriority, name="pr_priority", length=20),
        nullable=False,
        default=PrPriority.NORMAL,
        server_default=PrPriority.NORMAL.value,
    )
    status: Mapped[PrTaskStatus] = mapped_column(
        value_enum(PrTaskStatus, name="pr_task_status", length=30),
        nullable=False,
        default=PrTaskStatus.TODO,
        server_default=PrTaskStatus.TODO.value,
        index=True,
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrTaskAssignment(Base, UUIDPrimaryKeyMixin):
    """One person attached to one task in one capacity.

    A separate table rather than an ``assignee_user_id`` column, because a task
    routinely has an owner, someone helping and someone reviewing, and each of
    them accepts and finishes at a different moment. The per-person
    ``accepted_at`` and ``completed_at`` are why: a task's own
    ``completed_at`` cannot say when the reviewer finished.

    No ``updated_at``: the only mutations are stamping ``accepted_at`` and
    ``completed_at``, each of which is its own dated fact.
    """

    __tablename__ = "pr_task_assignments"
    __table_args__ = (
        Index(
            "uq_pr_task_assignments_task_user_role",
            "task_id",
            "user_id",
            "assignment_role",
            unique=True,
        ),
        # "What is still open for this person" - the query a person's own
        # task list runs, answered from the index alone.
        Index("ix_pr_task_assignments_user_completed", "user_id", "completed_at"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_tasks.id", ondelete=RESTRICT), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    assignment_role: Mapped[PrTaskAssignmentRole] = mapped_column(
        value_enum(PrTaskAssignmentRole, name="pr_task_assignment_role", length=20), nullable=False
    )
    #: When the work was handed over. Required, and distinct from
    #: ``created_at``: a backfilled row is created today and was assigned in
    #: March, and a report about March needs the second number.
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When this person acknowledged it. Null means nobody has confirmed they
    #: saw it, which is a different state from "not started".
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrApprovalEvent(Base, UUIDPrimaryKeyMixin):
    """One review decision. Append-only, and never updated.

    This table is the record of who approved what. It has no ``updated_at`` and
    nothing in the module updates a row: a reviewer who changes their mind adds
    a second event, and both are true - the first at the version it was made
    against, the second at the version after that.

    ``version_reviewed`` is what makes the record meaningful. "Approved" is
    only ever an answer about a specific draft, so the version is required and
    must be at least 1. Approving a content item that has since been rewritten
    is exactly the failure this column exists to make visible.

    ``task_id`` is nullable: some reviews happen against the content item
    itself, with no task standing behind them.
    """

    __tablename__ = "pr_approval_events"
    __table_args__ = (
        CheckConstraint("version_reviewed >= 1", name="version_reviewed_positive"),
        # The history of one item, in order - what a content card renders.
        Index("ix_pr_approval_events_content_decided", "content_id", "decided_at"),
        # "How often does head review send things back" - answered without
        # touching the table.
        Index("ix_pr_approval_events_stage_decision", "approval_stage", "decision"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pr_content_items.id", ondelete=RESTRICT), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_tasks.id", ondelete=RESTRICT), nullable=True, index=True
    )
    #: Which production submission this decision was about. Step 1F.2.3, and set
    #: only for ``INTERNAL_REVIEW`` rows: the other two gates judge a script,
    #: which ``version_reviewed`` already identifies.
    #:
    #: It exists because ``version_reviewed`` cannot identify a *cut*. A piece
    #: that is sent back and re-cut carries several submissions against one
    #: script version, so without this column the second internal review would be
    #: indistinguishable from the first - two rows, same stage, same version,
    #: opposite decisions, and nothing saying which file each was about.
    production_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_production_submissions.id", ondelete=RESTRICT),
        nullable=True,
        index=True,
    )
    approval_stage: Mapped[PrApprovalStage] = mapped_column(
        value_enum(PrApprovalStage, name="pr_approval_stage", length=30), nullable=False
    )
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{USERS_TABLE}.id", ondelete=RESTRICT), nullable=False, index=True
    )
    decision: Mapped[PrApprovalDecision] = mapped_column(
        value_enum(PrApprovalDecision, name="pr_approval_decision", length=30), nullable=False
    )
    #: Which draft this decision was about. At least 1.
    version_reviewed: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: When the reviewer decided, which is not when the row was written - a
    #: decision taken in a meeting is recorded afterwards.
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__: list[str] = [
    "PrApprovalEvent",
    "PrBrand",
    "PrChannel",
    "PrChannelAssignment",
    "PrContentFormat",
    "PrContentItem",
    "PrContentPillar",
    "PrContentTarget",
    "PrPlatform",
    "PrTask",
    "PrTaskAssignment",
]
