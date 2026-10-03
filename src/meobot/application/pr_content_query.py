"""One specification for "which content rows", and the SQL it becomes.

Step 1F.2.2. Before it, narrowing the content list meant adding a keyword
argument to ``PrQueryService.list_contents`` and a ``WHERE`` clause beside the
three already there, and the board's counts came from a *different* query that
knew about none of them - so a filtered board could say "Chờ duyệt: 120" over
seven visible cards. The counts were not wrong; they were answering a question
nobody had asked.

So there is one filter object, :class:`ContentQuery`, and one function that turns
it into predicates, :func:`content_conditions`. The list, the total and the
per-stage counts are three statements over the *same* conditions, which is what
makes them agree by construction rather than by review.

Where the responsibility model comes from
-----------------------------------------

Nothing here invents an ownership column. Every scope is built from a
relationship the schema already had, and this is the whole of it:

===================  =====================================================
"responsible for"    ``pr_content_items.owner_user_id`` - *"who is
                     accountable for this now"*, per Step 1A - **or** an
                     unfinished ``pr_task_assignments`` row against one of
                     the content's tasks.
"on my team"         An in-force ``pr_channel_assignments`` row for a
                     channel one of the content's targets points at.
"I am producing it"  ``pr_content_items.producer_user_id`` - Step 1F.2.3, and
                     deliberately **not** part of "responsible for": the owner
                     answers for the piece, the producer for one file, and
                     "Của tôi" keeps meaning the first. See
                     :func:`responsible_for`.
"I can act on it"    A gate the actor holds the grant for - *every* item
                     waiting there, not only theirs - or a draft they own at
                     an editable stage, or an unfinished task of theirs.
===================  =====================================================

The asymmetry in that last row is the point of it. Ownership and assignment
answer "whose work is this"; a review gate answers "what is this waiting for",
and the two are different questions. Content sitting at ``TEAM_LEAD_REVIEW`` is
waiting for a team-lead decision from *whoever may make one*, so intersecting
the gate with an ownership relationship would leave a lead's queue holding the
few items they happen to own and none of the ones they are the bottleneck for.

:func:`responsible_for` is that first row, written once and used by **both** the
``MY_CONTENT`` scope and the "Người phụ trách" filter. That is deliberate: a
dropdown that meant something subtly different from the tab above it would give
two different answers to "what is Nguyễn A working on", and only one of them
could be right.

Multi-target matching, and why ``EXISTS``
-----------------------------------------

One content item is published to several channels, so "on TikTok" and "on the
Apexmed TikTok channel" are questions about *any* of its targets. Each is an
``EXISTS`` correlated on ``pr_content_items.id``, which:

* matches an item once however many of its targets qualify - a ``JOIN`` would
  return the row twice for a piece going to two TikTok channels, and the
  ``count`` behind the board's tiles would then be larger than the list;
* does the work in the database. Loading every target into Python to filter
  there is the shape this module exists to avoid, and at fifty items a day with
  months of history it is the shape that stops working first.

No new index. ``pr_content_targets`` already indexes ``channel_id``,
``pr_channels`` already indexes ``platform_id``, ``pr_task_assignments`` already
has ``ix_pr_task_assignments_user_completed``, and ``pr_content_items`` already
indexes ``created_at`` implicitly through its primary key ordering plus
``planned_publish_at``. The ``EXISTS`` subqueries are index lookups on those
columns, so this step adds no migration - see
``docs/pr/STEP_1F22_CONTENT_VIEWS_AND_REVIEW_ROLES.md``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, case, false, func, null, or_, select
from sqlalchemy.sql.selectable import ScalarSelect

from meobot.application.pr_capability_service import PrCapabilityGrant
from meobot.application.pr_grant_scope_sql import approvable_by
from meobot.db.models.pr import (
    PrChannel,
    PrChannelAssignment,
    PrContentItem,
    PrContentTarget,
    PrTask,
    PrTaskAssignment,
)
from meobot.db.models.pr_reporting import PrPublication
from meobot.db.models.pr_transition import PrContentTransitionEvent
from meobot.domain.pr.content_views import (
    ARCHIVE_STAGES,
    STAGE_PERIOD_FACT,
    PrContentBoardView,
    PrContentDateField,
    PrContentGroup,
    PrContentLane,
    PrContentViewScope,
    PrPeriodFact,
    board_stages,
    lane_production_state,
    lane_stage,
    stages_in_group,
)
from meobot.domain.pr.models import (
    PrContentType,
    PrPriority,
    PrProductionHandoff,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.priority import PRIORITY_RANK
from meobot.domain.pr.production import HANDOFF_STAGES, handoff_state
from meobot.domain.pr.reporting import ACTIVE_PUBLICATION_STATUSES
from meobot.domain.pr.workflow import EDITABLE_STAGES, TERMINAL_TASK_STATUSES, PrTransitionTrigger

#: The two stages a producer can hold a piece at, in enum order. ``APPROVED`` is
#: there because of Step 1F.2.3b: the handoff happens before production starts,
#: so "my production work" begins at the moment somebody takes the job rather
#: than at the moment they begin it.
_HANDOFF_IN_ORDER: tuple[PrWorkflowStage, ...] = tuple(
    stage for stage in PrWorkflowStage if stage in HANDOFF_STAGES
)

#: ``EDITABLE_STAGES`` in the enum's own order, so a rendered ``IN`` clause is
#: stable across runs. The set is the authority; this is only its ordering.
_EDITABLE_IN_ORDER: tuple[PrWorkflowStage, ...] = tuple(
    stage for stage in PrWorkflowStage if stage in EDITABLE_STAGES
)

#: Task statuses nothing moves out of, ordered for the same reason.
_TERMINAL_TASKS = sorted(TERMINAL_TASK_STATUSES, key=lambda status: status.value)


@dataclass(frozen=True, slots=True)
class ActorWorkQueue:
    """What one actor can actually act on, resolved from their capabilities.

    Passed into :func:`content_conditions` rather than looked up there, for the
    reason ``PrQueryService.content_awaiting`` already gives: which gates a
    person may decide at is
    :class:`~meobot.application.pr_capability_service.PrCapabilityService`'s
    answer, and a second module deriving it would be a second authority.

    All three fields are empty or ``None`` for somebody with no grants, which
    makes ``MY_ACTIONS`` correctly empty rather than accidentally wide.
    """

    #: ``users.id`` of the actor, or ``None`` for a principal with no user row.
    #: Every personal scope needs one; without it they match nothing.
    user_id: uuid.UUID | None
    #: The grants that currently authorise this actor at a review gate, from
    #: :meth:`~meobot.application.pr_capability_service.PrCapabilityService.approval_grants_for`.
    #:
    #: Step 1F.2.7a replaced a tuple of stages with the grants themselves, and
    #: the difference is the whole fix: a stage list says *Head Review is your
    #: queue* and a grant says *Head Review, for these classifications, on these
    #: channels*. The first put items in front of people the write path would
    #: then refuse.
    approval_grants: tuple[PrCapabilityGrant, ...] = ()
    #: Whether they hold ``PR_CONTENT_EDIT``, which is what makes an owned draft
    #: at an editable stage something they can act on rather than only watch.
    may_edit: bool = False
    #: Whether they hold ``PR_PRODUCTION_EXECUTE``. Step 1F.2.3, and what makes
    #: an *unclaimed* production visible in their queue - see
    #: :func:`_executable_by`. Production already assigned to them is theirs
    #: whatever they hold, so this bears only on the unclaimed branch.
    may_produce: bool = False
    #: Whether they hold ``PR_PRODUCTION_ASSIGN``. Step 1F.2.3b: arranging the
    #: handoff is management's work, so an approved piece nobody has taken is in
    #: their queue until somebody does.
    may_assign: bool = False
    #: The capability set the two fields above were derived from. Carried so the
    #: default-scope decision can be taken from the same lookup rather than a
    #: second one, and empty when no lookup was needed (an ``ALL`` request).
    held: frozenset[PrCapability] = frozenset()

    @classmethod
    def for_capabilities(
        cls,
        user_id: uuid.UUID | None,
        *,
        held: frozenset[PrCapability],
        approval_grants: Sequence[PrCapabilityGrant] = (),
    ) -> ActorWorkQueue:
        """Build from a resolved capability set and the grants behind it.

        Both come from
        :class:`~meobot.application.pr_capability_service.PrCapabilityService`
        and neither is derived here: ``held`` decides which *kinds* of work are
        in this queue at all, and ``approval_grants`` decides which items the
        review kind reaches.
        """
        return cls(
            user_id=user_id,
            approval_grants=tuple(approval_grants),
            may_edit=PrCapability.PR_CONTENT_EDIT in held,
            may_produce=PrCapability.PR_PRODUCTION_EXECUTE in held,
            may_assign=PrCapability.PR_PRODUCTION_ASSIGN in held,
            held=held,
        )


@dataclass(frozen=True, slots=True)
class ContentQuery:
    """Everything that narrows a content list, in one place.

    Every field defaults to "not filtering", so an unfiltered list is
    ``ContentQuery()`` and a caller adds only what it means. Composition is
    conjunction throughout: each field that is set narrows what the previous ones
    left, so *Cần tôi xử lý + Hôm nay + TikTok + Apexmed TikTok + Nguyễn A* is
    the intersection and not four independent lists.

    ``scope`` is ``None`` for "the server decides", which is how the default in
    :func:`~meobot.domain.pr.content_views.default_scope` reaches somebody who
    has not chosen a view. A client that *has* chosen sends the scope, and gets
    it.
    """

    #: ``None`` means "apply this actor's default". Never silently ``ALL``.
    scope: PrContentViewScope | None = None
    #: Which phase of the pipeline, per
    #: :data:`~meobot.domain.pr.content_views.GROUP_STAGES`. ``None`` is every
    #: group, which is what the flat list and the Telegram tools want.
    #:
    #: Step 1F.2.3c1, and one of the two fields the *counts* deliberately ignore
    #: - see :meth:`ContentQuery.for_counts` and
    #: :meth:`~meobot.application.pr_query_service.PrQueryService.content_page`.
    group: PrContentGroup | None = None
    #: One column of the board, per
    #: :class:`~meobot.domain.pr.content_views.PrContentLane`. ``None`` is every
    #: lane, which is what the flat list and the Telegram tools want.
    #:
    #: Step 1F.2.3c2, and the same correction as ``group`` one level deeper: a
    #: group is four queues stacked into one page, so paginating the group still
    #: let the 155 items waiting for a producer occupy every slot the three
    #: actually-in-production items needed. A lane narrows before ``LIMIT`` for
    #: exactly that reason, and each column of a board pages on its own.
    #:
    #: It **intersects** with ``group`` and with ``stage`` rather than
    #: overriding either: ``group=PREPARATION`` with ``lane=IN_PRODUCTION`` is
    #: empty, because that is what was asked for. See the note on ``stage``.
    lane: PrContentLane | None = None
    #: One stage, and it **intersects** with ``group`` rather than replacing it:
    #: ``group=PREPARATION`` with ``stage=HEAD_REVIEW`` is empty, because that is
    #: what the person asked for. Silently dropping one of the two would answer a
    #: question nobody put.
    stage: PrWorkflowStage | None = None
    brand_id: uuid.UUID | None = None
    #: Strictly ``owner_user_id``. Predates this step and is kept for the
    #: Telegram tool and the existing route contract.
    owner_user_id: uuid.UUID | None = None
    #: The broader "Người phụ trách" - owner *or* task assignee, per
    #: :func:`responsible_for`. Prefer this one in new callers.
    responsible_user_id: uuid.UUID | None = None
    #: Matches when **any** target points at this channel.
    channel_id: uuid.UUID | None = None
    #: Matches when **any** target's channel sits on this platform. Never
    #: inferred from a channel's name.
    platform_id: uuid.UUID | None = None
    date_field: PrContentDateField = PrContentDateField.CREATED_AT
    #: Inclusive, as a **calendar day in the business timezone** - so "Hôm nay"
    #: is the day people are having, not a UTC window offset from it.
    date_from: date | None = None
    #: Inclusive. ``date_from == date_to`` is one day, which is what a person
    #: picking a single date means.
    date_to: date | None = None
    #: Substring of code or title. Composes with everything else, unlike the
    #: legacy ``search`` path on ``/contents`` - see ``PrQueryService``.
    search: str | None = None
    #: One urgency level. Step 1F.2.3d, and a filter like every other one here:
    #: it narrows before the page is cut, so *Rất gấp* asked of a 146-item board
    #: returns the critical items whole rather than whichever of them fell in the
    #: first sixty rows.
    #:
    #: Single-valued on purpose. Every other enum filter on this object is, the
    #: panel's control is a ``<select>`` like the rest of the bar, and "Rất gấp
    #: **or** Gấp" has not been asked for - a multi-select would be a second
    #: shape of filter in the query object for a question nobody has put yet.
    priority: PrPriority | None = None
    #: One content format. Step 1F.2.3e, and a filter like every other one here:
    #: it narrows before the page is cut.
    #:
    #: ``None`` means "every format **including** the unclassified ones", which
    #: is what an unfiltered board shows. Asking for the unclassified ones
    #: specifically is :attr:`unclassified_content_type`, below - a separate flag
    #: rather than a magic member of the enum, because *absent* is not a format
    #: and putting it in :class:`~meobot.domain.pr.models.PrContentType` would
    #: mean every create form had to hide one of its own values.
    content_type: PrContentType | None = None
    #: *Chưa phân loại*: the historical rows that predate the column.
    #:
    #: Mutually exclusive with ``content_type`` in practice - the panel's one
    #: dropdown cannot produce both - and if both arrive the conjunction is
    #: empty, which is the honest answer to "Facebook posts that have no format".
    unclassified_content_type: bool = False
    #: **Kỳ báo cáo - the reporting month the whole board is read against.**
    #: Step 1F.2.3f.6, and the first day of that month - ``date(2026, 9, 1)``
    #: for *"Kỳ 09/2026"*.
    #:
    #: One month for every group and every lane, and a **different fact per
    #: lane** deciding whether a row belongs to it - see
    #: :data:`~meobot.domain.pr.content_views.STAGE_PERIOD_FACT` and
    #: :func:`reporting_instant`. *Sản xuất* in September is the content whose
    #: current production started in September; *Đã đăng* is what actually went
    #: out in September; *Lưu trữ* is what was archived in it. Nothing here is
    #: ``created_at`` for every lane, and nothing here is ``updated_at``.
    #:
    #: Deliberately **not** one of the date filters above. ``date_from`` /
    #: ``date_to`` narrow whatever the person is looking at on whichever
    #: dimension they picked; this decides which month's board they are
    #: looking at. They compose - September's *Đã đăng*, narrowed to the last 7
    #: days by ``UPDATED_AT`` - and neither redefines the other.
    #:
    #: ``None`` - **the board's default since Step 1F.2.3f.6d** - applies no
    #: month at all: every lane is cumulative over the active board, and
    #: :func:`content_conditions` builds no reporting expression, so a
    #: no-period request never evaluates a transition history or a publication
    #: instant for the sake of a filter nobody asked for. A month is an
    #: explicit, optional filter the person chooses.
    period_month: date | None = None
    #: *"Kỳ hiện tại"*: resolve :attr:`period_month` to the current business
    #: month at query time. The wire form is ``period=CURRENT`` - see
    #: :data:`~meobot.domain.pr.content_views.PERIOD_CURRENT`. An explicit
    #: opt-in for a caller that wants "this month" without naming it; **not**
    #: what a missing ``period`` means, which is no month. Resolved by
    #: :meth:`with_period`, in the one place ``today`` is already known.
    current_period: bool = False
    #: **Which board.** Step 1F.2.3f.6c. ``ACTIVE`` is the operational board
    #: and excludes ``ARCHIVED`` outright - one clause, and a reporting-month
    #: expression with no ``archived_at`` arm in it; ``ARCHIVE`` is the
    #: archived rows alone. ``None`` is "no opinion" and is what the flat list
    #: and the Telegram tools keep: they filter by ``stage`` when they mean a
    #: stage, and a caller asking for ``stage=ARCHIVED`` there must still get
    #: it. The board route resolves ``None`` to ``ACTIVE``.
    view: PrContentBoardView | None = None
    limit: int = 50
    offset: int = 0

    def with_scope(self, scope: PrContentViewScope) -> ContentQuery:
        """A copy with ``scope`` resolved. Used once the default is known."""
        return replace(self, scope=scope)

    def with_period(self, today: date) -> ContentQuery:
        """A copy with *Kỳ hiện tại* resolved against ``today``.

        ``today`` is the business-timezone day the service already computed for
        the dated grants, so the current month is derived **once**, from one
        calendar, and the same value is echoed back to the client. An explicit
        month wins over the flag: a caller that named a month meant it.
        """
        if not self.current_period or self.period_month is not None:
            return replace(self, current_period=False)
        return replace(self, period_month=today.replace(day=1), current_period=False)

    def for_counts(self) -> ContentQuery:
        """The same question, asked of every group and every lane.

        What the board's **tab counts and lane counts** are about. *Chuẩn bị 5 ·
        Chờ duyệt 86 · Sản xuất 46* has to describe the dataset the person is
        filtering, not the group they are standing in - counted under the
        selected group, four of those five numbers would be zero and the tabs
        would stop being navigable the moment somebody used them. The per-lane
        figures behind them are the same argument again: *Đang sản xuất 3* has to
        say 3 while the person is reading *Chờ nhận sản xuất*.

        Both narrowings come off, and they come off together. Dropping only the
        group would leave every count describing one column, which is the number
        that is already on that column's own header.

        **The reporting month stays on.** Step 1F.2.3f.6: *Kỳ báo cáo* is a
        dimension of the dataset like the scope and the dates, not a narrowing
        of the board, so the figure over *Đã đăng* is September's 18 and not the
        cumulative 67 - the mismatch this step exists to have fixed.

        The list and its ``total`` use the full query, group and lane included.
        The two count scopes are different on purpose, and this is the seam.
        """
        return replace(self, group=None, lane=None)


# --- The responsibility model, written once ----------------------------------


def responsible_for(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Content this person is meaningfully responsible for.

    The union of the two authoritative relationships, and **no third one**:

    * they are ``owner_user_id`` - Step 1A's *"who is accountable for this
      now"*, as against ``created_by_user_id``, which answers "who started it"
      and does not move when responsibility does;
    * they hold an unfinished ``pr_task_assignments`` row against one of the
      item's tasks. A writer who was handed the script is responsible for the
      piece even though the item's owner is their lead.

    Creation is deliberately not included. ``created_by_user_id`` is a
    historical fact by design and never changes hands, so a list built on it
    would keep showing somebody work they handed over months ago.

    **Production is deliberately not included either.** Step 1F.2.3 considered
    it and kept the two apart: "Người phụ trách" is a question about the piece
    and ``producer_user_id`` is a question about one file, and folding the
    second into the first would silently redefine a filter and a tab that people
    already use - a lead filtering "Nguyễn A" would start seeing pieces Nguyễn A
    is only editing, with no way to ask the narrower question again. The producer
    reaches their work through ``MY_ACTIONS``, which is where "what should I be
    doing" lives, and the panel shows the two fields separately.
    """
    return or_(PrContentItem.owner_user_id == user_id, _has_open_task_for(user_id))


def _has_open_task_for(user_id: uuid.UUID) -> ColumnElement[bool]:
    """An unfinished task of this person's, on this content item.

    "Unfinished" is two conditions, because they mean different things: the
    person's own ``completed_at`` is null (*they* have not finished), and the
    task is not at a status nothing moves out of (nobody has). A task cancelled
    under somebody still shows an open assignment row, and it is not work.
    """
    return (
        select(PrTaskAssignment.id)
        .join(PrTask, PrTask.id == PrTaskAssignment.task_id)
        .where(
            PrTask.content_id == PrContentItem.id,
            PrTaskAssignment.user_id == user_id,
            PrTaskAssignment.completed_at.is_(None),
            PrTask.status.not_in(_TERMINAL_TASKS),
        )
        .exists()
    )


def _targets_channel(channel_id: uuid.UUID) -> ColumnElement[bool]:
    """Any target of this item points at ``channel_id``."""
    return (
        select(PrContentTarget.id)
        .where(
            PrContentTarget.content_id == PrContentItem.id,
            PrContentTarget.channel_id == channel_id,
        )
        .exists()
    )


def _targets_platform(platform_id: uuid.UUID) -> ColumnElement[bool]:
    """Any target of this item sits on ``platform_id``.

    Through ``pr_content_targets -> pr_channels -> pr_platforms``, which is the
    canonical path. A channel's *name* often contains the platform and is never
    consulted: "Apexmed TikTok" on a mis-registered channel row would answer the
    wrong question, confidently.
    """
    return (
        select(PrContentTarget.id)
        .join(PrChannel, PrChannel.id == PrContentTarget.channel_id)
        .where(
            PrContentTarget.content_id == PrContentItem.id,
            PrChannel.platform_id == platform_id,
        )
        .exists()
    )


def _on_team_channel(user_id: uuid.UUID, *, on: date) -> ColumnElement[bool]:
    """A target of this item is on a channel assigned to this person on ``on``.

    "Assigned" reads the dated interval the way Step 1A defined it: closed and
    inclusive, so ``effective_to`` is the last day in force and a row ending
    today still counts today. Any :class:`PrChannelAssignmentRole` qualifies -
    the analytics owner of a channel is on the team for it, and asking which
    roles count would be inventing a hierarchy this step was told not to build.
    """
    return (
        select(PrContentTarget.id)
        .join(PrChannelAssignment, PrChannelAssignment.channel_id == PrContentTarget.channel_id)
        .where(
            PrContentTarget.content_id == PrContentItem.id,
            PrChannelAssignment.user_id == user_id,
            PrChannelAssignment.effective_from <= on,
            or_(
                PrChannelAssignment.effective_to.is_(None),
                PrChannelAssignment.effective_to >= on,
            ),
        )
        .exists()
    )


def _executable_by(queue: ActorWorkQueue, *, today: date) -> ColumnElement[bool]:
    """Content this actor has a next action on, from what they may do.

    Five ways in. The first is **the review queue and asks nothing about who
    the work belongs to**; the rest are personal:

    * the item stands at a gate in :attr:`ActorWorkQueue.gate_stages`, i.e. one
      the actor holds the grant for. *Every* item waiting there is theirs to act
      on, whoever owns it, whoever the tasks are assigned to and whichever
      channel it targets - because the decision the item is waiting for is one
      this actor is authorized to make, and the only question a queue can
      usefully answer is "am I what this is waiting for". Requiring ownership
      here would have hidden a lead's own queue from them, since a reviewer
      owns almost nothing they review. It is still not ``stage IN (...)`` with a
      friendly name: the stages come from the grant, so somebody without the
      Head grant never sees ``HEAD_REVIEW`` items, whatever their role, and
      somebody holding two grants sees both queues at once;
    * they own it and it is at a stage
      :data:`~meobot.domain.pr.workflow.EDITABLE_STAGES` admits, and they hold
      ``PR_CONTENT_EDIT`` - so the draft is theirs to write, and the queue is
      also where "cần sửa" work lands, since a revision request sends content
      back to ``SCRIPTING``;
    * an unfinished task of theirs hangs off it;
    * **they are the producer, at either handoff stage.** Step 1F.2.3, widened
      by 1F.2.3b to include ``APPROVED``: somebody was handed an edit and has
      either not started it or not filed it. No capability is asked, because
      being the producer *is* the assignment - taking the item out of their queue
      because a grant lapsed would hide work they are still expected to hand in;
    * **nobody has claimed it, and they could.** The one branch that is neither
      a gate nor an assignment, and the only one that needs an argument.

    Why unclaimed production is not shown to everybody
    --------------------------------------------------

    An unclaimed edit is genuinely actionable by anyone holding
    ``PR_PRODUCTION_EXECUTE``, and every ``EMPLOYEE`` holds it. Putting every
    unclaimed piece in every member's "Cần tôi xử lý" would push each person's
    own work off the screen with other teams' backlogs, which is the failure
    Step 1F.2.2 existed to fix.

    So the branch is narrowed to people the piece actually concerns: whoever is
    responsible for it - the writer whose approved script is now waiting - and
    whoever answers for one of its channels, the *display* relation the ``TEAM``
    scope already uses. Management sees all of them through the branch below,
    because arranging the handoff is their job. That narrowing is **not**
    authorization. Step 1F.2.2's
    distinction holds exactly: somebody sent a link to a piece outside their
    channels may still claim it, and
    :meth:`~meobot.application.pr_production_service.PrProductionService.claim_production`
    will let them. They simply were not shown it unasked.

    ``false()`` when none applies. An actor with no grants and no assignments has
    an empty queue, and that is the honest answer - a queue padded out with rows
    they cannot move would train them to ignore it.

    The gate clause is built before ``user_id`` is consulted, and not inside the
    identity check the two personal clauses need. Today the distinction is
    theoretical - a review capability is grant-backed, so a principal with no
    user row holds none - but the *reason* the two differ is not: the gate
    branch is authorization, and reading it through an identity would be the
    second permission system this scope is careful not to become.
    """
    clauses: list[ColumnElement[bool]] = []
    if queue.approval_grants:
        # Step 1F.2.7a. The gate branch is now the grants' own scopes, translated
        # by ``approvable_by`` - the same rule ``PrCapabilityService.can_approve``
        # applies at the write, expressed over columns so it narrows the query
        # rather than the page. See ``pr_grant_scope_sql``.
        clauses.append(approvable_by(queue.approval_grants))
    if queue.user_id is not None:
        clauses.append(_has_open_task_for(queue.user_id))
        clauses.append(
            and_(
                PrContentItem.workflow_stage.in_(list(_HANDOFF_IN_ORDER)),
                PrContentItem.producer_user_id == queue.user_id,
            )
        )
        if queue.may_edit:
            clauses.append(
                and_(
                    PrContentItem.owner_user_id == queue.user_id,
                    PrContentItem.workflow_stage.in_(list(_EDITABLE_IN_ORDER)),
                )
            )
        if queue.may_produce:
            clauses.append(
                and_(
                    PrContentItem.workflow_stage.in_(list(_HANDOFF_IN_ORDER)),
                    PrContentItem.producer_user_id.is_(None),
                    or_(
                        # Their own piece, waiting for somebody to take it. The
                        # writer whose script was just approved is exactly who
                        # should see "chờ nhận sản xuất" first.
                        responsible_for(queue.user_id),
                        _on_team_channel(queue.user_id, on=today),
                    ),
                )
            )
        if queue.may_assign:
            # Step 1F.2.3b. Arranging the handoff is management's own work, so an
            # unclaimed piece is in their queue whoever it belongs to. Bounded by
            # the capability rather than by a relationship on purpose: somebody
            # has to be answerable for the pieces nobody has picked up, and the
            # people who can answer are exactly the ones who may assign.
            clauses.append(
                and_(
                    PrContentItem.workflow_stage.in_(list(_HANDOFF_IN_ORDER)),
                    PrContentItem.producer_user_id.is_(None),
                )
            )
    if not clauses:
        return false()
    return or_(*clauses)


# --- Dates -------------------------------------------------------------------


def day_bounds(
    date_from: date | None, date_to: date | None, *, tz: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    """A pair of calendar days as the half-open UTC instant range they name.

    The stored columns are ``DateTime(timezone=True)`` in UTC and the dates come
    from a person looking at a Vietnamese calendar, so the conversion has to
    happen somewhere; doing it here means no caller compares a date to a
    timestamp and hopes.

    ``[from 00:00 local, to+1 day 00:00 local)`` - lower bound inclusive, upper
    exclusive, which is what makes ``date_to`` **inclusive of its whole day**
    without naming 23:59:59 and losing the last second. Note the asymmetry is in
    the instants, not in the dates: both dates a person types are included.

    Why not a naive comparison: 2026-08-10 in ``Asia/Ho_Chi_Minh`` starts at
    2026-08-09T17:00Z. Filtering ``created_at >= 2026-08-10T00:00Z`` would drop
    everything created in the first seven hours of the Vietnamese working day -
    which is most of a morning, every day, silently.
    """
    lower = (
        datetime.combine(date_from, time.min, tzinfo=tz).astimezone(UTC)
        if date_from is not None
        else None
    )
    upper = (
        (datetime.combine(date_to, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(UTC)
        if date_to is not None
        else None
    )
    return lower, upper


def _date_conditions(
    field: PrContentDateField, lower: datetime | None, upper: datetime | None
) -> list[ColumnElement[bool]]:
    """The half-open range applied to whichever date dimension was chosen.

    ``created_at`` is never null so a range on it is a plain window;
    ``planned_publish_at`` is nullable, and a row with no plan falls out of any
    range on it. That is correct - it has no planned date to be inside the window
    - and it is why ``CREATED_AT`` is the default rather than this one.

    ``updated_at`` is the row's own timestamp, maintained by
    :class:`~meobot.db.base.TimestampMixin` with ``onupdate=func.now()``, so it
    is non-null and moves whenever a column of ``pr_content_items`` changes: a
    revision, a retriage, a priority or content-type change, a producer
    assignment, and every workflow transition - which is how approvals,
    production submissions and publication registrations reach it, since each
    goes through ``PrWorkflowService.apply``.

    **What it deliberately does not cover.** Writes that touch only a child
    table leave the item row alone, so a comment, a resource attachment or a
    publication *edit* does not move it. That is the existing meaning of the
    column rather than something chosen here, and changing it - to a maximum
    over child tables, or to a touch on every child write - would be redefining
    ``updated_at`` for every other reader of it. This filter reports the column
    as it stands.
    """
    column = (
        PrContentItem.planned_publish_at
        if field is PrContentDateField.PLANNED_PUBLISH_AT
        else PrContentItem.updated_at
        if field is PrContentDateField.UPDATED_AT
        else PrContentItem.created_at
    )
    conditions: list[ColumnElement[bool]] = []
    if lower is not None:
        conditions.append(column >= lower)
    if upper is not None:
        conditions.append(column < upper)
    return conditions


# --- One column of the board, as a predicate ---------------------------------

#: Stands in for "somebody is named as the producer" when the only thing being
#: asked is whether the column was null. Never dereferenced -
#: :func:`~meobot.domain.pr.production.handoff_state` asks only whether it is
#: ``None`` - and passing a sentinel is what lets a lane's producer clause be
#: *read out of* the domain rule instead of being a second copy of it.
_A_PRODUCER: object = object()


def published_instant() -> ScalarSelect[datetime]:
    """**When a content item went out**, as a correlated scalar. Step 1F.2.3f.4.

    ``MIN(published_at)`` over that item's **active** publications - every status
    but ``REVERSED``, read from
    :data:`~meobot.domain.pr.reporting.ACTIVE_PUBLICATION_STATUSES` rather than
    spelled out, so a fifth status is active here for the same reason it is
    active everywhere else.

    ## Why the minimum, and why this is a derivation rather than a column

    There is no item-level publication timestamp in the schema, because there is
    no item-level publication: ``pr_publications`` holds **one row per channel**,
    each with its own instant. So "which month was this published in" has to be
    derived, and the minimum is the answer that matches what the rest of the
    module already means by *published*: registering the first publication is
    what moves a piece to ``PUBLISHED``, and
    :meth:`~meobot.application.pr_publication_service.PrPublicationService.reverse_publication`
    keeps it there while **any** active publication remains. A piece that went to
    Facebook on 28 August and TikTok on 3 September has been published since
    August, and August is the month it counts in.

    ``NULL`` is reachable and is not an error: a reversal can leave the stage at
    ``PUBLISHED`` when a metric snapshot hangs off the content, and imported rows
    may predate the publication table. It means *published, instant unknown* -
    :func:`reporting_instant` then falls back to the entry into ``PUBLISHED``,
    and to nothing after that.

    **Retroactively mutable**, and worth knowing:
    ``PrPublicationService.update_publication`` can move a ``published_at``, so a
    card can change month later. That is correct - the fact was corrected - and
    it is why this is computed at read time rather than stamped once.
    """
    return (
        select(func.min(PrPublication.published_at))
        .where(
            PrPublication.content_id == PrContentItem.id,
            PrPublication.status.in_(tuple(ACTIVE_PUBLICATION_STATUSES)),
        )
        .correlate(PrContentItem)
        .scalar_subquery()
    )


def stage_entry_instant() -> ScalarSelect[datetime]:
    """**When a row entered the stage it is at now**, as a correlated scalar.

    Step 1F.2.3f.6. ``MAX(created_at)`` over the row's transition events whose
    ``to_stage`` is the row's **current** ``workflow_stage``, excluding:

    * ``UNDO`` edges - an undo puts the content *back* where it was, and the
      stay it returns to began when the content first arrived there. Counting
      the undo as an arrival would move a piece into the month somebody
      corrected a mistake in;
    * reversed edges - a transition that has been taken back is not the one that
      put the content where it is. This is the same reading
      :meth:`~meobot.application.pr_undo_service.PrUndoService._latest_effective`
      applies, for the same reason.

    ``to_stage`` is compared to the row's own column rather than to a stage the
    caller names, so one expression serves every lane and no lane can be handed
    the wrong stage. ``NULL`` when there is no such event, which is exactly two
    cases: ``IDEA``, which content is created at and never moves into, and rows
    whose last move predates ``pr_content_transition_events`` (migration 0022
    backfilled nothing). Which fallback applies then is
    :func:`reporting_instant`'s decision, per fact, not this function's.

    Cost: one index lookup on ``(content_id, created_at)`` per row the outer
    ``WHERE`` reaches - no history is loaded, and nothing is done per row in
    Python.
    """
    return (
        select(func.max(PrContentTransitionEvent.created_at))
        .where(
            PrContentTransitionEvent.content_id == PrContentItem.id,
            PrContentTransitionEvent.to_stage == PrContentItem.workflow_stage,
            PrContentTransitionEvent.trigger != PrTransitionTrigger.UNDO,
            PrContentTransitionEvent.reversed_by_event_id.is_(None),
        )
        .correlate(PrContentItem)
        .scalar_subquery()
    )


def _stage_is(stages: Sequence[PrWorkflowStage]) -> ColumnElement[bool]:
    """``workflow_stage IN (...)`` - or ``=`` for one - over the given stages."""
    if len(stages) == 1:
        return PrContentItem.workflow_stage == stages[0]
    return PrContentItem.workflow_stage.in_(list(stages))


def reporting_instant(
    view: PrContentBoardView | None = None,
) -> ColumnElement[datetime | None]:
    """**The instant *Kỳ báo cáo* reads a row against**, decided by its stage.

    Step 1F.2.3f.6, and the one place the lane-to-timestamp mapping becomes
    SQL. Read off :data:`~meobot.domain.pr.content_views.STAGE_PERIOD_FACT` -
    the table is the authority, this function has no opinion of its own, and a
    stage the table forgot is a test failure there rather than a silent
    ``NULL`` here.

    ## Shape: one ``COALESCE``, not one ``CASE`` arm per stage

    Every fact in the table is the same three-step chain with different
    members: *a stage-specific first fact* (the publication instant, the
    archive instant, or nothing), then *the entry into the current stage*,
    then *a stage-specific last resort* (``production_started_at`` and/or
    ``created_at``, or nothing). So the expression is::

        COALESCE(
            CASE stage WHEN PUBLISHED THEN MIN(published_at)
                       WHEN ARCHIVED  THEN archived_at END,
            <entry into the current stage>,
            CASE stage WHEN terminal   THEN NULL
                       WHEN PRODUCTION THEN COALESCE(production_started_at, created_at)
                       ELSE created_at END,
        )

    and the entry subquery appears **once** per comparison rather than once
    per stage. That is not cosmetic. Step 1F.2.3f.6c measured the previous
    form - one correlated subquery per arm, twenty-eight in the two bounds of
    a month predicate - on a 20 000-row PostgreSQL: the rows cost ~100 ms and
    JIT-compiling the 333 functions the planner generated for them cost 4.4 s
    on every board request. This form has two subplans per bound, and
    ``CASE`` still evaluates lazily, so a ``SCRIPTING`` row never runs the
    publication aggregate.

    ``view`` decides which first facts exist at all: the operational board
    never holds an ``ARCHIVED`` row, so ``archived_at`` is not in its
    statement; the archive view is one stage, so its expression is
    ``COALESCE(archived_at, entry)`` and nothing else. ``None`` keeps every
    fact, for a caller with no view.

    ## What it means, and what it deliberately does not

    The board stays a **current-workflow** board. A piece created in August,
    cut in September and posted in September is at ``PUBLISHED``; in September
    it is in *Đã đăng* and nowhere else, and in August it is nowhere - its
    current stage's fact is a September fact. This is a filter on the lane the
    row is in now, not a reconstruction of where it stood on 31 August, and
    that limitation is stated in ``docs/pr/STEP_1F23F6_REPORTING_PERIOD.md``
    rather than papered over.
    """
    stages = tuple(STAGE_PERIOD_FACT) if view is None else board_stages(view)
    facts = {stage: STAGE_PERIOD_FACT[stage] for stage in stages}
    entered = stage_entry_instant()

    def stages_with(*wanted: PrPeriodFact) -> tuple[PrWorkflowStage, ...]:
        return tuple(stage for stage, fact in facts.items() if fact in wanted)

    published = stages_with(PrPeriodFact.PUBLICATION)
    archived = stages_with(PrPeriodFact.ARCHIVE)
    terminal = stages_with(
        PrPeriodFact.PUBLICATION, PrPeriodFact.ARCHIVE, PrPeriodFact.CANCELLATION
    )
    production = stages_with(PrPeriodFact.PRODUCTION_ENTRY)

    if set(facts.values()) == {PrPeriodFact.ARCHIVE}:
        # The archive view: one stage, one fact, no CASE.
        return func.coalesce(PrContentItem.archived_at, entered)

    first_arms: list[tuple[ColumnElement[bool], Any]] = []
    if published:
        first_arms.append((_stage_is(published), published_instant()))
    if archived:
        first_arms.append((_stage_is(archived), PrContentItem.archived_at))
    first = case(*first_arms, else_=null()) if first_arms else null()

    last_arms: list[tuple[ColumnElement[bool], Any]] = []
    if terminal:
        # A terminal lane makes a claim about a dated event: nothing recorded,
        # nothing invented. ``NULL`` here is the whole of that policy.
        last_arms.append((_stage_is(terminal), null()))
    if production:
        last_arms.append(
            (
                _stage_is(production),
                func.coalesce(PrContentItem.production_started_at, PrContentItem.created_at),
            )
        )
    last = case(*last_arms, else_=PrContentItem.created_at)

    return func.coalesce(first, entered, last)


def view_conditions(view: PrContentBoardView | None) -> list[ColumnElement[bool]]:
    """Which board, as ``WHERE`` clauses. Step 1F.2.3f.6c.

    ``ACTIVE`` is one inequality, ``workflow_stage <> 'ARCHIVED'`` - the
    cheapest predicate that removes the archive from every statement the board
    runs, and the one that lets :func:`reporting_instant` drop the archive arm
    without a row ever reaching an ``ELSE`` it was not meant for. ``ARCHIVE``
    is the matching ``IN``. ``None`` adds nothing.

    Read from :data:`~meobot.domain.pr.content_views.ARCHIVE_STAGES` rather
    than spelling the stage, so a second archival stage changes one table.
    """
    if view is None:
        return []
    archived = sorted(ARCHIVE_STAGES, key=lambda stage: stage.value)
    if view is PrContentBoardView.ARCHIVE:
        return [PrContentItem.workflow_stage.in_(archived)]
    if len(archived) == 1:
        return [PrContentItem.workflow_stage != archived[0]]
    return [PrContentItem.workflow_stage.not_in(archived)]


def period_bounds(month: date, *, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """One calendar month as the half-open instant range it names.

    ``[first day 00:00 local, first day of next month 00:00 local)``, through
    the **same** :func:`day_bounds` the date filters use - so there is one
    business-timezone calendar in this module and not two, and 22:00 UTC on 31
    August is September's here exactly as it is for a ``CREATED_AT`` filter.
    """
    first = month.replace(day=1)
    next_first = (first + timedelta(days=31)).replace(day=1)
    lower, _ = day_bounds(first, None, tz=tz)
    upper, _ = day_bounds(next_first, None, tz=tz)
    assert lower is not None and upper is not None
    return lower, upper


def period_conditions(
    month: date, *, tz: ZoneInfo, view: PrContentBoardView | None = None
) -> list[ColumnElement[bool]]:
    """*Kỳ báo cáo* as ``WHERE`` clauses: the row's reporting instant is in ``month``.

    Two comparisons over :func:`reporting_instant`, and nothing lane-specific
    at this level - which lane a row is in is :func:`lane_conditions`' clause,
    which fact the month reads is the row's stage's, and the two compose by
    ``AND``. A row whose instant is ``NULL`` fails both comparisons and is in no
    month; see :func:`_fact_instant` for which lanes can produce one.
    """
    lower, upper = period_bounds(month, tz=tz)
    instant = reporting_instant(view)
    return [instant >= lower, instant < upper]


def period_applies(scope: PrContentViewScope | None) -> bool:
    """Whether *Kỳ báo cáo* narrows this scope at all. Step 1F.2.3f.6a.

    ``False`` for exactly one scope, ``MY_ACTIONS``. *Cần tôi xử lý* is an
    **action queue**, not a monthly report: it answers "what is waiting on me
    now", and a piece that entered Head review on 28 August and is still
    waiting for the Head's decision on 8 September is waiting on them *now*.
    Reading that queue through September's month would hide it - and hide
    exactly the carry-over work a queue exists to surface. Under ``ALL``,
    ``MY_CONTENT`` and ``TEAM`` the same piece is August's, which is correct
    for a report and what the month is for.

    Owned by the backend, and asked of the resolved scope, so the browser
    cannot get it wrong by sending or withholding a parameter: the month is
    still sent, still resolved and still echoed - the selector keeps its value
    across the tab change - and the server simply does not read it against
    this scope.
    """
    return scope is not PrContentViewScope.MY_ACTIONS


def lane_conditions(lane: PrContentLane) -> list[ColumnElement[bool]]:
    """One board column as ``WHERE`` clauses. Step 1F.2.3c2.

    Two halves, and the second one is the interesting half:

    * the **stage**, from
      :data:`~meobot.domain.pr.content_views.LANE_STAGES`. Eleven of the fifteen
      lanes are nothing but this;
    * for the four production lanes, whichever ``producer_user_id`` test
      distinguishes this lane from the other one at the same stage.

    That second clause is **derived from**
    :func:`~meobot.domain.pr.production.handoff_state` rather than written out
    beside the stage, and the difference matters. Spelling it ``APPROVED AND
    producer_user_id IS NULL`` here would be a second derivation of the handoff
    state - one that agrees with the domain rule today and would quietly stop
    agreeing the moment somebody widened
    :data:`~meobot.domain.pr.production.HANDOFF_STAGES` or added a third reading
    of ``APPROVED``. So the function is asked instead: *which producer column
    would put a row at this stage into this lane?* When both answers are the same
    lane - ``PRODUCTION`` is ``IN_PRODUCTION`` whoever holds it - no producer
    clause is added at all, which is how ``IN_PRODUCTION`` avoids inheriting a
    spurious ``IS NULL``.

    Returned as a list, like :func:`content_conditions`, so the caller extends
    rather than nests.
    """
    stage = lane_stage(lane)
    conditions: list[ColumnElement[bool]] = [PrContentItem.workflow_stage == stage]
    state = lane_production_state(lane)
    if state is None:
        return conditions
    unclaimed_state = handoff_state(stage, None)
    claimed_state = handoff_state(stage, _A_PRODUCER)
    if unclaimed_state is claimed_state:
        # The stage alone decides this lane; the producer column is not part of
        # what it means. ``IN_PRODUCTION`` and ``IN_INTERNAL_REVIEW`` land here.
        return conditions
    conditions.append(
        PrContentItem.producer_user_id.is_(None)
        if state is unclaimed_state
        else PrContentItem.producer_user_id.is_not(None)
    )
    return conditions


# --- The whole specification --------------------------------------------------


def content_conditions(
    query: ContentQuery, *, work_queue: ActorWorkQueue, tz: ZoneInfo, today: date
) -> list[ColumnElement[bool]]:
    """``query`` as a list of predicates to ``AND`` together.

    A list rather than a single expression so a caller can drop it into
    ``select(...).where(*conditions)`` for the rows, the count and the grouped
    per-stage count without restating any of it.

    There is no "not deleted" clause here, and that is deliberate rather than an
    omission: Step 1F.2.3a made deletion permanent, so a deleted item is absent
    from every one of these statements because its row is gone. The soft-delete
    filter this function briefly carried is removed with the column it read.

    Args:
        query: What to narrow by. ``scope`` must already be resolved - see
            :meth:`ContentQuery.with_scope`; ``None`` is treated as ``ALL``
            here, and resolving the default is the service's job because it
            needs the capability lookup. ``group`` is included like any other
            filter, so a caller wanting the counts *behind* the tabs passes
            :meth:`ContentQuery.for_counts` rather than dropping a clause of
            its own, and ``lane`` likewise.
        work_queue: What this actor may act on. Only ``MY_ACTIONS``,
            ``MY_CONTENT`` and ``TEAM`` read it.
        tz: The business timezone the date filters are expressed in.
        today: The day ``TEAM`` judges assignment intervals against, and the day
            ``MY_ACTIONS`` judges them against for unclaimed production. Passed
            in rather than taken from the clock, so a test can pin it.
    """
    conditions: list[ColumnElement[bool]] = []

    scope = query.scope or PrContentViewScope.ALL
    if scope is PrContentViewScope.MY_ACTIONS:
        conditions.append(_executable_by(work_queue, today=today))
    elif scope is PrContentViewScope.MY_CONTENT:
        conditions.append(responsible_for(work_queue.user_id) if work_queue.user_id else false())
    elif scope is PrContentViewScope.TEAM:
        conditions.append(
            _on_team_channel(work_queue.user_id, on=today) if work_queue.user_id else false()
        )
    # ALL adds nothing, which is exactly the pre-1F.2.2 behaviour.

    # Step 1F.2.3f.6c. Which board, before anything else narrows: the
    # operational board is everything but the archive, and that clause is on
    # the count statement as much as on the cards - which is what takes the
    # archive out of *Hoàn tất N* rather than only out of a column.
    conditions.extend(view_conditions(query.view))

    if query.group is not None:
        # An ``IN`` over the group's stages, and therefore part of the ``WHERE``
        # the ``LIMIT`` is applied to. Step 1F.2.3c1: this used to be a pass over
        # the page in the browser, which meant a five-item group arriving one
        # card on page 1 and four on page 3.
        conditions.append(PrContentItem.workflow_stage.in_(list(stages_in_group(query.group))))
    if query.lane is not None:
        # Step 1F.2.3c2. The same argument as the group above, for the column: a
        # lane is part of the ``WHERE`` the ``LIMIT`` is applied to, so a page of
        # *Đang sản xuất* is a page of the three items in it rather than
        # whichever slots the 155 waiting ones left over.
        conditions.extend(lane_conditions(query.lane))
    if query.period_month is not None and period_applies(scope):
        # Step 1F.2.3f.6. *Kỳ báo cáo*, over every lane. Applied here, beside the
        # group and the lane rather than inside either, because it is orthogonal
        # to both: which lane a row is in is its stage; whether it belongs to the
        # month is its stage's reporting fact. The clause is the same whether or
        # not a group or lane was named - which is what lets ``for_counts`` keep
        # it, and what makes the tab and lane figures the same question as the
        # cards under them.
        #
        # **Not for ``MY_ACTIONS``** - see :func:`period_applies`. The decision
        # is taken here, in the one predicate builder, so the cards, the total
        # and both count tables of an action queue all read the same
        # month-free predicate; a client that dropped the month itself would
        # have to drop it from four requests and get all four right.
        conditions.extend(period_conditions(query.period_month, tz=tz, view=query.view))
    if query.stage is not None:
        # Intersected with the group above rather than checked against it. An
        # impossible pair returns nothing, which is the honest answer; refusing
        # it would make a legitimate "narrow within this tab" a 422, and ignoring
        # either half would show rows the person excluded.
        conditions.append(PrContentItem.workflow_stage == query.stage)
    if query.priority is not None:
        # Step 1F.2.3d. An equality, and deliberately *only* an equality:
        # "Gấp" means the items marked Gấp, not "Gấp and anything above it".
        # A person filtering to one level is looking for that level, and a
        # ``>=`` would quietly return the Rất gấp items too - which is the
        # ordering's job, not the filter's.
        conditions.append(PrContentItem.priority == query.priority)
    if query.content_type is not None:
        conditions.append(PrContentItem.content_type == query.content_type)
    if query.unclassified_content_type:
        # Step 1F.2.3e. *Chưa phân loại* is a real slice of the board - it is the
        # work list for classifying the backlog - so it is a predicate rather
        # than something a client filters out of a page it was handed.
        conditions.append(PrContentItem.content_type.is_(None))
    if query.brand_id is not None:
        conditions.append(PrContentItem.brand_id == query.brand_id)
    if query.owner_user_id is not None:
        conditions.append(PrContentItem.owner_user_id == query.owner_user_id)
    if query.responsible_user_id is not None:
        conditions.append(responsible_for(query.responsible_user_id))
    if query.channel_id is not None:
        conditions.append(_targets_channel(query.channel_id))
    if query.platform_id is not None:
        conditions.append(_targets_platform(query.platform_id))

    lower, upper = day_bounds(query.date_from, query.date_to, tz=tz)
    conditions.extend(_date_conditions(query.date_field, lower, upper))

    if query.search:
        needle = f"%{query.search.strip()}%"
        conditions.append(or_(PrContentItem.code.ilike(needle), PrContentItem.title.ilike(needle)))
    return conditions


# --- The order the rows come back in -----------------------------------------

#: Urgency as something SQL can sort by, built from
#: :data:`~meobot.domain.pr.priority.PRIORITY_RANK` rather than restating it.
#:
#: A ``CASE`` and not an indexed column, and not ``ORDER BY priority``: the
#: column is a ``VARCHAR`` holding the enum's value, so ordering by it directly
#: is alphabetical - ``CRITICAL, HIGH, NORMAL, URGENT`` - which would put *Bình
#: thường* above *Gấp*. See :mod:`meobot.domain.pr.priority` for why the rank is
#: derived on every query instead of stored beside the string.
_PRIORITY_RANK_SQL = case(
    *((PrContentItem.priority == priority, rank) for priority, rank in PRIORITY_RANK.items()),
    else_=PRIORITY_RANK[PrPriority.NORMAL],
)


def content_order_by() -> list[ColumnElement[Any]]:
    """The order a content list comes back in, most urgent first.

    Four keys, and every one of them earns its place:

    1. **priority rank, descending.** Step 1F.2.3d, and the reason this function
       exists. *Rất gấp* before *Gấp* before *Ưu tiên* before *Bình thường*,
       decided here so that it is decided **before** ``LIMIT`` - a browser
       sorting the page it was handed would disagree with the pager handed to it
       alongside, and a critical item would sit on page three looking like it
       had been dealt with. That is the failure Step 1F.2.3c1 fixed for workflow
       groups, and this is the same one wearing a different hat;
    2. **planned publish date, ascending, nulls last.** Within one urgency the
       useful question is what is due soonest. Items with no planned date sort
       after the ones that have one rather than before: *no deadline* is not
       *the most urgent deadline*, and the SQLite default for ``ASC`` would have
       put them first. ``nulls_last()`` says so explicitly rather than relying on
       either backend's default;
    3. **created at, descending.** The ordering the board had before this step,
       kept as the third key so that an unfiltered *Bình thường* list with no
       planned dates - which is most of them - reads exactly as it always has;
    4. **id, ascending.** The tiebreaker that makes pagination *stable*. Three
       keys still tie for two items created in the same millisecond with the same
       priority and no date, and a tie under ``LIMIT``/``OFFSET`` is not a
       cosmetic problem: the database may order the tied rows differently between
       the page-1 query and the page-2 query, which silently shows one row twice
       and hides another entirely. A unique column last makes the sort total.

    Returned as a list so the caller spreads it into ``order_by(*...)``, matching
    how :func:`content_conditions` hands over its predicates.
    """
    return [
        _PRIORITY_RANK_SQL.desc(),
        PrContentItem.planned_publish_at.asc().nulls_last(),
        PrContentItem.created_at.desc(),
        PrContentItem.id.asc(),
    ]


def stage_counts_from(rows: Sequence[tuple[PrWorkflowStage, int]]) -> dict[PrWorkflowStage, int]:
    """A grouped-count result as a full mapping, zeros included.

    Every stage is present. A client rendering a tile per stage should not have
    to distinguish "no rows" from "stage missing from the response", and a
    ``.get(stage, 0)`` at every call site is the same defaulting written many
    times instead of once.

    Repeated stages are **summed** rather than overwritten. Step 1F.2.3c groups
    the one count statement by stage *and* by whether a producer has been named,
    so ``APPROVED`` legitimately arrives twice; a ``dict(rows)`` would have kept
    the second row and silently halved the total.
    """
    counted: dict[PrWorkflowStage, int] = {}
    for stage, count in rows:
        counted[stage] = counted.get(stage, 0) + count
    return {stage: counted.get(stage, 0) for stage in PrWorkflowStage}


def handoff_counts_from(
    rows: Sequence[tuple[PrWorkflowStage, bool, int]],
) -> dict[PrProductionHandoff, int]:
    """The same grouped count, read as production states rather than stages.

    Step 1F.2.3c. The board's production half is four columns and only three
    stages, because ``APPROVED`` is two different jobs depending on whether
    anybody has taken the edit - so a count per stage cannot label them. The
    rows carry that second dimension (``producer_user_id IS NULL``) and this
    turns the pair into the derived state through
    :func:`~meobot.domain.pr.production.handoff_state`, which stays the one
    place the rule lives.

    Every :class:`~meobot.domain.pr.models.PrProductionHandoff` is present,
    zeros included, for the reason :func:`stage_counts_from` gives. Rows outside
    the production half - an item at ``SCRIPTING`` - have no handoff state and
    are simply not counted here; they are still in the stage counts.

    Args:
        rows: ``(stage, producer_user_id IS NULL, count)`` triples, as grouped by
            :meth:`~meobot.application.pr_query_service.PrQueryService.content_page`.
    """
    counted: dict[PrProductionHandoff, int] = dict.fromkeys(PrProductionHandoff, 0)
    for stage, unclaimed, count in rows:
        state = handoff_state(stage, None if unclaimed else _A_PRODUCER)
        if state is not None:
            counted[state] += count
    return counted


__all__: list[str] = [
    "ActorWorkQueue",
    "ContentQuery",
    "content_conditions",
    "content_order_by",
    "day_bounds",
    "handoff_counts_from",
    "lane_conditions",
    "period_applies",
    "period_bounds",
    "period_conditions",
    "published_instant",
    "reporting_instant",
    "responsible_for",
    "stage_counts_from",
    "stage_entry_instant",
    "view_conditions",
]
