"""Who belongs to which unit - read once per request, shared by gate and route.

:class:`UnitDirectoryService` answers one question many times: *which units
may this actor see, and as what?* The API gate, the order scope, the board
and the admin page all ask it, so the answer is a value object
(:class:`~meobot.domain.units.models.UnitMembership`) built from the
``org_unit_members`` rows and nothing else.

The legacy rule
---------------

A user with **no** membership row at all is a PR ``MEMBER``. Migration
``0042`` tags every account that existed before units did, so in production
the rule only ever applies to somebody invited afterwards and not yet tagged -
who gets exactly what they got before units existed. It is also what keeps
every test world that builds a ``User`` without a tag green behind the PR
gate: the rule is a product decision ("untagged means PR"), and the tests
exercise it rather than bypass it.

A user whose rows have **all** been closed (``left_at`` set) belongs to no
unit. That is a decision somebody made, and it is honoured.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.org_unit import OrgUnit, OrgUnitMember
from meobot.db.models.user import User
from meobot.domain.identity.models import Actor, Role
from meobot.domain.orders.models import OrderNodeType
from meobot.domain.units.errors import UnitNotFoundError
from meobot.domain.units.models import (
    UnitCode,
    UnitMemberRole,
    UnitMembership,
    UnitMembershipEntry,
    UnitSettings,
    unit_seed_id,
)

#: The one sentence a person outside a unit reads, whatever they asked for.
#: Same wording as the PR routes' "not visible to you": an outsider learns
#: neither that the unit exists nor that it has data.
NOT_VISIBLE = "Không tìm thấy."

#: Which node a function role serves. The role and the node share a name on
#: purpose; this is the single place that says so.
ROLE_FOR_NODE: dict[OrderNodeType, UnitMemberRole] = {
    OrderNodeType.BIEN_TAP: UnitMemberRole.BIEN_TAP,
    OrderNodeType.THIET_KE: UnitMemberRole.THIET_KE,
    OrderNodeType.DUNG: UnitMemberRole.DUNG,
    # The link is attached by an editor; the unit setting may hand it to the
    # script lead instead, and the command service reads that setting.
    OrderNodeType.GAN_LINK: UnitMemberRole.DUNG,
}


@dataclass(frozen=True, slots=True)
class UnitMemberRow:
    """One member as the admin page and the assignee pickers list them."""

    membership: OrgUnitMember
    user: User


class UnitDirectoryService:
    """Reads of units and membership. Writes live in :mod:`.admin`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- membership ---------------------------------------------------------

    async def membership_for(self, actor: Actor) -> UnitMembership:
        """Everything the gate and the scopes need to know about ``actor``."""
        is_owner = actor.role is Role.OWNER
        is_admin = actor.role is Role.ADMIN
        if actor.user_id is None:
            # The bootstrap owner and the worker's system actor have no row.
            return UnitMembership(user_id=None, entries=(), is_owner=is_owner)
        rows = (
            await self._session.execute(
                select(OrgUnitMember, OrgUnit.code)
                .join(OrgUnit, OrgUnit.id == OrgUnitMember.unit_id)
                .where(OrgUnitMember.user_id == actor.user_id)
                .order_by(OrgUnit.code)
            )
        ).all()
        if not rows:
            return UnitMembership(
                user_id=actor.user_id,
                entries=(self._legacy_pr_entry(),),
                is_owner=is_owner,
                is_admin=is_admin,
            )
        entries = tuple(
            UnitMembershipEntry(
                unit_id=member.unit_id,
                unit_code=UnitCode(code),
                role=member.role,
                is_lead=member.is_lead,
                member_code=member.member_code,
                personal_nas_url=member.personal_nas_url,
            )
            for member, code in rows
            if member.left_at is None
        )
        return UnitMembership(
            user_id=actor.user_id, entries=entries, is_owner=is_owner, is_admin=is_admin
        )

    @staticmethod
    def _legacy_pr_entry() -> UnitMembershipEntry:
        return UnitMembershipEntry(
            unit_id=unit_seed_id(UnitCode.PR), unit_code=UnitCode.PR, role=UnitMemberRole.MEMBER
        )

    async def require(self, actor: Actor, code: UnitCode) -> UnitMembership:
        """The membership, or :class:`UnitNotFoundError` when the actor is outside.

        A 404 rather than a 403 on purpose: the person outside a unit is told
        nothing about what is behind the wall.
        """
        membership = await self.membership_for(actor)
        if not membership.has(code):
            raise UnitNotFoundError(NOT_VISIBLE, details={"reason": "unit_not_visible"})
        return membership

    # --- units --------------------------------------------------------------

    async def unit(self, code: UnitCode) -> OrgUnit:
        row = await self._session.scalar(select(OrgUnit).where(OrgUnit.code == code))
        if row is None:
            raise UnitNotFoundError(NOT_VISIBLE, details={"reason": "unit_missing"})
        return row

    async def units(self) -> Sequence[OrgUnit]:
        return (await self._session.scalars(select(OrgUnit).order_by(OrgUnit.code))).all()

    async def settings(self, code: UnitCode) -> UnitSettings:
        """The unit's knobs, validated so an old row keeps working."""
        unit = await self.unit(code)
        return UnitSettings.model_validate(unit.settings or {})

    # --- members ------------------------------------------------------------

    async def members(
        self,
        unit_id: uuid.UUID,
        *,
        active_only: bool = True,
        role: UnitMemberRole | None = None,
        lead_only: bool = False,
    ) -> list[UnitMemberRow]:
        """Members of one unit, with their user rows, in name order."""
        query = (
            select(OrgUnitMember, User)
            .join(User, User.id == OrgUnitMember.user_id)
            .where(OrgUnitMember.unit_id == unit_id)
            .order_by(User.full_name, User.id)
        )
        if active_only:
            query = query.where(OrgUnitMember.left_at.is_(None), User.active.is_(True))
        if role is not None:
            query = query.where(OrgUnitMember.role == role)
        if lead_only:
            query = query.where(OrgUnitMember.is_lead.is_(True))
        rows = (await self._session.execute(query)).all()
        return [UnitMemberRow(membership=member, user=user) for member, user in rows]

    async def heads(self, unit_id: uuid.UUID) -> list[UnitMemberRow]:
        return await self.members(unit_id, role=UnitMemberRole.HEAD)

    async def leads(self, unit_id: uuid.UUID, node_type: OrderNodeType) -> list[UnitMemberRow]:
        return await self.members(unit_id, role=ROLE_FOR_NODE[node_type], lead_only=True)

    async def function_members(
        self, unit_id: uuid.UUID, node_type: OrderNodeType
    ) -> list[UnitMemberRow]:
        return await self.members(unit_id, role=ROLE_FOR_NODE[node_type])

    async def member(self, unit_id: uuid.UUID, user_id: uuid.UUID) -> OrgUnitMember | None:
        """The tag row for one person in one unit, open or closed."""
        row: OrgUnitMember | None = await self._session.scalar(
            select(OrgUnitMember).where(
                OrgUnitMember.unit_id == unit_id, OrgUnitMember.user_id == user_id
            )
        )
        return row


__all__ = ["NOT_VISIBLE", "ROLE_FOR_NODE", "UnitDirectoryService", "UnitMemberRow"]
