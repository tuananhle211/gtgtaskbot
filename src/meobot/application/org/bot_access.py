"""What the Telegram bot learns about a person from the unified layers (0067).

The bot's gates were written for the role matrix: the whole bot for the
OWNER, a tool for whoever's role holds its permission. The bot's tools serve
the PR team, so the head of PR Thương Hiệu gets the whole bot, and every
tool whose permission a PR capability is built on opens for whoever holds
that capability through their seat or a personal grant. The system half -
``user.manage`` and its kin - never comes this way.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.org.access import load_access
from meobot.domain.identity.models import Actor
from meobot.domain.org.models import MODULE_PR, DepartmentFunction
from meobot.domain.permissions.matrix import Permission
from meobot.domain.pr.policy import PrCapability, baseline_permission

#: Permissions a PR capability is built on but that stay with the system
#: roles in the bot: deciding who is in the system is not a PR right.
SYSTEM_PERMISSIONS: frozenset[Permission] = frozenset(
    {Permission.USER_MANAGE, Permission.USER_ROLE_MANAGE, Permission.USER_STATUS_MANAGE}
)


async def with_unified_access(session: AsyncSession, actor: Actor) -> Actor:
    """The actor with ``full_bot`` and ``extra_permissions`` filled in."""
    if actor.user_id is None:
        return actor
    access = await load_access(session, user_id=actor.user_id, role=actor.role)
    extra = {
        baseline_permission(capability).value
        for capability in PrCapability
        if access.allows(MODULE_PR, capability.value)
        and baseline_permission(capability) not in SYSTEM_PERMISSIONS
    }
    return actor.model_copy(
        update={
            # The OWNER, and the PR head. An ADMIN keeps the basic bot, as before.
            "full_bot": access.is_owner or access.heads(DepartmentFunction.PR),
            "extra_permissions": frozenset(extra),
        }
    )


__all__ = ["SYSTEM_PERMISSIONS", "with_unified_access"]
