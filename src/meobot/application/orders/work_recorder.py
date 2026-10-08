"""A Leader's first approval of a production node is one result in the KPI ledger.

Inline, inside the approval's transaction, rather than through the pending-row
and sweep pattern the content projector uses: an order has no replay and no
undo, the approval already holds the row lock, and a projector would add a
table, a Celery task and thirty seconds of delay for no convergence benefit.
The result row and the approval commit together or not at all.

What counts (design §12):

* the nodes ``BIEN_TAP``, ``THIET_KE`` and ``DUNG``; attaching the link is a
  hand-off, not measured work;
* the **first** approval only. A node sent back and approved again is the
  same work, and the ledger's ``(source_type, source_key)`` uniqueness
  guarantees it even if this were called twice;
* the subject is the assignee at the moment of approval, the validator the
  Leader. A Leader who did the work themselves leaves the result ``PENDING``
  for the head to confirm - the ledger's own rule, not re-implemented here;
* the work type comes from ``order_work_rules``: the row for (node, video
  type), else the node's default. **No rule means no result**, logged, and the
  approval still succeeds - the admin page reports the gap.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.pr_work_result_service import PrWorkResultService
from meobot.core.logging import get_logger
from meobot.db.models.order import Order, OrderNode, OrderWorkRule
from meobot.domain.identity.models import Actor
from meobot.domain.orders.models import KPI_NODE_TYPES
from meobot.domain.pr.work_results import PrWorkResultSource

logger = get_logger(__name__)


def order_source_key(node: OrderNode) -> str:
    """``order:<node uuid>:<NODE_TYPE>`` - a different namespace from PR's keys."""
    return f"order:{node.id}:{node.node_type.value}"


class OrderWorkRecorder:
    def __init__(self, session: AsyncSession, results: PrWorkResultService) -> None:
        self._session = session
        self._results = results

    async def work_type_for(self, order: Order, node: OrderNode) -> uuid.UUID | None:
        rows = (
            await self._session.scalars(
                select(OrderWorkRule).where(
                    OrderWorkRule.unit_id == order.unit_id,
                    OrderWorkRule.node_type == node.node_type,
                    OrderWorkRule.is_active.is_(True),
                )
            )
        ).all()
        exact = next((rule for rule in rows if rule.video_type is order.video_type), None)
        default = next((rule for rule in rows if rule.video_type is None), None)
        chosen = exact or default
        return None if chosen is None else chosen.work_type_id

    async def record_first_approval(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        order: Order,
        node: OrderNode,
        approved_at: datetime,
        link: str | None,
    ) -> bool:
        """Record the result. ``False`` when nothing was recorded, and why is logged."""
        if node.node_type not in KPI_NODE_TYPES or node.assignee_user_id is None:
            return False
        work_type_id = await self.work_type_for(order, node)
        if work_type_id is None:
            logger.warning(
                "order_kpi_rule_missing",
                extra={
                    "order_code": order.code,
                    "node_type": node.node_type.value,
                    "video_type": order.video_type.value,
                },
            )
            return False
        await self._results.record_source_result(
            actor=actor,
            request_id=request_id,
            source_type=PrWorkResultSource.ORDER,
            source_key=order_source_key(node),
            work_type_id=work_type_id,
            subject_user_id=node.assignee_user_id,
            occurred_at=approved_at,
            validated_by_user_id=actor.user_id,
            validated_at=approved_at,
            label=f"{order.code} — {order.title}",
            link=link,
        )
        return True


__all__ = ["OrderWorkRecorder", "order_source_key"]
