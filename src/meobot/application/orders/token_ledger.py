"""Tokens taken off a person's day when their node is approved (0053).

Inline, inside the approval's transaction, like the KPI recorder beside it
(:mod:`~meobot.application.orders.work_recorder`) - and separate from it: the
token ledger is ORD's own effort measure, never the PR KPI ledger.

What is taken, and when:

* the **first** completion of a node takes its ``token_estimate`` (row kind
  ``ESTIMATE``, round 0) and fixes ``deadline_met`` against ``deadline_at``;
* any completion takes the revision tokens not taken yet
  (``token_revision`` minus the ``REVISION`` rows so far), as a ``REVISION``
  row numbered by the node's ``revision_count``;
* the person is the assignee at that moment; the day is the Vietnamese
  calendar day of the approval (:func:`~meobot.domain.orders.deadlines.work_day`).

``(node_id, kind, revision_no)`` is unique, so a retry never takes twice.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.time import ensure_utc
from meobot.db.models.order import Order, OrderNode, OrderTokenLedger
from meobot.domain.orders.deadlines import work_day
from meobot.domain.orders.models import OrderTokenKind


class OrderTokenLedgerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def revision_taken(self, node: OrderNode) -> Decimal:
        """The revision tokens of ``node`` already taken off somebody's day."""
        taken = await self._session.scalar(
            select(func.coalesce(func.sum(OrderTokenLedger.tokens), 0)).where(
                OrderTokenLedger.node_id == node.id,
                OrderTokenLedger.kind == OrderTokenKind.REVISION,
            )
        )
        return Decimal(taken or 0)

    async def revision_outstanding(self, node: OrderNode) -> Decimal:
        """This round's revision tokens: entered, not taken yet."""
        return max(Decimal(node.token_revision or 0) - await self.revision_taken(node), Decimal(0))

    async def record_completion(
        self, *, order: Order, node: OrderNode, at: datetime, first: bool
    ) -> None:
        if first:
            node.deadline_met = (
                None if node.deadline_at is None else at <= ensure_utc(node.deadline_at)
            )
        worker = node.assignee_user_id
        if worker is None:
            return
        day = work_day(at)
        if first and node.token_estimate:
            estimate = node.token_estimate
            await self._take(order, node, worker, day, estimate, OrderTokenKind.ESTIMATE, 0)
        outstanding = await self.revision_outstanding(node)
        if outstanding > 0:
            numbers = set(
                await self._session.scalars(
                    select(OrderTokenLedger.revision_no).where(
                        OrderTokenLedger.node_id == node.id,
                        OrderTokenLedger.kind == OrderTokenKind.REVISION,
                    )
                )
            )
            number = max(node.revision_count, 1)
            while number in numbers:
                number += 1
            await self._take(order, node, worker, day, outstanding, OrderTokenKind.REVISION, number)
        await self._session.flush()

    async def _take(
        self,
        order: Order,
        node: OrderNode,
        worker: uuid.UUID,
        day: date,
        tokens: Decimal,
        kind: OrderTokenKind,
        number: int,
    ) -> None:
        self._session.add(
            OrderTokenLedger(
                unit_id=order.unit_id,
                user_id=worker,
                order_id=order.id,
                node_id=node.id,
                work_date=day,
                tokens=Decimal(tokens),
                kind=kind,
                revision_no=number,
            )
        )


__all__ = ["OrderTokenLedgerService"]
