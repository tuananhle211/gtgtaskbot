"""Which slice of the content list somebody is looking at, and which one first.

Step 1F.2.2. At roughly fifty new content items a day, ``/contents`` returning
"everything, newest first" is a database explorer rather than a workspace: the
three items waiting on *you* are somewhere on page one of a list that grows by a
screenful a day, and finding them is a scrolling exercise the tool should have
done.

This module is the vocabulary for that, and nothing more. It has no session, no
SQL and no capability lookups - what it owns is the **names** of the scopes, the
**choice of default**, and the **table of workflow groups** the board is divided
into, all of which are decisions rather than queries. Turning a scope into a
``WHERE`` clause is
:mod:`meobot.application.pr_content_query`; resolving which gates an actor
actually holds is :class:`~meobot.application.pr_capability_service.PrCapabilityService`.

Display scope is not authorization
----------------------------------

This is the distinction to hold on to while reading the rest of the step. A
scope decides **what is shown first**; the read permission decides **what may be
read at all**, and that is
:data:`~meobot.domain.pr.policy.PR_READ_PERMISSION`, unchanged. Somebody who
lands on ``MY_CONTENT`` may switch to ``ALL`` and see the same rows they could
already have seen, because they were always allowed to - the default merely
stopped putting them in the way. Nothing here is row-level security and nothing
here should ever become it: a scope that silently hid rows a person is entitled
to would be an access rule pretending to be a preference, and the two need
different tests, different audit and different reviewers.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from meobot.domain.pr.models import PrProductionHandoff, PrWorkflowStage
from meobot.domain.pr.policy import APPROVAL_CAPABILITIES, PrCapability
from meobot.domain.pr.workflow import RETIRED_STAGES, STAGE_APPROVAL_GATES


class PrContentViewScope(StrEnum):
    """The four ways of narrowing the content list to something workable.

    Each is defined against relationships the module already has; none of them
    invents an ownership field.

    Declaration order is narrowest to widest and carries no display meaning. In
    which order a client offers the four is the client's decision - the web panel
    leads with *Tất cả* because it is the view people orient by - and it is
    deliberately not encoded here, because a member reordering their tab strip
    should not read like a change to the vocabulary. Nor does any order imply a
    default: which scope somebody lands on is :func:`default_scope`, below.
    """

    #: *Cần tôi xử lý.* Content with an executable next action **for this
    #: actor**, which is one of three things and not only the personal ones:
    #: any item standing at a review gate they hold the grant for - whoever owns
    #: it - a draft they own at a stage that may still be rewritten, or an
    #: unfinished task assigned to them. Derived from what the actor may do, not
    #: from a list of stages and not from who the work is filed under.
    MY_ACTIONS = "MY_ACTIONS"
    #: *Của tôi.* Content this actor is responsible for: they are the
    #: ``owner_user_id``, or they hold an unfinished ``pr_task_assignments`` row
    #: against it. Both are existing authoritative relationships.
    MY_CONTENT = "MY_CONTENT"
    #: *Team.* Content targeting a channel this actor has an in-force
    #: ``pr_channel_assignments`` row for, in any role. The broadest existing
    #: assignment grouping - see the note below on why it is not a hierarchy.
    TEAM = "TEAM"
    #: *Tất cả.* The unnarrowed list, exactly as before this step.
    ALL = "ALL"


class PrContentGroup(StrEnum):
    """Which phase of the pipeline somebody is working in.

    Step 1F.2.3c made these the tabs on the content board; this step makes them
    a **filter**, because a tab that only regroups the page it was given is not
    one. With 146 items matching the filters and five of them in preparation,
    the browser was handed sixty rows, kept whichever of them happened to be
    preparation, and drew a board with one card on page 1 and four on page 3 -
    over a tab that correctly said 5. The group has to narrow the query before
    ``LIMIT``, and a group is a query dimension for exactly that reason.

    Five, and they **partition** :class:`~meobot.domain.pr.models.PrWorkflowStage`:
    every stage is in exactly one, so the groups cannot double-count an item and
    cannot lose one. A fourteenth stage is a ``KeyError`` in the test that
    asserts the partition rather than a card that silently stops appearing.

    Grouping is not workflow. Which transitions are legal is
    :mod:`meobot.domain.pr.workflow`, and moving a stage between groups changes
    none of them; two stages being adjacent in a group is not an edge. Nor is it
    authorization - ``PR_INTERNAL_REVIEW`` decides who may act at
    ``INTERNAL_REVIEW`` whichever tab draws it.
    """

    #: *Chuẩn bị.* Before anybody has been asked to decide anything.
    PREPARATION = "PREPARATION"
    #: *Chờ duyệt.* The two **script** gates, and only those.
    #: ``INTERNAL_REVIEW`` is a review of a finished cut and belongs below.
    EDITORIAL_REVIEW = "EDITORIAL_REVIEW"
    #: *Sản xuất.* From the moment the script is approved to the moment the cut
    #: passes internal review. Four columns over three stages - see
    #: :class:`~meobot.domain.pr.models.PrProductionHandoff`, which is what tells
    #: an ``APPROVED`` item nobody has taken from one somebody is about to cut.
    PRODUCTION = "PRODUCTION"
    #: *Hoàn tất.* Ready, and out. Not filed: ``ARCHIVED`` left this group in
    #: Step 1F.2.3f.6c and is the archive view's - see
    #: :class:`PrContentBoardView`.
    COMPLETED = "COMPLETED"
    #: *Đã hủy.* Abandoned work, which people do go looking for.
    CANCELLED = "CANCELLED"


#: The one mapping from group to stages. Everything that filters, counts or
#: draws a group reads it, so adding a stage means adding it here once.
#:
#: A ``MappingProxyType`` because it is a table and not a variable: a caller
#: mutating it would change what "Sản xuất" means for every request in the
#: process, and the failure would surface as a wrong card three screens away.
GROUP_STAGES: Mapping[PrContentGroup, tuple[PrWorkflowStage, ...]] = MappingProxyType(
    {
        PrContentGroup.PREPARATION: (
            PrWorkflowStage.IDEA,
            PrWorkflowStage.BRIEFING,
            PrWorkflowStage.SCRIPTING,
            PrWorkflowStage.AI_REVIEW,
        ),
        PrContentGroup.EDITORIAL_REVIEW: (
            PrWorkflowStage.TEAM_LEAD_REVIEW,
            PrWorkflowStage.HEAD_REVIEW,
        ),
        PrContentGroup.PRODUCTION: (
            PrWorkflowStage.APPROVED,
            PrWorkflowStage.PRODUCTION,
            PrWorkflowStage.INTERNAL_REVIEW,
        ),
        PrContentGroup.COMPLETED: (
            PrWorkflowStage.READY_TO_PUBLISH,
            PrWorkflowStage.PUBLISHED,
        ),
        PrContentGroup.CANCELLED: (PrWorkflowStage.CANCELLED,),
    }
)


class PrContentBoardView(StrEnum):
    """**Which board is being asked for**: the operational one, or the archive.

    Step 1F.2.3f.6c. ``ARCHIVED`` is a real terminal state and stays one, but
    it is not operational work: nothing moves from it, nobody acts on it, and
    at fifty items a day it is the fastest-growing stage there is. Counting it
    into *Hoàn tất* made the tab a lifetime figure again, and reading it
    against the month meant an ``archived_at`` arm in every board statement
    for a column most people never opened.

    So the archive is a **view**, asked for on purpose, and the default board
    never touches it - not in its cards, not in its counts, and not in the SQL
    it sends. This is the one place that split is named; the stage tables
    below, the query builder and the panel all read it from here.
    """

    #: The operational board: every stage that is not archived (and not
    #: retired). The default for the board route.
    ACTIVE = "ACTIVE"
    #: *Nội dung lưu trữ*: ``ARCHIVED`` alone, fetched only when opened.
    ARCHIVE = "ARCHIVE"


#: The stages the archive view holds. A set, like ``RETIRED_STAGES``, so the
#: active board is defined by subtraction and a second archival stage would
#: need one edit rather than five.
ARCHIVE_STAGES: frozenset[PrWorkflowStage] = frozenset({PrWorkflowStage.ARCHIVED})


def board_stages(view: PrContentBoardView) -> tuple[PrWorkflowStage, ...]:
    """The stages one view draws, in enum order.

    ``ACTIVE`` is every stage that is neither archived nor retired - the union
    of the five groups, which :data:`GROUP_STAGES` no longer lists ``ARCHIVED``
    in. ``ARCHIVE`` is :data:`ARCHIVE_STAGES`.
    """
    if view is PrContentBoardView.ARCHIVE:
        return tuple(stage for stage in PrWorkflowStage if stage in ARCHIVE_STAGES)
    return tuple(
        stage
        for stage in PrWorkflowStage
        if stage not in ARCHIVE_STAGES and stage not in RETIRED_STAGES
    )


def stages_in_group(group: PrContentGroup) -> tuple[PrWorkflowStage, ...]:
    """The stages this group covers, in workflow order.

    A function rather than a bare dictionary read at the call sites, so the
    ``IN`` clause a filter renders and the columns a client draws come from one
    lookup - and so a group missing from the table fails loudly here rather than
    quietly returning nothing and filtering everything away.
    """
    return GROUP_STAGES[group]


class PrContentLane(StrEnum):
    """One column of the board, and one independently paginated queue.

    Step 1F.2.3c2. The group was made a filter in 1F.2.3c1 so that a tab reading
    *Chuẩn bị 5* showed five cards rather than one; this is the same correction
    one level deeper. A group is still several queues stacked into one page, and
    a page of *Sản xuất* is dominated by whichever of its four columns happens to
    be largest: 155 items waiting for a producer, three actually being cut, and a
    sixty-row page that contains none of the three. The lane reading *Đang sản
    xuất 3* was right, and all three cards were on page three.

    So a lane is a **query dimension**, exactly as the group is, and each one is
    paginated on its own. Fifteen of them, and they partition the thirteen
    stages the way :data:`GROUP_STAGES` partitions them into groups - with one
    exception that is the whole reason this enum exists rather than being
    ``PrWorkflowStage`` under another name: ``APPROVED`` is **two** lanes.

    Four things this is not, and confusing any of them with a lane is how the
    board gets a second opinion about where a card belongs:

    * a **workflow group** (:class:`PrContentGroup`) is a phase of the pipeline
      and holds several lanes;
    * a **workflow stage** (:class:`~meobot.domain.pr.models.PrWorkflowStage`)
      is how far the work has got, and is what transitions move;
    * a **production handoff state**
      (:class:`~meobot.domain.pr.models.PrProductionHandoff`) is derived from
      ``(workflow_stage, producer_user_id)`` and is what tells the two halves of
      ``APPROVED`` apart;
    * a **lane** is none of those. It is a *projection*: a name for one
      ``WHERE`` clause, chosen so that a screen can ask for one column's rows
      and page through them without the other columns' volume in the way.

    The values are deliberately the stage's own name where a lane is a stage,
    and the handoff state's own name where it is not, so a client's column key
    and this enum are the same string and nothing has to translate between them.
    """

    #: ``workflow_stage = IDEA``.
    IDEA = "IDEA"
    #: ``workflow_stage = BRIEFING``.
    BRIEFING = "BRIEFING"
    #: ``workflow_stage = SCRIPTING``.
    SCRIPTING = "SCRIPTING"
    #: ``workflow_stage = AI_REVIEW``.
    AI_REVIEW = "AI_REVIEW"
    #: ``workflow_stage = TEAM_LEAD_REVIEW``.
    TEAM_LEAD_REVIEW = "TEAM_LEAD_REVIEW"
    #: ``workflow_stage = HEAD_REVIEW``.
    HEAD_REVIEW = "HEAD_REVIEW"
    #: ``workflow_stage = APPROVED AND producer_user_id IS NULL``. *Chờ nhận
    #: sản xuất* - the lane that is 155 items long and was burying the next two.
    WAITING_FOR_PRODUCER = "WAITING_FOR_PRODUCER"
    #: ``workflow_stage = APPROVED AND producer_user_id IS NOT NULL``.
    READY_FOR_PRODUCTION = "READY_FOR_PRODUCTION"
    #: ``workflow_stage = PRODUCTION``.
    IN_PRODUCTION = "IN_PRODUCTION"
    #: ``workflow_stage = INTERNAL_REVIEW``.
    IN_INTERNAL_REVIEW = "IN_INTERNAL_REVIEW"
    #: ``workflow_stage = READY_TO_PUBLISH``.
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    #: ``workflow_stage = PUBLISHED``.
    PUBLISHED = "PUBLISHED"
    #: ``workflow_stage = ARCHIVED``. Step 1F.2.3f.6c: **not a column of the
    #: operational board** - it is the archive view's one lane, asked for with
    #: ``view=ARCHIVE`` and in no group. See :class:`PrContentBoardView`.
    ARCHIVED = "ARCHIVED"
    #: ``workflow_stage = CANCELLED``. One lane, and not a special case: the
    #: group happens to hold exactly one queue, which needs no second
    #: pagination architecture to say so.
    CANCELLED = "CANCELLED"


#: The stage each lane draws from. Every lane has exactly one; two lanes share
#: ``APPROVED`` and are told apart by :data:`LANE_PRODUCTION_STATES`.
#:
#: This is the table, and the ``WHERE`` clause is built from it - see
#: :func:`~meobot.application.pr_content_query.lane_conditions`. A lane whose
#: stage is missing here is a ``KeyError`` at the filter rather than a column
#: that silently matches everything.
LANE_STAGES: Mapping[PrContentLane, PrWorkflowStage] = MappingProxyType(
    {
        PrContentLane.IDEA: PrWorkflowStage.IDEA,
        PrContentLane.BRIEFING: PrWorkflowStage.BRIEFING,
        PrContentLane.SCRIPTING: PrWorkflowStage.SCRIPTING,
        PrContentLane.AI_REVIEW: PrWorkflowStage.AI_REVIEW,
        PrContentLane.TEAM_LEAD_REVIEW: PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrContentLane.HEAD_REVIEW: PrWorkflowStage.HEAD_REVIEW,
        PrContentLane.WAITING_FOR_PRODUCER: PrWorkflowStage.APPROVED,
        PrContentLane.READY_FOR_PRODUCTION: PrWorkflowStage.APPROVED,
        PrContentLane.IN_PRODUCTION: PrWorkflowStage.PRODUCTION,
        PrContentLane.IN_INTERNAL_REVIEW: PrWorkflowStage.INTERNAL_REVIEW,
        PrContentLane.READY_TO_PUBLISH: PrWorkflowStage.READY_TO_PUBLISH,
        PrContentLane.PUBLISHED: PrWorkflowStage.PUBLISHED,
        PrContentLane.ARCHIVED: PrWorkflowStage.ARCHIVED,
        PrContentLane.CANCELLED: PrWorkflowStage.CANCELLED,
    }
)


#: The four lanes whose stage does not decide them on its own, and the derived
#: production state each one means.
#:
#: Note what this table does **not** contain: a rule. It says which
#: :class:`~meobot.domain.pr.models.PrProductionHandoff` a lane is, and
#: :func:`~meobot.domain.pr.production.handoff_state` remains the only thing
#: that knows a null ``producer_user_id`` at ``APPROVED`` means *chờ nhận sản
#: xuất*. The predicate is read back out of that function rather than restated
#: as ``IS NULL`` beside a stage - see
#: :func:`~meobot.application.pr_content_query.lane_conditions`.
LANE_PRODUCTION_STATES: Mapping[PrContentLane, PrProductionHandoff] = MappingProxyType(
    {
        PrContentLane.WAITING_FOR_PRODUCER: PrProductionHandoff.WAITING_FOR_PRODUCER,
        PrContentLane.READY_FOR_PRODUCTION: PrProductionHandoff.READY_FOR_PRODUCTION,
        PrContentLane.IN_PRODUCTION: PrProductionHandoff.IN_PRODUCTION,
        PrContentLane.IN_INTERNAL_REVIEW: PrProductionHandoff.IN_INTERNAL_REVIEW,
    }
)


#: Which lanes each group draws, in the order the work actually moves.
#:
#: The companion to :data:`GROUP_STAGES` and the reason a client does not have
#: to derive one from the other: *Sản xuất* is three stages and **four** lanes,
#: so "the columns of a group" is not "the stages of a group" and a browser
#: computing it would have to know why.
GROUP_LANES: Mapping[PrContentGroup, tuple[PrContentLane, ...]] = MappingProxyType(
    {
        PrContentGroup.PREPARATION: (
            PrContentLane.IDEA,
            PrContentLane.BRIEFING,
            PrContentLane.SCRIPTING,
            PrContentLane.AI_REVIEW,
        ),
        PrContentGroup.EDITORIAL_REVIEW: (
            PrContentLane.TEAM_LEAD_REVIEW,
            PrContentLane.HEAD_REVIEW,
        ),
        PrContentGroup.PRODUCTION: (
            PrContentLane.WAITING_FOR_PRODUCER,
            PrContentLane.READY_FOR_PRODUCTION,
            PrContentLane.IN_PRODUCTION,
            PrContentLane.IN_INTERNAL_REVIEW,
        ),
        PrContentGroup.COMPLETED: (
            PrContentLane.READY_TO_PUBLISH,
            PrContentLane.PUBLISHED,
        ),
        PrContentGroup.CANCELLED: (PrContentLane.CANCELLED,),
    }
)

#: The lanes that belong to no group because they belong to no operational
#: board: the archive view's columns. :data:`GROUP_LANES` and this are the
#: two halves of the lane enum.
ARCHIVE_LANES: frozenset[PrContentLane] = frozenset(
    lane for lane, stage in LANE_STAGES.items() if stage in ARCHIVE_STAGES
)


def lanes_in_group(group: PrContentGroup) -> tuple[PrContentLane, ...]:
    """The lanes this group draws, in operational order.

    The lane counterpart of :func:`stages_in_group`, and a function for the same
    reason: one lookup, and a loud failure for a group nobody added lanes for.
    """
    return GROUP_LANES[group]


def lane_stage(lane: PrContentLane) -> PrWorkflowStage:
    """The workflow stage this lane draws from."""
    return LANE_STAGES[lane]


def lane_production_state(lane: PrContentLane) -> PrProductionHandoff | None:
    """The derived production state this lane means, or ``None``.

    ``None`` for the eleven lanes their stage decides on its own. A lane with a
    state still narrows by :func:`lane_stage` first; the state is the *second*
    half of the predicate, never a replacement for the first.
    """
    return LANE_PRODUCTION_STATES.get(lane)


def group_of_lane(lane: PrContentLane) -> PrContentGroup:
    """Which group draws this lane. Read off :data:`GROUP_LANES`.

    ``KeyError`` for an archive lane, which is in no group by design.

    Used by nothing that filters - a lane narrows on its own, and a lane asked
    for alongside a group it is not in is an empty intersection rather than a
    reinterpretation. It exists so a test can assert the partition, which is what
    keeps a sixteenth lane from being added to the enum and to no group.
    """
    for group, lanes in GROUP_LANES.items():
        if lane in lanes:
            return group
    raise KeyError(lane)


#: The ``content_type`` query value meaning *Chưa phân loại* - the historical
#: rows that predate the column. Step 1F.2.3e.
#:
#: A sentinel on the **wire** rather than a member of
#: :class:`~meobot.domain.pr.models.PrContentType`, because absent is not a
#: format: a seventh enum member would appear in every create dropdown and every
#: future report as though somebody could choose it. It lives here, with the rest
#: of the "which slice of the list" vocabulary, rather than in the router, so the
#: panel and the API agree on one spelling.
UNCLASSIFIED_CONTENT_TYPE = "UNCLASSIFIED"


class PrContentDateField(StrEnum):
    """Which date a date filter is about.

    Three, and they answer different operational questions, which is why the
    filter is not simply "date": ``CREATED_AT`` is *"what came in"*,
    ``PLANNED_PUBLISH_AT`` is *"what is due"*, and ``UPDATED_AT`` is *"what has
    been touched"*. A single unlabelled date control would silently pick one and
    be wrong for two thirds of the people using it.

    ``CREATED_AT`` is the default because it is always populated;
    ``planned_publish_at`` is nullable, and a row with no plan simply falls out
    of a filter on it - correctly, but surprisingly if it were the default.

    ``UPDATED_AT`` answers the question the other two cannot: *"what has moved
    lately"*. A piece drafted in August, scheduled for October and edited
    yesterday is invisible to a recent window on either of the others, and it is
    exactly the piece somebody scanning for activity is looking for. It is the
    row's own ``updated_at`` - maintained by the database on every update to
    ``pr_content_items`` - and never a maximum computed over child tables; see
    the note in :func:`~meobot.application.pr_content_query._date_conditions`
    about what that does and does not cover.
    """

    CREATED_AT = "CREATED_AT"
    PLANNED_PUBLISH_AT = "PLANNED_PUBLISH_AT"
    UPDATED_AT = "UPDATED_AT"


class PrPeriodFact(StrEnum):
    """**Which business fact decides the month a card counts in.** Step 1F.2.3f.6.

    *Kỳ báo cáo* is one global month over the whole board, and the question it
    asks of each lane is *"when did this content enter / perform this
    operational step"*. That is a different fact per step, and this enum names
    the facts. There is deliberately **no** ``CREATED_AT`` member: a piece
    created on 28 August, cut on 3 September and posted on 8 September is
    September's production and September's publication, and classifying every
    lane by its creation date would make it disappear from both.

    The table below (:data:`STAGE_PERIOD_FACT`) is the whole mapping; the SQL
    that reads each fact is
    :func:`~meobot.application.pr_content_query.reporting_instant`, and nothing
    in a browser derives a month from a card.
    """

    #: The latest still-effective transition **into the row's current stage** -
    #: ``pr_content_transition_events.created_at`` for the newest row with
    #: ``to_stage = workflow_stage`` that is neither an ``UNDO`` edge nor
    #: reversed by one. An undone approval therefore does not restart the clock
    #: on the stage it was taken back to: the content never left it.
    #:
    #: Falls back to ``created_at`` **only** where the stage has no entry event:
    #: ``IDEA``, which content is created at and never transitions into, and
    #: rows whose last move predates the history table (migration 0022 backfilled
    #: nothing, deliberately). For those rows creation is the only persisted
    #: fact, and a card that no month could reach is worse than a card in the
    #: month it was created.
    STAGE_ENTRY = "STAGE_ENTRY"
    #: The same entry rule, with ``production_started_at`` before ``created_at``
    #: in the fallback chain. The column is stamped on the *first* entry into
    #: ``PRODUCTION`` and never moved - it answers "has this ever been
    #: produced" - so for a piece sent back and re-cut the transition history is
    #: the truer answer to "when did the current production start", and the
    #: column only speaks for rows that predate that history.
    PRODUCTION_ENTRY = "PRODUCTION_ENTRY"
    #: ``MIN(published_at)`` over the row's **active** publications - every
    #: status but ``REVERSED`` - the rule Step 1F.2.3f.4 settled and this step
    #: keeps. Falls back to the entry into ``PUBLISHED`` only when no active
    #: publication remains (a reversal that left the stage alone). Never
    #: ``created_at``: a month of publication that nothing recorded is not a
    #: fact to invent.
    PUBLICATION = "PUBLICATION"
    #: ``archived_at`` - stamped by the one method that writes ``ARCHIVED`` -
    #: with the entry into ``ARCHIVED`` as its only fallback. Never
    #: ``created_at``.
    ARCHIVE = "ARCHIVE"
    #: The entry into ``CANCELLED``. No ``created_at`` fallback, for the same
    #: reason as the two above: "cancelled in the month it was created" is a
    #: claim, and a cancelled row with no recorded cancellation makes none.
    CANCELLATION = "CANCELLATION"


#: **The one mapping from workflow stage to reporting fact.** Every stage is
#: present - the test suite asserts it - so a fourteenth stage is a failing test
#: rather than a lane the month silently ignores. Read by
#: :func:`~meobot.application.pr_content_query.reporting_instant` and nowhere
#: else; a second copy of this table, in the count query or in the browser, is
#: how "Đã đăng 67" ended up over a column of 27 cards.
#:
#: Keyed by **stage** rather than by lane on purpose. The two ``APPROVED`` lanes
#: are told apart by whether a producer has been named, and naming one is not a
#: transition: it leaves no dated row anywhere but the audit trail, which is
#: text and not authority (see migration 0022). So *Chờ nhận sản xuất* and *Sẵn
#: sàng sản xuất* both count in the month the script was approved for
#: production, which is the step both of them belong to.
#:
#: ``MEASURED`` is retired and reachable from nothing, but it is still a value
#: history rows may carry, so it is mapped rather than left to a ``KeyError``.
STAGE_PERIOD_FACT: Mapping[PrWorkflowStage, PrPeriodFact] = MappingProxyType(
    {
        PrWorkflowStage.IDEA: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.BRIEFING: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.SCRIPTING: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.AI_REVIEW: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.TEAM_LEAD_REVIEW: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.HEAD_REVIEW: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.APPROVED: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.PRODUCTION: PrPeriodFact.PRODUCTION_ENTRY,
        PrWorkflowStage.INTERNAL_REVIEW: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.READY_TO_PUBLISH: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.PUBLISHED: PrPeriodFact.PUBLICATION,
        PrWorkflowStage.MEASURED: PrPeriodFact.STAGE_ENTRY,
        PrWorkflowStage.ARCHIVED: PrPeriodFact.ARCHIVE,
        PrWorkflowStage.CANCELLED: PrPeriodFact.CANCELLATION,
    }
)


def period_fact_for(stage: PrWorkflowStage) -> PrPeriodFact:
    """Which fact decides the reporting month of a row at ``stage``.

    A function rather than a bare read for the reason :func:`stages_in_group`
    is one: a stage missing from the table fails here, loudly, rather than as
    a lane whose count is silently zero in every month.
    """
    return STAGE_PERIOD_FACT[stage]


def lane_period_fact(lane: PrContentLane) -> PrPeriodFact:
    """The reporting fact of one board column - its stage's, always.

    Two lanes over ``APPROVED`` share one answer, and that is documented on
    :data:`STAGE_PERIOD_FACT` rather than hidden here.
    """
    return period_fact_for(lane_stage(lane))


#: The ``period`` wire value that asks the server for the current business
#: month, explicitly. Kept as an opt-in for callers that want "this month"
#: without computing one; since Step 1F.2.3f.6d it is **not** the board's
#: default - a missing ``period`` means no month, and the current business
#: month reaches the browser as ``current_period`` on every board response
#: for the selector to anchor on.
PERIOD_CURRENT = "CURRENT"


#: The capabilities whose holder reviews other people's work rather than
#: producing their own. Read off :data:`APPROVAL_CAPABILITIES` rather than
#: listed, so a fourth gate is a reviewer capability without an edit here.
REVIEW_CAPABILITIES: frozenset[PrCapability] = frozenset(APPROVAL_CAPABILITIES.values())


#: Which workflow stage each review capability decides at. The inverse of
#: :data:`~meobot.domain.pr.workflow.STAGE_APPROVAL_GATES` composed with
#: :data:`~meobot.domain.pr.policy.APPROVAL_CAPABILITIES`, written **once**.
#:
#: Step 1F.2.7a. Three places needed this composition - the board's queue, the
#: dashboard's, and the Telegram pending list - and two of them had built it
#: themselves. A queue keyed on a capability the composition disagreed about is
#: a queue that shows the wrong gate, silently, to one client only.
STAGE_FOR_APPROVAL_CAPABILITY: Mapping[PrCapability, PrWorkflowStage] = MappingProxyType(
    {
        APPROVAL_CAPABILITIES[approval_stage]: stage
        for stage, approval_stage in STAGE_APPROVAL_GATES.items()
    }
)


def gate_stages_for(held: frozenset[PrCapability]) -> tuple[PrWorkflowStage, ...]:
    """The workflow stages this set of capabilities may actually decide at.

    Composed from the two domain mappings rather than a list of its own:
    :data:`~meobot.domain.pr.workflow.STAGE_APPROVAL_GATES` says which stage is
    which gate, and :data:`~meobot.domain.pr.policy.APPROVAL_CAPABILITIES` says
    which capability that gate needs. A stage survives only if the capability is
    held.

    Empty for somebody with no grant, whatever their role - including an
    ``OWNER``. Holding every ``Permission`` is not holding a grant, and a queue
    somebody cannot act on is a misleading queue. In workflow order, because
    ``STAGE_APPROVAL_GATES`` is.

    Every gate the pair of mappings agrees on is returned, and a holder of two
    grants gets two stages: the queues do not exclude each other, because the
    authorization they mirror does not either. ``INTERNAL_REVIEW`` needs no edit
    here when Step 1F.2.3 puts content through it - it is already in both
    mappings, so holding ``PR_INTERNAL_REVIEW`` already means holding that queue.
    """
    return tuple(
        stage
        for stage, approval_stage in STAGE_APPROVAL_GATES.items()
        if APPROVAL_CAPABILITIES[approval_stage] in held
    )


def gate_stage_for(capability: PrCapability) -> PrWorkflowStage | None:
    """The stage one review capability decides at, or ``None`` for the rest.

    ``None`` for the ten capabilities that are not review gates - which is the
    honest answer rather than a ``KeyError``, because the caller asking is
    walking a person's grants and only some of them are gates.
    """
    return STAGE_FOR_APPROVAL_CAPABILITY.get(capability)


def default_scope(held: frozenset[PrCapability]) -> PrContentViewScope:
    """Where somebody lands when they have not asked for a particular view.

    **``ALL``, for everybody.** Step 1F.2.3f.6a, and a reversal of Step
    1F.2.2's capability-derived default (``MY_ACTIONS`` for reviewers,
    ``MY_CONTENT`` for writers). What changed is the board itself: since Step
    1F.2.3f.6 it opens on **one reporting month**, so *Tất cả* is no longer
    "every row in the database" - it is this month's board, which is the
    screen a manager and a writer both orient by. The queue is one click away
    at *Cần tôi xử lý*, and it is deliberately not the landing page: a queue
    reads as "your work is only this", and the month's board is the context
    that work sits in.

    Still a function of the capabilities held, and still consulted from the
    one place the default is applied, so the signature stays - a future default
    that depends on a grant again needs no caller to change. ``held`` is
    currently unread.
    """
    del held
    return PrContentViewScope.ALL


__all__: list[str] = [
    "ARCHIVE_LANES",
    "ARCHIVE_STAGES",
    "GROUP_LANES",
    "GROUP_STAGES",
    "LANE_PRODUCTION_STATES",
    "LANE_STAGES",
    "PERIOD_CURRENT",
    "REVIEW_CAPABILITIES",
    "STAGE_FOR_APPROVAL_CAPABILITY",
    "STAGE_PERIOD_FACT",
    "UNCLASSIFIED_CONTENT_TYPE",
    "PrContentBoardView",
    "PrContentDateField",
    "PrContentGroup",
    "PrContentLane",
    "PrContentViewScope",
    "PrPeriodFact",
    "board_stages",
    "default_scope",
    "gate_stage_for",
    "gate_stages_for",
    "group_of_lane",
    "lane_period_fact",
    "lane_production_state",
    "lane_stage",
    "lanes_in_group",
    "period_fact_for",
    "stages_in_group",
]
