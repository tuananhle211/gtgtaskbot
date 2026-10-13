"""What one person may see of the Ads orders, as a ``WHERE`` clause.

The scope is built once per request from the person's unit tag and applied
to every query that reads ``orders`` - the board, the detail, and the load
inside every command - so a person cannot act on an order they cannot list.
An order outside the scope does not exist for them: a 404, never a 403.

The rule, from the design (§6.3), narrowed to teams so that one order team
does not read another's ideas:

* the OWNER and an ADMIN see every order of the unit;
* a Trưởng phòng ORD sees their **own team's** orders - placed by the
  orderers who report to them (``org_unit_members.manager_user_id``) or by
  themselves, the ones they were chosen to approve, and the orders nobody else
  answers for (an orderer with no head set and no approver chosen);
* a marketer sees the orders they placed - not their teammates';
* a function's Leader sees every order that **has reached** their node - not
  the ones still upstream (an order at Biên kịch is not Design's or Dựng's
  yet), and still the ones that moved on;
* a function's member sees the orders where they hold, or were chosen for, a
  node the order has reached;
* the Design team - Leader and staff alike - sees every **birthday order**
  (0058): that board is the team's own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, exists, false, or_, select
from sqlalchemy.orm import aliased

from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.orders.errors import OrderNotFoundError
from meobot.domain.orders.models import OrderCategory, OrderNodeStatus, OrderNodeType
from meobot.domain.orders.permissions import AdsPermission
from meobot.domain.orders.pipeline import OrderActorContext
from meobot.domain.units.models import UnitCode, UnitMemberRole, UnitMembership, UnitSettings

NOT_VISIBLE = "Không tìm thấy."

#: A node the order has not come to: skipped by the process, or still upstream.
#: A function's people see an order only once it has reached their node.
NOT_REACHED = (OrderNodeStatus.BO_QUA, OrderNodeStatus.CHUA_TOI)


@dataclass(frozen=True, slots=True)
class OrderScope:
    unit_id: uuid.UUID
    user_id: uuid.UUID | None
    sees_all: bool
    lead_node_types: frozenset[OrderNodeType]
    context: OrderActorContext
    #: ``HEAD`` when the viewer is a Trưởng phòng ORD, limited to their own
    #: team's orders. None for everybody else.
    team_role: UnitMemberRole | None = None
    #: In the Design team: every birthday order is theirs to see.
    design_team: bool = False
    #: May use the birthday-order board at all (0059). Without it the board
    #: lists nothing - an order somebody placed before losing the grant is
    #: still theirs to open by its link.
    birthday_board: bool = True

    @classmethod
    def for_actor(
        cls, membership: UnitMembership, settings: UnitSettings, *, unit_id: uuid.UUID
    ) -> OrderScope:
        """The scope, or :class:`OrderNotFoundError` for somebody outside Ads."""
        if not membership.has(UnitCode.ADS):
            raise OrderNotFoundError(NOT_VISIBLE, details={"reason": "unit_not_visible"})
        context = OrderActorContext.from_membership(membership, settings)
        entry = membership.entry(UnitCode.ADS)
        # A head sees their own team's orders only (an orderer just their
        # own, by the matrix). The OWNER and an ADMIN are above the teams.
        team_role = (
            entry.role
            if entry is not None and not membership.sees_all and entry.role is UnitMemberRole.HEAD
            else None
        )
        return cls(
            unit_id=unit_id,
            user_id=membership.user_id,
            sees_all=team_role is None and context.permissions.allows(AdsPermission.VIEW),
            # "Own" visibility: the orders that reach a node they manage. A
            # head manages nodes of their team's orders only, so none here.
            lead_node_types=frozenset()
            if team_role is not None
            else context.permissions.nodes(AdsPermission.NODE_ASSIGN)
            | context.permissions.nodes(AdsPermission.NODE_REVIEW)
            | context.lead_node_types,
            context=context,
            team_role=team_role,
            design_team=entry is not None and entry.role is UnitMemberRole.THIET_KE,
            birthday_board=membership.sees_all
            or (
                entry is not None
                and (entry.role is UnitMemberRole.THIET_KE or entry.birthday_orders)
            ),
        )

    def apply(self, statement: Select) -> Select:  # type: ignore[type-arg]
        """Narrow a ``select`` over :class:`Order` to what this person may see."""
        statement = statement.where(Order.unit_id == self.unit_id)
        if self.sees_all:
            return statement
        if self.user_id is None:
            return statement.where(false())
        conditions = [Order.owner_user_id == self.user_id]
        if self.design_team:
            conditions.append(Order.category == OrderCategory.BIRTHDAY)
        if self.team_role is UnitMemberRole.HEAD:
            owner_head = boss_of(Order.owner_user_id, head=True)
            conditions.extend(
                [
                    # Their orderers' orders, and the ones they approve.
                    owner_head == self.user_id,
                    Order.approver_user_id == self.user_id,
                    # Nobody else answers for these: every head does.
                    and_(owner_head.is_(None), Order.approver_user_id.is_(None)),
                ]
            )
        if self.lead_node_types:
            conditions.append(
                exists(
                    select(OrderNode.id).where(
                        OrderNode.order_id == Order.id,
                        OrderNode.node_type.in_(list(self.lead_node_types)),
                        OrderNode.status.notin_(NOT_REACHED),
                    )
                )
            )
        conditions.append(
            exists(
                select(OrderNode.id).where(
                    OrderNode.order_id == Order.id,
                    # Chosen on the form counts once the order gets there.
                    OrderNode.status.notin_(NOT_REACHED),
                    or_(
                        OrderNode.assignee_user_id == self.user_id,
                        OrderNode.preassigned_user_id == self.user_id,
                    ),
                )
            )
        )
        return statement.where(or_(*conditions))


def boss_of(member: Any, *, head: bool = False) -> Any:
    """The active manager ``member`` (a column or a user id) reports to in the
    order's unit while the link fits - an orderer's head (``head=True``), a
    staff member's Leader of the same function - else NULL. The SQL twin of
    :func:`~meobot.application.units.directory.manager_fits`."""
    worker = aliased(OrgUnitMember)
    boss = aliased(OrgUnitMember)
    boss_user = aliased(User)
    fits = (
        and_(worker.role == UnitMemberRole.ORDERER, boss.role == UnitMemberRole.HEAD)
        if head
        else and_(worker.is_lead.is_(False), boss.is_lead.is_(True), boss.role == worker.role)
    )
    return (
        select(boss.user_id)
        .select_from(worker)
        .join(boss, and_(boss.unit_id == worker.unit_id, boss.user_id == worker.manager_user_id))
        .join(boss_user, boss_user.id == boss.user_id)
        .where(
            worker.unit_id == Order.unit_id,
            worker.user_id == member,
            worker.left_at.is_(None),
            boss.left_at.is_(None),
            boss_user.active.is_(True),
            fits,
        )
        .limit(1)
        .scalar_subquery()
    )


__all__ = ["NOT_VISIBLE", "OrderScope", "boss_of"]
