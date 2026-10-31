"""Who may chat with the report assistant (``ASSISTANT CHAT``).

The OWNER by role; anybody else only by a personal grant, which the OWNER
alone gives (``ModuleSpec.owner_only``) - an ADMIN neither holds it by role
nor grants it. The bot reads the same answer through
:func:`~meobot.application.org.bot_access.with_unified_access`.
"""

from __future__ import annotations

from meobot.application.org.access import Access
from meobot.domain.assistant.reports import MODULE_ASSISTANT, AssistantPermission
from meobot.domain.units.models import UnitMembership


def may_chat_assistant(membership: UnitMembership) -> bool:
    if membership.is_owner:
        return True
    access = membership.access
    return isinstance(access, Access) and access.allows(
        MODULE_ASSISTANT, AssistantPermission.CHAT.value
    )


__all__ = ["may_chat_assistant"]
