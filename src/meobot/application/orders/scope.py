"""What one person may see of the Ads orders, as a ``WHERE`` clause.

The scope is built once per request from the person's unit tag and applied
to every query that reads ``orders`` - the board, the detail, and the load
inside every command - so a person cannot act on an order they cannot list.
An order outside the scope does not exist for them: a 404, never a 403.

The rule, from the design (§6.3):

* the department head sees every order of the unit (the OWNER too);
* a marketer sees the orders they placed;
* a function's Leader sees every order that visits their node;
* a function's member sees the orders where they hold, or were chosen for, a
  node.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, exists, false, or_, select

from meobot.db.models.order import Order, OrderNode
from meobot.domain.orders.errors import OrderNotFoundError
from meobot.domain.orders.models import OrderNodeStatus, OrderNodeType
from meobot.domain.orders.permissions import AdsPermission
from meobot.domain.orders.pipeline import OrderActorContext
from meobot.domain.units.models import UnitCode, UnitMembership, UnitSettings

NOT_VISIBLE = "Không tìm thấy."


@dataclass(frozen=True, slots=True)
class OrderScope:
    unit_id: uuid.UUID
    user_id: uuid.UUID | None
    sees_all: bool
    lead_node_types: frozenset[OrderNodeType]
    context: OrderActorContext

    @classmethod
    def for_actor(
        cls, membership: UnitMembership, settings: UnitSettings, *, unit_id: uuid.UUID
    ) -> OrderScope:
        """The scope, or :class:`OrderNotFoundError` for somebody outside Ads."""
        if not membership.has(UnitCode.ADS):
            raise OrderNotFoundError(NOT_VISIBLE, details={"reason": "unit_not_visible"})
        context = OrderActorContext.from_membership(membership, settings)
        return cls(
            unit_id=unit_id,
            user_id=membership.user_id,
            sees_all=context.permissions.allows(AdsPermission.VIEW),
            # "Own" visibility: the orders that reach a node they manage.
            lead_node_types=context.permissions.nodes(AdsPermission.NODE_ASSIGN)
            | context.permissions.nodes(AdsPermission.NODE_REVIEW)
            | context.lead_node_types,
            context=context,
        )

    def apply(self, statement: Select) -> Select:  # type: ignore[type-arg]
        """Narrow a ``select`` over :class:`Order` to what this person may see."""
        statement = statement.where(Order.unit_id == self.unit_id)
        if self.sees_all:
            return statement
        if self.user_id is None:
            return statement.where(false())
        conditions = [Order.owner_user_id == self.user_id]
        if self.lead_node_types:
            conditions.append(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        OrderNode.node_type.in_(list(self.lead_node_types)),
                        OrderNode.status != OrderNodeStatus.BO_QUA,
                    )
                )
            )
        conditions.append(
            exists(
                select(OrderNode.id).where(
                    OrderNode.order_id == Order.id,
                    or_(
                        OrderNode.assignee_user_id == self.user_id,
                        OrderNode.preassigned_user_id == self.user_id,
                    ),
                )
            )
        )
        return statement.where(or_(*conditions))


__all__ = ["NOT_VISIBLE", "OrderScope"]
