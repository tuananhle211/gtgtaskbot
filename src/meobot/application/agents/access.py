"""Who chats with which agent (Đội agent, 0091).

* the OWNER / an ADMIN: every active agent; the OWNER alone manages them;
* ``AGENTS CHAT`` (ticked per person or per department): every active agent;
* anybody else: the active agents whose lists name them - ``everyone``, one
  of their departments (an open seat), or themselves.

``AGENTS MANAGE`` is ``owner_only``: the OWNER by role, or a person the OWNER
ticked it for.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import Access, load_access
from meobot.application.org.seats import load_seats
from meobot.db.models.agent_chat import AgentProfile
from meobot.db.models.user import User
from meobot.domain.agents.errors import AgentsForbiddenError
from meobot.domain.agents.models import MODULE_AGENTS, AgentPermission
from meobot.domain.identity.models import Role
from meobot.domain.org.models import Seat
from meobot.domain.units.models import UnitMembership


@dataclass(frozen=True, slots=True)
class AgentAccess:
    user_id: uuid.UUID | None
    #: Every active agent, whatever their lists say.
    chat_all: bool = False
    #: Edit the agents and their lists.
    manage: bool = False
    department_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def may_chat(self, agent: AgentProfile) -> bool:
        if not agent.active:
            return False
        if self.chat_all or agent.everyone:
            return True
        if self.user_id is not None and str(self.user_id) in {
            str(v) for v in agent.allowed_user_ids
        }:
            return True
        allowed = {str(v) for v in agent.allowed_department_ids}
        return any(str(dept) in allowed for dept in self.department_ids)

    def require_manage(self) -> None:
        if not self.manage:
            raise AgentsForbiddenError()


def agent_access(membership: UnitMembership) -> AgentAccess:
    access = membership.access if isinstance(membership.access, Access) else None
    chat_all = membership.sees_all or bool(
        access and access.allows(MODULE_AGENTS, AgentPermission.CHAT.value)
    )
    manage = membership.is_owner or bool(
        access and access.allows(MODULE_AGENTS, AgentPermission.MANAGE.value)
    )
    return AgentAccess(
        user_id=membership.user_id,
        chat_all=chat_all,
        manage=manage,
        department_ids=frozenset(seat.department_id for seat in membership.seats),
    )


async def agent_access_for(
    session: AsyncSession, user: User, seats: Sequence[Seat] | None = None
) -> AgentAccess:
    """Somebody else's :class:`AgentAccess` (a person invited to a room): the same
    rules as :func:`agent_access`, from their role, seats and personal grants.
    Never ``manage`` (it is not asked here)."""
    if user.deleted_at is not None or not user.active:
        return AgentAccess(user_id=user.id)
    if user.role in (Role.OWNER, Role.ADMIN):
        return AgentAccess(user_id=user.id, chat_all=True)
    if seats is None:
        seats = (await load_seats(session, [user.id])).get(user.id, ())
    access = await load_access(session, user_id=user.id, role=user.role, seats=seats)
    return AgentAccess(
        user_id=user.id,
        chat_all=access.allows(MODULE_AGENTS, AgentPermission.CHAT.value),
        department_ids=frozenset(seat.department_id for seat in seats),
    )


__all__ = ["AgentAccess", "agent_access", "agent_access_for"]
