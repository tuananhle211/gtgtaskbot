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
from dataclasses import dataclass, field

from meobot.application.org.access import Access
from meobot.db.models.agent_chat import AgentProfile
from meobot.domain.agents.errors import AgentsForbiddenError
from meobot.domain.agents.models import MODULE_AGENTS, AgentPermission
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


__all__ = ["AgentAccess", "agent_access"]
