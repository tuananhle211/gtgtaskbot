"""Who is told what, at every hand-off of an order.

Modelled on :class:`~meobot.application.pr_notifications.PrNotificationService`:
the web inbox row is written **first and unconditionally** - it is the
notification - and Telegram is one delivery channel, attempted only when the
unit opted in (``UnitSettings.telegram_enabled``) and the person has a
private chat with the bot. Both writes ride the caller's transaction, so the
decision and the record of announcing it commit together.

The recipients, from the design (§11.1): the department heads when an order
is submitted; the next node's Leaders and the person chosen for it when it
comes up; the orderer when somebody accepts or when it is sent back; the
assignee when assigned or returned; the next approver when work is handed in
- for the final review that is the orderer. Never the person who acted.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.notification_router import NotificationRouter, RouteRequest
from meobot.application.recipient_resolver import RecipientResolver
from meobot.application.units.directory import UnitDirectoryService
from meobot.application.user_notification_service import UserNotificationService
from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.db.models.order import Order, OrderNode
from meobot.domain.identity.models import Actor
from meobot.domain.notifications.models import NotificationEvent
from meobot.domain.notifications.web import web_title
from meobot.domain.orders.labels import node_type_label
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.orders.pipeline import link_attacher_node
from meobot.domain.units.models import UnitSettings

logger = get_logger(__name__)

TEMPLATE_KEY = "orders.update"


class OrderNotificationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._router = NotificationRouter(session, settings)
        self._resolver = RecipientResolver(session, settings)
        self._inbox = UserNotificationService(session)
        self._directory = UnitDirectoryService(session)

    # --- the events -----------------------------------------------------------

    async def order_submitted(self, *, actor: Actor, order: Order, unit: UnitSettings) -> None:
        heads = await self._directory.heads(order.unit_id)
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_SUBMITTED,
            recipients=[row.user.id for row in heads],
            body=f"“{order.title}” ({order.code}) đang chờ bạn duyệt order.",
            suffix=f"round{order.version}",
        )

    async def order_returned(
        self, *, actor: Actor, order: Order, unit: UnitSettings, reason: str
    ) -> None:
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_RETURNED,
            recipients=[order.owner_user_id],
            body=f"Trưởng phòng chưa duyệt “{order.title}” ({order.code}). Sửa rồi gửi lại.",
            note=reason,
            suffix=f"v{order.version}",
        )

    async def node_turn(
        self, *, actor: Actor, order: Order, node: OrderNode, unit: UnitSettings, first: bool
    ) -> None:
        """The node came up: its Leaders, and whoever was chosen for it. The
        link node's Leaders are those of the function attaching it."""
        function = (
            link_attacher_node(unit, order.video_type)
            if node.node_type is OrderNodeType.GAN_LINK
            else node.node_type
        )
        leads = await self._directory.leads(order.unit_id, function)
        recipients = [row.user.id for row in leads]
        if node.assignee_user_id is not None:
            recipients.append(node.assignee_user_id)
        label = node_type_label(node.node_type)
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_APPROVED if first else NotificationEvent.ORDER_NODE_TURN,
            recipients=recipients,
            body=f"“{order.title}” ({order.code}) đã tới công đoạn {label}.",
            suffix=f"{node.node_type.value}:{node.revision_count}",
        )

    async def node_assigned(
        self, *, actor: Actor, order: Order, node: OrderNode, unit: UnitSettings
    ) -> None:
        if node.assignee_user_id is None:
            return
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_NODE_ASSIGNED,
            recipients=[node.assignee_user_id],
            body=(
                f"Bạn được giao công đoạn {node_type_label(node.node_type)} của "
                f"“{order.title}” ({order.code})."
            ),
            suffix=f"{node.node_type.value}:{node.assignee_user_id}:{node.version}",
        )

    async def node_accepted(
        self, *, actor: Actor, order: Order, node: OrderNode, unit: UnitSettings
    ) -> None:
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_NODE_ACCEPTED,
            recipients=[order.owner_user_id],
            body=(
                f"{actor.full_name} đã nhận công đoạn {node_type_label(node.node_type)} của "
                f"“{order.title}” ({order.code})."
            ),
            suffix=f"{node.node_type.value}:{node.version}",
        )

    async def submission_ready(
        self,
        *,
        actor: Actor,
        order: Order,
        node: OrderNode,
        unit: UnitSettings,
        approver_node: OrderNodeType | None,
        to_owner: bool = False,
    ) -> None:
        """Work was handed in: the Leaders of ``approver_node``, or - for the
        final review - the orderer, whose review it is."""
        if to_owner:
            recipients = [order.owner_user_id]
        else:
            rows = await self._directory.leads(order.unit_id, approver_node or node.node_type)
            recipients = [row.user.id for row in rows]
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_SUBMISSION_READY,
            recipients=recipients,
            body=(
                f"“{order.title}” ({order.code}): công đoạn {node_type_label(node.node_type)} "
                f"đã nộp bản V{node.submission_count}, chờ bạn duyệt."
            ),
            suffix=f"{node.node_type.value}:V{node.submission_count}",
        )

    async def node_returned(
        self,
        *,
        actor: Actor,
        order: Order,
        node: OrderNode,
        unit: UnitSettings,
        note: str,
        final: bool,
    ) -> None:
        if node.assignee_user_id is None:
            return
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_FINAL_RETURNED
            if final
            else NotificationEvent.ORDER_NODE_RETURNED,
            recipients=[node.assignee_user_id],
            body=(
                f"“{order.title}” ({order.code}): công đoạn {node_type_label(node.node_type)} "
                "cần sửa lại."
            ),
            note=note,
            suffix=f"{node.node_type.value}:rev{node.revision_count}",
        )

    async def completed(self, *, actor: Actor, order: Order, unit: UnitSettings) -> None:
        await self._tell(
            actor=actor,
            order=order,
            unit=unit,
            event=NotificationEvent.ORDER_COMPLETED,
            recipients=[order.owner_user_id],
            body=f"“{order.title}” ({order.code}) đã được duyệt Final. Link sản phẩm đã sẵn sàng.",
            suffix="done",
        )

    # --- plumbing ---------------------------------------------------------------

    async def _tell(
        self,
        *,
        actor: Actor,
        order: Order,
        unit: UnitSettings,
        event: NotificationEvent,
        recipients: Iterable[uuid.UUID],
        body: str,
        suffix: str,
        note: str = "",
    ) -> None:
        seen: set[uuid.UUID] = set()
        for recipient in recipients:
            if recipient in seen or recipient == actor.user_id:
                continue
            seen.add(recipient)
            key = f"order:{order.id}:{event.value}:{suffix}:{recipient}"
            await self._inbox.record(
                recipient_user_id=recipient,
                event=event,
                title=web_title(event),
                body=body if not note else f"{body} Ghi chú: {note}",
                idempotency_key=key,
                target_kind="order",
                target_id=order.id,
            )
            if not unit.telegram_enabled:
                continue
            resolved = await self._resolver.private_destination(user_id=recipient)
            if not resolved.is_resolved or resolved.telegram_chat_id is None:
                logger.info(
                    "order_notification_recipient_unreachable",
                    extra={"user_id": str(recipient), "reason": resolved.message},
                )
                continue
            payload: dict[str, object] = {
                "heading": web_title(event).upper(),
                "title": order.title,
                "code": order.code,
                "body": body,
                "link": f"{self._settings.web_base_url.rstrip('/')}/orders/{order.code}",
            }
            if note:
                payload["note"] = note
            await self._router.route(
                [
                    RouteRequest(
                        event_type=event,
                        template_key=TEMPLATE_KEY,
                        payload=payload,
                        idempotency_key=key,
                        aggregate_type="order",
                        aggregate_id=order.id,
                        recipient_user_id=recipient,
                        private_chat_id=resolved.telegram_chat_id,
                        created_by_user_id=actor.user_id,
                        business_summary=f"{order.code} — {order.title}",
                    )
                ]
            )


__all__ = ["TEMPLATE_KEY", "OrderNotificationService"]
