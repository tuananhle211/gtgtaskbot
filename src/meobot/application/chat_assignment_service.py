"""Who is allowed to speak into which registered group.

Before this existed, a Trưởng nhóm's set of addressable groups was derived from
the group's *purpose*: a team lead could broadcast to a "team" group. In
practice that set was always empty, because nothing ever connected a particular
lead to a particular team, and the 0.6.0a1 report said so plainly rather than
pretending otherwise.

The fix is a resource-level assignment, and two properties of it matter more
than the table itself:

**An assignment is not a role.** Making Linh the manager of the Content group
grants authority over that group and nothing else. Her global role is
unchanged, she cannot register new destinations, and she cannot address the
department. Conflating the two - "she manages a group, so promote her" - is how
a permission system stops being auditable.

**Only OWNER assigns.** Not ADMIN, in this release. Deciding who may speak to a
team is an organisational decision, and there is exactly one person in this
deployment whose decision that is.

**The target must already be a Trưởng nhóm.** Assigning an EMPLOYEE as a group
manager is refused rather than silently promoting them: a permission grant that
quietly widens somebody's role is precisely the failure this design exists to
prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.errors import AuthorizationError, ConflictError, ValidationError
from meobot.core.logging import get_logger
from meobot.db.models.notifications import TelegramChat, TelegramChatAssignment
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.labels import role_label
from meobot.domain.identity.models import Actor, Role
from meobot.domain.notifications.models import AssignmentRole

logger = get_logger(__name__)

ONLY_OWNER_ASSIGNS = f"Chỉ {role_label(Role.OWNER)} được giao quyền quản lý group."
TARGET_MUST_BE_TEAM_LEAD = (
    f"MeoBot chỉ giao quyền quản lý group cho người đã là {role_label(Role.TEAM_LEAD)}.\n"
    f"Bạn đổi vai trò cho người này trước, rồi giao lại quyền group nhé."
)
TARGET_NOT_ACTIVE = "Tài khoản này hiện không sử dụng được MeoBot."
NO_SUCH_ASSIGNMENT = "Người này hiện không quản lý group đó."


class ChatAssignmentService:
    """Creates, revokes and reads group assignments.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Writing ----------------------------------------------------------
    async def assign_manager(
        self,
        *,
        actor: Actor,
        chat: TelegramChat,
        target: User,
        can_broadcast: bool = True,
        can_view_read_receipts: bool = True,
        can_manage_audience: bool = False,
    ) -> TelegramChatAssignment:
        """Make ``target`` the manager of ``chat``.

        Idempotent: re-assigning somebody who already manages the group updates
        the row and bumps its version rather than creating a second one.

        Raises:
            AuthorizationError: The actor is not the owner.
            ValidationError: The target is not an active Trưởng nhóm.
        """
        self._require_owner(actor)
        self._require_team_lead(target)

        existing = await self.assignment_for(chat_row_id=chat.id, user_id=target.id)
        if existing is not None:
            existing.assignment_role = AssignmentRole.MANAGER
            existing.can_broadcast = can_broadcast
            existing.can_view_read_receipts = can_view_read_receipts
            existing.can_manage_audience = can_manage_audience
            existing.is_active = True
            existing.assigned_by_user_id = actor.user_id
            existing.version += 1
            await self._session.flush()
            return existing

        row = TelegramChatAssignment(
            telegram_chat_row_id=chat.id,
            user_id=target.id,
            assignment_role=AssignmentRole.MANAGER,
            can_broadcast=can_broadcast,
            can_view_read_receipts=can_view_read_receipts,
            can_manage_audience=can_manage_audience,
            is_active=True,
            assigned_by_user_id=actor.user_id,
            version=1,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "chat_manager_assigned",
            extra={"chat_row_id": str(chat.id), "user_id": str(target.id)},
        )
        return row

    async def add_member(
        self, *, actor: Actor, chat: TelegramChat, target: User
    ) -> TelegramChatAssignment:
        """Record that ``target`` belongs to ``chat``'s expected audience.

        Distinct from managing it: a member is counted in "ai chưa đọc" and may
        not broadcast.
        """
        self._require_owner(actor)
        existing = await self.assignment_for(chat_row_id=chat.id, user_id=target.id)
        if existing is not None:
            existing.is_active = True
            existing.version += 1
            await self._session.flush()
            return existing

        row = TelegramChatAssignment(
            telegram_chat_row_id=chat.id,
            user_id=target.id,
            assignment_role=AssignmentRole.MEMBER,
            can_broadcast=False,
            can_view_read_receipts=False,
            can_manage_audience=False,
            is_active=True,
            assigned_by_user_id=actor.user_id,
            version=1,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def revoke(
        self, *, actor: Actor, chat_row_id: uuid.UUID, user_id: uuid.UUID
    ) -> TelegramChatAssignment:
        """Withdraw an assignment. The row is kept, deactivated.

        Revocation takes effect immediately for anything *future*; announcements
        already published and their audit trail are untouched, because what
        somebody was allowed to do last week is a fact and not a setting.

        Raises:
            AuthorizationError: The actor is not the owner.
            ConflictError: There is no such assignment.
        """
        self._require_owner(actor)
        row = await self.assignment_for(chat_row_id=chat_row_id, user_id=user_id)
        if row is None or not row.is_active:
            raise ConflictError(NO_SUCH_ASSIGNMENT)
        row.is_active = False
        row.can_broadcast = False
        row.can_view_read_receipts = False
        row.can_manage_audience = False
        row.version += 1
        await self._session.flush()
        logger.info(
            "chat_manager_revoked",
            extra={"chat_row_id": str(chat_row_id), "user_id": str(user_id)},
        )
        return row

    # --- Reading ----------------------------------------------------------
    async def assignment_for(
        self, *, chat_row_id: uuid.UUID, user_id: uuid.UUID
    ) -> TelegramChatAssignment | None:
        """One person's assignment to one group, active or not."""
        result = await self._session.execute(
            select(TelegramChatAssignment).where(
                TelegramChatAssignment.telegram_chat_row_id == chat_row_id,
                TelegramChatAssignment.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def may_broadcast_to(self, *, user_id: uuid.UUID | None, chat: TelegramChat) -> bool:
        """Whether this person may send into this group.

        The only question the announcement and reminder paths ask. It is
        deliberately about *this* group rather than about a class of groups: a
        team lead who manages Content has no authority over Seeding, and
        purpose-based reasoning would give them both.
        """
        if user_id is None:
            return False
        row = await self.assignment_for(chat_row_id=chat.id, user_id=user_id)
        return row is not None and row.is_active and row.can_broadcast

    async def managed_chats(self, *, user_id: uuid.UUID) -> Sequence[TelegramChat]:
        """Every active group this person may broadcast to."""
        result = await self._session.execute(
            select(TelegramChat)
            .join(
                TelegramChatAssignment,
                TelegramChatAssignment.telegram_chat_row_id == TelegramChat.id,
            )
            .where(
                TelegramChatAssignment.user_id == user_id,
                TelegramChatAssignment.is_active.is_(True),
                TelegramChatAssignment.can_broadcast.is_(True),
                TelegramChat.is_active.is_(True),
            )
            .order_by(TelegramChat.display_name.asc())
        )
        return result.scalars().all()

    async def managers_of(self, *, chat_row_id: uuid.UUID) -> Sequence[User]:
        """Everybody currently managing one group."""
        result = await self._session.execute(
            select(User)
            .join(TelegramChatAssignment, TelegramChatAssignment.user_id == User.id)
            .where(
                TelegramChatAssignment.telegram_chat_row_id == chat_row_id,
                TelegramChatAssignment.is_active.is_(True),
                TelegramChatAssignment.assignment_role == AssignmentRole.MANAGER,
            )
            .order_by(User.full_name.asc())
        )
        return result.scalars().all()

    async def audience_of(self, *, chat_row_id: uuid.UUID) -> Sequence[User]:
        """Every active person assigned to one group, manager or member.

        This is the denominator for "ai chưa đọc" on a team announcement.
        Telegram will not enumerate a group's members for a bot, so where no
        assignment exists MeoBot says it cannot tell rather than guessing.
        """
        result = await self._session.execute(
            select(User)
            .join(TelegramChatAssignment, TelegramChatAssignment.user_id == User.id)
            .where(
                TelegramChatAssignment.telegram_chat_row_id == chat_row_id,
                TelegramChatAssignment.is_active.is_(True),
                User.status == UserStatus.ACTIVE,
            )
            .order_by(User.full_name.asc())
        )
        return result.scalars().all()

    # --- Guards -----------------------------------------------------------
    @staticmethod
    def _require_owner(actor: Actor) -> None:
        if actor.role is not Role.OWNER:
            raise AuthorizationError(ONLY_OWNER_ASSIGNS)

    @staticmethod
    def _require_team_lead(target: User) -> None:
        """The target must already hold the role. Assignment never grants one."""
        if target.status is not UserStatus.ACTIVE or not target.active:
            raise ValidationError(TARGET_NOT_ACTIVE)
        if target.role is not Role.TEAM_LEAD:
            raise ValidationError(TARGET_MUST_BE_TEAM_LEAD)
