"""What one person may see of the Ads orders, as a ``WHERE`` clause.

The scope is built once per request from the person's unit tag and applied
to every query that reads ``orders`` - the board, the detail, and the load
inside every command - so a person cannot act on an order they cannot list.
An order outside the scope does not exist for them: a 404, never a 403.

The rule, from the design (§6.3), narrowed to teams so that one order team
does not read another's ideas:

* the OWNER and an ADMIN see every order of the unit;
* whoever approves orders with ``OWN`` scope (a Trưởng phòng ADS, the head of
  any department that orders) sees the orders that reach them (0061): the ones
  they were chosen to approve, and the ones that default to them - in each of
  the orderer's approving departments, the head the orderer reports to, else
  every head there (:meth:`AdsApprovers.default_approvers`);
* anybody sees the orders of the people who report to them (their team);
* an orderer sees the orders they placed - not their teammates';
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

from meobot.db.models.department import Department, DepartmentMember, DepartmentPermission
from meobot.db.models.order import Order, OrderNode
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.orders.errors import OrderNotFoundError
from meobot.domain.orders.models import OrderCategory, OrderNodeStatus, OrderNodeType
from meobot.domain.orders.permissions import AdsPermission
from meobot.domain.orders.pipeline import OrderActorContext
from meobot.domain.org.models import MODULE_ORDER, Position
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
    #: 0061: approves orders in ``OWN`` scope - the orders routed to them.
    approves: bool = False
    #: 0062: ``BIRTHDAY`` granted ``ALL`` (Thiết Kế by default): every
    #: birthday order is theirs to see.
    birthday_all: bool = False
    #: May use the birthday-order board at all (``BIRTHDAY`` in any scope, or
    #: seeing every order). Without it the board lists nothing - an order
    #: somebody placed before losing the grant is still theirs to open by link.
    birthday_board: bool = True

    @classmethod
    def for_actor(
        cls, membership: UnitMembership, settings: UnitSettings, *, unit_id: uuid.UUID
    ) -> OrderScope:
        """The scope, or :class:`OrderNotFoundError` for somebody outside Ads."""
        if not membership.has(UnitCode.ADS):
            raise OrderNotFoundError(NOT_VISIBLE, details={"reason": "unit_not_visible"})
        context = OrderActorContext.from_membership(membership, settings)
        may = context.permissions
        return cls(
            unit_id=unit_id,
            user_id=membership.user_id,
            sees_all=may.allows(AdsPermission.VIEW),
            # "Own" visibility: the orders that reach a node they manage.
            lead_node_types=may.nodes(AdsPermission.NODE_ASSIGN)
            | may.nodes(AdsPermission.NODE_REVIEW)
            | context.lead_node_types,
            context=context,
            approves=may.holds(AdsPermission.ORDER_APPROVE),
            birthday_all=may.allows(AdsPermission.BIRTHDAY),
            birthday_board=may.holds(AdsPermission.BIRTHDAY) or may.allows(AdsPermission.VIEW),
        )

    def apply(self, statement: Select) -> Select:  # type: ignore[type-arg]
        """Narrow a ``select`` over :class:`Order` to what this person may see."""
        statement = statement.where(Order.unit_id == self.unit_id)
        if self.sees_all:
            return statement
        if self.user_id is None:
            return statement.where(false())
        conditions = [Order.owner_user_id == self.user_id]
        if self.birthday_all:
            conditions.append(Order.category == OrderCategory.BIRTHDAY)
        # Their team: the people who report to them, in any department.
        conditions.append(manages(self.user_id, Order.owner_user_id))
        if self.approves:
            conditions.extend(
                [Order.approver_user_id == self.user_id, approval_reaches(self.user_id)]
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


def manages(manager_id: uuid.UUID, member: Any) -> Any:
    """``member`` reports to ``manager_id`` in some department (0061)."""
    seat = aliased(DepartmentMember)
    return exists(
        select(seat.id).where(
            seat.user_id == member,
            seat.manager_user_id == manager_id,
            seat.left_at.is_(None),
        )
    )


def _chosen_is_active() -> Any:
    """The order's chosen approver is still an active account."""
    chosen = aliased(User)
    return exists(
        select(chosen.id).where(chosen.id == Order.approver_user_id, chosen.active.is_(True))
    )


def approval_reaches(user_id: uuid.UUID) -> Any:
    """SQL twin of :meth:`AdsApprovers.order_approvers` for one person: the
    order waits on ``user_id`` - chosen for it, or (nobody chosen, or chosen
    and gone) one of the orderer's default approvers. An orderer with no seat
    at all is read the old way (their own head, else every approver)."""
    owner_seat = aliased(DepartmentMember)
    department = aliased(Department)
    grant = aliased(DepartmentPermission)
    my_seat = aliased(DepartmentMember)
    boss_seat = aliased(DepartmentMember)
    boss_user = aliased(User)
    any_seat = aliased(DepartmentMember)
    valid_boss = exists(
        select(boss_seat.id)
        .join(boss_user, boss_user.id == boss_seat.user_id)
        .where(
            boss_seat.department_id == owner_seat.department_id,
            boss_seat.user_id == owner_seat.manager_user_id,
            boss_seat.position == Position.HEAD,
            boss_seat.left_at.is_(None),
            boss_user.active.is_(True),
        )
    )
    i_head_it = exists(
        select(my_seat.id).where(
            my_seat.department_id == owner_seat.department_id,
            my_seat.user_id == user_id,
            my_seat.position == Position.HEAD,
            my_seat.left_at.is_(None),
        )
    )
    by_department = exists(
        select(owner_seat.id)
        .join(department, department.id == owner_seat.department_id)
        .join(
            grant,
            and_(
                grant.department_id == owner_seat.department_id,
                grant.position == Position.HEAD,
                grant.module == MODULE_ORDER,
                grant.permission == AdsPermission.ORDER_APPROVE.value,
                grant.scope != "NONE",
            ),
        )
        .where(
            owner_seat.user_id == Order.owner_user_id,
            owner_seat.left_at.is_(None),
            owner_seat.user_id != user_id,
            department.active.is_(True),
            or_(
                and_(owner_seat.manager_user_id == user_id, valid_boss),
                and_(~valid_boss, i_head_it),
            ),
        )
    )
    unseated = ~exists(
        select(any_seat.id).where(
            any_seat.user_id == Order.owner_user_id, any_seat.left_at.is_(None)
        )
    )
    old_head = boss_of(Order.owner_user_id, head=True)
    return and_(
        or_(Order.approver_user_id.is_(None), ~_chosen_is_active()),
        or_(
            by_department,
            and_(unseated, or_(old_head.is_(None), old_head == user_id)),
        ),
    )


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


__all__ = ["NOT_VISIBLE", "OrderScope", "approval_reaches", "boss_of", "manages"]
