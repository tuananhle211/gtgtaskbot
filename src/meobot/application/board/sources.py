"""One source per unit, both answering the same two questions: *which rows*,
and *how many of each*.

:class:`PrBoardSource` reads ``pr_content_items`` through the existing
:class:`~meobot.application.pr_query_service.PrQueryService`, so PR's own
visibility rules (scopes, grants) decide what a PR member sees, and the board
adds nothing of its own. It maps each stage onto a phase and writes nothing.

:class:`AdsBoardSource` reads ``orders`` through
:class:`~meobot.application.orders.scope.OrderScope`, so an Ads member sees
exactly what the order routes would show them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import ColumnElement, Select, and_, case, exists, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from meobot.application.board.holders import (
    AWAITING_ASSIGNMENT,
    NOBODY,
    AdsApprovers,
    Holder,
    People,
    Person,
    ads_approvers,
    format_names,
    held_by,
    pr_production_assigners,
    pr_reviewers,
    waiting_for,
)
from meobot.application.orders.node_assignees import assignee_sets, load_open_assignees
from meobot.application.orders.scope import OrderScope, approval_reaches, boss_of
from meobot.application.org.seats import load_seats
from meobot.application.pr_content_query import ContentQuery
from meobot.application.pr_query_service import PrQueryService
from meobot.core.config import Settings
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.order import Order, OrderNode, OrderNodeAssignee, OrderSubmission
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.pr import PrBrand, PrContentItem
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.user import User
from meobot.domain.board.models import (
    ADS_STAGE_PHASE,
    ADS_STEPS,
    PHASE_LABELS,
    PR_STAGE_PHASE,
    BirthdayView,
    BoardPage,
    BoardQuery,
    DesignAssigneeView,
    DesignCodeView,
    DesignView,
    Phase,
    TaskCell,
    TaskRow,
)
from meobot.domain.identity.models import Actor
from meobot.domain.orders.deadlines import (
    done_status,
    node_deadline,
    node_deadline_status,
    open_status,
)
from meobot.domain.orders.design_codes import DesignItem, items_from_json, split_links
from meobot.domain.orders.labels import (
    node_status_label,
    node_type_label,
    stage_label,
    video_type_label,
)
from meobot.domain.orders.models import (
    ACTIVE_NODE_STATUSES,
    PRODUCTION_NODES,
    TERMINAL_STAGES,
    OrderCategory,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    OrderVideoType,
    last_production_node,
)
from meobot.domain.orders.permissions import AdsPermission
from meobot.domain.orders.pipeline import NodeView, OrderView, available_actions, is_urgent
from meobot.domain.pr.content_views import PrContentViewScope
from meobot.domain.pr.models import PrContentType, PrPriority, PrWorkflowStage
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES
from meobot.domain.units.models import UnitCode, UnitMemberRole, UnitSettings

#: The three PR stages that mean "somebody has to decide".
PR_PENDING_REVIEW: frozenset[PrWorkflowStage] = frozenset(
    {
        PrWorkflowStage.AI_REVIEW,
        PrWorkflowStage.TEAM_LEAD_REVIEW,
        PrWorkflowStage.HEAD_REVIEW,
        PrWorkflowStage.INTERNAL_REVIEW,
    }
)
#: The Ads stages that mean "somebody has to decide".
ADS_PENDING_REVIEW: frozenset[OrderStage] = frozenset(
    {OrderStage.ORDER_PENDING, OrderStage.DUYET_VIDEO_BT, OrderStage.FINAL_REVIEW}
)

# The display states a cell (or a row, in ``TaskRow.state``) can be in on top
# of the node statuses. The screens colour every waiting state amber:
#: Somebody has to decide: a hand-in, an order, the video or the final review.
STATE_CHO_DUYET = "CHO_DUYET"
#: The node came up with nobody chosen and was routed to a Leader to hand out.
STATE_CHO_PHAN_CONG = "CHO_PHAN_CONG"
#: Handed to a staff member who has not pressed "Nhận việc" yet.
STATE_DA_GIAO = "DA_GIAO"
#: Needs somebody and nobody may hand it out ("Chờ giao").
STATE_CHUA_GIAO = "CHUA_GIAO"
#: Every state that means "waiting for somebody", for the screens' colours.
WAITING_STATES: frozenset[str] = frozenset(
    {STATE_CHO_DUYET, STATE_CHO_PHAN_CONG, STATE_DA_GIAO, STATE_CHUA_GIAO}
)
#: How a step of a cancelled order reads: muted (slate), never waiting, so a
#: cancelled row cannot look as if it were still live. ``(status, label)``.
_CANCELLED_CELL: tuple[str, str] = (OrderStage.CANCELLED.value, "Đã huỷ")


def awaiting_hand_out(node: OrderNode) -> bool:
    """A node that came up with nobody chosen and was routed to a Leader (or
    the head) to hand out, who has neither handed it out nor taken it.

    Read from the row alone: activation routes it (``assigned_at`` =
    ``activated_at``, an assignee who was not the orderer's choice); an
    explicit hand-out stamps a later ``assigned_at``; taking it stamps
    ``accepted_at``.
    """
    if (
        node.status is not OrderNodeStatus.DANG_LAM
        or node.accepted_at is not None
        or node.assignee_user_id is None
        or node.assignee_user_id == node.preassigned_user_id
    ):
        return False
    if node.assigned_at is not None and node.activated_at is not None:
        return ensure_utc(node.assigned_at) <= ensure_utc(node.activated_at)
    return True


def _design_state(state: str) -> ColumnElement[bool] | None:
    """Where a node stands, as a condition on ``order_nodes`` - the SQL twin
    of :meth:`AdsBoardSource._node_state` for the Design team's status filter
    (:data:`~meobot.domain.board.models.DESIGN_STATES`). ``None`` = unknown."""
    untaken = OrderNode.accepted_at.is_(None)
    # ``awaiting_hand_out``: routed to a Leader at activation, not handed on.
    routed = and_(
        OrderNode.status == OrderNodeStatus.DANG_LAM,
        untaken,
        OrderNode.assignee_user_id.is_not(None),
        or_(
            OrderNode.preassigned_user_id.is_(None),
            OrderNode.assignee_user_id != OrderNode.preassigned_user_id,
        ),
        or_(
            OrderNode.assigned_at.is_(None),
            OrderNode.activated_at.is_(None),
            OrderNode.assigned_at <= OrderNode.activated_at,
        ),
    )
    conditions: dict[str, ColumnElement[bool]] = {
        STATE_CHO_PHAN_CONG: or_(OrderNode.status == OrderNodeStatus.CHUA_GIAO, routed),
        STATE_DA_GIAO: and_(
            OrderNode.status.in_([OrderNodeStatus.DANG_LAM, OrderNodeStatus.DANG_SUA]),
            OrderNode.assignee_user_id.is_not(None),
            untaken,
            ~routed,
        ),
        OrderNodeStatus.DANG_LAM.value: and_(
            OrderNode.status == OrderNodeStatus.DANG_LAM, OrderNode.accepted_at.is_not(None)
        ),
        OrderNodeStatus.DANG_SUA.value: and_(
            OrderNode.status == OrderNodeStatus.DANG_SUA, OrderNode.accepted_at.is_not(None)
        ),
        STATE_CHO_DUYET: OrderNode.status == OrderNodeStatus.CHO_DUYET,
        OrderNodeStatus.HOAN_THANH.value: OrderNode.status == OrderNodeStatus.HOAN_THANH,
    }
    return conditions.get(state)


def _task_state(state: str) -> ColumnElement[bool] | None:
    """Where the whole order stands, as a condition on ``orders`` - the SQL
    side of the ORD table's "Trạng thái task" filter (2026-10-19,
    :data:`~meobot.domain.board.models.TASK_STATES`). The step states read
    the node the order is at (the production stages carry its type).
    ``None`` = unknown."""
    if state == "CHO_DUYET":
        return or_(
            Order.stage.in_(sorted(ADS_PENDING_REVIEW)),
            _at_active_node(_design_state(STATE_CHO_DUYET)),
        )
    if state == "ORDER_RETURNED":
        return Order.stage == OrderStage.ORDER_RETURNED
    if state == "HOAN_THANH":
        return Order.stage == OrderStage.COMPLETED
    if state == "DA_HUY":
        return Order.stage == OrderStage.CANCELLED
    condition = _design_state(state)
    return None if condition is None else _at_active_node(condition)


def _at_active_node(condition: ColumnElement[bool] | None) -> ColumnElement[bool]:
    """``condition`` holds on the production node the order is at."""
    if condition is None:
        return Order.id.is_(None)
    return exists(
        select(OrderNode.id).where(
            OrderNode.order_id == Order.id,
            or_(
                *[
                    and_(
                        Order.stage == OrderStage(node_type.value), OrderNode.node_type == node_type
                    )
                    for node_type in (
                        OrderNodeType.BIEN_TAP,
                        OrderNodeType.THIET_KE,
                        OrderNodeType.DUNG,
                    )
                ]
            ),
            condition,
        )
    )


def awaiting_acceptance(node: OrderNode) -> bool:
    """Handed to somebody who has not pressed "Nhận việc" yet."""
    return (
        node.status in (OrderNodeStatus.DANG_LAM, OrderNodeStatus.DANG_SUA)
        and node.assignee_user_id is not None
        and node.accepted_at is None
        and not awaiting_hand_out(node)
    )


def assignee_rows_of(
    user_id: uuid.UUID, *, unsubmitted: bool = False, active: bool = False
) -> ColumnElement[bool]:
    """``EXISTS`` an open ``order_node_assignees`` row of this person on a node
    of the correlated ``Order`` (a Design node handed to several people, 0073).

    ``unsubmitted`` keeps only rows the person has not handed in yet; ``active``
    keeps only rows on a node still being worked (``DANG_LAM``/``DANG_SUA``).
    """
    statement = (
        select(OrderNodeAssignee.id)
        .join(OrderNode, OrderNode.id == OrderNodeAssignee.node_id)
        .where(
            OrderNode.order_id == Order.id,
            OrderNodeAssignee.user_id == user_id,
            OrderNodeAssignee.removed_at.is_(None),
        )
    )
    if unsubmitted:
        statement = statement.where(OrderNodeAssignee.submitted_at.is_(None))
    if active:
        statement = statement.where(
            OrderNode.status.in_([OrderNodeStatus.DANG_LAM, OrderNodeStatus.DANG_SUA])
        )
    return statement.exists()


def node_has_assignee_rows() -> ColumnElement[bool]:
    """``EXISTS`` an open per-person row on the correlated ``OrderNode`` - a
    node handed out through ``order_node_assignees`` rather than its single
    ``assignee_user_id`` (0073)."""
    return (
        select(OrderNodeAssignee.id)
        .where(
            OrderNodeAssignee.node_id == OrderNode.id,
            OrderNodeAssignee.removed_at.is_(None),
        )
        .exists()
    )


PR_STAGE_LABELS: dict[PrWorkflowStage, str] = {
    PrWorkflowStage.IDEA: "Ý tưởng",
    PrWorkflowStage.BRIEFING: "Brief",
    PrWorkflowStage.SCRIPTING: "Viết kịch bản",
    PrWorkflowStage.AI_REVIEW: "AI review",
    PrWorkflowStage.TEAM_LEAD_REVIEW: "Trưởng nhóm duyệt",
    PrWorkflowStage.HEAD_REVIEW: "Trưởng phòng duyệt",
    PrWorkflowStage.APPROVED: "Đã duyệt",
    PrWorkflowStage.PRODUCTION: "Đang sản xuất",
    PrWorkflowStage.INTERNAL_REVIEW: "Duyệt nội bộ",
    PrWorkflowStage.READY_TO_PUBLISH: "Sẵn sàng đăng",
    PrWorkflowStage.PUBLISHED: "Đã đăng",
    PrWorkflowStage.MEASURED: "Đã đo",
    PrWorkflowStage.ARCHIVED: "Lưu trữ",
    PrWorkflowStage.CANCELLED: "Đã huỷ",
}


class BoardSource(Protocol):
    unit: UnitCode

    async def rows(self, actor: Actor, query: BoardQuery) -> BoardPage: ...

    async def count_by_phase(self, actor: Actor, query: BoardQuery) -> dict[Phase, int]: ...


async def display_names(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = (
        (await session.execute(select(User.id, User.full_name).where(User.id.in_(list(user_ids)))))
        .tuples()
        .all()
    )
    return dict(rows)


def _local_bounds(
    settings: Settings, day_from: date | None, day_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """Inclusive calendar days in the application timezone, as UTC instants."""
    zone = settings.timezone
    start = None if day_from is None else datetime.combine(day_from, time.min, tzinfo=zone)
    end = (
        None
        if day_to is None
        else datetime.combine(day_to + timedelta(days=1), time.min, tzinfo=zone)
    )
    return start, end


# --- PR ------------------------------------------------------------------------

#: How many PR items the board reads at once when it has to filter or reorder
#: in memory. The PR list is small; the same bound the phase filter always had.
_OVERFETCH = 500


class PrBoardSource:
    unit = UnitCode.PR

    def __init__(self, session: AsyncSession, settings: Settings, queries: PrQueryService) -> None:
        self._session = session
        self._settings = settings
        self._queries = queries

    def _content_query(self, query: BoardQuery, *, limit: int, offset: int) -> ContentQuery:
        scope = PrContentViewScope.ALL
        if query.awaiting_me:
            scope = PrContentViewScope.MY_ACTIONS
        elif query.mine:
            scope = PrContentViewScope.MY_CONTENT
        stage = None
        if query.status:
            try:
                stage = PrWorkflowStage(query.status)
            except ValueError:
                stage = None
        content_type = None
        if query.kind:
            try:
                content_type = PrContentType(query.kind)
            except ValueError:
                content_type = None
        return ContentQuery(
            scope=scope,
            stage=stage,
            owner_user_id=query.owner_user_id,
            responsible_user_id=query.assignee_user_id or query.person_user_id,
            date_from=query.date_from,
            date_to=query.date_to,
            search=query.search,
            priority=PrPriority.HIGH if query.priority else None,
            content_type=content_type,
            limit=limit,
            offset=offset,
        )

    async def _awaiting_ids(self, actor: Actor, query: BoardQuery) -> set[uuid.UUID]:
        """The filter's items waiting on ``actor``: PR's own "Cần tôi xử lý"
        (``MY_ACTIONS``), with the board's other filters."""
        if query.awaiting_me:
            return set()  # every row is; the caller marks them all
        page = await self._queries.content_page(
            actor=actor,
            query=self._content_query(
                replace(query, awaiting_me=True, mine=False), limit=_OVERFETCH, offset=0
            ),
            with_counts=False,
        )
        return {item.id for item in page.items}

    async def rows(self, actor: Actor, query: BoardQuery) -> BoardPage:
        # A phase or urgency filter narrows after the query, so over-fetch and
        # page in memory; the PR list is small and both filters are rare. The
        # "todo first" order partitions the filter the same way.
        narrow = query.phase is not None or query.urgent or query.todo_first or query.overdue
        awaiting = await self._awaiting_ids(actor, query)
        page = await self._queries.content_page(
            actor=actor,
            query=self._content_query(
                query,
                limit=_OVERFETCH if narrow else query.limit,
                offset=0 if narrow else query.offset,
            ),
            with_counts=False,
        )
        names = await display_names(
            self._session,
            {item.owner_user_id for item in page.items}
            | {item.producer_user_id for item in page.items if item.producer_user_id},
        )
        brands = await self._brand_names({item.brand_id for item in page.items})
        reviewers = await pr_reviewers(self._session, page.items)
        assigners: People = ()
        if any(
            item.producer_user_id is None
            and item.workflow_stage in (PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION)
            for item in page.items
        ):
            assigners = await pr_production_assigners(self._session)
        delivered = await self._delivered(item.id for item in page.items)
        now = utcnow()
        rows = [
            replace(
                self._row(item, names, brands, now, reviewers, assigners),
                delivered_at=delivered.get(item.id),
                awaiting_me=query.awaiting_me or item.id in awaiting,
            )
            for item in page.items
        ]
        if query.phase is not None:
            rows = [row for row in rows if row.phase is query.phase]
        if query.urgent:
            rows = [row for row in rows if row.urgent]
        if query.overdue:
            # PR rows carry no deadline: none is overdue.
            rows = [row for row in rows if row.deadline_status == "OVERDUE"]
        if query.todo_first:
            # Stable: each part keeps PR's own order.
            rows = [row for row in rows if row.awaiting_me] + [
                row for row in rows if not row.awaiting_me
            ]
        if narrow:
            total = len(rows)
            rows = rows[query.offset : query.offset + query.limit]
        else:
            total = page.total
        return BoardPage(rows=tuple(rows), total=total, limit=query.limit, offset=query.offset)

    async def count_by_phase(self, actor: Actor, query: BoardQuery) -> dict[Phase, int]:
        page = await self._queries.content_page(
            actor=actor, query=self._content_query(query, limit=1, offset=0), with_counts=True
        )
        counts: dict[Phase, int] = dict.fromkeys(Phase, 0)
        for stage, count in page.stage_counts.items():
            counts[PR_STAGE_PHASE[stage]] += count
        return counts

    async def _delivered(self, content_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        """The newest production hand-in per item: when the link was delivered."""
        ids = list(content_ids)
        if not ids:
            return {}
        found = await self._session.execute(
            select(PrProductionSubmission.content_id, func.max(PrProductionSubmission.created_at))
            .where(PrProductionSubmission.content_id.in_(ids))
            .group_by(PrProductionSubmission.content_id)
        )
        return {content_id: ensure_utc(at) for content_id, at in found.all() if at is not None}

    async def _brand_names(self, brand_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not brand_ids:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(PrBrand.id, PrBrand.name).where(PrBrand.id.in_(list(brand_ids)))
                )
            )
            .tuples()
            .all()
        )
        return dict(rows)

    def _row(
        self,
        item: PrContentItem,
        names: dict[uuid.UUID, str],
        brands: dict[uuid.UUID, str],
        now: datetime,
        reviewers: dict[uuid.UUID, People] | None = None,
        assigners: People = (),
    ) -> TaskRow:
        phase = PR_STAGE_PHASE[item.workflow_stage]
        owner = names.get(item.owner_user_id, "")
        producer = None if item.producer_user_id is None else names.get(item.producer_user_id)
        stage = item.workflow_stage
        created = ensure_utc(item.created_at)
        updated = ensure_utc(item.updated_at) if item.updated_at is not None else created
        extras: list[tuple[str, str]] = []
        brand = brands.get(item.brand_id)
        if brand:
            extras.append(("Brand", brand))
        if item.planned_publish_at is not None:
            local = ensure_utc(item.planned_publish_at).astimezone(self._settings.timezone)
            extras.append(("Lịch đăng", local.strftime("%d/%m/%Y")))

        def cell(
            key: str,
            phases: tuple[Phase, ...],
            person: str | None,
            label: str,
            person_id: uuid.UUID | None = None,
        ) -> TaskCell:
            """One column over one or more phases (2026-10-19: the two review
            phases fold into the phase they review - a script review is still
            the order's column, an internal review the production's - so the
            PR row shows three phase columns and no review columns)."""
            first = phases[0]
            current = phase in phases
            reached = _phase_index(phase) >= _phase_index(first)
            return TaskCell(
                key=key,
                label=PHASE_LABELS[first],
                person_name=person,
                person_user_id=person_id if person else None,
                status="DONE" if reached and not current else ("CURRENT" if current else "PENDING"),
                status_label=label if current else ("Xong" if reached else "Chưa tới"),
                is_current=current,
            )

        owner_person = Person(item.owner_user_id, owner)
        producer_person = (
            None
            if item.producer_user_id is None or producer is None
            else Person(item.producer_user_id, producer)
        )
        status_label = PR_STAGE_LABELS[stage]
        state: str | None = None
        holder: Holder
        if stage in STAGE_APPROVAL_GATES:
            # A review gate: the named reviewer whose grant reaches this item.
            reviewer = (reviewers or {}).get(item.id, ())
            holder = held_by(reviewer)
            status_label = waiting_for(reviewer)
            state = STATE_CHO_DUYET
        elif stage in (PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION):
            if producer_person is not None:
                holder = held_by(producer_person)
            else:
                # Nobody producing yet: it waits for whoever may hand it out.
                holder = held_by(assigners)
                state = STATE_CHO_PHAN_CONG if assigners else STATE_CHUA_GIAO
                status_label = (
                    f"{status_label} · {waiting_for(assigners, 'giao sản xuất')}"
                    if assigners
                    else f"{status_label} · Chờ giao"
                )
        elif phase in (Phase.DONE, Phase.CANCELLED):
            holder = NOBODY
        else:
            # Writing, AI review, ready to publish: the owner answers for it.
            holder = Holder((owner_person,))
        reviewer_name = holder.name if stage in STAGE_APPROVAL_GATES else None
        cells = (
            cell(
                "ORDER",
                (Phase.ORDER, Phase.REVIEW),
                reviewer_name if phase is Phase.REVIEW else owner,
                status_label,
                holder.user_id if phase is Phase.REVIEW else item.owner_user_id,
            ),
            cell(
                "PRODUCTION",
                (Phase.PRODUCTION, Phase.FINAL_REVIEW),
                reviewer_name if phase is Phase.FINAL_REVIEW else producer,
                status_label,
                holder.user_id if phase is Phase.FINAL_REVIEW else item.producer_user_id,
            ),
            cell("DONE", (Phase.DONE,), None, status_label),
        )
        return TaskRow(
            unit=UnitCode.PR,
            id=item.id,
            code=item.code,
            title=item.title,
            kind=item.content_type.value if item.content_type else "",
            kind_label=item.content_type.value.replace("_", " ").title()
            if item.content_type
            else "",
            owner_user_id=item.owner_user_id,
            owner_name=owner,
            created_at=created,
            phase=phase,
            phase_label=PHASE_LABELS[phase],
            status=stage.value,
            status_label=status_label,
            state=state,
            cells=cells,
            product_link=None,
            returned_at=None,
            is_priority=item.priority in (PrPriority.HIGH, PrPriority.URGENT, PrPriority.CRITICAL),
            urgent=phase not in (Phase.DONE, Phase.CANCELLED) and now - created > timedelta(days=7),
            detail_path=f"/tasks/{item.code}",
            version=item.version if hasattr(item, "version") else 0,
            stage_since=updated,
            revisions=0,
            current_person_name=holder.name,
            current_person_user_id=holder.user_id,
            awaiting_assignment=holder.awaiting,
            latest_link=None,
            extras=tuple(extras),
        )


def _phase_index(phase: Phase) -> int:
    order = (Phase.ORDER, Phase.REVIEW, Phase.PRODUCTION, Phase.FINAL_REVIEW, Phase.DONE)
    return order.index(phase) if phase in order else -1


# --- Ads -----------------------------------------------------------------------


class AdsBoardSource:
    unit = UnitCode.ADS

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        scope: OrderScope,
        unit_settings: UnitSettings,
    ) -> None:
        self._session = session
        self._settings = settings
        self._scope = scope
        self._unit = unit_settings

    def _statement(self, query: BoardQuery, now: datetime) -> Select:  # type: ignore[type-arg]
        statement = self._scope.apply(select(Order))
        start, end = _local_bounds(self._settings, query.date_from, query.date_to)
        if start is not None:
            statement = statement.where(Order.submitted_at >= start)
        if end is not None:
            statement = statement.where(Order.submitted_at < end)
        if query.phase is not None:
            stages = [stage for stage, phase in ADS_STAGE_PHASE.items() if phase is query.phase]
            statement = statement.where(Order.stage.in_(stages))
        if query.step is not None:
            step = ADS_STEPS.get(query.step)
            if step is None:
                statement = statement.where(Order.id.is_(None))
            else:
                statement = statement.where(Order.stage.in_(sorted(step[1])))
        if query.kind:
            try:
                statement = statement.where(
                    Order.video_type == OrderVideoType(query.kind.strip().upper())
                )
            except ValueError:
                statement = statement.where(Order.id.is_(None))
        if query.video_kind_id is not None:
            statement = statement.where(Order.video_kind_id == query.video_kind_id)
        if query.category:
            try:
                category = OrderCategory(query.category.strip().upper())
            except ValueError:
                statement = statement.where(Order.id.is_(None))
            else:
                statement = statement.where(Order.category == category)
                if category is OrderCategory.BIRTHDAY and not self._scope.birthday_board:
                    # The birthday board is for those granted it (0059).
                    statement = statement.where(Order.id.is_(None))
        if query.design_status:
            state = _design_state(query.design_status.strip().upper())
            statement = statement.where(
                Order.id.is_(None)
                if state is None
                else exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        OrderNode.node_type == OrderNodeType.THIET_KE,
                        state,
                    )
                )
            )
        if query.status:
            try:
                statement = statement.where(Order.stage == OrderStage(query.status))
            except ValueError:
                statement = statement.where(Order.id.is_(None))
        if query.state:
            condition = _task_state(query.state.strip().upper())
            statement = statement.where(Order.id.is_(None) if condition is None else condition)
        # A cancelled order carries no live work and only clutters the board, so
        # it is hidden unless the viewer explicitly asks for it: the "Đã huỷ"
        # task state (or a direct CANCELLED stage filter). Every other filter,
        # and the no-filter default, leaves it out.
        wants_cancelled = (query.state or "").strip().upper() == "DA_HUY" or (
            query.status or ""
        ).strip().upper() == OrderStage.CANCELLED.value
        if not wants_cancelled:
            statement = statement.where(Order.stage != OrderStage.CANCELLED)
        if query.owner_user_id is not None:
            statement = statement.where(Order.owner_user_id == query.owner_user_id)
        if query.assignee_user_id is not None:
            statement = statement.where(
                or_(
                    exists(
                        select(OrderNode.id).where(
                            OrderNode.order_id == Order.id,
                            OrderNode.assignee_user_id == query.assignee_user_id,
                        )
                    ),
                    assignee_rows_of(query.assignee_user_id),
                )
            )
        if query.person_user_id is not None:
            statement = statement.where(
                or_(
                    Order.owner_user_id == query.person_user_id,
                    exists(
                        select(OrderNode.id).where(
                            OrderNode.order_id == Order.id,
                            OrderNode.assignee_user_id == query.person_user_id,
                        )
                    ),
                    assignee_rows_of(query.person_user_id),
                )
            )
        user_id = self._scope.user_id
        if query.mine and user_id is not None:
            statement = statement.where(
                or_(
                    Order.owner_user_id == user_id,
                    exists(
                        select(OrderNode.id).where(
                            OrderNode.order_id == Order.id, OrderNode.assignee_user_id == user_id
                        )
                    ),
                    assignee_rows_of(user_id),
                )
            )
        if query.awaiting_me and user_id is not None:
            statement = statement.where(self._awaiting(user_id))
        if query.priority:
            statement = statement.where(Order.is_priority.is_(True))
        if query.urgent:
            cutoff = now - timedelta(days=self._unit.urgent_days)
            statement = statement.where(
                Order.submitted_at < cutoff,
                Order.stage.notin_([OrderStage.COMPLETED, OrderStage.CANCELLED]),
            )
        if query.overdue:
            statement = statement.where(_overdue(now))
        if query.search:
            needle = f"%{query.search.strip()}%"
            statement = statement.where(or_(Order.code.ilike(needle), Order.title.ilike(needle)))
        return statement

    def _awaiting(self, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
        """What waits on this person: a gate they decide, a node they hold or lead."""
        may = self._scope.context.permissions
        conditions: list[ColumnElement[bool]] = []
        if may.holds(AdsPermission.ORDER_APPROVE):
            # The approver the orderer chose (0056), else the orderer's own
            # approvers (0061: the head they report to, else every head of
            # their department).
            conditions.append(
                (Order.stage == OrderStage.ORDER_PENDING)
                & or_(
                    and_(Order.approver_user_id == user_id, _chosen_approver().is_not(None)),
                    approval_reaches(user_id),
                )
            )
        # The final review waits on the orderer only; a stand-in may decide it
        # but it is not "theirs".
        conditions.append(
            (Order.owner_user_id == user_id) & (Order.stage == OrderStage.FINAL_REVIEW)
        )
        if OrderNodeType.BIEN_TAP in may.nodes(AdsPermission.VIDEO_REVIEW):
            conditions.append(Order.stage == OrderStage.DUYET_VIDEO_BT)
        assigning = may.nodes(AdsPermission.NODE_ASSIGN)
        if assigning:
            conditions.append(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        self._managed(assigning),
                        OrderNode.status == OrderNodeStatus.CHUA_GIAO,
                    )
                )
            )
        # A hand-in waiting for its Leader. A legacy link node is never
        # reviewed by a Leader.
        reviewing = may.nodes(AdsPermission.NODE_REVIEW) - {OrderNodeType.GAN_LINK}
        if reviewing:
            conditions.append(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        OrderNode.node_type.in_(sorted(reviewing, key=lambda n: n.value)),
                        OrderNode.status == OrderNodeStatus.CHO_DUYET,
                        # A worker with their own Leader waits on that Leader only.
                        or_(
                            boss_of(OrderNode.assignee_user_id).is_(None),
                            boss_of(OrderNode.assignee_user_id) == user_id,
                        ),
                    )
                )
            )
        assigns = may.nodes(AdsPermission.NODE_ASSIGN)
        if assigns:
            # Routed to a Leader (or the head) to hand out and not taken yet
            # (:func:`awaiting_hand_out`): it waits on everybody who may hand
            # that function out, not only the routed one.
            routing = select(OrgUnitMember.user_id).where(
                OrgUnitMember.unit_id == Order.unit_id,
                OrgUnitMember.left_at.is_(None),
                or_(
                    OrgUnitMember.is_lead.is_(True),
                    OrgUnitMember.role == UnitMemberRole.HEAD,
                ),
            )
            conditions.append(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        self._managed(assigns),
                        OrderNode.status == OrderNodeStatus.DANG_LAM,
                        OrderNode.accepted_at.is_(None),
                        OrderNode.assignee_user_id.in_(routing),
                        or_(
                            OrderNode.preassigned_user_id.is_(None),
                            OrderNode.preassigned_user_id != OrderNode.assignee_user_id,
                        ),
                        or_(
                            OrderNode.assigned_at.is_(None),
                            OrderNode.activated_at.is_(None),
                            OrderNode.assigned_at <= OrderNode.activated_at,
                        ),
                    )
                )
            )
        if assigns and self._unit.effort_enabled:
            # Sent back by the orderer or the script lead: the Leader still has
            # to enter the revision tokens ("Nhập token sửa", 0053).
            conditions.append(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        self._managed(assigns),
                        OrderNode.revision_tokens_pending.is_(True),
                        OrderNode.status.in_(
                            sorted(ACTIVE_NODE_STATUSES, key=lambda status: status.value)
                        ),
                    )
                )
            )
        conditions.append(
            exists(
                select(OrderNode.id).where(
                    OrderNode.order_id == Order.id,
                    OrderNode.assignee_user_id == user_id,
                    OrderNode.status.in_([OrderNodeStatus.DANG_LAM, OrderNodeStatus.DANG_SUA]),
                    # A node handed to several people (0073) waits on each of
                    # them through their own row, below, not its first assignee.
                    ~node_has_assignee_rows(),
                )
            )
        )
        # A Design node (0073) still waits on every assignee who has not handed
        # in yet - accept / edit items / submit; once they submit it waits on
        # the others and then their Leader, so it is no longer "theirs".
        conditions.append(assignee_rows_of(user_id, unsubmitted=True, active=True))
        conditions.append(
            (Order.owner_user_id == user_id) & (Order.stage == OrderStage.ORDER_RETURNED)
        )
        return or_(*conditions)

    def _managed(self, nodes: frozenset[OrderNodeType]) -> ColumnElement[bool]:
        """The order's nodes a person manages, given the node types their
        permission reaches. A legacy link node is managed by the function of
        the process's last production node."""
        functions = sorted(nodes - {OrderNodeType.GAN_LINK}, key=lambda n: n.value)
        condition: ColumnElement[bool] = OrderNode.node_type.in_(functions)
        if OrderNodeType.GAN_LINK in nodes:
            return or_(condition, OrderNode.node_type == OrderNodeType.GAN_LINK)
        processes = sorted(
            (code for code in OrderVideoType if last_production_node(code) in nodes),
            key=lambda code: code.value,
        )
        if not processes:
            return condition
        return or_(
            condition,
            (OrderNode.node_type == OrderNodeType.GAN_LINK) & Order.video_type.in_(processes),
        )

    async def rows(self, actor: Actor, query: BoardQuery) -> BoardPage:
        now = utcnow()
        base = self._statement(query, now)
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        user_id = self._scope.user_id
        # Priority first; for the orderer, returned orders next; then newest.
        returned_first = (Order.owner_user_id == user_id) & (
            Order.stage == OrderStage.ORDER_RETURNED
        )
        order_by: list[Any] = []
        if query.todo_first and user_id is not None and not query.awaiting_me:
            # "Cần làm" first: the rows waiting on this person, then the rest.
            order_by.append(case((self._awaiting(user_id), 0), else_=1).asc())
        if query.birthday_first:
            # The nearest coming birthday first; the ones already past this
            # year after them; an order with no date last.
            today = now.astimezone(self._settings.timezone).date()
            day_of_year = extract("month", Order.birthday_on) * 100 + extract(
                "day", Order.birthday_on
            )
            order_by += [
                case(
                    (Order.birthday_on.is_(None), 2),
                    (day_of_year >= today.month * 100 + today.day, 0),
                    else_=1,
                ).asc(),
                day_of_year.asc(),
            ]
        order_by += [
            Order.is_priority.desc(),
            returned_first.desc(),
            Order.submitted_at.desc(),
            Order.code.desc(),
        ]
        ordered = base.order_by(*order_by).limit(query.limit).offset(query.offset)
        orders = list((await self._session.scalars(ordered)).all())
        awaiting = await self._awaiting_among(orders, query)
        nodes_by_order = await self._nodes(orders)
        assignees = await load_open_assignees(
            self._session, [node.id for nodes in nodes_by_order.values() for node in nodes]
        )
        names = await display_names(
            self._session,
            {order.owner_user_id for order in orders}
            | {
                person
                for nodes in nodes_by_order.values()
                for node in nodes
                for person in (node.assignee_user_id, node.preassigned_user_id)
                if person is not None
            }
            | {row.user_id for rows in assignees.values() for row in rows},
        )
        links = await self._latest_links(nodes_by_order)
        node_links = await self.node_links(nodes_by_order)
        approvers = (
            await ads_approvers(self._session, orders[0].unit_id) if orders else AdsApprovers()
        )
        # Which department each order came from: the orderer's department name(s).
        seats = await load_seats(self._session, {order.owner_user_id for order in orders})
        departments = {
            user_id: " · ".join(dict.fromkeys(seat.name for seat in rows_of if seat.name))
            for user_id, rows_of in seats.items()
        }
        rows = tuple(
            replace(
                self._row(
                    order,
                    nodes_by_order.get(order.id, []),
                    names,
                    links,
                    now,
                    approvers,
                    assignees,
                ),
                owner_department=departments.get(order.owner_user_id) or None,
                awaiting_me=order.id in awaiting,
                design=design_view(
                    order, nodes_by_order.get(order.id, []), names, node_links, assignees
                ),
                actions=self.action_kinds(order, nodes_by_order.get(order.id, []), assignees),
            )
            for order in orders
        )
        return BoardPage(rows=rows, total=int(total or 0), limit=query.limit, offset=query.offset)

    async def _awaiting_among(self, orders: list[Order], query: BoardQuery) -> set[uuid.UUID]:
        """Which of these orders wait on the viewer - the "Cần làm" marker."""
        user_id = self._scope.user_id
        if not orders or user_id is None:
            return set()
        ids = [order.id for order in orders]
        if query.awaiting_me:
            return set(ids)
        found = await self._session.scalars(
            select(Order.id).where(Order.id.in_(ids), self._awaiting(user_id))
        )
        return set(found.all())

    async def _latest_links(
        self, nodes_by_order: dict[uuid.UUID, list[OrderNode]]
    ) -> dict[uuid.UUID, str]:
        """The newest link handed in on any node of each order."""
        node_to_order = {
            node.id: order_id for order_id, nodes in nodes_by_order.items() for node in nodes
        }
        if not node_to_order:
            return {}
        submissions = (
            await self._session.scalars(
                select(OrderSubmission)
                .where(
                    OrderSubmission.node_id.in_(list(node_to_order)),
                    OrderSubmission.link.is_not(None),
                )
                .order_by(OrderSubmission.created_at.desc(), OrderSubmission.submission_no.desc())
            )
        ).all()
        latest: dict[uuid.UUID, str] = {}
        for submission in submissions:
            order_id = node_to_order[submission.node_id]
            if order_id not in latest and submission.link:
                latest[order_id] = submission.link
        return latest

    def action_kinds(
        self,
        order: Order,
        nodes: list[OrderNode],
        assignees: dict[uuid.UUID, list[OrderNodeAssignee]] | None = None,
    ) -> tuple[str, ...]:
        """What the viewer may do on this order right now, as action kinds
        (``SUBMIT_WORK``, ``APPROVE_NODE``...) - the policy's own answer, so a
        list can offer the button without opening the task."""
        assignees = assignees or {}
        view = OrderView(
            id=order.id,
            video_type=order.video_type,
            stage=order.stage,
            owner_user_id=order.owner_user_id,
            is_priority=order.is_priority,
            category=order.category,
            approved=order.order_approved_at is not None,
        )
        node_views = []
        for node in nodes:
            assigned, accepted, submitted = assignee_sets(assignees.get(node.id, ()))
            node_views.append(
                NodeView(
                    id=node.id,
                    node_type=node.node_type,
                    status=node.status,
                    assignee_user_id=node.assignee_user_id,
                    preassigned_user_id=node.preassigned_user_id,
                    accepted_at=node.accepted_at,
                    assignee_user_ids=assigned,
                    accepted_user_ids=accepted,
                    submitted_user_ids=submitted,
                )
            )
        kinds = [
            action.kind.value
            for action in available_actions(view, tuple(node_views), self._scope.context)
        ]
        return tuple(dict.fromkeys(kinds))

    async def node_links(
        self, nodes_by_order: dict[uuid.UUID, list[OrderNode]]
    ) -> dict[uuid.UUID, tuple[str, datetime]]:
        """The newest link handed in on each node and when, by node id."""
        node_ids = [node.id for nodes in nodes_by_order.values() for node in nodes]
        if not node_ids:
            return {}
        submissions = (
            await self._session.scalars(
                select(OrderSubmission)
                .where(OrderSubmission.node_id.in_(node_ids), OrderSubmission.link.is_not(None))
                .order_by(OrderSubmission.created_at.desc(), OrderSubmission.submission_no.desc())
            )
        ).all()
        latest: dict[uuid.UUID, tuple[str, datetime]] = {}
        for submission in submissions:
            if submission.node_id not in latest and submission.link:
                latest[submission.node_id] = (submission.link, ensure_utc(submission.created_at))
        return latest

    async def count_by_phase(self, actor: Actor, query: BoardQuery) -> dict[Phase, int]:
        base = self._statement(query, utcnow()).subquery()
        counted = (
            await self._session.execute(select(base.c.stage, func.count()).group_by(base.c.stage))
        ).all()
        counts: dict[Phase, int] = dict.fromkeys(Phase, 0)
        for stage, count in counted:
            counts[ADS_STAGE_PHASE[OrderStage(stage)]] += int(count)
        return counts

    async def _nodes(self, orders: list[Order]) -> dict[uuid.UUID, list[OrderNode]]:
        if not orders:
            return {}
        nodes = (
            await self._session.scalars(
                select(OrderNode)
                .where(OrderNode.order_id.in_([order.id for order in orders]))
                .order_by(OrderNode.created_at)
            )
        ).all()
        grouped: dict[uuid.UUID, list[OrderNode]] = {}
        for node in nodes:
            grouped.setdefault(node.order_id, []).append(node)
        return grouped

    def _row(
        self,
        order: Order,
        nodes: list[OrderNode],
        names: dict[uuid.UUID, str],
        links: dict[uuid.UUID, str],
        now: datetime,
        approvers: AdsApprovers | None = None,
        assignees: dict[uuid.UUID, list[OrderNodeAssignee]] | None = None,
    ) -> TaskRow:
        approvers = approvers or AdsApprovers()
        assignees = assignees or {}
        by_type = {node.node_type: node for node in nodes}
        phase = ADS_STAGE_PHASE[order.stage]
        cancelled = order.stage is OrderStage.CANCELLED
        cells = []
        # A legacy link node may still be the active one; it has no cell.
        current: OrderNode | None = next(
            (node for node in nodes if node.status in ACTIVE_NODE_STATUSES), None
        )
        for node_type in PRODUCTION_NODES:
            node = by_type.get(node_type)
            if node is None:
                continue
            cell_state, label = self._node_state(node, order, names, approvers)
            person_name = self._cell_person(node, names, approvers, order.video_type)
            person_user_id = node.assignee_user_id if node.assignee_user_id in names else None
            multi = self._multi_design(node, assignees.get(node.id, []), names)
            if multi is not None:
                workers, cell_state, label = multi
                person_name = format_names(tuple(workers))
                person_user_id = None
            is_current = node.status in ACTIVE_NODE_STATUSES
            since = node.approved_at or node.submitted_at or node.assigned_at or node.activated_at
            deadline_at = _node_deadline(node)
            deadline_status = _node_deadline_status(node, now)
            # A cancelled order has no live work: every step that was not
            # finished reads "Đã huỷ" (muted, not current, no deadline), so the
            # row can never look as if it were still waiting on somebody or
            # overdue. Finished steps keep "Hoàn thành" - that work happened.
            if cancelled and node.status is not OrderNodeStatus.HOAN_THANH:
                cell_state, label = _CANCELLED_CELL
                is_current = False
                deadline_at = None
                deadline_status = None
                if node.assignee_user_id is None and node.preassigned_user_id is None:
                    # Only a "Chờ X phân công" placeholder; nobody actually held it.
                    person_name = None
                    person_user_id = None
            cells.append(
                TaskCell(
                    key=node_type.value,
                    label=node_type_label(node_type),
                    person_name=person_name,
                    person_user_id=person_user_id,
                    status=cell_state,
                    status_label=label,
                    is_current=is_current,
                    since=None if since is None else ensure_utc(since),
                    revisions=node.revision_count,
                    deadline_at=deadline_at,
                    deadline_status=deadline_status,
                    tokens=_node_tokens(node) if self._unit.effort_enabled else None,
                )
            )
        final = self._final_cell(order, by_type, names, approvers)
        desired = (
            None if order.desired_deadline_at is None else ensure_utc(order.desired_deadline_at)
        )
        if cancelled:
            # The product was never reviewed: the final cell reads "Đã huỷ" too.
            final = replace(final, status=_CANCELLED_CELL[0], status_label=_CANCELLED_CELL[1])
            cells.append(replace(final, deadline_at=None, deadline_status=None))
        else:
            cells.append(
                replace(
                    final, deadline_at=desired, deadline_status=_order_deadline_status(order, now)
                )
            )
        submitted = ensure_utc(order.submitted_at)
        if order.stage is OrderStage.COMPLETED and order.completed_at is not None:
            stage_since: datetime | None = ensure_utc(order.completed_at)
        elif current is not None and current.activated_at is not None:
            stage_since = ensure_utc(current.activated_at)
        elif (handed := _product_handed_in(order, by_type)) is not None:
            stage_since = handed
        elif order.order_approved_at is not None:
            stage_since = ensure_utc(order.order_approved_at)
        else:
            stage_since = submitted
        owner_name = names.get(order.owner_user_id, "")
        holder, status_label, state = self._holder(
            order, current, names, owner_name, approvers, assignees
        )
        extras: list[tuple[str, str]] = []
        if order.script_source is not None:
            extras.append(("Source", "AI" if order.script_source.value == "AI" else "Quay thực tế"))
        handed_in = sum(node.submission_count for node in nodes)
        if handed_in:
            extras.append(("Bản nộp", str(handed_in)))
        if order.video_kind_name:
            extras.append(("Loại video", order.video_kind_name))
        if order.video_kind_points is not None:
            extras.append(("Điểm", format_points(order.video_kind_points)))
        return TaskRow(
            unit=UnitCode.ADS,
            id=order.id,
            code=order.code,
            title=order.title,
            kind=order.video_type.value,
            kind_label=ads_kind_label(order),
            owner_user_id=order.owner_user_id,
            owner_name=owner_name,
            created_at=submitted,
            phase=phase,
            phase_label=PHASE_LABELS[phase],
            status=order.stage.value,
            status_label=status_label,
            state=state,
            cells=tuple(cells),
            product_link=order.product_link if order.stage is OrderStage.COMPLETED else None,
            returned_at=None if order.completed_at is None else ensure_utc(order.completed_at),
            is_priority=order.is_priority,
            urgent=is_urgent(
                submitted_at=submitted,
                now=now,
                stage=order.stage,
                urgent_days=self._unit.urgent_days,
            ),
            detail_path=f"/tasks/{order.code}",
            version=order.version,
            stage_since=stage_since,
            revisions=sum(node.revision_count for node in nodes),
            current_person_name=holder.name,
            current_person_user_id=holder.user_id,
            awaiting_assignment=holder.awaiting,
            latest_link=links.get(order.id),
            delivered_at=_product_handed_in(order, by_type),
            extras=tuple(extras),
            desired_deadline_at=desired,
            deadline_at=(
                None if cancelled else _node_deadline(current) if current is not None else desired
            ),
            deadline_status=(
                None
                if cancelled
                else _node_deadline_status(current, now)
                if current is not None
                else _order_deadline_status(order, now)
            ),
            over_deadline_count=order.over_deadline_count,
            category=order.category.value,
            birthday=birthday_view(order),
        )

    @staticmethod
    def _cell_person(
        node: OrderNode,
        names: dict[uuid.UUID, str],
        approvers: AdsApprovers,
        video_type: OrderVideoType | None = None,
    ) -> str | None:
        """Who a step belongs to, by name: its worker, else the person chosen
        at order time, else (not reached yet) whoever will hand it out."""
        if node.assignee_user_id is not None:
            return names.get(node.assignee_user_id)
        if node.preassigned_user_id is not None:
            return names.get(node.preassigned_user_id)
        if node.status is OrderNodeStatus.BO_QUA:
            return None
        team = approvers.assigners_of(node.node_type, video_type)
        return f"{format_names(team)} giao" if team else None

    @staticmethod
    def _multi_design(
        node: OrderNode,
        rows: list[OrderNodeAssignee],
        names: dict[uuid.UUID, str],
    ) -> tuple[list[Person], str, str] | None:
        """A Design node handed to several people (0073), while they are still
        working: the workers, the step's state code and its words - "Đã giao"
        (nobody has accepted), "Đang nhận/làm" (some have), "Đang làm" (all) or
        "Đang sửa" (sent back). ``None`` otherwise, so a single-assignee node
        keeps its usual holder and status."""
        if node.node_type is not OrderNodeType.THIET_KE or len(rows) <= 1:
            return None
        if node.status not in (OrderNodeStatus.DANG_LAM, OrderNodeStatus.DANG_SUA):
            return None
        workers = [Person(row.user_id, names.get(row.user_id, "")) for row in rows]
        if node.status is OrderNodeStatus.DANG_SUA:
            return workers, OrderNodeStatus.DANG_SUA.value, "Đang sửa"
        accepted = sum(1 for row in rows if row.accepted_at is not None)
        if accepted == 0:
            return workers, STATE_DA_GIAO, "Đã giao"
        if accepted == len(rows):
            return workers, OrderNodeStatus.DANG_LAM.value, "Đang làm"
        return workers, OrderNodeStatus.DANG_LAM.value, "Đang nhận/làm"

    @staticmethod
    def _node_state(
        node: OrderNode,
        order: Order,
        names: dict[uuid.UUID, str],
        approvers: AdsApprovers,
    ) -> tuple[str, str]:
        """A step's state and words, waiting states told apart: routed to a
        Leader to hand out ("Chờ X phân công"), handed out but not taken yet
        ("Đã giao X"), waiting for review ("Chờ X duyệt")."""
        status = node.status
        if status is OrderNodeStatus.CHUA_GIAO:
            return STATE_CHUA_GIAO, AWAITING_ASSIGNMENT
        if awaiting_hand_out(node):
            return STATE_CHO_PHAN_CONG, waiting_for(
                _hand_out_team(node, order, names, approvers), "phân công"
            )
        if awaiting_acceptance(node):
            assert node.assignee_user_id is not None
            return STATE_DA_GIAO, f"Đã giao {names.get(node.assignee_user_id, '')}".rstrip()
        if status is OrderNodeStatus.CHO_DUYET:
            return STATE_CHO_DUYET, waiting_for(
                approvers.reviewers(node.node_type, node.assignee_user_id)
            )
        return status.value, node_status_label(status)

    @staticmethod
    def _final_cell(
        order: Order,
        by_type: dict[OrderNodeType, OrderNode],
        names: dict[uuid.UUID, str],
        approvers: AdsApprovers,
    ) -> TaskCell:
        """ "Duyệt final": the gates the product goes through once the last
        production node is done - the script lead's video review where it
        applies, then the orderer's final review."""
        orderer = _orderer(order, names)
        handed = _product_handed_in(order, by_type)
        if order.stage is OrderStage.DUYET_VIDEO_BT:
            return TaskCell(
                key="FINAL",
                label="Duyệt final",
                person_name=format_names(approvers.video) or None,
                status=STATE_CHO_DUYET,
                status_label=waiting_for(approvers.video, "duyệt video"),
                is_current=True,
                since=handed,
            )
        if order.stage is OrderStage.FINAL_REVIEW:
            return TaskCell(
                key="FINAL",
                label="Duyệt final",
                person_name=orderer.name,
                person_user_id=orderer.user_id,
                status=STATE_CHO_DUYET,
                status_label=waiting_for(orderer, "duyệt final"),
                is_current=True,
                since=handed,
            )
        if order.stage is OrderStage.COMPLETED:
            return TaskCell(
                key="FINAL",
                label="Duyệt final",
                person_name=orderer.name,
                person_user_id=orderer.user_id,
                status=OrderNodeStatus.HOAN_THANH.value,
                status_label=node_status_label(OrderNodeStatus.HOAN_THANH),
                is_current=False,
                since=None if order.completed_at is None else ensure_utc(order.completed_at),
            )
        return TaskCell(
            key="FINAL",
            label="Duyệt final",
            person_name=orderer.name,
            person_user_id=orderer.user_id,
            status=OrderNodeStatus.CHUA_TOI.value,
            status_label=node_status_label(OrderNodeStatus.CHUA_TOI),
            is_current=False,
        )

    @staticmethod
    def _holder(
        order: Order,
        current: OrderNode | None,
        names: dict[uuid.UUID, str],
        owner_name: str,
        approvers: AdsApprovers,
        assignees: dict[uuid.UUID, list[OrderNodeAssignee]] | None = None,
    ) -> tuple[Holder, str, str | None]:
        """The member(s) holding the order, the status naming them, and the
        row's state (a waiting state, or the node's status).

        Approval steps name the head or the Leader; a node names its worker,
        or its Leader while the hand-in waits for review. Nobody to name is
        "Chờ giao" - never a role.
        """
        stage = order.stage
        label = stage_label(stage)
        if stage is OrderStage.COMPLETED:
            return NOBODY, label, OrderNodeStatus.HOAN_THANH.value
        if stage in TERMINAL_STAGES:
            return NOBODY, label, None
        if stage is OrderStage.ORDER_PENDING:
            heads = approvers.order_approvers(order.owner_user_id, order.approver_user_id)
            return (
                held_by(heads),
                waiting_for(heads, "duyệt order"),
                STATE_CHO_DUYET if heads else STATE_CHUA_GIAO,
            )
        if stage is OrderStage.ORDER_RETURNED:
            return (
                Holder((Person(order.owner_user_id, owner_name),)),
                f"Trả {owner_name} sửa",
                OrderNodeStatus.DANG_SUA.value,
            )
        if stage is OrderStage.DUYET_VIDEO_BT:
            return (
                held_by(approvers.video),
                waiting_for(approvers.video, "duyệt video"),
                STATE_CHO_DUYET if approvers.video else STATE_CHUA_GIAO,
            )
        if stage is OrderStage.FINAL_REVIEW:
            # The orderer's review. Stand-ins may decide, but are not named.
            orderer = Person(order.owner_user_id, owner_name)
            return Holder((orderer,)), waiting_for(orderer, "duyệt final"), STATE_CHO_DUYET
        if current is None:
            return held_by(None), f"{label} · Chờ giao", STATE_CHUA_GIAO
        if current.status is OrderNodeStatus.CHO_DUYET:
            lead = approvers.reviewers(current.node_type, current.assignee_user_id)
            return (
                held_by(lead),
                waiting_for(lead),
                STATE_CHO_DUYET if lead else STATE_CHUA_GIAO,
            )
        multi = AdsBoardSource._multi_design(current, (assignees or {}).get(current.id, []), names)
        if multi is not None:
            # A Design node handed to several people (0073): name them all.
            workers, state, word = multi
            return Holder(tuple(workers)), f"{label} · {word}", state
        if current.assignee_user_id is None:
            return held_by(None), f"{label} · Chờ giao", STATE_CHUA_GIAO
        worker = Person(current.assignee_user_id, names.get(current.assignee_user_id, ""))
        if awaiting_hand_out(current):
            # Routed to the Leader to hand out, not yet taken by anybody: any
            # of the function's Leaders may hand it out, the routed one first.
            team = _hand_out_team(current, order, names, approvers)
            return (
                Holder(team),
                f"{label} · Chờ {format_names(team)} phân công",
                STATE_CHO_PHAN_CONG,
            )
        if awaiting_acceptance(current):
            # Handed out, not taken yet: it is theirs to accept.
            return Holder((worker,)), f"{label} · Đã giao {worker.name}", STATE_DA_GIAO
        return (
            Holder((worker,)),
            f"{label} · {node_status_label(current.status)}",
            current.status.value,
        )


#: The orderer's own steps: there the wished finish is the deadline that counts.
_ORDERER_GATES = (
    OrderStage.ORDER_PENDING,
    OrderStage.ORDER_RETURNED,
    OrderStage.DUYET_VIDEO_BT,
    OrderStage.FINAL_REVIEW,
)


def _node_deadline(node: OrderNode) -> datetime | None:
    deadline = node_deadline(
        deadline_at=node.deadline_at,
        revision_deadline_at=node.revision_deadline_at,
        revision_count=node.revision_count,
    )
    return None if deadline is None else ensure_utc(deadline)


def _node_deadline_status(node: OrderNode, now: datetime) -> str | None:
    found = node_deadline_status(
        status=node.status,
        deadline_at=None if node.deadline_at is None else ensure_utc(node.deadline_at),
        revision_deadline_at=(
            None if node.revision_deadline_at is None else ensure_utc(node.revision_deadline_at)
        ),
        revision_count=node.revision_count,
        deadline_met=node.deadline_met,
        now=now,
    )
    return None if found is None else found.value


def _node_tokens(node: OrderNode) -> float | None:
    if node.token_estimate is None and not node.token_revision:
        return None
    return float((node.token_estimate or 0) + (node.token_revision or 0))


def _order_deadline_status(order: Order, now: datetime) -> str | None:
    """The orderer's wish: met or missed once finished, else against now -
    only while the order is at one of the orderer's own steps."""
    if order.desired_deadline_at is None:
        return None
    desired = ensure_utc(order.desired_deadline_at)
    if order.stage is OrderStage.COMPLETED and order.completed_at is not None:
        found = done_status(ensure_utc(order.completed_at) <= desired)
    elif order.stage in _ORDERER_GATES:
        found = open_status(desired, now)
    else:
        found = None
    return None if found is None else found.value


def _overdue(now: datetime) -> ColumnElement[bool]:
    """SQL twin of an OVERDUE row: the active node's deadline that counts has
    passed, or the order sits at the orderer's own steps past their wish."""
    counts = case(
        (
            (OrderNode.revision_count > 0) & OrderNode.revision_deadline_at.is_not(None),
            OrderNode.revision_deadline_at,
        ),
        else_=OrderNode.deadline_at,
    )
    late_node = exists(
        select(OrderNode.id).where(
            OrderNode.order_id == Order.id,
            OrderNode.status.in_(sorted(ACTIVE_NODE_STATUSES, key=lambda status: status.value)),
            counts < now,
        )
    )
    late_wish = Order.stage.in_(_ORDERER_GATES) & (Order.desired_deadline_at < now)
    return or_(late_node, late_wish)


def _chosen_approver() -> Any:
    """SQL twin of ``AdsApprovers.order_approvers``'s first rule: the order's
    chosen approver while they are still an active account, else NULL."""
    chosen = aliased(User)
    return (
        select(chosen.id)
        .where(chosen.id == Order.approver_user_id, chosen.active.is_(True))
        .limit(1)
        .scalar_subquery()
    )


def _hand_out_team(
    node: OrderNode, order: Order, names: dict[uuid.UUID, str], approvers: AdsApprovers
) -> People:
    """Who may hand a routed node out: the person it was routed to first,
    then everybody else who may assign that function."""
    assert node.assignee_user_id is not None
    routed = Person(node.assignee_user_id, names.get(node.assignee_user_id, ""))
    return (
        routed,
        *(
            person
            for person in approvers.assigners_of(node.node_type, order.video_type)
            if person.user_id != routed.user_id
        ),
    )


def _orderer(order: Order, names: dict[uuid.UUID, str]) -> Person:
    return Person(order.owner_user_id, names.get(order.owner_user_id, ""))


def format_points(value: Decimal) -> str:
    """``1``, ``1.5``, ``12.25``: no trailing zeros, no exponent."""
    text = f"{Decimal(value):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def ads_kind_label(order: Order) -> str:
    """What the "Loại" column says: the video kind when one was picked, else
    the process ("Biên kịch, Dựng"). A birthday order says so."""
    if order.category is OrderCategory.BIRTHDAY:
        return BIRTHDAY_KIND_LABEL
    return order.video_kind_name or video_type_label(order.video_type)


BIRTHDAY_KIND_LABEL = "Sinh nhật"


def code_views(raw: Any) -> tuple[DesignCodeView, ...]:
    return tuple(code_view(item) for item in items_from_json(raw))


def code_view(item: DesignItem) -> DesignCodeView:
    return DesignCodeView(
        code_id=item.code_id,
        code=item.code,
        name=item.name,
        quantity=item.quantity,
        form=item.form,
        form_label=item.form_label,
        form_pct=item.form_pct,
        unit_points=item.unit_points,
        points=item.points,
        note=item.note,
        free=item.free,
        examples=item.examples,
        unit_label=item.unit_label,
    )


def design_view(
    order: Order,
    nodes: list[OrderNode],
    names: dict[uuid.UUID, str],
    node_links: dict[uuid.UUID, tuple[str, datetime]],
    assignees: dict[uuid.UUID, list[OrderNodeAssignee]] | None = None,
) -> DesignView:
    """The Design team's reading of one order (0058, 0069)."""
    assignees = assignees or {}
    by_type = {node.node_type: node for node in nodes}
    script = by_type.get(OrderNodeType.BIEN_TAP)
    design = by_type.get(OrderNodeType.THIET_KE)

    def person(node: OrderNode | None) -> str | None:
        if node is None or node.status is OrderNodeStatus.BO_QUA:
            return None
        holder = node.assignee_user_id or node.preassigned_user_id
        return None if holder is None else names.get(holder)

    def link_of(node: OrderNode | None) -> str | None:
        return None if node is None or node.id not in node_links else node_links[node.id][0]

    design_rows = [] if design is None else assignees.get(design.id, [])
    assignee_views = tuple(
        DesignAssigneeView(
            user_id=row.user_id,
            name=names.get(row.user_id, "—"),
            status=(
                "DA_NOP"
                if row.submitted_at is not None
                else ("DANG_LAM" if row.accepted_at is not None else "CHUA_NHAN")
            ),
            submitted=row.submitted_at is not None,
            codes=code_views(row.work_items),
        )
        for row in design_rows
    )
    submitted_count = sum(1 for view in assignee_views if view.submitted)
    # Several designers: name them all; otherwise the one holder as before.
    designer: str | None = (
        ", ".join(view.name for view in assignee_views) if assignee_views else person(design)
    )
    proposed = None if design is None else design.proposed_design_items
    references = split_links(order.design_link) or split_links(order.reference_link)
    return DesignView(
        script_link=link_of(script),
        script_by=person(script),
        reference_link=references[0] if references else None,
        reference_links=references,
        note=order.note,
        codes=code_views(order.design_items),
        proposed_codes=None if proposed is None else code_views(proposed),
        designer=designer,
        assignees=assignee_views,
        submitted_count=submitted_count,
        assignee_total=len(assignee_views),
        design_link=link_of(design),
        design_submitted_at=(
            None if design is None or design.id not in node_links else node_links[design.id][1]
        ),
    )


def birthday_view(order: Order) -> BirthdayView | None:
    if order.category is not OrderCategory.BIRTHDAY:
        return None
    return BirthdayView(
        full_name=order.title,
        branch=order.birthday_branch,
        gender=None if order.birthday_gender is None else order.birthday_gender.value,
        birthday_on=order.birthday_on,
    )


def _product_handed_in(order: Order, by_type: dict[OrderNodeType, OrderNode]) -> datetime | None:
    """When the finished product (the last production node's hand-in, or a
    legacy link node's) was handed over - once production is over."""
    if order.stage not in (
        OrderStage.DUYET_VIDEO_BT,
        OrderStage.FINAL_REVIEW,
        OrderStage.COMPLETED,
    ):
        return None
    stamps = [
        ensure_utc(node.submitted_at)
        for node in (
            by_type.get(last_production_node(order.video_type)),
            by_type.get(OrderNodeType.GAN_LINK),
        )
        if node is not None and node.submitted_at is not None
    ]
    return max(stamps) if stamps else None


__all__ = [
    "ADS_PENDING_REVIEW",
    "PR_PENDING_REVIEW",
    "PR_STAGE_LABELS",
    "STATE_CHO_DUYET",
    "STATE_CHO_PHAN_CONG",
    "STATE_CHUA_GIAO",
    "STATE_DA_GIAO",
    "WAITING_STATES",
    "AdsBoardSource",
    "BoardSource",
    "PrBoardSource",
    "ads_kind_label",
    "awaiting_acceptance",
    "awaiting_hand_out",
    "birthday_view",
    "code_views",
    "design_view",
    "display_names",
    "format_points",
]
