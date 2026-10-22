"""How much work each person is holding, across ORD and PR (0069).

Two numbers for the "Giao việc" pickers, everywhere in the app: how many pieces
of work are **assigned** to a person but not started, and how many they are
**doing**. Counting both streams gives a Leader the real load before they hand
one more task out.

Read-only; it changes nothing. It does not depend on effort mode.

* **ORD**: a person's active order nodes. A Design node handed to several people
  is counted per person through ``order_node_assignees`` (0069); every other
  node through ``order_nodes.assignee_user_id``.
* **PR**: the content a person is the producer of. ``APPROVED`` with a producer
  is assigned (waiting to start); ``PRODUCTION`` / ``INTERNAL_REVIEW`` is doing.

Each source query groups only by ``user_id`` and splits assigned vs doing with
``count(*) FILTER (WHERE …)``; grouping by a parameterised boolean expression is
rejected by PostgreSQL ("column must appear in the GROUP BY clause").
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.order import Order, OrderNode, OrderNodeAssignee
from meobot.db.models.pr import PrContentItem
from meobot.domain.orders.models import ACTIVE_NODE_STATUSES, TERMINAL_STAGES, OrderNodeStatus
from meobot.domain.pr.models import PrWorkflowStage


@dataclass(frozen=True, slots=True)
class Workload:
    """What one person is holding right now."""

    assigned: int = 0
    doing: int = 0


class WorkloadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def counts(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Workload]:
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        assigned: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
        doing: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
        for source in (self._ord, self._pr):
            for user_id, add_assigned, add_doing in await source(ids):
                assigned[user_id] += add_assigned
                doing[user_id] += add_doing
        return {user_id: Workload(assigned[user_id], doing[user_id]) for user_id in ids}

    async def _ord(self, ids: Sequence[uuid.UUID]) -> list[tuple[uuid.UUID, int, int]]:
        """ORD nodes, as ``(user, assigned, doing)``. Nodes with per-person rows
        (0069) are counted through the rows; the rest through the node."""
        active = sorted(ACTIVE_NODE_STATUSES, key=lambda status: status.value)
        closed = sorted(TERMINAL_STAGES, key=lambda stage: stage.value)
        rows: list[tuple[uuid.UUID, int, int]] = []
        # Per-person Design rows: accepted = doing, not yet accepted = assigned.
        row_q = (
            select(
                OrderNodeAssignee.user_id,
                func.count().filter(OrderNodeAssignee.accepted_at.is_(None)),
                func.count().filter(OrderNodeAssignee.accepted_at.is_not(None)),
            )
            .join(OrderNode, OrderNode.id == OrderNodeAssignee.node_id)
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                OrderNodeAssignee.user_id.in_(ids),
                OrderNodeAssignee.removed_at.is_(None),
                OrderNode.status.in_(active),
                Order.stage.notin_(closed),
            )
            .group_by(OrderNodeAssignee.user_id)
        )
        rows.extend(
            (user_id, int(assigned), int(doing))
            for user_id, assigned, doing in await self._session.execute(row_q)
        )
        # Single-assignee nodes: only those with no per-person rows, so a Design
        # node is never counted twice.
        has_rows = (
            select(OrderNodeAssignee.id)
            .where(
                OrderNodeAssignee.node_id == OrderNode.id,
                OrderNodeAssignee.removed_at.is_(None),
            )
            .exists()
        )
        node_q = (
            select(
                OrderNode.assignee_user_id,
                func.count().filter(OrderNode.accepted_at.is_(None)),
                func.count().filter(OrderNode.accepted_at.is_not(None)),
            )
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                OrderNode.assignee_user_id.in_(ids),
                OrderNode.status.in_(active),
                OrderNode.status != OrderNodeStatus.CHO_DUYET,
                Order.stage.notin_(closed),
                ~has_rows,
            )
            .group_by(OrderNode.assignee_user_id)
        )
        rows.extend(
            (user_id, int(assigned), int(doing))
            for user_id, assigned, doing in await self._session.execute(node_q)
            if user_id is not None
        )
        return rows

    async def _pr(self, ids: Sequence[uuid.UUID]) -> list[tuple[uuid.UUID, int, int]]:
        """PR productions, as ``(user, assigned, doing)``."""
        doing_stages = [PrWorkflowStage.PRODUCTION, PrWorkflowStage.INTERNAL_REVIEW]
        query = (
            select(
                PrContentItem.producer_user_id,
                func.count().filter(PrContentItem.workflow_stage == PrWorkflowStage.APPROVED),
                func.count().filter(PrContentItem.workflow_stage.in_(doing_stages)),
            )
            .where(
                PrContentItem.producer_user_id.in_(ids),
                PrContentItem.workflow_stage.in_([PrWorkflowStage.APPROVED, *doing_stages]),
            )
            .group_by(PrContentItem.producer_user_id)
        )
        return [
            (user_id, int(assigned), int(doing))
            for user_id, assigned, doing in await self._session.execute(query)
            if user_id is not None
        ]


__all__ = ["Workload", "WorkloadService"]
