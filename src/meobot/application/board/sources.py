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
from typing import Protocol

from sqlalchemy import ColumnElement, Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.board.holders import (
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
from meobot.application.orders.scope import OrderScope
from meobot.application.pr_content_query import ContentQuery
from meobot.application.pr_query_service import PrQueryService
from meobot.core.config import Settings
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.order import Order, OrderNode, OrderSubmission
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.pr import PrBrand, PrContentItem
from meobot.db.models.pr_production import PrProductionSubmission
from meobot.db.models.user import User
from meobot.domain.board.models import (
    ADS_STAGE_PHASE,
    ADS_STEPS,
    PHASE_LABELS,
    PR_STAGE_PHASE,
    BoardPage,
    BoardQuery,
    Phase,
    TaskCell,
    TaskRow,
)
from meobot.domain.identity.models import Actor
from meobot.domain.orders.labels import (
    node_status_label,
    node_type_label,
    stage_label,
    video_type_label,
)
from meobot.domain.orders.models import (
    ACTIVE_NODE_STATUSES,
    TERMINAL_STAGES,
    OrderNodeStatus,
    OrderNodeType,
    OrderStage,
    OrderVideoType,
)
from meobot.domain.orders.permissions import AdsPermission
from meobot.domain.orders.pipeline import is_urgent, link_attacher_for
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

    async def rows(self, actor: Actor, query: BoardQuery) -> BoardPage:
        # A phase or urgency filter narrows after the query, so over-fetch and
        # page in memory; the PR list is small and both filters are rare.
        narrow = query.phase is not None or query.urgent
        page = await self._queries.content_page(
            actor=actor,
            query=self._content_query(
                query,
                limit=500 if narrow else query.limit,
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
            )
            for item in page.items
        ]
        if query.phase is not None:
            rows = [row for row in rows if row.phase is query.phase]
        if query.urgent:
            rows = [row for row in rows if row.urgent]
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
            extras.append(("Lịch đăng", local.strftime("%d/%m")))

        def cell(key: str, phase_of_cell: Phase, person: str | None, label: str) -> TaskCell:
            reached = _phase_index(phase) >= _phase_index(phase_of_cell)
            return TaskCell(
                key=key,
                label=PHASE_LABELS[phase_of_cell],
                person_name=person,
                status="DONE"
                if reached and phase is not phase_of_cell
                else ("CURRENT" if phase is phase_of_cell else "PENDING"),
                status_label=label
                if phase is phase_of_cell
                else ("Xong" if reached else "Chưa tới"),
                is_current=phase is phase_of_cell,
            )

        owner_person = Person(item.owner_user_id, owner)
        producer_person = (
            None
            if item.producer_user_id is None or producer is None
            else Person(item.producer_user_id, producer)
        )
        status_label = PR_STAGE_LABELS[stage]
        holder: Holder
        if stage in STAGE_APPROVAL_GATES:
            # A review gate: the named reviewer whose grant reaches this item.
            reviewer = (reviewers or {}).get(item.id, ())
            holder = held_by(reviewer)
            status_label = waiting_for(reviewer)
        elif stage in (PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION):
            if producer_person is not None:
                holder = held_by(producer_person)
            else:
                # Nobody producing yet: it waits for whoever may hand it out.
                holder = held_by(assigners)
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
            cell("ORDER", Phase.ORDER, owner, status_label),
            cell(
                "REVIEW",
                Phase.REVIEW,
                reviewer_name if phase is Phase.REVIEW else None,
                status_label,
            ),
            cell("PRODUCTION", Phase.PRODUCTION, producer, status_label),
            cell(
                "FINAL_REVIEW",
                Phase.FINAL_REVIEW,
                reviewer_name if phase is Phase.FINAL_REVIEW else producer,
                status_label,
            ),
            cell("DONE", Phase.DONE, None, status_label),
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
        if query.status:
            try:
                statement = statement.where(Order.stage == OrderStage(query.status))
            except ValueError:
                statement = statement.where(Order.id.is_(None))
        if query.owner_user_id is not None:
            statement = statement.where(Order.owner_user_id == query.owner_user_id)
        if query.assignee_user_id is not None:
            statement = statement.where(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        OrderNode.assignee_user_id == query.assignee_user_id,
                    )
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
        if query.search:
            needle = f"%{query.search.strip()}%"
            statement = statement.where(or_(Order.code.ilike(needle), Order.title.ilike(needle)))
        return statement

    def _awaiting(self, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
        """What waits on this person: a gate they decide, a node they hold or lead."""
        may = self._scope.context.permissions
        conditions: list[ColumnElement[bool]] = []
        if may.allows(AdsPermission.ORDER_APPROVE):
            conditions.append(Order.stage == OrderStage.ORDER_PENDING)
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
        # A hand-in waiting for its Leader. The link node is never reviewed by
        # a Leader: a link "Chờ duyệt" waits for the video or final gate.
        reviewing = may.nodes(AdsPermission.NODE_REVIEW) - {OrderNodeType.GAN_LINK}
        if reviewing:
            conditions.append(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        OrderNode.node_type.in_(sorted(reviewing, key=lambda n: n.value)),
                        OrderNode.status == OrderNodeStatus.CHO_DUYET,
                    )
                )
            )
        assigns = may.nodes(AdsPermission.NODE_ASSIGN)
        if assigns:
            # Routed to a Leader (or the head) to hand out and not taken yet:
            # it waits on every Leader of that function, not only the routed one.
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
                    )
                )
            )
        conditions.append(
            exists(
                select(OrderNode.id).where(
                    OrderNode.order_id == Order.id,
                    OrderNode.assignee_user_id == user_id,
                    OrderNode.status.in_([OrderNodeStatus.DANG_LAM, OrderNodeStatus.DANG_SUA]),
                )
            )
        )
        conditions.append(
            (Order.owner_user_id == user_id) & (Order.stage == OrderStage.ORDER_RETURNED)
        )
        return or_(*conditions)

    def _managed(self, nodes: frozenset[OrderNodeType]) -> ColumnElement[bool]:
        """The order's nodes a person manages, given the node types their
        permission reaches. The link node is managed by the function that
        attaches it, which depends on the order's process."""
        functions = sorted(nodes - {OrderNodeType.GAN_LINK}, key=lambda n: n.value)
        condition: ColumnElement[bool] = OrderNode.node_type.in_(functions)
        if OrderNodeType.GAN_LINK in nodes:
            return or_(condition, OrderNode.node_type == OrderNodeType.GAN_LINK)
        attacher = self._scope.context.btd_link_attacher
        processes = sorted(
            (code for code in OrderVideoType if link_attacher_for(code, attacher) in nodes),
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
        ordered = (
            base.order_by(
                Order.is_priority.desc(),
                returned_first.desc(),
                Order.submitted_at.desc(),
                Order.code.desc(),
            )
            .limit(query.limit)
            .offset(query.offset)
        )
        orders = list((await self._session.scalars(ordered)).all())
        nodes_by_order = await self._nodes(orders)
        names = await display_names(
            self._session,
            {order.owner_user_id for order in orders}
            | {
                person
                for nodes in nodes_by_order.values()
                for node in nodes
                for person in (node.assignee_user_id, node.preassigned_user_id)
                if person is not None
            },
        )
        links = await self._latest_links(nodes_by_order)
        approvers = (
            await ads_approvers(self._session, orders[0].unit_id) if orders else AdsApprovers()
        )
        rows = tuple(
            self._row(order, nodes_by_order.get(order.id, []), names, links, now, approvers)
            for order in orders
        )
        return BoardPage(rows=rows, total=int(total or 0), limit=query.limit, offset=query.offset)

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
    ) -> TaskRow:
        approvers = approvers or AdsApprovers()
        by_type = {node.node_type: node for node in nodes}
        phase = ADS_STAGE_PHASE[order.stage]
        cells = []
        current: OrderNode | None = None
        for node_type in OrderNodeType:
            node = by_type.get(node_type)
            if node is None:
                continue
            status = node.status
            label = node_status_label(status)
            if status is OrderNodeStatus.CHUA_GIAO:
                label = "Chờ giao"
            elif (
                status is OrderNodeStatus.DANG_LAM
                and node.accepted_at is None
                and node.assignee_user_id is not None
                and (routed := approvers.assigner(node_type, order.video_type)) is not None
                and routed.user_id == node.assignee_user_id
            ):
                label = waiting_for(
                    approvers.assigners_of(node_type, order.video_type), "phân công"
                )
            elif status is OrderNodeStatus.CHO_DUYET and node_type is not OrderNodeType.GAN_LINK:
                label = waiting_for(approvers.lead(node_type))
            if node_type is OrderNodeType.GAN_LINK and order.stage is OrderStage.DUYET_VIDEO_BT:
                label = waiting_for(approvers.video, "duyệt video")
            elif node_type is OrderNodeType.GAN_LINK and order.stage is OrderStage.FINAL_REVIEW:
                label = waiting_for(_orderer(order, names), "duyệt final")
            active = status in ACTIVE_NODE_STATUSES
            if active:
                current = node
            since = node.approved_at or node.submitted_at or node.assigned_at or node.activated_at
            cells.append(
                TaskCell(
                    key=node_type.value,
                    label=node_type_label(node_type),
                    person_name=self._cell_person(node, names, approvers, order.video_type),
                    status=status.value,
                    status_label=label,
                    is_current=active,
                    since=None if since is None else ensure_utc(since),
                    revisions=node.revision_count,
                )
            )
        submitted = ensure_utc(order.submitted_at)
        if order.stage is OrderStage.COMPLETED and order.completed_at is not None:
            stage_since: datetime | None = ensure_utc(order.completed_at)
        elif current is not None and current.activated_at is not None:
            stage_since = ensure_utc(current.activated_at)
        elif order.order_approved_at is not None:
            stage_since = ensure_utc(order.order_approved_at)
        else:
            stage_since = submitted
        owner_name = names.get(order.owner_user_id, "")
        holder, status_label = self._holder(order, current, names, owner_name, approvers)
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
            delivered_at=_delivered_at(by_type.get(OrderNodeType.GAN_LINK)),
            extras=tuple(extras),
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
    def _holder(
        order: Order,
        current: OrderNode | None,
        names: dict[uuid.UUID, str],
        owner_name: str,
        approvers: AdsApprovers,
    ) -> tuple[Holder, str]:
        """The one member holding the order, and the status naming them.

        Approval steps name the head or the Leader; a node names its worker,
        or its Leader while the hand-in waits for review. Nobody to name is
        "Chờ giao" - never a role.
        """
        stage = order.stage
        label = stage_label(stage)
        if stage in TERMINAL_STAGES:
            return NOBODY, label
        if stage is OrderStage.ORDER_PENDING:
            return held_by(approvers.head), waiting_for(approvers.head, "duyệt order")
        if stage is OrderStage.ORDER_RETURNED:
            return Holder((Person(order.owner_user_id, owner_name),)), f"Trả {owner_name} sửa"
        if stage is OrderStage.DUYET_VIDEO_BT:
            return held_by(approvers.video), waiting_for(approvers.video, "duyệt video")
        if stage is OrderStage.FINAL_REVIEW:
            # The orderer's review. Stand-ins may decide, but are not named.
            orderer = Person(order.owner_user_id, owner_name)
            return Holder((orderer,)), waiting_for(orderer, "duyệt final")
        if current is None:
            return held_by(None), f"{label} · Chờ giao"
        if current.status is OrderNodeStatus.CHO_DUYET:
            lead = approvers.lead(current.node_type)
            return held_by(lead), waiting_for(lead)
        if current.assignee_user_id is None:
            return held_by(None), f"{label} · Chờ giao"
        worker = Person(current.assignee_user_id, names.get(current.assignee_user_id, ""))
        routed = approvers.assigner(current.node_type, order.video_type)
        if (
            routed is not None
            and routed.user_id == worker.user_id
            and current.accepted_at is None
            and current.status is OrderNodeStatus.DANG_LAM
        ):
            # Routed to the Leader to hand out, not yet taken by anybody: any
            # of the function's Leaders may hand it out, the routed one first.
            team = (
                worker,
                *(
                    p
                    for p in approvers.assigners_of(current.node_type, order.video_type)
                    if p != worker
                ),
            )
            return Holder(team), f"{label} · Chờ {format_names(team)} phân công"
        return Holder((worker,)), label


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
    the process ("Biên kịch, Dựng")."""
    return order.video_kind_name or video_type_label(order.video_type)


def _delivered_at(link_node: OrderNode | None) -> datetime | None:
    """When the product link was attached on "Gắn link"."""
    if link_node is None or link_node.submitted_at is None:
        return None
    return ensure_utc(link_node.submitted_at)


__all__ = [
    "ADS_PENDING_REVIEW",
    "PR_PENDING_REVIEW",
    "PR_STAGE_LABELS",
    "AdsBoardSource",
    "BoardSource",
    "PrBoardSource",
    "ads_kind_label",
    "display_names",
    "format_points",
]
