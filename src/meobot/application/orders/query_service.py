"""Reads of one order, inside the caller's scope.

The detail is everything the order page shows: the order, its four nodes,
every hand-in, every gate decision, the log, and the actions this person may
take right now - the same policy the command service enforces, so a button
that appears is a button that works.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.orders.scope import OrderScope
from meobot.application.units.directory import UnitDirectoryService
from meobot.core.config import Settings
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.order import Order, OrderApproval, OrderEvent, OrderNode, OrderSubmission
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor
from meobot.domain.orders.errors import OrderNotFoundError
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.orders.pipeline import (
    NodeView,
    OrderAction,
    OrderView,
    available_actions,
    is_urgent,
)
from meobot.domain.units.models import UnitCode, UnitSettings


@dataclass(slots=True)
class OrderDetail:
    order: Order
    nodes: list[OrderNode]
    submissions: list[OrderSubmission]
    approvals: list[OrderApproval]
    events: list[OrderEvent]
    actions: tuple[OrderAction, ...]
    urgent: bool
    #: Display names for every user id the detail mentions.
    names: dict[uuid.UUID, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScopedUnit:
    scope: OrderScope
    settings: UnitSettings


class OrderQueryService:
    def __init__(
        self, session: AsyncSession, settings: Settings, directory: UnitDirectoryService
    ) -> None:
        self._session = session
        self._settings = settings
        self._directory = directory

    async def scoped(self, actor: Actor) -> ScopedUnit:
        membership = await self._directory.require(actor, UnitCode.ADS)
        unit = await self._directory.unit(UnitCode.ADS)
        unit_settings = UnitSettings.model_validate(unit.settings or {})
        return ScopedUnit(
            scope=OrderScope.for_actor(membership, unit_settings, unit_id=unit.id),
            settings=unit_settings,
        )

    async def resolve(self, actor: Actor, ref: str) -> Order:
        """An order by id or by code, if this person may see it."""
        scoped = await self.scoped(actor)
        statement = select(Order)
        try:
            statement = statement.where(Order.id == uuid.UUID(ref))
        except ValueError:
            statement = statement.where(Order.code == ref.strip().upper())
        order: Order | None = await self._session.scalar(scoped.scope.apply(statement))
        if order is None:
            raise OrderNotFoundError("Không tìm thấy.", details={"reason": "order_not_visible"})
        return order

    async def detail(self, actor: Actor, ref: str) -> OrderDetail:
        scoped = await self.scoped(actor)
        order = await self.resolve(actor, ref)
        # Pipeline order (Biên tập, Thiết kế, Dựng, Gắn link), not insert
        # or alphabetical order: the strip on the page reads left to right.
        rank = {node_type: index for index, node_type in enumerate(OrderNodeType)}
        nodes = sorted(
            (
                await self._session.scalars(select(OrderNode).where(OrderNode.order_id == order.id))
            ).all(),
            key=lambda node: rank[node.node_type],
        )
        node_ids = [node.id for node in nodes]
        submissions = list(
            (
                await self._session.scalars(
                    select(OrderSubmission)
                    .where(OrderSubmission.node_id.in_(node_ids))
                    .order_by(OrderSubmission.created_at)
                )
            ).all()
        )
        approvals = list(
            (
                await self._session.scalars(
                    select(OrderApproval)
                    .where(OrderApproval.order_id == order.id)
                    .order_by(OrderApproval.created_at)
                )
            ).all()
        )
        events = list(
            (
                await self._session.scalars(
                    select(OrderEvent)
                    .where(OrderEvent.order_id == order.id)
                    .order_by(OrderEvent.created_at)
                )
            ).all()
        )
        view = OrderView(
            id=order.id,
            video_type=order.video_type,
            stage=order.stage,
            owner_user_id=order.owner_user_id,
            is_priority=order.is_priority,
        )
        node_views = tuple(
            NodeView(
                id=node.id,
                node_type=node.node_type,
                status=node.status,
                assignee_user_id=node.assignee_user_id,
                preassigned_user_id=node.preassigned_user_id,
                accepted_at=node.accepted_at,
            )
            for node in nodes
        )
        actions = available_actions(view, node_views, scoped.scope.context)
        user_ids: set[uuid.UUID] = {order.owner_user_id}
        if order.order_approved_by_user_id:
            user_ids.add(order.order_approved_by_user_id)
        for node in nodes:
            user_ids.update(
                item
                for item in (
                    node.assignee_user_id,
                    node.preassigned_user_id,
                    node.approved_by_user_id,
                )
                if item is not None
            )
        for submission in submissions:
            user_ids.add(submission.submitted_by_user_id)
        for approval in approvals:
            user_ids.add(approval.actor_user_id)
        for event in events:
            user_ids.add(event.actor_user_id)
            if event.assignee_user_id is not None:
                user_ids.add(event.assignee_user_id)
        names = await self.names(user_ids)
        return OrderDetail(
            order=order,
            nodes=nodes,
            submissions=submissions,
            approvals=approvals,
            events=events,
            actions=actions,
            urgent=is_urgent(
                submitted_at=ensure_utc(order.submitted_at),
                now=utcnow(),
                stage=order.stage,
                urgent_days=scoped.settings.urgent_days,
            ),
            names=names,
        )

    async def names(self, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not user_ids:
            return {}
        rows = (
            (
                await self._session.execute(
                    select(User.id, User.full_name).where(User.id.in_(list(user_ids)))
                )
            )
            .tuples()
            .all()
        )
        return dict(rows)


__all__ = ["OrderDetail", "OrderQueryService", "ScopedUnit"]
