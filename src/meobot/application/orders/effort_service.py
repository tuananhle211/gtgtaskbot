"""Token effort per person and day for ORD (0053). Read-only.

Each member has a token budget per working day (their tag's ``daily_tokens``,
else the unit's ``default_daily_tokens``): all of it on ``work_weekdays``,
half on ``half_weekdays`` (Saturday morning), none on the other days.
Approved work takes tokens off the day it was approved
(:mod:`~meobot.application.orders.token_ledger`). The day's balance is
budget - used: positive is effort left, negative is over effort.

"Đang ôm" - what a person holds open: the tokens of the active nodes they are
the assignee of that are not taken yet (the estimate before the first
completion, plus the revision tokens not taken).

Every figure is a grouped query over the whole set of people.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.errors import AuthorizationError, ValidationError
from meobot.core.time import utcnow
from meobot.db.models.order import Order, OrderNode, OrderTokenLedger
from meobot.db.models.org_unit import OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor
from meobot.domain.orders.deadlines import WORK_TIMEZONE, work_day
from meobot.domain.orders.models import (
    ACTIVE_NODE_STATUSES,
    TERMINAL_STAGES,
    OrderNodeType,
    OrderTokenKind,
)
from meobot.domain.units.labels import function_tag, unit_role_label
from meobot.domain.units.models import (
    FUNCTION_ROLES,
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitSettings,
)

#: The longest range the grid answers for.
MAX_DAYS = 62


@dataclass(frozen=True, slots=True)
class DayEffort:
    day: date
    budget: float
    used: float

    @property
    def left(self) -> float:
        return round(self.budget - self.used, 2)


@dataclass(frozen=True, slots=True)
class PersonEffort:
    user_id: uuid.UUID
    full_name: str
    role_label: str
    function_tag: str | None
    is_lead: bool
    daily_tokens: float
    open_tokens: float
    open_tasks: int
    days: tuple[DayEffort, ...]


@dataclass(frozen=True, slots=True)
class EffortGrid:
    date_from: date
    date_to: date
    today: date
    people: tuple[PersonEffort, ...]

    @property
    def days(self) -> tuple[date, ...]:
        span = (self.date_to - self.date_from).days
        return tuple(self.date_from + timedelta(days=offset) for offset in range(span + 1))


@dataclass(frozen=True, slots=True)
class BanMember:
    user_id: uuid.UUID
    full_name: str
    is_lead: bool
    budget: float
    used: float
    #: Today's budget left (the "Giao việc" figure); may be negative.
    today_left: float
    open_tokens: float
    open_tasks: int
    done: int

    @property
    def left(self) -> float:
        return round(self.budget - self.used, 2)


@dataclass(frozen=True, slots=True)
class BanStat:
    """One ban (Biên kịch / Design / Dựng) over a date range."""

    role: UnitMemberRole
    label: str
    members: tuple[BanMember, ...]
    done: int
    in_progress: int
    overdue: int
    late: int

    @property
    def budget(self) -> float:
        return round(sum(member.budget for member in self.members), 2)

    @property
    def used(self) -> float:
        return round(sum(member.used for member in self.members), 2)

    @property
    def left(self) -> float:
        return round(self.budget - self.used, 2)

    @property
    def open_tokens(self) -> float:
        return round(sum(member.open_tokens for member in self.members), 2)

    @property
    def open_tasks(self) -> int:
        return sum(member.open_tasks for member in self.members)


@dataclass(frozen=True, slots=True)
class BanStats:
    date_from: date
    date_to: date
    today: date
    #: The viewer's own ban, the dashboard's default; None = "Tất cả".
    my_ban: UnitMemberRole | None
    bans: tuple[BanStat, ...]


@dataclass(frozen=True, slots=True)
class Load:
    """One person today, for the "Giao việc" picker."""

    budget: float
    used: float
    open_tokens: float
    open_tasks: int

    @property
    def left(self) -> float:
        return round(self.budget - self.used, 2)


def daily_budget(member: OrgUnitMember | None, settings: UnitSettings) -> float:
    """The person's budget on a working day."""
    if member is not None and member.daily_tokens is not None:
        return float(member.daily_tokens)
    return float(settings.default_daily_tokens)


def day_share(day: date, settings: UnitSettings) -> float:
    """How much of a day's budget ``day`` carries: 1 on a working day, 0.5
    on a half day (Saturday morning), 0 otherwise."""
    weekday = day.weekday()
    if weekday in settings.work_weekdays:
        return 1.0
    if weekday in settings.half_weekdays:
        return 0.5
    return 0.0


def working_share(first: date, last: date, settings: UnitSettings) -> float:
    """The working days in ``[first, last]``, a half day counting 0.5."""
    return sum(
        day_share(first + timedelta(days=offset), settings)
        for offset in range((last - first).days + 1)
    )


def budget_on(day: date, daily: float, settings: UnitSettings) -> float:
    return round(daily * day_share(day, settings), 2)


def week_of(day: date) -> tuple[date, date]:
    """Monday to Sunday around ``day``."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


class EffortService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def used(
        self, unit_id: uuid.UUID, user_ids: Iterable[uuid.UUID], date_from: date, date_to: date
    ) -> dict[tuple[uuid.UUID, date], float]:
        ids = list(user_ids)
        if not ids:
            return {}
        rows = await self._session.execute(
            select(
                OrderTokenLedger.user_id,
                OrderTokenLedger.work_date,
                func.sum(OrderTokenLedger.tokens),
            )
            .where(
                OrderTokenLedger.unit_id == unit_id,
                OrderTokenLedger.user_id.in_(ids),
                OrderTokenLedger.work_date >= date_from,
                OrderTokenLedger.work_date <= date_to,
            )
            .group_by(OrderTokenLedger.user_id, OrderTokenLedger.work_date)
        )
        return {(user_id, day): float(total or 0) for user_id, day, total in rows.all()}

    async def holding(
        self, unit_id: uuid.UUID, user_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[float, int]]:
        """``user -> (open tokens, open nodes)``: "Đang ôm"."""
        ids = list(user_ids)
        if not ids:
            return {}
        taken = (
            select(func.coalesce(func.sum(OrderTokenLedger.tokens), 0))
            .where(
                OrderTokenLedger.node_id == OrderNode.id,
                OrderTokenLedger.kind == OrderTokenKind.REVISION,
            )
            .scalar_subquery()
        )
        estimate = case(
            (OrderNode.approved_at.is_(None), func.coalesce(OrderNode.token_estimate, 0)),
            else_=0,
        )
        # max(revision - taken, 0), portable (SQLite has no GREATEST).
        revision = case(
            (OrderNode.token_revision > taken, OrderNode.token_revision - taken), else_=0
        )
        rows = await self._session.execute(
            select(
                OrderNode.assignee_user_id,
                func.sum(estimate + revision),
                func.count(OrderNode.id),
            )
            .join(Order, Order.id == OrderNode.order_id)
            .where(
                Order.unit_id == unit_id,
                Order.stage.notin_(sorted(TERMINAL_STAGES, key=lambda stage: stage.value)),
                OrderNode.assignee_user_id.in_(ids),
                OrderNode.status.in_(sorted(ACTIVE_NODE_STATUSES, key=lambda s: s.value)),
            )
            .group_by(OrderNode.assignee_user_id)
        )
        return {user_id: (float(tokens or 0), int(count)) for user_id, tokens, count in rows.all()}

    async def loads(
        self, unit_id: uuid.UUID, user_ids: Iterable[uuid.UUID], settings: UnitSettings
    ) -> dict[uuid.UUID, Load]:
        """Today's budget, used and held for each person: the assign picker."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        today = work_day(utcnow())
        members = {
            row.user_id: row
            for row in await self._session.scalars(
                select(OrgUnitMember).where(
                    OrgUnitMember.unit_id == unit_id, OrgUnitMember.user_id.in_(ids)
                )
            )
        }
        used = await self.used(unit_id, ids, today, today)
        held = await self.holding(unit_id, ids)
        return {
            user_id: Load(
                budget=budget_on(today, daily_budget(members.get(user_id), settings), settings),
                used=used.get((user_id, today), 0.0),
                open_tokens=held.get(user_id, (0.0, 0))[0],
                open_tasks=held.get(user_id, (0.0, 0))[1],
            )
            for user_id in ids
        }

    async def grid(
        self,
        *,
        actor: Actor,
        membership: UnitMembership,
        unit_id: uuid.UUID,
        settings: UnitSettings,
        date_from: date | None,
        date_to: date | None,
        user_id: uuid.UUID | None,
    ) -> EffortGrid:
        """The effort grid: people by days. The OWNER, an ADMIN and the ORD
        heads see everyone; a ban's Leader their ban; anybody else themselves."""
        today = work_day(utcnow())
        if date_from is None or date_to is None:
            week_from, week_to = week_of(today)
            date_from = date_from or week_from
            date_to = date_to or week_to
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        if (date_to - date_from).days + 1 > MAX_DAYS:
            raise ValidationError(
                f"Chỉ xem được tối đa {MAX_DAYS} ngày một lần.",
                details={"reason": "range_too_long"},
            )
        rows = (
            await self._session.execute(
                select(OrgUnitMember, User)
                .join(User, User.id == OrgUnitMember.user_id)
                .where(
                    OrgUnitMember.unit_id == unit_id,
                    OrgUnitMember.left_at.is_(None),
                    User.active.is_(True),
                    OrgUnitMember.role.in_(sorted(FUNCTION_ROLES, key=lambda role: role.value)),
                )
                .order_by(OrgUnitMember.role, User.full_name)
            )
        ).all()
        visible = _visible(actor, membership, [member for member, _ in rows])
        if user_id is not None:
            if user_id not in visible:
                raise AuthorizationError(
                    "Bạn không xem được effort của người này.",
                    details={"reason": "effort_forbidden"},
                )
            visible = {user_id}
        chosen = [(member, user) for member, user in rows if member.user_id in visible]
        ids = [member.user_id for member, _ in chosen]
        used = await self.used(unit_id, ids, date_from, date_to)
        held = await self.holding(unit_id, ids)
        span = (date_to - date_from).days + 1
        days = [date_from + timedelta(days=offset) for offset in range(span)]
        people = []
        for member, user in chosen:
            daily = daily_budget(member, settings)
            people.append(
                PersonEffort(
                    user_id=user.id,
                    full_name=user.full_name,
                    role_label=unit_role_label(member.role, member.is_lead),
                    function_tag=function_tag(UnitCode.ADS, member.role),
                    is_lead=member.is_lead,
                    daily_tokens=daily,
                    open_tokens=held.get(user.id, (0.0, 0))[0],
                    open_tasks=held.get(user.id, (0.0, 0))[1],
                    days=tuple(
                        DayEffort(
                            day=day,
                            budget=budget_on(day, daily, settings),
                            used=used.get((user.id, day), 0.0),
                        )
                        for day in days
                    ),
                )
            )
        return EffortGrid(date_from=date_from, date_to=date_to, today=today, people=tuple(people))


_BAN_NODES: dict[UnitMemberRole, OrderNodeType] = {
    UnitMemberRole.BIEN_TAP: OrderNodeType.BIEN_TAP,
    UnitMemberRole.THIET_KE: OrderNodeType.THIET_KE,
    UnitMemberRole.DUNG: OrderNodeType.DUNG,
}
#: The bans as the screens name them.
BAN_LABELS: dict[UnitMemberRole, str] = {
    UnitMemberRole.BIEN_TAP: "Biên kịch",
    UnitMemberRole.THIET_KE: "Design",
    UnitMemberRole.DUNG: "Dựng",
}
#: The longest range the ban figures answer for.
MAX_BAN_DAYS = 366


async def ban_stats(
    session: AsyncSession,
    *,
    membership: UnitMembership,
    unit_id: uuid.UUID,
    settings: UnitSettings,
    date_from: date | None,
    date_to: date | None,
) -> BanStats:
    """Each ban's tokens and work over ``[date_from, date_to]`` (Vietnamese
    days; this month by default), with a row per member. Any ORD member may
    read it: it is the team's dashboard."""
    today = work_day(utcnow())
    if date_from is None or date_to is None:
        first = today.replace(day=1)
        date_from = date_from or first
        date_to = date_to or (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days + 1 > MAX_BAN_DAYS:
        raise ValidationError(
            f"Chỉ xem được tối đa {MAX_BAN_DAYS} ngày một lần.",
            details={"reason": "range_too_long"},
        )
    start = datetime.combine(date_from, time.min, tzinfo=WORK_TIMEZONE)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=WORK_TIMEZONE)
    work_days = working_share(date_from, date_to, settings)
    rows = (
        await session.execute(
            select(OrgUnitMember, User)
            .join(User, User.id == OrgUnitMember.user_id)
            .where(
                OrgUnitMember.unit_id == unit_id,
                OrgUnitMember.left_at.is_(None),
                User.active.is_(True),
                OrgUnitMember.role.in_(sorted(FUNCTION_ROLES, key=lambda role: role.value)),
            )
            .order_by(OrgUnitMember.is_lead.desc(), User.full_name)
        )
    ).all()
    ids = [member.user_id for member, _ in rows]
    service = EffortService(session)
    used = await service.used(unit_id, ids, date_from, date_to)
    used_today = await service.used(unit_id, ids, today, today)
    held = await service.holding(unit_id, ids)
    done_by = {
        (user_id, node_type): int(count)
        for user_id, node_type, count in (
            await session.execute(
                select(OrderNode.assignee_user_id, OrderNode.node_type, func.count(OrderNode.id))
                .join(Order, Order.id == OrderNode.order_id)
                .where(
                    Order.unit_id == unit_id,
                    OrderNode.approved_at >= start,
                    OrderNode.approved_at < end,
                )
                .group_by(OrderNode.assignee_user_id, OrderNode.node_type)
            )
        ).all()
    }
    now = utcnow()
    counts = case(
        (
            (OrderNode.revision_count > 0) & OrderNode.revision_deadline_at.is_not(None),
            OrderNode.revision_deadline_at,
        ),
        else_=OrderNode.deadline_at,
    )
    active = OrderNode.status.in_(sorted(ACTIVE_NODE_STATUSES, key=lambda s: s.value))
    per_node = {
        node_type: (int(open_count or 0), int(overdue or 0))
        for node_type, open_count, overdue in (
            await session.execute(
                select(
                    OrderNode.node_type,
                    func.count(OrderNode.id),
                    func.sum(case((counts < now, 1), else_=0)),
                )
                .join(Order, Order.id == OrderNode.order_id)
                .where(
                    Order.unit_id == unit_id,
                    Order.stage.notin_(sorted(TERMINAL_STAGES, key=lambda stage: stage.value)),
                    active,
                )
                .group_by(OrderNode.node_type)
            )
        ).all()
    }
    late_by = {
        node_type: int(count)
        for node_type, count in (
            await session.execute(
                select(OrderNode.node_type, func.count(OrderNode.id))
                .join(Order, Order.id == OrderNode.order_id)
                .where(
                    Order.unit_id == unit_id,
                    OrderNode.approved_at >= start,
                    OrderNode.approved_at < end,
                    OrderNode.deadline_met.is_(False),
                )
                .group_by(OrderNode.node_type)
            )
        ).all()
    }
    bans = []
    for role, node_type in _BAN_NODES.items():
        members = []
        for member, user in rows:
            if member.role is not role:
                continue
            daily = daily_budget(member, settings)
            members.append(
                BanMember(
                    user_id=user.id,
                    full_name=user.full_name,
                    is_lead=member.is_lead,
                    budget=round(daily * work_days, 2),
                    used=used_total(used, user.id),
                    today_left=round(
                        budget_on(today, daily, settings) - used_today.get((user.id, today), 0.0),
                        2,
                    ),
                    open_tokens=held.get(user.id, (0.0, 0))[0],
                    open_tasks=held.get(user.id, (0.0, 0))[1],
                    done=done_by.get((user.id, node_type), 0),
                )
            )
        bans.append(
            BanStat(
                role=role,
                label=BAN_LABELS[role],
                members=tuple(members),
                done=sum(count for (_, kind), count in done_by.items() if kind is node_type),
                in_progress=per_node.get(node_type, (0, 0))[0],
                overdue=per_node.get(node_type, (0, 0))[1],
                late=late_by.get(node_type, 0),
            )
        )
    entry = membership.entry(UnitCode.ADS)
    mine = entry.role if entry is not None and entry.role in FUNCTION_ROLES else None
    return BanStats(
        date_from=date_from, date_to=date_to, today=today, my_ban=mine, bans=tuple(bans)
    )


def used_total(used: dict[tuple[uuid.UUID, date], float], user_id: uuid.UUID) -> float:
    return round(sum(value for (who, _), value in used.items() if who == user_id), 2)


def _visible(
    actor: Actor, membership: UnitMembership, members: list[OrgUnitMember]
) -> set[uuid.UUID]:
    everyone = {member.user_id for member in members}
    if membership.sees_all:
        return everyone
    entry = membership.entry(UnitCode.ADS)
    if entry is not None and entry.role is UnitMemberRole.HEAD:
        return everyone
    own = set() if actor.user_id is None else {actor.user_id} & everyone
    if entry is not None and entry.is_lead:
        return own | {member.user_id for member in members if member.role is entry.role}
    return own


__all__ = [
    "MAX_BAN_DAYS",
    "MAX_DAYS",
    "BanMember",
    "BanStat",
    "BanStats",
    "DayEffort",
    "EffortGrid",
    "EffortService",
    "Load",
    "PersonEffort",
    "ban_stats",
    "budget_on",
    "daily_budget",
    "day_share",
    "week_of",
    "working_share",
]
