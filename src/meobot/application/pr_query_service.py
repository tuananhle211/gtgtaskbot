"""The read side: structured answers, for whichever client is asking.

Every return value here is a dataclass of domain objects. Nothing formats a
message, nothing knows what a Telegram card looks like and nothing returns a
rendered string, because the Telegram bot, a future web admin UI and CLI
scripts all read through this service and each renders differently. Transport
concerns stop at the caller.

The one that matters: :meth:`PrQueryService.get_content_review_context`
------------------------------------------------------------------------

This is what a human reviewer is shown before they decide, and its shape is the
whole argument of the module. It returns the AI verdict **as a verdict** - the
result enum, the score, the summary, the issues, the suggestions, the policy
flags - and never as a boolean.

``PASS`` and ``PASS_WITH_WARNINGS`` both let content reach a human, which is
exactly why collapsing them into "AI passed: true" would be the most damaging
simplification available: the warnings are the reason the second value exists,
and a reviewer who never sees them is reviewing with less information than the
machine had. :attr:`ContentReviewContext.ai_has_warnings` and
:attr:`ContentReviewContext.ai_result` are separate for that reason, and
``ai_passed`` is deliberately absent.

The AI review returned is the newest ``FULL_REVIEW`` **for the current
version**, not the newest overall. After a rewrite there is no such review
until the new draft is checked, and this reports ``None`` rather than the
previous draft's verdict - which is the read-side half of the version-safety
rule.

Filtering: one specification, three statements
----------------------------------------------

Step 1F.2.2 added :meth:`PrQueryService.content_page`, and every filter the
panel offers goes through it. What it does *not* do is grow a keyword argument
per filter: the filter is
:class:`~meobot.application.pr_content_query.ContentQuery`, it becomes predicates
in one function, and the rows, the total and the per-stage counts are three
statements over that one list. Counts that disagreed with the list they captioned
is the bug this shape makes unrepresentable.

``list_contents`` is now a thin call into it and keeps its old signature, so the
Telegram tools and the existing route contract are unchanged. Note the one
deliberate difference between the two paths: ``ContentQuery.search`` **composes**
with the other filters, while ``/contents?search=`` still short-circuits to
:meth:`search_contents`. Somebody typing a code wants that item; somebody typing
a word into a filtered board wants that word inside their filter.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_capability_service import PrCapabilityGrant, PrCapabilityService
from meobot.application.pr_content_query import (
    ActorWorkQueue,
    ContentQuery,
    content_conditions,
    content_order_by,
    handoff_counts_from,
    lane_conditions,
    period_applies,
    period_conditions,
    published_instant,
    stage_counts_from,
)
from meobot.application.pr_grant_scope_sql import approvable_by
from meobot.core.time import utcnow
from meobot.db.models.pr import (
    PrApprovalEvent,
    PrBrand,
    PrChannel,
    PrChannelAssignment,
    PrContentItem,
    PrContentTarget,
    PrPlatform,
    PrTask,
)
from meobot.db.models.pr_ai_review import PrAiReview
from meobot.db.models.pr_content_asset import PrContentDerivative, PrContentDestination
from meobot.db.models.pr_content_resource import PrContentResource
from meobot.db.models.pr_content_version import PrContentVersion
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.pr_reporting import PrPublication
from meobot.domain.identity.models import Actor
from meobot.domain.pr.content_views import (
    PrContentBoardView,
    PrContentLane,
    PrContentViewScope,
    default_scope,
)
from meobot.domain.pr.errors import PrNotFoundError, PrValidationError
from meobot.domain.pr.models import (
    PrAiReviewResult,
    PrAiReviewType,
    PrApprovalDecision,
    PrApprovalStage,
    PrChannelAssignmentRole,
    PrEntityStatus,
    PrProductionHandoff,
    PrTaskStatus,
    PrWorkflowStage,
)
from meobot.domain.pr.policy import (
    APPROVAL_CAPABILITIES,
    BULK_APPROVAL_MAX_ITEMS,
    BULK_ARCHIVE_MAX_ITEMS,
    PR_READ_PERMISSION,
    PrCapability,
    require_permission,
)
from meobot.domain.pr.reporting import ACTIVE_PUBLICATION_STATUSES
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES, TERMINAL_TASK_STATUSES


@dataclass(frozen=True, slots=True)
class ContentDetail:
    """A content item with its current draft and planned channels.

    ``brand`` and ``target_channels`` were added in Step 1E.2.1 so a screen can
    say "Apexmed · Facebook" instead of printing two UUIDs. They are the rows
    themselves rather than pre-formatted names, because the Telegram bot and the
    web panel word things differently.

    They cost **two bounded queries** for the whole item - one ``get`` for the
    brand, one ``IN`` for however many channels the targets name - and are
    loaded here rather than per target, which is where the N+1 would have been.
    Both default to absent so a caller constructing this dataclass without them
    still gets a valid value; the shape is optional, not the data.
    """

    content: PrContentItem
    current_version: PrContentVersion | None
    targets: tuple[PrContentTarget, ...]
    brand: PrBrand | None = None
    #: The channels the targets point at, in no particular order. A client
    #: joins them to targets by id.
    target_channels: tuple[PrChannel, ...] = ()
    #: ``platform_id`` -> ``pr_platforms.code``. Step 1F.1: the canonical
    #: platform identity, so a client can be told a target is policy-grounded
    #: without matching on a channel's name.
    platform_codes: dict[uuid.UUID, str] = field(default_factory=dict)

    @property
    def current_version_no(self) -> int | None:
        return self.current_version.version_no if self.current_version else None


@dataclass(frozen=True, slots=True)
class ContentReviewContext:
    """Everything a human needs in front of them to decide.

    Read the module docstring on why the AI verdict is exposed in pieces rather
    than as a judgement.
    """

    content: PrContentItem
    current_version: PrContentVersion
    targets: tuple[PrContentTarget, ...]
    tasks: tuple[PrTask, ...]
    #: The newest gating review **of this exact draft**, or ``None`` when this
    #: draft has not been machine-checked yet.
    ai_review: PrAiReview | None
    #: Every AI review of this draft, gating or not, newest first. The three
    #: non-gating types moved nothing, and a reviewer should still see them.
    ai_reviews_for_version: tuple[PrAiReview, ...]
    approvals: tuple[PrApprovalEvent, ...]

    # --- The AI verdict, in pieces ---------------------------------------
    @property
    def ai_result(self) -> PrAiReviewResult | None:
        """``PASS``, ``PASS_WITH_WARNINGS``, ``REVISION_REQUIRED`` or ``None``."""
        return self.ai_review.result if self.ai_review else None

    @property
    def ai_score(self) -> Decimal | None:
        return self.ai_review.score if self.ai_review else None

    @property
    def ai_summary(self) -> str | None:
        return self.ai_review.summary if self.ai_review else None

    @property
    def ai_issues(self) -> list[Any]:
        return list(self.ai_review.issues or []) if self.ai_review else []

    @property
    def ai_suggestions(self) -> list[Any]:
        return list(self.ai_review.suggestions or []) if self.ai_review else []

    @property
    def ai_policy_flags(self) -> list[Any]:
        return list(self.ai_review.policy_flags or []) if self.ai_review else []

    @property
    def ai_has_warnings(self) -> bool:
        """True only for ``PASS_WITH_WARNINGS``.

        Separate from :attr:`ai_result` so a client can highlight the warning
        state without re-deriving it, and so the distinction survives being
        passed through a template that only reads booleans.
        """
        return self.ai_result is PrAiReviewResult.PASS_WITH_WARNINGS

    @property
    def ai_review_matches_current_version(self) -> bool:
        """True when a gating verdict exists for the draft on screen."""
        return self.ai_review is not None


@dataclass(frozen=True, slots=True)
class ContentPage:
    """One page of a filtered content list, with the counts that describe it.

    Step 1F.2.2. The three fields are three statements over **one** set of
    conditions, and that is the whole point of returning them together: before
    this, the board's cards came from ``/contents`` and its tiles from
    ``/dashboard``, which knew about none of the filters - so a scoped view of
    seven items could be captioned "Chờ duyệt: 120". The numbers were not stale,
    they were about a different question.

    :attr:`total` and :attr:`stage_counts` describe the **whole** filtered set,
    not this page of it. A client showing "7 / 213" needs both, and a total
    computed from ``len(items)`` after a ``LIMIT`` is a number that looks
    authoritative and is not.

    The one place the three fields are *not* over identical conditions is
    :attr:`ContentQuery.group`, and it is deliberate: see the two counts
    explained on :attr:`total` and :attr:`stage_counts` below.
    """

    items: tuple[PrContentItem, ...]

    #: Rows matching the filter, ignoring ``limit``/``offset`` - **including**
    #: the selected group and the selected lane. With a lane this is that lane's
    #: whole queue: *Đang sản xuất* says 3 on a request that returned all three,
    #: and *Chờ nhận sản xuất* says 155 on one that returned the first twenty.
    #: Step 1F.2.3c2.
    total: int
    #: Every :class:`PrWorkflowStage`, zeros included - see
    #: :func:`~meobot.application.pr_content_query.stage_counts_from`.
    #:
    #: Over the filters **without** the group and **without** the lane, because
    #: these are what the group tabs and the lane headers are labelled from, and
    #: a tab has to say how much work is in the group you are not currently in.
    #: Steps 1F.2.3c1 and 1F.2.3c2.
    #:
    #: **Empty** on a lane request, which asks about one column and is not the
    #: place these belong - see :meth:`PrQueryService.content_page`.
    stage_counts: Mapping[PrWorkflowStage, int]
    #: Every :class:`PrProductionHandoff`, zeros included. Step 1F.2.3c: the
    #: production half of the board is four columns over three stages, because
    #: ``APPROVED`` with nobody on it and ``APPROVED`` with a producer are two
    #: different jobs. A stage count cannot label those two, and a client
    #: counting the cards it happens to have been sent would relabel them on
    #: every page turn - so the split is counted here, over exactly the
    #: conditions :attr:`stage_counts` uses: one grouped statement, read twice,
    #: and therefore the group excluded for the same reason.
    #:
    #: This is also the authority for the four production **lane** totals, and
    #: the reason nothing recomputes ``APPROVED`` + producer-null anywhere else:
    #: a lane header reads its figure from here. Step 1F.2.3c2.
    #:
    #: **Empty** on a lane request, for the reason above.
    production_state_counts: Mapping[PrProductionHandoff, int]
    #: The scope actually applied. Echoed back because the caller may have sent
    #: none, in which case this is the actor's default and the client needs to
    #: know which tab to light up.
    scope: PrContentViewScope
    #: Step 1F.2.8. Which of :attr:`items` **this actor could approve right
    #: now**, as ids. The subset of this page, never of the filtered set: it is
    #: what decides whether a card gets a bulk-selection checkbox, and a card
    #: that is not on the page has no checkbox to decide about.
    #:
    #: Computed by one extra statement over the same
    #: :func:`~meobot.application.pr_grant_scope_sql.approvable_by` predicate the
    #: queue and the write use, restricted to the ids already returned - so it is
    #: **not** a filter applied after pagination (the page is unchanged) and it
    #: cannot disagree with what a bulk approval would accept. Empty for an actor
    #: holding no review grant, which is the honest answer and also the cheap
    #: one: the statement is skipped entirely.
    approvable_ids: frozenset[uuid.UUID] = frozenset()
    #: Step 1F.2.3f.4. **When each item on this page went out**, for the items
    #: that have, keyed by content id.
    #:
    #: Populated only when the query selected a reporting month - the board is
    #: the one screen that draws *"Thực tế đăng"*, and every other caller would
    #: be paying a round trip for a column it does not show. One grouped query
    #: over the page's ids rather than a lookup per card: a board page is fifty
    #: items, and fifty extra selects is how a list screen becomes slow in
    #: production and nowhere else.
    published_at: Mapping[uuid.UUID, datetime] = field(default_factory=dict)
    #: Step 1F.2.3f.6. **The reporting month actually applied** - the first day
    #: of it - or ``None`` when the caller asked for the cumulative board.
    #:
    #: Echoed for the same reason :attr:`scope` is: a client may send
    #: ``period=CURRENT`` and let the server resolve it, and a selector that
    #: then guessed which month that was would be the browser deciding the
    #: calendar all over again.
    period: date | None = None
    #: Step 1F.2.3f.6b. **The current business month** - the first day of the
    #: month ``today`` falls in, in the service's timezone - whatever month was
    #: selected. Echoed so the selector can anchor its option list on it: a
    #: list anchored on the *selected* month made September vanish the moment
    #: somebody chose August, with no way back but editing the URL. Always set,
    #: and never derived from a browser clock.
    current_period: date | None = None
    #: Step 1F.2.3f.6c. Which board this page is: the operational one, the
    #: archive, or ``None`` for a caller with no view (the flat list).
    view: PrContentBoardView | None = None

    @property
    def period_applied(self) -> bool:
        """Whether :attr:`period` narrowed this page. Step 1F.2.3f.6a.

        ``False`` when no month was selected, and ``False`` under ``MY_ACTIONS``
        whatever was selected - the action queue is read month-free (see
        :func:`~meobot.application.pr_content_query.period_applies`). The
        month itself is still echoed so a selector keeps its value; this is
        what tells the selector to say so rather than pretend.
        """
        return self.period is not None and period_applies(self.scope)


@dataclass(frozen=True, slots=True)
class ArchiveCandidates:
    """The frozen answer to *"lưu trữ nội dung kỳ 08/2026"*. Step 1F.2.3f.6.

    The same shape as :class:`ApprovableSelection`, for the same reasons:
    **explicit ids**, resolved server-side from the predicate the board draws
    *Đã đăng* with, at the moment the person asked. :attr:`total` and
    :attr:`content_ids` come from one condition list - the count is the same
    ``WHERE`` as the ids, without the ``LIMIT`` - so the number in the
    confirmation and the batch the button submits can never describe different
    sets. When the month holds more than the batch limit the ids are the first
    :data:`~meobot.domain.pr.policy.BULK_ARCHIVE_MAX_ITEMS` in the board's own
    order and :attr:`truncated` says so.

    :attr:`may_archive` is the server's answer to whether this actor holds the
    capability the transition needs, so the panel offers the action to the
    people the write would accept it from and to nobody else.
    """

    #: First day of the month whose published output is the candidate set.
    period: date
    total: int
    content_ids: tuple[uuid.UUID, ...]
    limit: int
    truncated: bool
    may_archive: bool


@dataclass(frozen=True, slots=True)
class ApprovableSelection:
    """The frozen answer to "select everything I can approve at this step".

    Step 1F.2.8, and the shape is the whole of requirement 17. It carries
    **explicit ids**, resolved server-side from the same predicate the board and
    the write use, at the moment the person asked for them. From that instant the
    batch is those ids and nothing else: an item created, moved into the gate or
    granted to the actor a second later is not in it, because it is not in this
    tuple. There is no query token to re-evaluate and therefore no window in
    which the target set can grow between the confirmation dialog and the button.

    :attr:`total` and :attr:`content_ids` come from **one** statement over one
    condition list - the count is the same ``WHERE`` as the ids, without the
    ``LIMIT`` - so the number the panel shows and the set it would approve can
    never describe different things (requirement 34).

    When ``total`` exceeds the batch limit the ids are the **first**
    :data:`~meobot.domain.pr.policy.BULK_APPROVAL_MAX_ITEMS` in the board's own
    reading order and :attr:`truncated` says so. That is the honest bounded
    answer: the panel offers "duyệt 200 nội dung" and says how many are left,
    rather than accepting 340 and approving some of them.
    """

    gate: PrApprovalStage
    #: Every item at this gate, under these filters, this actor may decide.
    #: Not the length of :attr:`content_ids` when :attr:`truncated`.
    total: int
    #: At most :attr:`limit` of them, in the board's order. The batch.
    content_ids: tuple[uuid.UUID, ...]
    limit: int
    #: ``total > limit``. The panel must say so; see the class docstring.
    truncated: bool


@dataclass(frozen=True, slots=True)
class ChannelDetail:
    """A channel and the assignments hanging off it.

    ``platform_code`` rides along so the detail response can answer the same
    "does a target here need Organic or Paid Ad" question the list route
    answers. Resolved here rather than in the schema: a response model that
    queried would be a read model in disguise.

    Step 1F.2.4a added the identity half. ``platform_code`` is also what the
    canonical platform badge is derived from - see
    :func:`~meobot.domain.pr.channel_metrics.platform_from_code` - and
    ``platform_name`` is the registered platform's own display name, which is
    the only thing worth showing for a platform outside the canonical six.

    The three ``can_*`` flags are the server's answer to "what should this
    screen offer", decided from the same capability every channel write
    requires. A client that inferred them from a role string would be
    reimplementing authorization in a browser, and would be wrong first for
    whoever holds a grant rather than a role.
    """

    channel: PrChannel
    assignments: tuple[PrChannelAssignment, ...]
    platform_code: str | None = None
    platform_name: str | None = None
    can_edit_channel: bool = False
    can_record_metrics: bool = False
    can_manage_assignments: bool = False


class PrQueryService:
    """Read-only PR queries.

    Args:
        session: A session. Nothing here writes, so a read-only session from
            :meth:`~meobot.db.session.Database.session` is enough.
        capabilities: Resolves PR capabilities. Optional because most queries
            do not need it, and a caller reading content should not have to
            construct an authorization service to do so; the capability
            queries raise a clear error rather than guessing when it is
            absent.
        timezone: The business timezone the date filters in
            :class:`~meobot.application.pr_content_query.ContentQuery` are
            expressed in. ``build_pr_services`` passes ``settings.timezone``.
            Defaults to UTC, which is only right for a caller that supplies no
            date filter - the day boundaries would otherwise be seven hours out
            of step with the calendar people are reading, so anything doing date
            filtering should be constructed with the configured zone rather than
            relying on this.
    """

    def __init__(
        self,
        session: AsyncSession,
        capabilities: PrCapabilityService | None = None,
        *,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self._session = session
        self._capabilities = capabilities
        self._timezone = timezone or ZoneInfo("UTC")

    # --- Content ----------------------------------------------------------
    async def get_content(self, *, actor: Actor, content_id: uuid.UUID) -> ContentDetail:
        """One content item, its current draft, its channels and its brand."""
        require_permission(actor, PR_READ_PERMISSION)
        content = await self._require_content(content_id)
        targets = tuple(await self._targets(content_id))
        return ContentDetail(
            content=content,
            current_version=await self._current_version(content_id),
            targets=targets,
            brand=await self._session.get(PrBrand, content.brand_id),
            target_channels=(channels := await self._channels_for(targets)),
            platform_codes=await self._platform_codes(channels),
        )

    async def _platform_codes(self, channels: Sequence[PrChannel]) -> dict[uuid.UUID, str]:
        """One query for the platforms a set of channels sits on."""
        platform_ids = {channel.platform_id for channel in channels}
        if not platform_ids:
            return {}
        result = await self._session.execute(
            select(PrPlatform.id, PrPlatform.code).where(PrPlatform.id.in_(platform_ids))
        )
        return dict(result.all())  # type: ignore[arg-type]

    async def _channels_for(self, targets: Sequence[PrContentTarget]) -> tuple[PrChannel, ...]:
        """The channels a set of targets names, in one query.

        One ``IN`` for the whole set, and no query at all when there are no
        targets - which is the ordinary case for something still at ``IDEA``.
        Per-target ``get`` calls here would be the N+1 this method exists to
        avoid.
        """
        channel_ids = {target.channel_id for target in targets}
        if not channel_ids:
            return ()
        result = await self._session.execute(select(PrChannel).where(PrChannel.id.in_(channel_ids)))
        return tuple(result.scalars().all())

    async def list_brands(
        self,
        *,
        actor: Actor,
        status: PrEntityStatus | None = PrEntityStatus.ACTIVE,
        limit: int = 200,
    ) -> Sequence[PrBrand]:
        """Brands somebody may pick from, by name.

        Active only by default. ``pr_brands`` rows are never deleted - archived
        work points at them - so retiring a brand means setting it ``INACTIVE``,
        and offering a retired brand in a picker would undo that decision one
        new content item at a time. Passing ``status=None`` returns every row,
        for a caller that is *displaying* a brand rather than choosing one.

        Not a write path in disguise: nothing here decides whether a brand may
        be *used*. ``PrContentService.create_content`` checks the brand exists,
        as it always has.
        """
        require_permission(actor, PR_READ_PERMISSION)
        statement = select(PrBrand)
        if status is not None:
            statement = statement.where(PrBrand.status == status)
        result = await self._session.execute(statement.order_by(PrBrand.name.asc()).limit(limit))
        return result.scalars().all()

    async def list_contents(
        self,
        *,
        actor: Actor,
        brand_id: uuid.UUID | None = None,
        stage: PrWorkflowStage | None = None,
        owner_user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[PrContentItem]:
        """Content items, newest first, narrowed by whatever was supplied.

        The three filters this has always taken, unchanged. Since Step 1F.2.2 it
        builds them through :class:`ContentQuery` rather than assembling its own
        ``WHERE`` clauses, so the Telegram list and the panel's board cannot
        disagree about what ``brand_id`` means. Scope defaults to ``ALL``: this
        method takes no actor-relative view, and callers wanting one ask
        :meth:`content_page`.
        """
        page = await self.content_page(
            actor=actor,
            query=ContentQuery(
                scope=PrContentViewScope.ALL,
                brand_id=brand_id,
                stage=stage,
                owner_user_id=owner_user_id,
                limit=limit,
                offset=offset,
            ),
            with_counts=False,
        )
        return page.items

    # --- The one filtered read the board and its counters share ------------
    async def content_page(
        self,
        *,
        actor: Actor,
        query: ContentQuery,
        on: date | None = None,
        with_counts: bool = True,
    ) -> ContentPage:
        """One page of content matching ``query``, and the counts describing it.

        Three statements over one condition list from
        :func:`~meobot.application.pr_content_query.content_conditions` - the
        rows, the total, and a single ``GROUP BY workflow_stage``. Fourteen
        separate counting queries is what this replaced, and the numbers now
        agree with the list because they are the same question asked three ways
        rather than two questions that resemble each other.

        With a ``group`` selected there are two condition lists rather than one,
        and the difference between them is exactly that clause: the rows and the
        total are about the group, the grouped counts are about the tabs that
        lead out of it. Both still come from ``content_conditions``, so nothing
        below restates a filter.

        With a ``lane`` selected it is one statement fewer rather than one more.
        Step 1F.2.3c2: a lane request is a request for **one column of the
        board** - its page of rows and the size of the whole queue behind them -
        so it returns those two and skips the board-wide aggregates entirely.
        Four such requests draw a four-column board without any of them
        recomputing the same two ``GROUP BY``s, and the figures those aggregates
        carry are asked for once, by a request that names no lane.

        Args:
            actor: Gated on the read permission, exactly as every other list
                here. A scope narrows what is *shown*; it never widens or
                narrows what may be read.
            query: The filter. A ``scope`` of ``None`` resolves to this actor's
                default via
                :func:`~meobot.domain.pr.content_views.default_scope`, and the
                resolved value comes back on :attr:`ContentPage.scope`.
            on: The day dated grants and channel assignments are judged against.
                Defaults to today in the business timezone.
            with_counts: ``False`` skips the two aggregate statements for a
                caller that only wants rows - :meth:`list_contents` and the
                Telegram tools. :attr:`ContentPage.total` is then the number of
                rows on this page, and that is documented rather than guessed at
                because the caller asked for it.

        Raises:
            PrValidationError: A personal scope was asked for without a
                capability service configured to resolve it.
        """
        require_permission(actor, PR_READ_PERMISSION)
        await self._read_row_at_a_time()
        today = on or datetime.now(self._timezone).date()
        work_queue = await self._work_queue(actor, on=today, scope=query.scope)
        scope = query.scope or default_scope(work_queue.held)
        # Step 1F.2.3f.6. ``period=CURRENT`` is resolved here, against the same
        # ``today`` the dated grants are judged on, and the month it became is
        # echoed on the page - one calendar, and the client reads it back.
        resolved = query.with_scope(scope).with_period(today)
        conditions = content_conditions(
            resolved, work_queue=work_queue, tz=self._timezone, today=today
        )

        # Step 1F.2.3d: the ``ORDER BY`` is part of this statement, so it is
        # applied *before* ``LIMIT``/``OFFSET`` rather than to the rows that come
        # back from them. Sorting the page in Python - or in the browser - would
        # order sixty arbitrary rows correctly and still leave a *Rất gấp* item
        # on page three. See
        # :func:`~meobot.application.pr_content_query.content_order_by`.
        rows = await self._session.execute(
            select(PrContentItem)
            .where(*conditions)
            .order_by(*content_order_by())
            .limit(query.limit)
            .offset(query.offset)
        )
        items = tuple(rows.scalars().all())
        # Step 1F.2.8. Which of the rows just returned this actor may decide.
        # After the page rather than before it, deliberately and harmlessly: it
        # removes nothing, so ``total``, the counts and the pager are untouched -
        # it only marks which cards may carry a checkbox.
        approvable = await self._approvable_among(
            items, actor=actor, on=today, grants=work_queue.approval_grants
        )
        if not with_counts:
            return ContentPage(
                items=items,
                total=len(items),
                stage_counts=stage_counts_from(()),
                production_state_counts=handoff_counts_from(()),
                scope=scope,
                approvable_ids=approvable,
            )

        total = await self._session.scalar(
            select(func.count()).select_from(PrContentItem).where(*conditions)
        )
        if resolved.lane is not None:
            # Step 1F.2.3c2. A lane request asks about **one column**: its rows
            # and its ``total``, which is the whole lane and not this page of it.
            # The two board-wide count tables are deliberately not computed for
            # it - they describe the filtered set with neither the group nor the
            # lane applied, so four lane requests would each recompute the same
            # two aggregates and a client would have four identical copies of
            # them. A screen wanting the figures asks once without a lane; see
            # ``ContentQuery.for_counts``. Empty rather than zero-filled, so a
            # client that reads them anyway gets nothing rather than a confident
            # zero.
            return ContentPage(
                items=items,
                total=total or 0,
                stage_counts={},
                production_state_counts={},
                scope=scope,
                approvable_ids=approvable,
                published_at=await self._published_instants(resolved, items),
                period=resolved.period_month,
                current_period=today.replace(day=1),
                view=resolved.view,
            )
        # Step 1F.2.3c1. The two counts on this object answer two different
        # questions, and only one of them is about the group the caller is
        # standing in:
        #
        # * ``total`` captions the pager, so it is the same ``WHERE`` as the rows
        #   - group included. "5 items, all on page 1" is the whole point;
        # * the grouped counts caption the **tabs**, which are how somebody
        #   leaves the group they are in. Counted under it, *Chờ duyệt* would
        #   read 0 the moment *Chuẩn bị* was open, and the strip would be a row
        #   of zeros pointing at the work it was hiding.
        #
        # So the tab counts are the same query minus the board's own narrowing,
        # built by ``ContentQuery.for_counts`` rather than by dropping a
        # predicate here - the scope, the dates, the platform and the search
        # still apply, because those the person *did* choose. The lane comes off
        # with the group, which is what makes one figure per lane available from
        # a request that asked for no lane at all: ``production_state_counts``
        # is *Chờ nhận sản xuất 155 · Sẵn sàng 0 · Đang sản xuất 3 · Chờ duyệt
        # nội bộ 12`` whichever of those four columns somebody is reading.
        count_conditions = (
            conditions
            if resolved.group is None
            else content_conditions(
                resolved.for_counts(), work_queue=work_queue, tz=self._timezone, today=today
            )
        )
        # Step 1F.2.3c. One statement, grouped by the stage **and** by whether a
        # producer has been named, because the board needs both readings and two
        # statements is two chances for them to disagree - which is the bug the
        # whole of this object exists to have fixed. The second dimension is a
        # null test rather than the id itself: who the producer is does not
        # change which column the item is in, and grouping by the id would
        # return a row per person.
        unclaimed = PrContentItem.producer_user_id.is_(None)
        grouped = await self._session.execute(
            select(PrContentItem.workflow_stage, unclaimed, func.count())
            .select_from(PrContentItem)
            .where(*count_conditions)
            .group_by(PrContentItem.workflow_stage, unclaimed)
        )
        # ``bool(...)`` because SQLite answers a boolean expression with 0/1 and
        # PostgreSQL with a real boolean; the mapping below reads it as a flag.
        counted = [(stage, bool(flag), count) for stage, flag, count in grouped.all()]
        return ContentPage(
            items=items,
            total=total or 0,
            stage_counts=stage_counts_from([(stage, count) for stage, _, count in counted]),
            production_state_counts=handoff_counts_from(counted),
            scope=scope,
            approvable_ids=approvable,
            published_at=await self._published_instants(resolved, items),
            period=resolved.period_month,
            current_period=today.replace(day=1),
            view=resolved.view,
        )

    async def archive_candidates(
        self,
        *,
        actor: Actor,
        period: date,
        on: date | None = None,
        limit: int = BULK_ARCHIVE_MAX_ITEMS,
    ) -> ArchiveCandidates:
        """Everything still ``PUBLISHED`` whose publication month is ``period``.

        Step 1F.2.3f.6, and what *"Lưu trữ nội dung kỳ 08/2026"* is resolved
        through. The predicate is **the board's own** - ``lane_conditions`` for
        *Đã đăng* and ``period_conditions`` for the month, the two clauses that
        draw the column - so the set offered for archiving is exactly the set
        the person was looking at under *Đã đăng* with that month selected, and
        the count in the confirmation is that column's header.

        No scope and no ordinary filter: this is a deliberate end-of-period
        action over the department's output, not over one person's view of it,
        and a candidate list that quietly honoured a channel filter would
        archive a third of the month and report it as the month. Authorization
        is the transition's own capability, resolved here so the panel can
        offer the button to the right people, and enforced again by the write.
        """
        require_permission(actor, PR_READ_PERMISSION)
        await self._read_row_at_a_time()
        today = on or datetime.now(self._timezone).date()
        may_archive = await self._require_capabilities().allows(
            actor, PrCapability.PR_CONTENT_TRANSITION, on=today
        )
        conditions = [
            *lane_conditions(PrContentLane.PUBLISHED),
            # The operational board's month expression: PUBLISHED rows only,
            # and no ``archived_at`` arm to plan for.
            *period_conditions(period, tz=self._timezone, view=PrContentBoardView.ACTIVE),
        ]
        total = await self._session.scalar(
            select(func.count()).select_from(PrContentItem).where(*conditions)
        )
        rows = await self._session.execute(
            select(PrContentItem.id).where(*conditions).order_by(*content_order_by()).limit(limit)
        )
        content_ids = tuple(rows.scalars().all())
        return ArchiveCandidates(
            period=period.replace(day=1),
            total=total or 0,
            content_ids=content_ids,
            limit=limit,
            truncated=(total or 0) > len(content_ids),
            may_archive=may_archive,
        )

    async def publication_instants(
        self, content_ids: Sequence[uuid.UUID]
    ) -> Mapping[uuid.UUID, datetime]:
        """The canonical publication instant of each of these rows, where one exists.

        The same ``MIN`` over active publications that
        :func:`~meobot.application.pr_content_query.published_instant` reads in
        SQL, asked of explicit ids - which is how the bulk archive re-checks,
        under its locks, that every item it was handed still belongs to the
        month it was selected for.
        """
        if not content_ids:
            return {}
        rows = await self._session.execute(
            select(PrContentItem.id, published_instant()).where(
                PrContentItem.id.in_(list(content_ids))
            )
        )
        return {content_id: moment for content_id, moment in rows.all() if moment is not None}

    async def _published_instants(
        self, query: ContentQuery, items: Sequence[PrContentItem]
    ) -> Mapping[uuid.UUID, datetime]:
        """When each published item on this page went out. Step 1F.2.3f.4.

        One grouped query over the page's ids, so a fifty-card board costs one
        extra round trip rather than fifty. Asked only when a reporting month
        is selected: the board is what draws *"Thực tế đăng"*, and no other
        caller should pay for a column it does not show.

        The aggregate is the same ``MIN`` over active publications that
        :func:`~meobot.application.pr_content_query.published_instant` reads
        the month against, so the day on a card and the month it was selected
        into can never disagree - they are the same number, asked twice.
        """
        if query.period_month is None or not items:
            return {}
        rows = await self._session.execute(
            select(PrPublication.content_id, func.min(PrPublication.published_at))
            .where(
                PrPublication.content_id.in_([one.id for one in items]),
                PrPublication.status.in_(tuple(ACTIVE_PUBLICATION_STATUSES)),
            )
            .group_by(PrPublication.content_id)
        )
        return {content_id: moment for content_id, moment in rows.all() if moment is not None}

    async def _approvable_among(
        self,
        items: Sequence[PrContentItem],
        *,
        actor: Actor,
        on: date,
        grants: Sequence[PrCapabilityGrant],
    ) -> frozenset[uuid.UUID]:
        """Which of these already-fetched rows this actor may decide. Step 1F.2.8.

        One statement, over
        :func:`~meobot.application.pr_grant_scope_sql.approvable_by` - the same
        predicate ``Cần tôi xử lý`` filters on and the same rule
        :meth:`PrCapabilityService.can_approve` applies item by item. It is asked
        of the ids on this page rather than per row in Python: twenty rows would
        otherwise be twenty round trips, each re-reading the same grants.

        ``grants`` is what the caller already resolved for this request, so the
        ordinary path costs no extra grant read. The ``ALL`` scope is the one
        case that resolves none - :meth:`_work_queue` short-circuits it, on
        purpose, so an unconfigured service still serves the unnarrowed list -
        and there the grants are fetched here, because somebody standing on
        *Tất cả* is as entitled to a checkbox as somebody on *Cần tôi xử lý*.

        Skips the statement entirely when nothing on the page is standing at a
        gate, which is most pages.
        """
        at_a_gate = [item.id for item in items if item.workflow_stage in STAGE_APPROVAL_GATES]
        if not at_a_gate:
            return frozenset()
        effective = tuple(grants)
        if not effective:
            if self._capabilities is None:
                return frozenset()
            effective = tuple(await self._capabilities.approval_grants_for(actor, on=on))
        if not effective:
            return frozenset()
        result = await self._session.execute(
            select(PrContentItem.id).where(
                PrContentItem.id.in_(at_a_gate), approvable_by(effective)
            )
        )
        return frozenset(result.scalars().all())

    async def approvable_selection(
        self,
        *,
        actor: Actor,
        gate: PrApprovalStage,
        query: ContentQuery,
        on: date | None = None,
        limit: int = BULK_APPROVAL_MAX_ITEMS,
    ) -> ApprovableSelection:
        """Everything at one gate, under one filter, this actor may approve.

        Step 1F.2.8, and what "Chọn tất cả nội dung ở bước này" is resolved
        through. Three properties, and each of them is a requirement of the step:

        * **eligibility is applied before the cut** (requirement 33). The
          ``WHERE`` is the board's own filter list *and*
          :func:`~meobot.application.pr_grant_scope_sql.approvable_by`, and the
          count and the ids are two statements over that one list. Fetching a
          page and discarding unauthorized rows afterwards would make both the
          number and the batch wrong, in different directions;
        * **the same query semantics answer both questions** (requirement 34).
          The total the panel shows and the ids it would submit differ only by
          ``LIMIT``;
        * **nothing is loaded into the browser to support it** (requirement 32).
          The client sends a filter and receives at most
          :data:`~meobot.domain.pr.policy.BULK_APPROVAL_MAX_ITEMS` ids - never
          the content, never the history, and never the rows it is choosing
          between.

        The ``stage`` is pinned to the gate's workflow stage rather than trusted
        from ``query``: a select-all is a select-all *at this step*, and letting
        the caller's stage filter widen it would be the one way a batch could
        reach outside the context the person is standing in (requirement 4).

        Ordered by :func:`~meobot.application.pr_content_query.content_order_by`
        - the board's own reading order - so the first 200 of 340 are the 200 at
        the top of the queue the person is looking at, and not an arbitrary 200.

        Returns an empty selection for somebody holding no grant at this gate.
        """
        require_permission(actor, PR_READ_PERMISSION)
        await self._read_row_at_a_time()
        today = on or datetime.now(self._timezone).date()
        capability = APPROVAL_CAPABILITIES[gate]
        grants = [
            grant
            for grant in await self._require_capabilities().approval_grants_for(actor, on=today)
            if grant.capability is capability
        ]
        empty = ApprovableSelection(
            gate=gate, total=0, content_ids=(), limit=limit, truncated=False
        )
        if not grants:
            return empty
        stage = next(
            (
                workflow_stage
                for workflow_stage, mapped in STAGE_APPROVAL_GATES.items()
                if mapped is gate
            ),
            None,
        )
        if stage is None:  # pragma: no cover - every gate has a stage
            return empty

        work_queue = await self._work_queue(actor, on=today, scope=query.scope)
        scope = query.scope or default_scope(work_queue.held)
        # The lane, the group, the page and any stage the caller asked for come
        # off; the gate's stage goes on. Everything else the person narrowed by -
        # search, channel, platform, responsible person, dates, priority, format
        # and the scope tab - stays, because a select-all inside a filtered board
        # must mean "all of what I am looking at".
        resolved = replace(
            query.with_scope(scope), group=None, lane=None, stage=stage, limit=limit, offset=0
        )
        conditions = [
            *content_conditions(resolved, work_queue=work_queue, tz=self._timezone, today=today),
            approvable_by(grants),
        ]
        total = await self._session.scalar(
            select(func.count()).select_from(PrContentItem).where(*conditions)
        )
        rows = await self._session.execute(
            select(PrContentItem.id).where(*conditions).order_by(*content_order_by()).limit(limit)
        )
        content_ids = tuple(rows.scalars().all())
        return ApprovableSelection(
            gate=gate,
            total=total or 0,
            content_ids=content_ids,
            limit=limit,
            truncated=(total or 0) > len(content_ids),
        )

    # No separate "what is my default scope" query. The default arrives on
    # :attr:`ContentPage.scope` with the page it applied to, which is the only
    # moment a client needs it - and a second endpoint answering the same
    # question is a second place for it to be answered differently.
    async def _work_queue(
        self, actor: Actor, *, on: date, scope: PrContentViewScope | None
    ) -> ActorWorkQueue:
        """What this actor may act on, or a bare identity for ``ALL``.

        ``ALL`` reads nothing from the capability table, so an unconfigured
        :class:`PrQueryService` still serves the unnarrowed list - which is what
        keeps this change from making every existing caller construct an
        authorization service.
        """
        if scope is PrContentViewScope.ALL:
            return ActorWorkQueue(user_id=actor.user_id)
        capabilities = self._require_capabilities()
        # Step 1F.2.7a. Both, from the one service: the capability set decides
        # which kinds of work this queue contains, and the grants decide which
        # items its review kind reaches. Asked together so a scope and a grant
        # read a moment apart cannot describe two different people.
        held = await capabilities.capabilities_for_actor(actor, on=on)
        return ActorWorkQueue.for_capabilities(
            actor.user_id,
            held=held,
            approval_grants=await capabilities.approval_grants_for(actor, on=on),
        )

    async def get_content_review_context(
        self, *, actor: Actor, content_id: uuid.UUID
    ) -> ContentReviewContext:
        """Everything a reviewer needs, bound to the draft now on screen.

        Raises:
            PrNotFoundError: No such content, or it has no versions yet.
        """
        require_permission(actor, PR_READ_PERMISSION)
        content = await self._require_content(content_id)
        version = await self._current_version(content_id)
        if version is None:
            raise PrNotFoundError(
                "PR content has no versions to review",
                details={"content_id": str(content_id)},
            )

        for_version = await self._session.execute(
            select(PrAiReview)
            .where(
                PrAiReview.content_id == content_id,
                PrAiReview.reviewed_version == version.version_no,
            )
            .order_by(PrAiReview.reviewed_at.desc(), PrAiReview.created_at.desc())
        )
        reviews = list(for_version.scalars().all())
        gating = next(
            (review for review in reviews if review.review_type is PrAiReviewType.FULL_REVIEW),
            None,
        )

        approvals = await self._session.execute(
            select(PrApprovalEvent)
            .where(PrApprovalEvent.content_id == content_id)
            .order_by(PrApprovalEvent.decided_at.asc(), PrApprovalEvent.created_at.asc())
        )
        tasks = await self._session.execute(
            select(PrTask).where(PrTask.content_id == content_id).order_by(PrTask.created_at.asc())
        )

        return ContentReviewContext(
            content=content,
            current_version=version,
            targets=tuple(await self._targets(content_id)),
            tasks=tuple(tasks.scalars().all()),
            ai_review=gating,
            ai_reviews_for_version=tuple(reviews),
            approvals=tuple(approvals.scalars().all()),
        )

    async def list_content_versions(
        self, *, actor: Actor, content_id: uuid.UUID, limit: int = 50
    ) -> Sequence[PrContentVersion]:
        """Every draft of one item, newest first."""
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            select(PrContentVersion)
            .where(PrContentVersion.content_id == content_id)
            .order_by(PrContentVersion.version_no.desc())
            .limit(limit)
        )
        return result.scalars().all()

    # --- Resolving what a person typed ------------------------------------
    async def get_content_by_code(self, *, actor: Actor, code: str) -> PrContentItem:
        """One content item by the code people say out loud.

        Exact match only. A code is a label a person read off a screen, and
        "close enough" resolution on an identifier is how the wrong thing gets
        approved - see :meth:`search_contents` for the fuzzy path, which never
        resolves on its own.
        """
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            select(PrContentItem).where(PrContentItem.code == code.strip())
        )
        content = result.scalars().one_or_none()
        if content is None:
            raise PrNotFoundError("No PR content item with that code", details={"code": code})
        return content

    async def search_contents(
        self, *, actor: Actor, text: str, limit: int = 10
    ) -> Sequence[PrContentItem]:
        """Content whose code or title contains ``text``, newest first.

        Returns **candidates**, never a decision. A caller with two hits asks;
        it does not pick the better one. Deliberately a substring match rather
        than anything cleverer: Step 1D was told not to build a semantic search
        subsystem, and a plain ``ILIKE`` is honest about how much it knows.
        """
        require_permission(actor, PR_READ_PERMISSION)
        needle = f"%{text.strip()}%"
        result = await self._session.execute(
            select(PrContentItem)
            .where(or_(PrContentItem.code.ilike(needle), PrContentItem.title.ilike(needle)))
            .order_by(PrContentItem.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_task_by_code(self, *, actor: Actor, code: str) -> PrTask:
        """One task by its ``TSK-…`` code."""
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(select(PrTask).where(PrTask.code == code.strip()))
        task = result.scalars().one_or_none()
        if task is None:
            raise PrNotFoundError("No PR task with that code", details={"code": code})
        return task

    async def search_channels(
        self, *, actor: Actor, text: str, limit: int = 10
    ) -> Sequence[PrChannel]:
        """Channels whose code or name contains ``text``. Candidates, not a pick."""
        require_permission(actor, PR_READ_PERMISSION)
        needle = f"%{text.strip()}%"
        result = await self._session.execute(
            select(PrChannel)
            .where(or_(PrChannel.code.ilike(needle), PrChannel.name.ilike(needle)))
            .order_by(PrChannel.code.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def content_awaiting(
        self, *, actor: Actor, on: date | None = None, limit: int = 25
    ) -> Sequence[PrContentItem]:
        """Content this actor may actually decide right now, oldest first.

        Oldest first because this answers "what is waiting for me" and the thing
        that has waited longest is the thing to look at.

        Step 1F.2.7a took the ``stages`` argument **away**, and the removal is
        the fix. Callers used to compute the gates from a capability set and pass
        them in, which meant every client held half the rule and none of them
        held the scope half at all - so a member granted *Duyệt Trưởng nhóm* over
        two Facebook channels was shown every item at that gate, including the
        ones the write would refuse. There is now nothing for a caller to get
        wrong: this method asks
        :meth:`~meobot.application.pr_capability_service.PrCapabilityService.approval_grants_for`
        for the grants that authorise this person and filters on their scopes in
        SQL, so the list is exactly what
        :meth:`~meobot.application.pr_capability_service.PrCapabilityService.can_approve`
        would admit, item by item.

        Empty for somebody with no effective grant, whatever their role.
        """
        require_permission(actor, PR_READ_PERMISSION)
        grants = await self._require_capabilities().approval_grants_for(actor, on=on)
        if not grants:
            return []
        result = await self._session.execute(
            select(PrContentItem)
            .where(approvable_by(grants))
            .order_by(PrContentItem.updated_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    # --- Tasks ------------------------------------------------------------
    async def list_tasks(
        self,
        *,
        actor: Actor,
        content_id: uuid.UUID | None = None,
        status: PrTaskStatus | None = None,
        open_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[PrTask]:
        """Tasks, by deadline then creation.

        ``open_only`` excludes ``DONE`` and ``CANCELLED`` - the two statuses
        nothing moves out of - rather than naming an "open" set that would have
        to be kept in step with the transition matrix.
        """
        require_permission(actor, PR_READ_PERMISSION)
        statement = select(PrTask)
        if content_id is not None:
            statement = statement.where(PrTask.content_id == content_id)
        if status is not None:
            statement = statement.where(PrTask.status == status)
        if open_only:
            statement = statement.where(
                PrTask.status.not_in(sorted(TERMINAL_TASK_STATUSES, key=lambda s: s.value))
            )
        statement = (
            statement.order_by(PrTask.deadline.asc(), PrTask.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def list_overdue_tasks(
        self, *, actor: Actor, now: datetime | None = None, limit: int = 50
    ) -> Sequence[PrTask]:
        """Unfinished tasks whose deadline has passed.

        A task with no deadline is never overdue - it was never promised for a
        date - and a ``DONE`` or ``CANCELLED`` task is not overdue either, even
        if it finished late. What "late" means for reporting is a Step 1B
        question about metrics, not this list.
        """
        require_permission(actor, PR_READ_PERMISSION)
        moment = now or utcnow()
        result = await self._session.execute(
            select(PrTask)
            .where(
                PrTask.deadline.is_not(None),
                PrTask.deadline < moment,
                PrTask.status.not_in(sorted(TERMINAL_TASK_STATUSES, key=lambda s: s.value)),
            )
            .order_by(PrTask.deadline.asc())
            .limit(limit)
        )
        return result.scalars().all()

    # --- Channels ---------------------------------------------------------
    async def get_channel(self, *, actor: Actor, channel_id: uuid.UUID) -> ChannelDetail:
        require_permission(actor, PR_READ_PERMISSION)
        channel = await self._session.get(PrChannel, channel_id)
        if channel is None:
            raise PrNotFoundError(
                "No PR channel with that id", details={"channel_id": str(channel_id)}
            )
        platform = await self._session.get(PrPlatform, channel.platform_id)
        # One capability question, asked once and reused for all three flags:
        # registering channels, editing them, assigning people to them and
        # writing down their numbers are the same job and have always been the
        # same grant. Three names rather than one so a later step can separate
        # them without every client having to notice.
        may_manage = await self._require_capabilities().allows(
            actor, PrCapability.PR_CHANNEL_MANAGE
        )
        return ChannelDetail(
            channel=channel,
            assignments=tuple(
                await self.list_channel_assignments(actor=actor, channel_id=channel_id)
            ),
            platform_code=platform.code if platform is not None else None,
            platform_name=platform.name if platform is not None else None,
            can_edit_channel=may_manage,
            can_record_metrics=may_manage,
            can_manage_assignments=may_manage,
        )

    async def list_channels(
        self, *, actor: Actor, brand_id: uuid.UUID | None = None, limit: int = 100
    ) -> Sequence[PrChannel]:
        require_permission(actor, PR_READ_PERMISSION)
        statement = select(PrChannel)
        if brand_id is not None:
            statement = statement.where(PrChannel.brand_id == brand_id)
        result = await self._session.execute(statement.order_by(PrChannel.code.asc()).limit(limit))
        return result.scalars().all()

    async def list_channel_assignments(
        self,
        *,
        actor: Actor,
        channel_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        assignment_role: PrChannelAssignmentRole | None = None,
        open_only: bool = False,
        limit: int = 100,
    ) -> Sequence[PrChannelAssignment]:
        """Assignments, in date order. At least one filter is usually supplied."""
        require_permission(actor, PR_READ_PERMISSION)
        statement = select(PrChannelAssignment)
        if channel_id is not None:
            statement = statement.where(PrChannelAssignment.channel_id == channel_id)
        if user_id is not None:
            statement = statement.where(PrChannelAssignment.user_id == user_id)
        if assignment_role is not None:
            statement = statement.where(PrChannelAssignment.assignment_role == assignment_role)
        if open_only:
            statement = statement.where(PrChannelAssignment.effective_to.is_(None))
        result = await self._session.execute(
            statement.order_by(PrChannelAssignment.effective_from.asc()).limit(limit)
        )
        return result.scalars().all()

    # --- Authorization, for a client deciding what to offer ---------------
    async def capabilities_for_actor(
        self, *, actor: Actor, on: date | None = None
    ) -> frozenset[PrCapability]:
        """Everything this actor may currently do in the PR module.

        What a client renders buttons from. It is **not** authorization: the
        write path re-asks
        :meth:`~meobot.application.pr_capability_service.PrCapabilityService.require`
        at the moment of the write, because a screen drawn a minute ago is not
        a decision.
        """
        return await self._require_capabilities().capabilities_for_actor(actor, on=on)

    async def capabilities_for_user(
        self, *, actor: Actor, user_id: uuid.UUID, on: date | None = None
    ) -> frozenset[PrCapability]:
        """The grant-backed capabilities one person holds.

        Reading somebody else's grants is reading PR data, so it takes the read
        permission - the same one that guards their content.
        """
        require_permission(actor, PR_READ_PERMISSION)
        return await self._require_capabilities().granted_capabilities(user_id, on=on)

    async def approval_grants(
        self,
        *,
        actor: Actor,
        capability: PrCapability | None = None,
        user_id: uuid.UUID | None = None,
        on: date | None = None,
    ) -> Sequence[PrCapabilityGrant]:
        """Every active approval grant, with its scope. What ``/pr/permissions`` lists.

        Reading who may approve what is reading PR data, so it takes the read
        permission - the same one that guards their content. Administering the
        grants is a different and much narrower permission, checked by
        :class:`~meobot.application.pr_capability_service.PrCapabilityService`.
        """
        require_permission(actor, PR_READ_PERMISSION)
        return await self._require_capabilities().active_grants(
            user_id=user_id, capability=capability, on=on
        )

    async def users_allowed_to(
        self, *, actor: Actor, capability: PrCapability, on: date | None = None
    ) -> Sequence[PrCapabilityGrant]:
        """Who may perform one review gate right now.

        Answers "who is allowed to perform TEAM_LEAD_REVIEW" and the same for
        the other two gates. Returns an empty sequence for a capability decided
        by role alone, because "who may create content" is a question about the
        permission matrix rather than about grants.
        """
        require_permission(actor, PR_READ_PERMISSION)
        return await self._require_capabilities().users_with(capability, on=on)

    async def team_lead_approver(
        self, *, actor: Actor, content_id: uuid.UUID, version_reviewed: int
    ) -> PrApprovalEvent | None:
        """Who approved Team Lead review for one draft, if anybody has.

        A client rendering the Head gate can name the signature already on this
        version. Since Step 1F.2.2 that is **display only** - the same person may
        sign the Head gate too, so this answer no longer withholds a button. What
        it still answers is "has the first gate been passed", which is the Head
        gate's remaining prerequisite.
        """
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            select(PrApprovalEvent)
            .where(
                PrApprovalEvent.content_id == content_id,
                PrApprovalEvent.version_reviewed == version_reviewed,
                PrApprovalEvent.approval_stage == PrApprovalStage.TEAM_LEAD_REVIEW,
                PrApprovalEvent.decision == PrApprovalDecision.APPROVED,
            )
            .order_by(PrApprovalEvent.decided_at.desc(), PrApprovalEvent.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    def _require_capabilities(self) -> PrCapabilityService:
        if self._capabilities is None:
            raise PrValidationError(
                "This query needs a PrCapabilityService; construct PrQueryService with one",
                details={"reason": "capability_service_not_configured"},
            )
        return self._capabilities

    # --- Internals --------------------------------------------------------
    async def _read_row_at_a_time(self) -> None:
        """Turn PostgreSQL's JIT off for the rest of this transaction. Step 1F.2.3f.6c.

        The board's statements are OLTP-shaped: a few hundred rows, each with
        one or two cheap correlated lookups. The planner nevertheless prices
        those lookups per *candidate* row, so on a table of twenty thousand
        items the estimate crosses ``jit_optimize_above_cost`` and PostgreSQL
        compiles and optimises the whole filter expression before running it.
        Measured on a 20 000-row table (60% archived) with the month predicate:
        the rows cost 67 ms and the compilation 4.4 s with the old
        per-stage ``CASE``, still 0.7 s after that expression was reduced to two
        subplans per bound. JIT buys nothing back on a statement this shape -
        it exists for long analytical scans - so it is switched off for the
        transaction, which on the API is the request. A no-op on SQLite.

        ``SET LOCAL`` rather than a server setting: the decision is about these
        statements and travels with them, and a deployment that never runs
        this code path keeps its defaults.
        """
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            await self._session.execute(text("SET LOCAL jit = off"))

    async def _require_content(self, content_id: uuid.UUID) -> PrContentItem:
        content = await self._session.get(PrContentItem, content_id)
        if content is None:
            raise PrNotFoundError(
                "No PR content item with that id", details={"content_id": str(content_id)}
            )
        return content

    async def _current_version(self, content_id: uuid.UUID) -> PrContentVersion | None:
        result = await self._session.execute(
            select(PrContentVersion)
            .where(PrContentVersion.content_id == content_id)
            .order_by(PrContentVersion.version_no.desc())
            .limit(1)
        )
        return result.scalars().one_or_none()

    async def content_resources(
        self, *, actor: Actor, content_id: uuid.UUID
    ) -> Sequence[PrContentResource]:
        """The review material attached to one item, most important first.

        Gated on the plain read permission, like every other read here, and
        **deliberately no narrower than that**: resources are the context a
        reviewer judges against, and a reviewer who cannot see the brief cannot
        do the job. Editing them is stricter - see
        :class:`~meobot.application.pr_content_resource_service.PrContentResourceService`.

        Ordering, in three keys:

        1. ``required_for_review`` **descending**, so the things somebody was
           told to read come first. This is the whole of what that flag does;
        2. ``resource_type``, so the list groups rather than interleaves. It
           sorts by the stored string, which is alphabetical rather than the
           enum's own order - accepted here because the point is stable grouping
           and not a meaningful ranking of kinds;
        3. ``created_at`` then ``id``, which makes the order **total**. Without
           the last key two resources added in the same transaction could come
           back in either order, and a list that reshuffles between reloads reads
           as a bug even when nothing changed.

        One query. The board deliberately does not call this - see
        :meth:`content_page`, which carries the content type and no resources at
        all, because sixty cards each fetching their references is the N+1 this
        module exists to avoid.
        """
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            select(PrContentResource)
            .where(PrContentResource.content_id == content_id)
            .order_by(
                PrContentResource.required_for_review.desc(),
                PrContentResource.resource_type.asc(),
                PrContentResource.created_at.asc(),
                PrContentResource.id.asc(),
            )
        )
        return result.scalars().all()

    async def content_derivatives(
        self, *, actor: Actor, content_id: uuid.UUID
    ) -> Sequence[PrContentDerivative]:
        """The produced re-cuts of one item, oldest first. Step 1F.2.3f.

        Gated on the plain read permission and **deliberately no narrower**: what
        files came out of a piece is part of reading it, and the same list is
        what the publication form's "Sản phẩm đã đăng" picker is made of.
        Recording one is stricter - see
        :class:`~meobot.application.pr_content_asset_service.PrContentAssetService`.

        Oldest first, because these accumulate: the master's cutdown was made
        before the caption variant, and a list that put the newest at the top
        would reorder itself every time somebody added one. ``id`` is the second
        key, which makes the order **total** - two derivatives recorded in one
        transaction would otherwise come back in either order.

        One query, and the board deliberately does not call it: sixty cards each
        fetching their outputs is the N+1 this module exists to avoid - see
        :meth:`content_page`, which carries neither derivatives nor publications.
        """
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            select(PrContentDerivative)
            .where(PrContentDerivative.content_id == content_id)
            .order_by(PrContentDerivative.created_at.asc(), PrContentDerivative.id.asc())
        )
        return result.scalars().all()

    async def content_destinations(
        self, *, actor: Actor, content_id: uuid.UUID
    ) -> Sequence[PrContentDestination]:
        """The product and landing pages one item points at. Step 1F.2.3f.

        Read permission, like its neighbours, and the same total ordering for the
        same reason. These are durable content metadata: they are as visible at
        ``IDEA`` as at ``ARCHIVED``, because where a piece sends people is a fact
        about the campaign rather than about how far the work has got.
        """
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            select(PrContentDestination)
            .where(PrContentDestination.content_id == content_id)
            .order_by(PrContentDestination.created_at.asc(), PrContentDestination.id.asc())
        )
        return result.scalars().all()

    async def content_submissions(
        self, *, actor: Actor, content_id: uuid.UUID
    ) -> Sequence[PrProductionSubmission]:
        """Every production file handed over for this item, in submission order.

        Step 1F.2.3f. The masters, beside the derivatives cut from them - both
        halves of "Sản phẩm sau sản xuất", and both entries in the publication
        form's output picker.

        **Not hidden after publication.** The submissions table is append-only
        and a published piece is exactly when somebody wants to look at what went
        out; a screen that dropped them once the stage moved would lose the file
        an internal reviewer approved at the moment it became historical record.

        By ``submission_no``, which is allocated under the content row's lock and
        is therefore already total - no tiebreaker needed, unlike the two lists
        above.
        """
        require_permission(actor, PR_READ_PERMISSION)
        result = await self._session.execute(
            select(PrProductionSubmission)
            .where(PrProductionSubmission.content_id == content_id)
            .order_by(PrProductionSubmission.submission_no.asc())
        )
        return result.scalars().all()

    async def _targets(self, content_id: uuid.UUID) -> Sequence[PrContentTarget]:
        result = await self._session.execute(
            select(PrContentTarget)
            .where(PrContentTarget.content_id == content_id)
            .order_by(PrContentTarget.created_at.asc())
        )
        return result.scalars().all()


__all__: list[str] = [
    "ApprovableSelection",
    "ArchiveCandidates",
    "ChannelDetail",
    "ContentDetail",
    "ContentPage",
    "ContentReviewContext",
    "PrQueryService",
]
