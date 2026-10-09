"""Who holds a task right now - always named members, or "Chờ giao".

The task table and the task page both answer "Đang giữ". A role is never an
answer ("Trưởng phòng", "Leader Biên tập", "Chờ duyệt"): the person behind the
role is looked up, and when nobody fills it - no head tagged, no Leader for the
function, no assignee, no reviewer granted for this item - the answer is
:data:`AWAITING_ASSIGNMENT`, so missing data shows instead of hiding behind a
title.

When several people may act on a step (two heads, two Leaders of a function,
every reviewer whose grant reaches a PR item) they are **all** named:
"Chờ Hùng, Trần Minh Trang duyệt order". The task belongs to each of them until
one acts.

Read-only. PR's rules are not re-decided here: a PR reviewer is the first
active person whose grant for the gate covers the item, exactly the test the
approval write runs (:func:`~meobot.domain.pr.policy.grant_admits`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.units.directory import UnitDirectoryService, UnitMemberRow
from meobot.db.models.org_unit import OrgUnit
from meobot.db.models.pr import PrContentItem, PrContentTarget
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.models import PRODUCTION_NODES, OrderNodeType, OrderVideoType
from meobot.domain.orders.permissions import AdsPermission, AdsPermissions
from meobot.domain.orders.pipeline import ROLE_NODES, OrderActorContext, function_node
from meobot.domain.pr.grants import ContentScopeKey
from meobot.domain.pr.policy import APPROVAL_CAPABILITIES, PrCapability, grant_admits
from meobot.domain.pr.workflow import STAGE_APPROVAL_GATES
from meobot.domain.units.models import (
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitMembershipEntry,
    UnitSettings,
)

#: What "Đang giữ" says when the step needs somebody and nobody is set.
AWAITING_ASSIGNMENT = "Chờ giao"


@dataclass(frozen=True, slots=True)
class Person:
    user_id: uuid.UUID
    name: str


#: Everybody who may act on one step, the preferred ones first.
People = tuple[Person, ...]

#: How many names a label spells out before "+N".
_NAMES_SHOWN = 3


def format_names(people: People) -> str:
    """``"Hùng, Trần Minh Trang"``; past three, ``"A, B, C +2"``."""
    shown = ", ".join(person.name for person in people[:_NAMES_SHOWN])
    rest = len(people) - _NAMES_SHOWN
    return f"{shown} +{rest}" if rest > 0 else shown


def _as_people(who: Person | People | None) -> People:
    if who is None:
        return ()
    if isinstance(who, Person):
        return (who,)
    return tuple(who)


@dataclass(frozen=True, slots=True)
class Holder:
    """One task's holders. ``people`` is empty only with ``awaiting`` or when
    nobody holds it (done, cancelled)."""

    people: People = ()
    awaiting: bool = False

    @property
    def person(self) -> Person | None:
        return self.people[0] if self.people else None

    @property
    def name(self) -> str | None:
        if self.people:
            return format_names(self.people)
        return AWAITING_ASSIGNMENT if self.awaiting else None

    @property
    def user_id(self) -> uuid.UUID | None:
        return None if not self.people else self.people[0].user_id

    @property
    def user_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(person.user_id for person in self.people)


#: A finished or cancelled task: nobody holds it.
NOBODY = Holder()
#: A step that needs somebody and has nobody.
UNASSIGNED = Holder((), awaiting=True)


def held_by(who: Person | People | None) -> Holder:
    people = _as_people(who)
    return Holder(people) if people else UNASSIGNED


def waiting_for(who: Person | People | None, what: str = "duyệt") -> str:
    """``"Chờ Hùng, Trần Minh Trang duyệt"``, or ``"Chờ giao"`` with nobody."""
    people = _as_people(who)
    return f"Chờ {format_names(people)} {what}" if people else AWAITING_ASSIGNMENT


# --- Ads ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdsApprovers:
    """Who decides each Ads gate: every member the matrix lets decide it,
    the role that normally does (head, the function's Leaders) first.

    The final review is the orderer's and is not here: ``final`` lists the
    stand-ins (holders of ``FINAL_REVIEW``), who may decide but are never
    named as the holder."""

    head: People = ()
    final: People = ()
    video: People = ()
    leads: dict[OrderNodeType, People] = field(default_factory=dict)
    #: Who may hand a production node out. The first is who a node is routed
    #: to when it comes up with nobody chosen (the assignee column holds one
    #: person). A legacy link node is handed out by the last node's function.
    assigners: dict[OrderNodeType, People] = field(default_factory=dict)

    def lead(self, node_type: OrderNodeType) -> People:
        return self.leads.get(node_type, ())

    def _function(
        self, node_type: OrderNodeType, video_type: OrderVideoType | None
    ) -> OrderNodeType:
        if video_type is None:
            return node_type
        return function_node(node_type, video_type)

    def assigner(
        self, node_type: OrderNodeType, video_type: OrderVideoType | None = None
    ) -> Person | None:
        found = self.assigners_of(node_type, video_type)
        return found[0] if found else None

    def assigners_of(
        self, node_type: OrderNodeType, video_type: OrderVideoType | None = None
    ) -> People:
        """Who may hand the node out; for a legacy link node, on an order of
        ``video_type`` (whose last production node's function owns it)."""
        return self.assigners.get(self._function(node_type, video_type), ())


async def ads_approvers(session: AsyncSession, unit_id: uuid.UUID) -> AdsApprovers:
    """The people each gate waits for: everyone the permission matrix lets
    decide it. When the role that normally decides is filled (the function's
    Leaders for a node, the heads for the order and final gates) only those are
    named; otherwise everybody else holding the permission (an Admin). Nobody
    holding it means "Chờ giao"."""
    directory = UnitDirectoryService(session)
    unit = await session.get(OrgUnit, unit_id)
    settings = UnitSettings.model_validate((unit.settings if unit else None) or {})
    rows = await directory.members(unit_id)
    people: list[tuple[UnitMemberRow, AdsPermissions]] = []
    for row in rows:
        membership = UnitMembership(
            user_id=row.user.id,
            entries=(
                UnitMembershipEntry(
                    unit_id=unit_id,
                    unit_code=UnitCode.ADS,
                    role=row.membership.role,
                    is_lead=row.membership.is_lead,
                    member_code=row.membership.member_code,
                ),
            ),
            is_owner=False,
            is_admin=row.user.role is Role.ADMIN,
        )
        people.append((row, OrderActorContext.from_membership(membership, settings).permissions))

    def pick(
        allowed: Callable[[AdsPermissions], bool], *preferred: Callable[[UnitMemberRow], bool]
    ) -> People:
        able = [row for row, may in people if allowed(may)]
        chosen = able
        for prefer in preferred:
            matches = [row for row in able if prefer(row)]
            if matches:
                chosen = matches
                break
        return tuple(Person(row.user.id, row.user.full_name) for row in chosen)

    def is_head(row: UnitMemberRow) -> bool:
        return row.membership.role is UnitMemberRole.HEAD

    def leads_function(node_type: OrderNodeType) -> Callable[[UnitMemberRow], bool]:
        def check(row: UnitMemberRow) -> bool:
            return row.membership.is_lead and ROLE_NODES.get(row.membership.role) is node_type

        return check

    leads: dict[OrderNodeType, People] = {}

    def reviews(node_type: OrderNodeType) -> Callable[[AdsPermissions], bool]:
        def check(may: AdsPermissions) -> bool:
            return node_type in may.nodes(AdsPermission.NODE_REVIEW)

        return check

    for node_type in (OrderNodeType.BIEN_TAP, OrderNodeType.THIET_KE, OrderNodeType.DUNG):
        found = pick(reviews(node_type), leads_function(node_type))
        if found:
            leads[node_type] = found

    def assigns(node_type: OrderNodeType) -> Callable[[AdsPermissions], bool]:
        def check(may: AdsPermissions) -> bool:
            return node_type in may.nodes(AdsPermission.NODE_ASSIGN)

        return check

    assigners: dict[OrderNodeType, People] = {}
    for node_type in PRODUCTION_NODES:
        found = pick(assigns(node_type), leads_function(node_type), is_head)
        if found:
            assigners[node_type] = found
    return AdsApprovers(
        assigners=assigners,
        head=pick(lambda may: may.allows(AdsPermission.ORDER_APPROVE), is_head),
        final=pick(lambda may: may.allows(AdsPermission.FINAL_REVIEW), is_head),
        video=pick(
            lambda may: OrderNodeType.BIEN_TAP in may.nodes(AdsPermission.VIDEO_REVIEW),
            leads_function(OrderNodeType.BIEN_TAP),
        ),
        leads=leads,
    )


# --- PR ----------------------------------------------------------------------------


async def pr_reviewers(
    session: AsyncSession, items: Iterable[PrContentItem]
) -> dict[uuid.UUID, People]:
    """Every reviewer each item at a review gate is waiting for.

    Keyed by item id, for items at a gate only; empty = nobody holds a grant
    that reaches it. Grants are read once per gate, targets once for the page.
    """
    at_gate = [item for item in items if item.workflow_stage in STAGE_APPROVAL_GATES]
    if not at_gate:
        return {}
    capabilities = PrCapabilityService(session, AuditService(session))
    grants = {
        stage: await capabilities.users_with(APPROVAL_CAPABILITIES[gate])
        for stage, gate in STAGE_APPROVAL_GATES.items()
        if any(item.workflow_stage is stage for item in at_gate)
    }
    user_ids = {grant.user_id for listed in grants.values() for grant in listed}
    users = {
        user.id: user
        for user in (
            await session.scalars(
                select(User).where(User.id.in_(list(user_ids)), User.active.is_(True))
            )
        ).all()
    }
    targets: dict[uuid.UUID, set[uuid.UUID]] = {item.id: set() for item in at_gate}
    for content_id, channel_id in (
        await session.execute(
            select(PrContentTarget.content_id, PrContentTarget.channel_id).where(
                PrContentTarget.content_id.in_(list(targets))
            )
        )
    ).all():
        targets[content_id].add(channel_id)
    found: dict[uuid.UUID, People] = {}
    for item in at_gate:
        key = ContentScopeKey(
            content_type=item.content_type, channel_ids=frozenset(targets[item.id])
        )
        admitted: list[Person] = []
        seen: set[uuid.UUID] = set()
        for grant in grants.get(item.workflow_stage, ()):
            user = users.get(grant.user_id)
            if user is None or user.id in seen:
                continue
            actor = Actor(user_id=user.id, full_name=user.full_name, role=user.role)
            if grant_admits(
                actor,
                grant.capability,
                requires_role_baseline=grant.requires_role_baseline,
                scope=grant.scope,
                key=key,
            ):
                seen.add(user.id)
                admitted.append(Person(user.id, user.full_name))
        found[item.id] = tuple(admitted)
    return found


async def pr_production_assigners(session: AsyncSession) -> People:
    """Everybody in PR who may hand an approved piece to a producer.

    Asked of the capability service itself, person by person, so the answer
    is exactly who the assign write would accept. Users outside PR (Ads only)
    are left out even when their base role would allow it.
    """
    capabilities = PrCapabilityService(session, AuditService(session))
    directory = UnitDirectoryService(session)
    users = (
        await session.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name))
    ).all()
    able: list[Person] = []
    for user in users:
        actor = Actor(user_id=user.id, full_name=user.full_name, role=user.role)
        if not (await directory.membership_for(actor)).has(UnitCode.PR):
            continue
        if await capabilities.allows(actor, PrCapability.PR_PRODUCTION_ASSIGN):
            able.append(Person(user.id, user.full_name))
    return tuple(able)


__all__ = [
    "AWAITING_ASSIGNMENT",
    "NOBODY",
    "UNASSIGNED",
    "AdsApprovers",
    "Holder",
    "People",
    "Person",
    "ads_approvers",
    "format_names",
    "held_by",
    "pr_production_assigners",
    "pr_reviewers",
    "waiting_for",
]
