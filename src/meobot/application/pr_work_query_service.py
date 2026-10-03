"""Reading the Work Ledger: what is on somebody's plate, and what they achieved.

M1. Two questions that must never be answered by one query, which is why this
module keeps them apart at the type level:

**Period performance** - *"what did this person achieve in September"* - is
filtered by a date range and driven by ``counted_at``. It is the KPI half, and
it is deliberately narrow: a contribution belongs to the period containing the
instant somebody independently validated it, not the period it was assigned in
and not the one the contributor said they finished in.

**Open and overdue work** - *"what is still outstanding"* - is filtered by
**status** and takes no date range at all. That is the requirement that
selecting *"Tháng này"* must never make June's unfinished work disappear, and
:meth:`PrWorkQueryService.summary` satisfies it by construction: the overdue
figure is computed without touching the period bounds, so there is no
combination of filters that can hide it.

Two counts, not one
-------------------

A shoot with three people is **one work item** and **three contributions**. The
department did one shoot; three people each did a day's work. Both numbers are
real and they are different, so :class:`WorkSummary` carries both and never
derives one from the other.

Where the day boundary comes from
----------------------------------

:func:`~meobot.application.pr_content_query.day_bounds`, unchanged and
unwrapped. It is the one place in this repository where a Vietnamese calendar
day becomes a pair of UTC instants, and a second implementation here would
eventually disagree with the content board about when Tuesday started.

Visibility, without a team model
---------------------------------

MeoBot has no department, team or manager relationship, and M1 declined to
invent one out of channel assignments. So the scopes below are **explicit
relationships**: work I contribute to, work I created, work I assigned, and -
for a holder of ``PR_WORK_MANAGE`` - the module-wide view. Somebody without
that capability cannot ask about another person's work at all; the service
refuses the filter rather than silently narrowing it, so a client never shows a
figure that means something other than what it says.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Select,
    SQLColumnExpression,
    and_,
    case,
    exists,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_query import day_bounds
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.core.time import utcnow
from meobot.db.models.pr_work import PrWorkContribution, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.domain.identity.models import Actor
from meobot.domain.pr.content_work import PrContentWorkKind, content_work_source_key
from meobot.domain.pr.errors import PrPermissionDeniedError
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.work import (
    OPEN_WORK_STATUSES,
    OVERDUE_STATUSES,
    PrWorkCountStatus,
    PrWorkSourceType,
    PrWorkStatus,
)


#: The largest page this service will return, and the default.
def work_period_instant() -> SQLColumnExpression[datetime | None]:
    """**When a work item belongs to a reporting month.** Post-M4.

    ``coalesce(execution_at, accepted_at, created_at)``, and each fallback is a
    deliberate step down in confidence rather than a convenience:

    * ``execution_at`` is the real answer, and content-derived and recurring
      work always have it;
    * ``accepted_at`` is the next best fact: *when this job entered somebody's
      workload*. A manually assigned job has it from the moment it is filed, and
      a proposal acquires it the moment a manager accepts;
    * ``created_at`` is the floor. A proposal nobody has accepted still has to
      appear in some month, and the month it was filed in is the only honest one
      left.

    **``due_at`` is deliberately not in this list.** A deadline is not an
    assignment date and not an execution date, and it must not decide which
    reporting month owns a row. Work assigned on 30 September and due on 2
    October is September's work - the department did it in September, and a
    month attributed by deadline would move it into October's figures while
    nobody moved anything. The reverse case is worse: work assigned on 30 August
    and due on 5 September would silently leave August, which is the month it
    belongs to and possibly a month somebody has already reported on.

    The deadline stays fully visible as *"Hạn"* on every card. It answers "when
    must this be finished", which is a different question from "which month is
    this in", and this expression is the second one.

    Deliberately also **not** the same thing as ``execution_at``. This expression
    decides *which month a row is listed under*; ``execution_at`` alone is what a
    screen may print as "Thực hiện". Collapsing them would put an assignment date
    under a heading claiming the work was performed.
    """
    return func.coalesce(PrWorkItem.execution_at, PrWorkItem.accepted_at, PrWorkItem.created_at)


MAX_WORK_PAGE = 200
DEFAULT_WORK_PAGE = 50


class PrWorkScope(StrEnum):
    """Which slice of the ledger a query is about.

    Each is an **existing relationship** rather than an inferred grouping. There
    is deliberately no ``TEAM``: MeoBot models no team, and
    ``PrContentViewScope.TEAM``'s channel-assignment proxy would be a hierarchy
    invented out of something that is not one - which M1 was explicitly told not
    to do.
    """

    #: *Công việc của tôi.* Work this actor contributes to. The employee view,
    #: and the only scope somebody without ``PR_WORK_MANAGE`` may use.
    MINE = "MINE"
    #: *Tôi giao.* Work this actor assigned or created. A manager's own book.
    ASSIGNED_BY_ME = "ASSIGNED_BY_ME"
    #: *Chờ tôi xử lý.* Proposals waiting to be accepted and finished work
    #: waiting to be validated - **minus anything this actor contributed to**,
    #: because those are exactly the items they may not decide. The queue is
    #: therefore what the actor can actually act on rather than what exists.
    NEEDS_MY_DECISION = "NEEDS_MY_DECISION"
    #: Everything. ``PR_WORK_MANAGE`` only.
    ALL = "ALL"


class PrWorkPreset(StrEnum):
    """The tabs an employee sees, as a server-side filter.

    The first four narrow by **date** and the last three by **status**, and the
    split is the point: a status preset ignores ``date_from``/``date_to``
    entirely, so no combination of parameters can make outstanding work vanish
    behind a period filter.
    """

    #: All open and closed work matching the other filters. No date narrowing.
    #:
    #: **The manager default**, post-M4. It used to be ``TODAY``, which made the
    #: management screen answer "what is happening right now" when the question
    #: a reporting month is selected for is "what work exists in this month".
    #: Combined with a ``period_id`` it means *Tất cả tháng*.
    #:
    #: **"All" is all operational work, not every row ever stored.** A
    #: ``CANCELLED`` item is abandoned work kept for its history; it is in
    #: nobody's workload and no figure counts it, and it is out of every query
    #: that does not name it - see :attr:`WorkQuery.status`. This is not a
    #: preset rule: it holds under every preset, and the preset is not where it
    #: is applied.
    ALL = "ALL"
    TODAY = "TODAY"
    #: *Hôm qua.* The other single day anybody asks for by name, and the one a
    #: person checks when they are catching up on what was missed.
    YESTERDAY = "YESTERDAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    #: Uses the caller's ``date_from``/``date_to``.
    CUSTOM = "CUSTOM"
    #: *Nợ việc.* Past its deadline and not finished. **Never date-filtered.**
    OVERDUE = "OVERDUE"
    #: *Sắp tới.* Open work with a deadline still ahead. Never date-filtered.
    UPCOMING = "UPCOMING"
    #: Open work, whatever its deadline. Never date-filtered.
    OPEN = "OPEN"


class PrWorkDateField(StrEnum):
    """Which timestamp a date preset filters on.

    Mirrors ``PrContentDateField``, and exists for the same reason: *"due this
    week"* and *"credited to me this week"* are different questions about the
    same rows, and a client that could only ask one of them would have to do
    date arithmetic on the other.
    """

    #: The default, and what the operational tabs use.
    DUE_AT = "DUE_AT"
    #: **When the work was performed.** Post-M4, and what the unified monthly
    #: view filters on: a day filter inside a reporting month is a question
    #: about when things happened, not about when they were due.
    #:
    #: Filters on :func:`work_period_instant` rather than on ``execution_at``
    #: alone, so a manually assigned job - which has no execution date and never
    #: will - is still reachable by "Hôm nay" through the day it was assigned,
    #: rather than silently dropping out of every day filter on the screen.
    EXECUTION_AT = "EXECUTION_AT"
    #: The performance question. Filters on the **contribution's** ``counted_at``.
    COUNTED_AT = "COUNTED_AT"
    CREATED_AT = "CREATED_AT"


@dataclass(frozen=True, slots=True)
class WorkQuery:
    """One request for a page of work."""

    scope: PrWorkScope = PrWorkScope.MINE
    preset: PrWorkPreset = PrWorkPreset.ALL
    date_field: PrWorkDateField = PrWorkDateField.DUE_AT
    date_from: date | None = None
    date_to: date | None = None
    #: Narrow to one person's contributions. ``PR_WORK_MANAGE`` only when it is
    #: somebody other than the caller.
    user_id: uuid.UUID | None = None
    work_type_id: uuid.UUID | None = None
    #: **M4A.** Narrow to where the work came from - *Thủ công*, *Nội dung*,
    #: *Định kỳ*. A filter and **not** a permission: it narrows whatever the
    #: caller's scope already allows, exactly like ``content_id`` above, so an
    #: employee filtering to ``CONTENT`` sees their own content-derived work and
    #: nothing new.
    #:
    #: It earns its place because the three sources answer to different people.
    #: Content work is produced by the workflow and nobody files it; recurring
    #: work is produced by a template somebody activated; manual work is the
    #: only kind a person typed. A validator sweeping a routine queue and a
    #: manager auditing what the projector wrote are two different jobs, and
    #: without this filter both read the same undifferentiated list.
    source_type: PrWorkSourceType | None = None
    #: **M3.1.** Narrow to the work one content item produced, for the *Công
    #: việc liên quan* section on content detail.
    #:
    #: A filter and **not** a permission: it narrows whatever the caller's scope
    #: already allows, so an employee asking for a colleague's content sees their
    #: own contributions on it and nothing else. Making it a way to read every
    #: contributor on any content item would be a back door into exactly the
    #: department-wide view ``PR_WORK_VIEW_ALL`` exists to gate.
    content_id: uuid.UUID | None = None
    #: One lifecycle status, or ``None`` for **the operational default**.
    #:
    #: ``None`` is not "any status". It is every status **except**
    #: ``CANCELLED``: cancelled work is kept - the row, its history and its
    #: audit trail stay, and nothing here deletes it - but it is not on
    #: anybody's dashboard, in any counter or on any page of the list unless
    #: the caller asks for it by name. ``status=CANCELLED`` is that explicit
    #: view, and the only way a cancelled row reaches a list or a summary.
    #:
    #: Decided **here**, in the query, rather than in the browser: a client
    #: that filtered cancelled rows out of a page it had already been handed
    #: would still page and count them, and any other client would show them.
    status: PrWorkStatus | None = None
    #: **The outer boundary of a management view.** Post-M4.
    #:
    #: A reporting period, and the query is bounded to the days it covers before
    #: any preset narrows it further. That ordering is the whole of the change:
    #: the screen used to ask "what is due today" and try to reconstruct a month
    #: from it, and now it asks "what work exists in September" and narrows.
    #:
    #: Bounded on :func:`work_period_instant`, so every source is reachable -
    #: content and recurring work by the day it happened, manual work by the day
    #: it entered somebody's workload. Never by a deadline.
    period_id: uuid.UUID | None = None
    search: str | None = None
    limit: int = DEFAULT_WORK_PAGE
    offset: int = 0


@dataclass(frozen=True, slots=True)
class WorkPage:
    """One page of work items, with the contributions and types they need."""

    items: tuple[PrWorkItem, ...] = ()
    total: int = 0
    limit: int = DEFAULT_WORK_PAGE
    offset: int = 0
    #: Every contribution on every item on this page, fetched in one query.
    #: The alternative - one query per card - is the N+1 the channel list went
    #: to some trouble to avoid, and a work list has more rows than that one.
    contributions: tuple[PrWorkContribution, ...] = ()
    work_types: tuple[PrWorkType, ...] = ()
    contributor_names: dict[uuid.UUID, str] = field(default_factory=dict)
    #: M3. ``content_id`` -> ``CNT-2026-000042``, for the source-derived rows on
    #: this page. One query for the page rather than one per card - the same
    #: N+1 the contributor names above are batched to avoid, and a work board
    #: after M3 is mostly source-derived rows.
    content_codes: dict[uuid.UUID, str] = field(default_factory=dict)
    #: Post-M4. ``occurrence_id`` -> ``(template_id, template_name)`` for the
    #: recurring rows on this page, so a card can offer *"đi tới công việc định
    #: kỳ"* by name. One query for the page, like ``content_codes`` above it -
    #: a monthly list of routine work is mostly these rows.
    recurring_templates: dict[uuid.UUID, tuple[uuid.UUID, str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkSummary:
    """The figures a summary strip shows. **Five different facts, five counts.**

    The period half and the operational half are computed differently on
    purpose, and the difference is the milestone's central claim:

    * ``created`` / ``accepted`` / ``completed`` / ``approved`` count **work
      items** whose respective timestamp falls inside the period. Four separate
      timestamps, because a piece of work created in August, finished in August
      and validated in September is one row that belongs to different figures in
      different months;
    * ``counted_contributions`` counts **contributions** whose ``counted_at``
      falls inside the period. This is the employee-workload number, and the one
      a future quota will be measured against;
    * ``open`` / ``in_progress`` / ``awaiting_validation`` / ``overdue`` take
      **no period at all**. They describe now. Selecting a month cannot change
      them, which is what stops June's unfinished work disappearing behind an
      August filter.
    """

    period_from: datetime | None = None
    period_to: datetime | None = None

    # --- Attributable to the period ---------------------------------------
    created: int = 0
    accepted: int = 0
    completed: int = 0
    approved: int = 0
    #: Distinct work items with at least one contribution counted in the period.
    counted_work_items: int = 0
    #: One per person per counted job. **Not** the same number as above when a
    #: job had several contributors - and both are true.
    counted_contributions: int = 0

    # --- True right now, whatever period was asked for --------------------
    open: int = 0
    in_progress: int = 0
    awaiting_validation: int = 0
    overdue: int = 0
    proposed: int = 0


class PrWorkQueryService:
    """Reads the ledger. Writes nothing, and decides nothing about counting.

    Args:
        session: Unit of work.
        capabilities: Decides whether a caller may widen a scope past their own
            work.
        timezone: The business timezone the date presets are expressed in - the
            same ``settings.timezone`` the content board uses, so "hôm nay"
            means one thing across the panel.
    """

    def __init__(
        self,
        session: AsyncSession,
        capabilities: PrCapabilityService,
        periods: PrWorkPeriodService,
        *,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self._session = session
        self._capabilities = capabilities
        # Post-M4. Held rather than reimplemented: a reporting period becoming a
        # pair of Vietnamese calendar instants happens in exactly one place in
        # MeoBot, and a query service that did its own arithmetic is how
        # "September" comes to mean two things on two screens.
        self._periods = periods
        self._timezone = timezone or ZoneInfo("UTC")

    # =====================================================================
    # Listing
    # =====================================================================
    async def page(
        self, *, actor: Actor, query: WorkQuery, now: datetime | None = None
    ) -> WorkPage:
        """One bounded page of work, plus everything a card needs to draw.

        Four queries however many rows come back: the page, the count, the
        contributions for the page, and the work types for the page. A card
        never issues its own request.
        """
        moment = now or utcnow()
        conditions = await self._conditions(actor=actor, query=query, now=moment)
        bounded = max(1, min(query.limit, MAX_WORK_PAGE))
        start = max(0, query.offset)

        total = await self._session.scalar(
            select(func.count()).select_from(PrWorkItem).where(*conditions)
        )
        rows = (
            (
                await self._session.execute(
                    select(PrWorkItem)
                    .where(*conditions)
                    # **Execution date first, nulls last.** Post-M4, and the
                    # order a daily operational list has to be readable in: what
                    # a person plans around is the day the work happens, and a
                    # month sorted by deadline interleaves Tuesday's seeding
                    # with a script whose deadline happens to fall the same day.
                    #
                    # Work with no execution date is the **tail**, never hidden -
                    # a manual job with a deadline and no execution date is real
                    # work somebody still has to do, and assigning it a
                    # ``created_at`` to make it sortable would be inventing the
                    # date this whole change exists to stop inventing.
                    #
                    # The deadline breaks ties **within that tail**, and is
                    # presentation only: for a row with no execution date the
                    # soonest deadline is what a person plans around. It decides
                    # no month - see ``work_period_instant``, which excludes it
                    # deliberately - and the id below makes the order total, so
                    # a page boundary lands in the same place every time.
                    .order_by(
                        PrWorkItem.execution_at.asc().nulls_last(),
                        PrWorkItem.due_at.asc().nulls_last(),
                        PrWorkItem.created_at.desc(),
                        PrWorkItem.id.asc(),
                    )
                    .limit(bounded)
                    .offset(start)
                )
            )
            .scalars()
            .all()
        )
        item_ids = [row.id for row in rows]
        return WorkPage(
            items=tuple(rows),
            total=int(total or 0),
            limit=bounded,
            offset=start,
            contributions=tuple(await self._contributions_for(item_ids)),
            work_types=tuple(await self._types_for({row.work_type_id for row in rows})),
            contributor_names=await self._names_for(item_ids),
            content_codes=await self._content_codes_for(rows),
            recurring_templates=await self._recurring_templates_for(rows),
        )

    async def summary(
        self, *, actor: Actor, query: WorkQuery, now: datetime | None = None
    ) -> WorkSummary:
        """The five period figures and the four operational ones.

        The operational figures are computed from a **second** set of
        conditions that has the date predicate removed - not from the same set
        with a different aggregate. That is what makes "a period filter cannot
        hide overdue work" a property of the code rather than a promise in a
        docstring.

        **The reporting period is kept in the base**, post-M4, and the day slice
        is not. That is the distinction the summary strip has to be honest
        about: the tiles describe the *month* the screen is scoped to, and
        narrowing the list to "Hôm nay" does not renumber them. A tile that
        moved with the day filter and one that did not would be two kinds of
        number in one row with nothing saying which was which.
        """
        moment = now or utcnow()
        # Scope, identity and the reporting month: no preset, no day slice.
        # Everything below either adds its own date predicate or deliberately
        # has none.
        base = await self._conditions(
            actor=actor,
            query=WorkQuery(
                scope=query.scope,
                user_id=query.user_id,
                work_type_id=query.work_type_id,
                source_type=query.source_type,
                period_id=query.period_id,
                preset=PrWorkPreset.ALL,
            ),
            now=moment,
        )
        # **The selected reporting period wins.** Post-M4, and it fixes a real
        # arithmetic error the period selector would otherwise have introduced:
        # the preset bounds are relative to *today*, so a manager looking at
        # August in September would have had every period figure computed over
        # September's days and read zero. When a period is named it is the
        # answer to "which month", and the preset has nothing left to say.
        lower, upper = (
            self._periods.bounds(await self._periods.require_period(query.period_id))
            if query.period_id is not None
            else self._bounds(query, now=moment)
        )

        def within(column: InstrumentedAttribute[Any]) -> ColumnElement[bool]:
            """This timestamp is inside the period. Always false when unset."""
            clauses: list[ColumnElement[bool]] = [column.is_not(None)]
            if lower is not None:
                clauses.append(column >= lower)
            if upper is not None:
                clauses.append(column < upper)
            return and_(*clauses)

        def tally(condition: ColumnElement[bool]) -> ColumnElement[int]:
            # ``sum(case(...))`` rather than ``count(...) FILTER``: the offline
            # test suite runs on SQLite and this is the portable spelling.
            return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

        row = (
            await self._session.execute(
                select(
                    tally(within(PrWorkItem.created_at)),
                    tally(within(PrWorkItem.accepted_at)),
                    tally(within(PrWorkItem.completed_at)),
                    tally(within(PrWorkItem.approved_at)),
                    # No period. These describe now.
                    tally(PrWorkItem.status.in_(sorted(OPEN_WORK_STATUSES))),
                    tally(PrWorkItem.status == PrWorkStatus.IN_PROGRESS),
                    tally(PrWorkItem.status == PrWorkStatus.COMPLETED),
                    tally(PrWorkItem.status == PrWorkStatus.PROPOSED),
                    tally(
                        and_(
                            PrWorkItem.due_at.is_not(None),
                            PrWorkItem.due_at < moment,
                            PrWorkItem.status.in_(sorted(OVERDUE_STATUSES)),
                        )
                    ),
                ).where(*base)
            )
        ).one()

        counted_items, counted_contributions = await self._counted(
            actor=actor, query=query, lower=lower, upper=upper, now=moment
        )
        return WorkSummary(
            period_from=lower,
            period_to=upper,
            created=int(row[0]),
            accepted=int(row[1]),
            completed=int(row[2]),
            approved=int(row[3]),
            counted_work_items=counted_items,
            counted_contributions=counted_contributions,
            open=int(row[4]),
            in_progress=int(row[5]),
            awaiting_validation=int(row[6]),
            proposed=int(row[7]),
            overdue=int(row[8]),
        )

    async def _counted(
        self,
        *,
        actor: Actor,
        query: WorkQuery,
        lower: datetime | None,
        upper: datetime | None,
        now: datetime,
    ) -> tuple[int, int]:
        """The two counted figures, from ``pr_work_contributions``.

        Read from the contribution table rather than the item table, because
        counting is a property of a person's share: a three-person shoot is one
        counted work item and three counted contributions, and a query over
        items could only ever produce the first.

        The scope is applied **twice**, and both halves are needed. The item
        subquery narrows to the work the caller may see; the ``user_id``
        predicate narrows to whose credit is being counted. Without the second,
        an employee asking about their own month would be handed every
        contribution on every job they touched - including their colleagues' -
        which is the same number wearing the wrong label.
        """
        conditions: list[ColumnElement[bool]] = [
            PrWorkContribution.count_status == PrWorkCountStatus.COUNTED
        ]
        if query.period_id is not None and lower is not None and upper is not None:
            # A period container is its month's whatever day its first result
            # was validated - the same predicate M2 and M6 apply, spelled over
            # the item subquery because this query has not joined the item.
            in_month = select(PrWorkItem.id).where(
                PrWorkItem.reporting_period_id == query.period_id
            )
            conditions.append(
                or_(
                    PrWorkContribution.work_item_id.in_(in_month),
                    and_(
                        PrWorkContribution.counted_at >= lower,
                        PrWorkContribution.counted_at < upper,
                    ),
                )
            )
        else:
            if lower is not None:
                conditions.append(PrWorkContribution.counted_at >= lower)
            if upper is not None:
                conditions.append(PrWorkContribution.counted_at < upper)

        # The same scope predicate the list uses, with the dates removed: which
        # *items* are in view. Reusing it means the two figures can never be
        # about different sets of work.
        scoped = await self._conditions(
            actor=actor,
            query=WorkQuery(
                scope=query.scope,
                user_id=query.user_id,
                work_type_id=query.work_type_id,
                source_type=query.source_type,
                preset=PrWorkPreset.ALL,
            ),
            now=now,
        )
        conditions.append(PrWorkContribution.work_item_id.in_(select(PrWorkItem.id).where(*scoped)))

        subject = await self._subject_user(actor=actor, query=query)
        if subject is None and query.scope is PrWorkScope.MINE:
            # "My month" means my credit, not every credit on jobs I was on.
            subject = actor.user_id
        if subject is not None:
            conditions.append(PrWorkContribution.user_id == subject)

        row = (
            await self._session.execute(
                select(
                    func.count(func.distinct(PrWorkContribution.work_item_id)),
                    func.count(PrWorkContribution.id),
                ).where(*conditions)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0)

    # =====================================================================
    # Building the filter
    # =====================================================================
    async def _conditions(
        self, *, actor: Actor, query: WorkQuery, now: datetime
    ) -> list[ColumnElement[bool]]:
        """``query`` as predicates to ``AND`` together.

        Authorization happens here, once, and it is a **refusal** rather than a
        narrowing: a caller who asks for a scope they may not have gets a
        ``403`` carrying ``reason: scope_not_permitted``. Silently reducing
        ``ALL`` to ``MINE`` would hand somebody a number labelled "the whole
        department" that is actually their own.

        **Each scope has its own gate.** They used to share one - holding
        ``PR_WORK_MANAGE`` admitted all three non-``MINE`` scopes, which meant a
        Trưởng nhóm who had assigned one job could read every colleague's
        record. Managing work and surveying it are different acts, so:

        * ``MINE`` - anybody;
        * ``ASSIGNED_BY_ME`` - ``PR_WORK_MANAGE``. Your own book, and nothing in
          it that you did not put there;
        * ``NEEDS_MY_DECISION`` - ``PR_WORK_MANAGE`` **or**
          ``PR_WORK_VALIDATE``, and the queue's contents are narrowed to what
          the holder can actually decide;
        * ``ALL`` - ``PR_WORK_VIEW_ALL``, which is Head and Admin.
        """
        conditions: list[ColumnElement[bool]] = []
        actor_id = actor.user_id
        if actor_id is None:
            raise PrPermissionDeniedError(
                "Chỉ tài khoản đã đăng nhập mới xem được công việc.",
                details={"reason": "actor_has_no_user_row"},
            )
        await self._require_scope(actor, query.scope)

        subject = await self._subject_user(actor=actor, query=query)
        if query.scope is PrWorkScope.MINE:
            conditions.append(self._contributed_by(subject or actor_id))
        elif query.scope is PrWorkScope.ASSIGNED_BY_ME:
            conditions.append(
                or_(
                    PrWorkItem.assigned_by_user_id == actor_id,
                    PrWorkItem.created_by_user_id == actor_id,
                )
            )
            if subject is not None:
                conditions.append(self._contributed_by(subject))
        elif query.scope is PrWorkScope.NEEDS_MY_DECISION:
            decision = await self.decision_queue_condition(actor)
            # ``_require_scope`` has already refused an actor who can decide
            # nothing, so this is never ``None`` here - the assert is the
            # invariant rather than a runtime branch.
            assert decision is not None
            conditions.append(decision)
        elif subject is not None:
            conditions.append(self._contributed_by(subject))

        if query.work_type_id is not None:
            conditions.append(PrWorkItem.work_type_id == query.work_type_id)
        if query.source_type is not None:
            conditions.append(PrWorkItem.source_type == query.source_type)
        if query.content_id is not None:
            # A legacy content work item names the piece on its own row; since
            # the period-container patch a piece contributes a **result** to a
            # stream, so the stream that holds one of its results is also "the
            # work this piece produced". Both, so *Công việc liên quan* keeps
            # listing across the change.
            keys = [content_work_source_key(kind, query.content_id) for kind in PrContentWorkKind]
            conditions.append(
                or_(
                    PrWorkItem.content_id == query.content_id,
                    exists().where(
                        PrWorkResult.work_item_id == PrWorkItem.id,
                        PrWorkResult.source_key.in_(keys),
                    ),
                )
            )
        if query.status is not None:
            conditions.append(PrWorkItem.status == query.status)
        else:
            # The operational default. See ``WorkQuery.status``: cancelled
            # work is reachable only by asking for it, so it never inflates a
            # page, a total or a summary tile - and a search under the default
            # view finds no cancelled row either, which keeps "Tất cả" and
            # "tìm kiếm trong Tất cả" the same set of work.
            conditions.append(PrWorkItem.status != PrWorkStatus.CANCELLED)
        if query.search:
            needle = f"%{query.search.strip()}%"
            conditions.append(or_(PrWorkItem.code.ilike(needle), PrWorkItem.title.ilike(needle)))

        conditions.extend(await self._period_conditions(query))
        conditions.extend(self._preset_conditions(query, now=now))
        return conditions

    async def _period_conditions(self, query: WorkQuery) -> list[ColumnElement[bool]]:
        """The reporting month, as the outer boundary. Post-M4.

        Applied **before** the preset, and that order is the requirement rather
        than an implementation detail: a management view of September narrowed
        to "Hôm nay" is September's work that happened today, and a "Hôm nay"
        query that somebody afterwards tried to widen into September would be a
        different - and wrong - question.

        Resolved through ``PrWorkPeriodService``, which is the one place a
        reporting period becomes a pair of Vietnamese calendar instants. A
        second implementation here is how "September" would come to mean two
        things on two screens.
        """
        if query.period_id is None:
            return []
        period = await self._periods.require_period(query.period_id)
        lower, upper = self._periods.bounds(period)
        instant = work_period_instant()
        return [instant >= lower, instant < upper]

    async def default_scope(self, actor: Actor) -> PrWorkScope:
        """The scope a request that names none gets: **the widest this actor may see.**

        *Toàn bộ* for anybody who holds ``PR_WORK_VIEW_ALL``, and *Công việc
        của tôi* otherwise - which for an employee is the same list under
        either name, since ``MINE`` is all they may see. The Work page opens on
        the department's month rather than on one person's slice, and the
        server rather than the browser decides that, so a call that omits the
        parameter and a screen on first load agree.
        """
        if await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL):
            return PrWorkScope.ALL
        return PrWorkScope.MINE

    async def _require_scope(self, actor: Actor, scope: PrWorkScope) -> None:
        """Refuse a scope this actor may not use. One gate per scope.

        ``PR_WORK_MANAGE`` deliberately does **not** admit ``ALL``: it is the
        capability for deciding about work, and the department-wide view has its
        own, ``PR_WORK_VIEW_ALL``. MeoBot models no team, so there is no honest
        middle ground between "what I am part of" and "everything" - and this
        patch declines to invent one out of channel assignments.
        """
        if scope is PrWorkScope.MINE:
            return
        if scope is PrWorkScope.ALL:
            needed: tuple[PrCapability, ...] = (PrCapability.PR_WORK_VIEW_ALL,)
        elif scope is PrWorkScope.ASSIGNED_BY_ME:
            needed = (PrCapability.PR_WORK_MANAGE,)
        else:
            # The decision queue is open to either kind of decider, because
            # accepting a proposal and validating finished work are two
            # different capabilities and a person may hold one without the
            # other.
            needed = (PrCapability.PR_WORK_MANAGE, PrCapability.PR_WORK_VALIDATE)
        for capability in needed:
            if await self._capabilities.allows(actor, capability):
                return
        raise PrPermissionDeniedError(
            "Bạn không có quyền xem phạm vi công việc này.",
            details={
                "reason": "scope_not_permitted",
                "scope": scope.value,
                "requires": [capability.value for capability in needed],
            },
        )

    async def decision_queue_condition(self, actor: Actor) -> ColumnElement[bool] | None:
        """What this actor may decide **right now**, as a predicate.

        Public because the detail route needs the same answer: a validator has
        to be able to open an item in their queue, and computing "is this in
        their queue" twice in two places is how the two start to disagree.

        Built from what the actor can actually do rather than from a fixed pair
        of statuses. Somebody holding only ``PR_WORK_VALIDATE`` gets finished
        work and no proposals; somebody holding only ``PR_WORK_MANAGE`` gets
        proposals and no finished work. A queue listing items its reader would
        be refused on is a queue of refusals.

        Both anti-gaming exclusions are in the predicate: work this actor
        contributed to, and proposals this actor filed. Those are precisely the
        items :meth:`~meobot.application.pr_work_service.PrWorkService.approve`
        and ``accept`` refuse them.

        Returns:
            ``None`` when the actor may decide nothing at all.
        """
        actor_id = actor.user_id
        if actor_id is None:
            return None
        branches: list[ColumnElement[bool]] = []
        if await self._capabilities.allows(actor, PrCapability.PR_WORK_MANAGE):
            branches.append(PrWorkItem.status == PrWorkStatus.PROPOSED)
        if await self._capabilities.allows(actor, PrCapability.PR_WORK_VALIDATE):
            branches.append(PrWorkItem.status == PrWorkStatus.COMPLETED)
            # A period container with a result nobody has counted is in the
            # validator's queue for the same reason finished work is - minus
            # the validator's own stream, which they may never count.
            branches.append(
                and_(
                    PrWorkItem.reporting_period_id.is_not(None),
                    PrWorkItem.subject_user_id != actor_id,
                    exists().where(
                        PrWorkResult.work_item_id == PrWorkItem.id,
                        PrWorkResult.status == PrWorkCountStatus.PENDING,
                    ),
                )
            )
        if not branches:
            return None
        return and_(
            or_(*branches),
            or_(
                ~self._contributed_by(actor_id),
                # A container's contributor is its subject, already excluded
                # above; a validator who opened somebody's stream by reporting
                # into it as a manager is not thereby its contributor.
                PrWorkItem.reporting_period_id.is_not(None),
            ),
            or_(
                PrWorkItem.created_by_user_id != actor_id,
                PrWorkItem.reporting_period_id.is_not(None),
            ),
        )

    def _preset_conditions(self, query: WorkQuery, *, now: datetime) -> list[ColumnElement[bool]]:
        """The preset, as predicates. Status presets carry no dates at all.

        The ``OVERDUE``/``UPCOMING``/``OPEN`` branch returns **before** the date
        bounds are looked at, which is the mechanism behind the requirement that
        a period filter can never hide carried-over work.
        """
        if query.preset is PrWorkPreset.OVERDUE:
            return [
                PrWorkItem.due_at.is_not(None),
                PrWorkItem.due_at < now,
                PrWorkItem.status.in_(sorted(OVERDUE_STATUSES)),
            ]
        if query.preset is PrWorkPreset.UPCOMING:
            return [
                PrWorkItem.due_at.is_not(None),
                PrWorkItem.due_at >= now,
                PrWorkItem.status.in_(sorted(OPEN_WORK_STATUSES)),
            ]
        if query.preset is PrWorkPreset.OPEN:
            return [PrWorkItem.status.in_(sorted(OPEN_WORK_STATUSES))]

        lower, upper = self._bounds(query, now=now)
        if lower is None and upper is None:
            return []
        column: SQLColumnExpression[datetime | None] = {
            PrWorkDateField.DUE_AT: PrWorkItem.due_at,
            PrWorkDateField.CREATED_AT: PrWorkItem.created_at,
            PrWorkDateField.COUNTED_AT: PrWorkItem.approved_at,
            # See ``PrWorkDateField.EXECUTION_AT``: the coalesced instant, not
            # ``execution_at`` alone, so a manual job is still reachable by a
            # day filter rather than vanishing from every one of them.
            PrWorkDateField.EXECUTION_AT: work_period_instant(),
        }[query.date_field]
        clauses: list[ColumnElement[bool]] = [column.is_not(None)]
        if lower is not None:
            clauses.append(column >= lower)
        if upper is not None:
            clauses.append(column < upper)
        return clauses

    def _bounds(
        self, query: WorkQuery, *, now: datetime
    ) -> tuple[datetime | None, datetime | None]:
        """The preset as a half-open pair of UTC instants.

        Resolved through the **existing** ``day_bounds``, which is the one place
        a Vietnamese calendar day becomes an instant. ``WEEK`` starts on Monday
        - the week a Vietnamese working calendar uses - and ``MONTH`` runs from
        the first of the month to today rather than to the month's end, because
        a figure for "this month" that included the future would be counting
        days that have not happened.
        """
        today = now.astimezone(self._timezone).date()
        if query.preset is PrWorkPreset.TODAY:
            return day_bounds(today, today, tz=self._timezone)
        if query.preset is PrWorkPreset.YESTERDAY:
            yesterday = today.fromordinal(today.toordinal() - 1)
            return day_bounds(yesterday, yesterday, tz=self._timezone)
        if query.preset is PrWorkPreset.WEEK:
            start = today.fromordinal(today.toordinal() - today.weekday())
            return day_bounds(start, today, tz=self._timezone)
        if query.preset is PrWorkPreset.MONTH:
            return day_bounds(today.replace(day=1), today, tz=self._timezone)
        if query.preset is PrWorkPreset.CUSTOM:
            return day_bounds(query.date_from, query.date_to, tz=self._timezone)
        return (None, None)

    async def _subject_user(self, *, actor: Actor, query: WorkQuery) -> uuid.UUID | None:
        """Whose work is being asked about, if a person was named.

        The quiet bypass this closes: before the patch, ``user_id`` was gated on
        ``PR_WORK_MANAGE``, so ``scope=MINE&user_id=<somebody else>`` handed a
        Trưởng nhóm any colleague's whole work list through the *employee*
        scope. Naming another person is now ``PR_WORK_VIEW_ALL``.

        The one exception is ``ASSIGNED_BY_ME``, where naming somebody narrows
        a list that is already restricted to work this actor put them on. That
        is a filter within your own book rather than a window into theirs.
        """
        if query.user_id is None:
            return None
        if query.user_id == actor.user_id:
            return query.user_id
        if query.scope is PrWorkScope.ASSIGNED_BY_ME:
            return query.user_id
        if not await self._capabilities.allows(actor, PrCapability.PR_WORK_VIEW_ALL):
            raise PrPermissionDeniedError(
                "Bạn chỉ xem được công việc của mình.",
                details={
                    "reason": "user_filter_not_permitted",
                    "requires": [PrCapability.PR_WORK_VIEW_ALL.value],
                },
            )
        return query.user_id

    @staticmethod
    def _contributed_by(user_id: uuid.UUID) -> ColumnElement[bool]:
        """ "This person has a contribution on this item", as an ``EXISTS``.

        A subquery rather than a join, so the outer query still returns one row
        per work item however many contributors it has - which is the difference
        between "three people worked on it" and "it appears three times".
        """
        return PrWorkItem.id.in_(
            select(PrWorkContribution.work_item_id).where(PrWorkContribution.user_id == user_id)
        )

    # =====================================================================
    # Page companions
    # =====================================================================
    async def _contributions_for(
        self, item_ids: Sequence[uuid.UUID]
    ) -> Sequence[PrWorkContribution]:
        if not item_ids:
            return []
        result = await self._session.execute(
            select(PrWorkContribution)
            .where(PrWorkContribution.work_item_id.in_(list(item_ids)))
            .order_by(PrWorkContribution.assigned_at.asc())
        )
        return result.scalars().all()

    async def _types_for(self, type_ids: set[uuid.UUID]) -> Sequence[PrWorkType]:
        if not type_ids:
            return []
        result = await self._session.execute(
            select(PrWorkType).where(PrWorkType.id.in_(sorted(type_ids, key=str)))
        )
        return result.scalars().all()

    async def _content_codes_for(self, items: Sequence[PrWorkItem]) -> dict[uuid.UUID, str]:
        """The content codes the source-derived rows on this page name. M3.

        One query for the page. A card says *"Nguồn: CNT-2026-000042"* rather
        than a UUID, and a browser is never handed an id to print - the same
        rule every other name on this page follows.
        """
        wanted = {row.content_id for row in items if row.content_id is not None}
        if not wanted:
            return {}
        from meobot.db.models.pr import PrContentItem

        statement = select(PrContentItem.id, PrContentItem.code).where(PrContentItem.id.in_(wanted))
        return {row[0]: row[1] for row in (await self._session.execute(statement)).all()}

    async def _recurring_templates_for(
        self, items: Sequence[PrWorkItem]
    ) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
        """The template behind each recurring row on this page. One query.

        Joined through ``recurring_occurrence_id`` - the column ``0037`` added
        for exactly this - rather than by decoding a source key, which the whole
        module is written not to do.
        """
        occurrence_ids = {
            row.recurring_occurrence_id for row in items if row.recurring_occurrence_id is not None
        }
        if not occurrence_ids:
            return {}
        from meobot.db.models.pr_work_recurring import (
            PrWorkRecurringOccurrence,
            PrWorkRecurringTemplate,
        )

        rows = await self._session.execute(
            select(
                PrWorkRecurringOccurrence.id,
                PrWorkRecurringTemplate.id,
                PrWorkRecurringTemplate.name,
            )
            .join(
                PrWorkRecurringTemplate,
                PrWorkRecurringTemplate.id == PrWorkRecurringOccurrence.template_id,
            )
            .where(PrWorkRecurringOccurrence.id.in_(tuple(occurrence_ids)))
        )
        return {occurrence_id: (template_id, name) for occurrence_id, template_id, name in rows}

    async def _names_for(self, item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Contributor names for the page, resolved server-side.

        So a work list never asks ``/people`` per card, and so the browser is
        never handed a bare UUID to print.
        """
        if not item_ids:
            return {}
        from meobot.db.models.user import User

        result = await self._session.execute(
            select(User.id, User.full_name).where(
                User.id.in_(
                    select(PrWorkContribution.user_id).where(
                        PrWorkContribution.work_item_id.in_(list(item_ids))
                    )
                )
            )
        )
        return {row[0]: row[1] for row in result.all()}


def work_item_select() -> Select[tuple[PrWorkItem]]:
    """The base statement, exported so a test can reason about the same shape."""
    return select(PrWorkItem)


__all__: list[str] = [
    "DEFAULT_WORK_PAGE",
    "MAX_WORK_PAGE",
    "PrWorkDateField",
    "PrWorkPreset",
    "PrWorkQueryService",
    "PrWorkScope",
    "WorkPage",
    "WorkQuery",
    "WorkSummary",
    "work_item_select",
]
