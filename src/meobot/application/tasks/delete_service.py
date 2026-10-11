"""Deleting a task for good ("Xoá task"). The OWNER only.

One door for both units, like the task page:

* an **ORD order** goes with everything hanging off it - its steps, hand-ins,
  approvals, history, the tokens it took off people's days, the KPI results it
  counted and the notifications about it. The audit row is written first, in
  the same transaction, as plain text: it outlives the order;
* a **PR content item** is deleted by PR's own permanent deletion
  (:class:`~meobot.application.pr_lifecycle_service.PrContentLifecycleService`),
  rules and all - a published item still cannot be deleted. Nothing of PR's is
  re-decided here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.orders.work_recorder import order_source_key
from meobot.application.pr_services import PrServices
from meobot.application.tasks.detail_service import TaskDetailService
from meobot.core.errors import AuthorizationError
from meobot.core.time import utcnow
from meobot.db.models.order import (
    Order,
    OrderApproval,
    OrderEvent,
    OrderNode,
    OrderSubmission,
    OrderTokenLedger,
)
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.task import SOURCE_PR_CONTENT, Task
from meobot.db.models.user_notification import UserNotification
from meobot.domain.audit.models import AuditAction, AuditResult
from meobot.domain.identity.models import Actor, Role
from meobot.domain.pr.work_results import PrWorkResultSource


class TaskDeleteForbiddenError(AuthorizationError):
    code = "task_delete_forbidden"

    def __init__(self) -> None:
        super().__init__("Chỉ chủ sở hữu mới xoá được task.", details={"reason": self.code})


def may_delete_tasks(actor: Actor) -> bool:
    return actor.role is Role.OWNER


class TaskDeleteService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        detail: TaskDetailService,
        pr: PrServices,
        audit: AuditService,
    ) -> None:
        self._session = session
        self._detail = detail
        self._pr = pr
        self._audit = audit

    async def delete(self, *, actor: Actor, request_id: uuid.UUID, ref: str) -> None:
        if not may_delete_tasks(actor):
            raise TaskDeleteForbiddenError()
        task = await self._detail.resolve(actor, ref)
        if task.source_type == SOURCE_PR_CONTENT:
            assert task.pr_content_id is not None
            await self._pr.lifecycle.delete_content(
                actor=actor, request_id=request_id, content_id=task.pr_content_id
            )
            return
        assert task.order_id is not None
        await self._delete_order(actor, request_id, task.order_id)

    async def _delete_order(self, actor: Actor, request_id: uuid.UUID, order_id: uuid.UUID) -> None:
        order = await self._session.get(Order, order_id)
        if order is None:  # pragma: no cover - resolved a moment ago
            return
        nodes = (
            await self._session.scalars(select(OrderNode).where(OrderNode.order_id == order_id))
        ).all()
        # Written first: if a delete below fails, this row goes with it.
        await self._audit.record_action(
            request_id=request_id,
            actor=actor,
            action=AuditAction.ORDER_DELETED.value,
            result=AuditResult.SUCCESS,
            entity_type="order",
            entity_id=str(order.id),
            after_data={
                # Plain text, no foreign key: this row outlives the order.
                "order_id": str(order.id),
                "order_code": order.code,
                "title": order.title,
                "stage_at_delete": order.stage.value,
                "owner_user_id": str(order.owner_user_id),
                "deleted_at": utcnow().isoformat(),
            },
        )
        keys = [order_source_key(node) for node in nodes]
        if keys:
            await self._session.execute(
                delete(PrWorkResult).where(
                    PrWorkResult.source_type == PrWorkResultSource.ORDER,
                    PrWorkResult.source_key.in_(keys),
                )
            )
        await self._session.execute(
            delete(UserNotification).where(
                UserNotification.target_kind == "order", UserNotification.target_id == order_id
            )
        )
        # Children before parents: every foreign key here is RESTRICT.
        node_ids = [node.id for node in nodes]
        for row in (order, *nodes):
            # Gone by SQL below: the session must not flush them back.
            self._session.expunge(row)
        await self._session.execute(
            delete(OrderTokenLedger).where(OrderTokenLedger.order_id == order_id)
        )
        await self._session.execute(delete(OrderApproval).where(OrderApproval.order_id == order_id))
        await self._session.execute(delete(OrderEvent).where(OrderEvent.order_id == order_id))
        if node_ids:
            await self._session.execute(
                delete(OrderSubmission).where(OrderSubmission.node_id.in_(node_ids))
            )
        await self._session.execute(delete(OrderNode).where(OrderNode.order_id == order_id))
        await self._session.execute(delete(Task).where(Task.order_id == order_id))
        await self._session.execute(delete(Order).where(Order.id == order_id))
        await self._session.flush()


__all__ = ["TaskDeleteForbiddenError", "TaskDeleteService", "may_delete_tasks"]
