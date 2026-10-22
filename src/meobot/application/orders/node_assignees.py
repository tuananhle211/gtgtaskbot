"""Several designers on one Design node, each with their own scored list (0069).

A Design (THIET_KE) node can be handed to more than one person. Each person
has a row in ``order_node_assignees`` carrying their own list of design work
items ("mã task design" + quantity), their accept / submit stamps, and (once
they hand in) their submission. The node only advances when **every** open
assignee has handed in; the whole team's lists are summed and compared to the
order's list to decide whether it finishes or waits for the Design Leader.

Every change to a person's list is written to ``order_node_assignee_events`` so
the monthly scoring can read what each person claimed and when it changed.

Single-assignee nodes (Biên kịch, Dựng) do not use this: they keep working off
``order_nodes.assignee_user_id``. This service is only reached for Design.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.time import utcnow
from meobot.db.models.order import OrderNode, OrderNodeAssignee, OrderNodeAssigneeEvent
from meobot.domain.identity.models import Actor
from meobot.domain.orders.design_codes import (
    DesignItem,
    items_from_json,
    items_to_json,
    sum_work_items,
)
from meobot.domain.orders.models import OrderNodeAssigneeEventKind


async def load_open_assignees(
    session: AsyncSession, node_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[OrderNodeAssignee]]:
    """Every open assignee row of the given nodes, grouped by node, in join
    order. Used by every read that builds a node's view or board cell (0069)."""
    if not node_ids:
        return {}
    rows = await session.scalars(
        select(OrderNodeAssignee)
        .where(
            OrderNodeAssignee.node_id.in_(node_ids),
            OrderNodeAssignee.removed_at.is_(None),
        )
        .order_by(OrderNodeAssignee.assigned_at, OrderNodeAssignee.id)
    )
    grouped: dict[uuid.UUID, list[OrderNodeAssignee]] = {}
    for row in rows:
        grouped.setdefault(row.node_id, []).append(row)
    return grouped


def assignee_sets(
    rows: Iterable[OrderNodeAssignee],
) -> tuple[frozenset[uuid.UUID], frozenset[uuid.UUID], frozenset[uuid.UUID]]:
    """``(assigned, accepted, submitted)`` user-id sets for a node's rows -
    what :class:`~meobot.domain.orders.pipeline.NodeView` carries."""
    rows = list(rows)
    return (
        frozenset(row.user_id for row in rows),
        frozenset(row.user_id for row in rows if row.accepted_at is not None),
        frozenset(row.user_id for row in rows if row.submitted_at is not None),
    )


class OrderNodeAssigneeService:
    """Reads and writes the per-person rows of a Design node."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_rows(self, node_id: uuid.UUID) -> list[OrderNodeAssignee]:
        """Everybody still on the node, in the order they joined it."""
        return list(
            await self._session.scalars(
                select(OrderNodeAssignee)
                .where(
                    OrderNodeAssignee.node_id == node_id,
                    OrderNodeAssignee.removed_at.is_(None),
                )
                .order_by(OrderNodeAssignee.assigned_at, OrderNodeAssignee.id)
            )
        )

    async def row_for(self, node_id: uuid.UUID, user_id: uuid.UUID) -> OrderNodeAssignee | None:
        row: OrderNodeAssignee | None = await self._session.scalar(
            select(OrderNodeAssignee).where(
                OrderNodeAssignee.node_id == node_id,
                OrderNodeAssignee.user_id == user_id,
                OrderNodeAssignee.removed_at.is_(None),
            )
        )
        return row

    async def set_people(
        self,
        node: OrderNode,
        user_ids: Sequence[uuid.UUID],
        *,
        actor: Actor,
        items_by_user: Mapping[uuid.UUID, Sequence[DesignItem]] | None = None,
    ) -> list[OrderNodeAssignee]:
        """Make the node's open assignees exactly ``user_ids``.

        A new person gets a row (with the Leader's split, if given); somebody
        dropped is marked ``removed_at``; somebody who stays keeps their accept
        / submit state, and their list is updated only when a new split is
        given for them. Every add or split is logged.
        """
        wanted = list(dict.fromkeys(user_ids))
        existing = {row.user_id: row for row in await self.open_rows(node.id)}
        now = utcnow()
        items_by_user = items_by_user or {}
        rows: list[OrderNodeAssignee] = []
        for user_id in wanted:
            items = items_by_user.get(user_id)
            row = existing.get(user_id)
            if row is None:
                row = OrderNodeAssignee(
                    node_id=node.id,
                    user_id=user_id,
                    assigned_at=now,
                    work_items=items_to_json(items) if items else None,
                )
                self._session.add(row)
                await self._session.flush()
                await self._log(
                    node, user_id, OrderNodeAssigneeEventKind.ASSIGNED, items or (), actor
                )
            elif items is not None:
                row.work_items = items_to_json(items)
                await self._log(
                    node, user_id, OrderNodeAssigneeEventKind.ITEMS_EDITED, items, actor
                )
            rows.append(row)
        for user_id, row in existing.items():
            if user_id not in wanted:
                row.removed_at = now
        await self._session.flush()
        return rows

    async def set_items(
        self, node: OrderNode, user_id: uuid.UUID, items: Sequence[DesignItem], *, actor: Actor
    ) -> OrderNodeAssignee:
        """One person edits their own list (before they hand in)."""
        row = await self.row_for(node.id, user_id)
        assert row is not None
        row.work_items = items_to_json(items)
        await self._log(node, user_id, OrderNodeAssigneeEventKind.ITEMS_EDITED, items, actor)
        await self._session.flush()
        return row

    async def take(self, node: OrderNode, user_id: uuid.UUID, *, actor: Actor) -> OrderNodeAssignee:
        """Accept the node. A Leader taking a node routed to them (no row yet)
        becomes one of its assignees here."""
        row = await self.row_for(node.id, user_id)
        if row is None:
            row = OrderNodeAssignee(
                node_id=node.id, user_id=user_id, assigned_at=utcnow(), accepted_at=utcnow()
            )
            self._session.add(row)
            await self._session.flush()
            await self._log(node, user_id, OrderNodeAssigneeEventKind.ASSIGNED, (), actor)
        elif row.accepted_at is None:
            row.accepted_at = utcnow()
        await self._session.flush()
        return row

    async def mark_submitted(
        self,
        node: OrderNode,
        user_id: uuid.UUID,
        items: Sequence[DesignItem],
        submission_id: uuid.UUID,
        *,
        actor: Actor,
    ) -> OrderNodeAssignee:
        row = await self.row_for(node.id, user_id)
        assert row is not None
        row.work_items = items_to_json(items)
        row.submitted_at = utcnow()
        row.submission_id = submission_id
        if row.accepted_at is None:
            row.accepted_at = row.submitted_at
        await self._log(node, user_id, OrderNodeAssigneeEventKind.SUBMITTED, items, actor)
        await self._session.flush()
        return row

    async def reopen(self, node: OrderNode, *, actor: Actor) -> None:
        """A return sends everybody's hand-in back; their lists are kept."""
        for row in await self.open_rows(node.id):
            row.submitted_at = None
            row.submission_id = None
            await self._log(
                node,
                row.user_id,
                OrderNodeAssigneeEventKind.RETURNED,
                items_from_json(row.work_items),
                actor,
            )
        await self._session.flush()

    async def all_submitted(self, node_id: uuid.UUID) -> bool:
        rows = await self.open_rows(node_id)
        return bool(rows) and all(row.submitted_at is not None for row in rows)

    async def combined_items(self, node_id: uuid.UUID) -> tuple[DesignItem, ...]:
        rows = await self.open_rows(node_id)
        return sum_work_items(items_from_json(row.work_items) for row in rows)

    async def log_adjustment(
        self, node: OrderNode, items: Sequence[DesignItem], *, actor: Actor
    ) -> None:
        """The Leader chose the final list on approval."""
        for row in await self.open_rows(node.id):
            await self._log(
                node, row.user_id, OrderNodeAssigneeEventKind.LEADER_ADJUSTED, items, actor
            )
        await self._session.flush()

    async def _log(
        self,
        node: OrderNode,
        user_id: uuid.UUID,
        kind: OrderNodeAssigneeEventKind,
        items: Iterable[DesignItem],
        actor: Actor,
        note: str | None = None,
    ) -> None:
        assert actor.user_id is not None
        self._session.add(
            OrderNodeAssigneeEvent(
                node_id=node.id,
                user_id=user_id,
                kind=kind,
                work_items=items_to_json(items) or None,
                revision_no=node.revision_count,
                actor_user_id=actor.user_id,
                note=note,
            )
        )


__all__ = ["OrderNodeAssigneeService", "assignee_sets", "load_open_assignees"]
