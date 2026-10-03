"""Resolving a Telegram account into an :class:`Actor`.

Bootstrap rule: the Telegram id in ``MEOBOT_OWNER_TELEGRAM_ID`` is always
treated as ``OWNER``, even before a ``users`` row exists. Everyone else must be
registered in the database; unknown accounts get no capabilities at all.

That rule was only half of what was needed. Until 0.6.0a2.1 the bootstrap owner
was resolved as an ``Actor`` with ``user_id=None`` and nothing ever wrote the
row, so ``/start`` reported "vai trò Trưởng phòng" while reminders, HR and
announcements - all of which need something to *own* their records - saw
nobody. :meth:`IdentityService.ensure_bootstrap_owner` closes that gap in one
place, so no module has to carry its own owner special case.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.user import User
from meobot.domain.access.models import UserStatus
from meobot.domain.identity.models import Actor, Role

logger = get_logger(__name__)


class IdentityService:
    """Looks up who is talking to MeoBot.

    Args:
        session: Session used for the lookup.
        settings: Supplies the bootstrap owner Telegram id.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def resolve_actor(
        self,
        telegram_user_id: int,
        *,
        telegram_username: str | None = None,
        full_name: str | None = None,
    ) -> Actor | None:
        """Return the actor for a Telegram id, or ``None`` if unregistered.

        A registered user always wins over the bootstrap rule, so promoting the
        owner into the ``users`` table later changes nothing for them.
        """
        user = await self.get_user_by_telegram_id(telegram_user_id)
        if user is not None:
            return Actor(
                user_id=user.id,
                telegram_user_id=user.telegram_user_id,
                telegram_username=user.telegram_username or telegram_username,
                full_name=user.full_name,
                role=user.role,
                active=user.active,
            )

        owner_id = self._settings.meobot_owner_telegram_id
        if owner_id is not None and telegram_user_id == owner_id:
            logger.info("bootstrap_owner_resolved", extra={"telegram_user_id": telegram_user_id})
            return Actor(
                user_id=None,
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
                full_name=full_name or "Owner",
                role=Role.OWNER,
                active=True,
                is_bootstrap_owner=True,
            )

        logger.info("unregistered_telegram_user", extra={"telegram_user_id": telegram_user_id})
        return None

    async def ensure_bootstrap_owner(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None = None,
        full_name: str | None = None,
        private_chat_id: int | None = None,
    ) -> User | None:
        """Turn the configured owner into a durable ``users`` row. Idempotent.

        The bug this exists to fix: :meth:`resolve_actor` happily returned an
        OWNER ``Actor`` with ``user_id=None``, so ``/start`` said "vai trò
        Trưởng phòng" while every service that needs to *own* something -
        reminders, HR requests, announcements - saw nobody at all. Each of them
        then produced its own confusing refusal.

        The fix belongs here rather than in each module. Authority comes from
        configuration; making that authority real is one operation, and every
        caller downstream gets the same identity.

        Args:
            telegram_user_id: Who is talking. Anything other than the
                configured owner is ignored and returns ``None`` - this method
                is the only path that creates an OWNER, and it will not create
                one for anybody else.
            private_chat_id: Set only from a private chat. Telegram will not
                let a bot open a conversation, so a private chat id is a fact
                that can only be learned when somebody uses one.

        Returns:
            The owner's row, or ``None`` when this account is not the
            configured owner.
        """
        configured = self._settings.meobot_owner_telegram_id
        if configured is None or telegram_user_id != configured:
            return None

        existing = await self.get_user_by_telegram_id(telegram_user_id)
        if existing is not None:
            self._repair_owner(
                existing,
                telegram_username=telegram_username,
                full_name=full_name,
                private_chat_id=private_chat_id,
            )
            await self._session.flush()
            return existing

        row = User(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            full_name=(full_name or "").strip() or "Trưởng phòng",
            role=Role.OWNER,
            active=True,
            status=UserStatus.ACTIVE,
        )
        self._repair_owner(
            row,
            telegram_username=telegram_username,
            full_name=full_name,
            private_chat_id=private_chat_id,
        )
        try:
            # A savepoint, not the whole transaction: this runs inside whatever
            # unit of work the caller is already using, and losing a race to a
            # concurrent /start must not discard their work.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # ``users.telegram_user_id`` is unique, which is what makes two
            # simultaneous /start presses produce one owner rather than two.
            duplicate = await self.get_user_by_telegram_id(telegram_user_id)
            if duplicate is None:  # pragma: no cover - only a real DB fault
                raise
            self._repair_owner(
                duplicate,
                telegram_username=telegram_username,
                full_name=full_name,
                private_chat_id=private_chat_id,
            )
            await self._session.flush()
            return duplicate

        logger.info("bootstrap_owner_materialized", extra={"user_id": str(row.id)})
        return row

    @staticmethod
    def _repair_owner(
        row: User,
        *,
        telegram_username: str | None,
        full_name: str | None,
        private_chat_id: int | None,
    ) -> None:
        """Bring an owner row up to what configuration says it must be.

        Deliberately additive. A blank incoming name never overwrites a real
        stored one - ``/start`` from a client that reports no name must not
        erase "Phương Nhung" - and the role is only ever set *to* OWNER, never
        away from it.
        """
        row.role = Role.OWNER
        row.status = UserStatus.ACTIVE
        row.active = True
        if telegram_username:
            row.telegram_username = telegram_username
        if (full_name or "").strip():
            row.full_name = full_name.strip()  # type: ignore[union-attr]
        if private_chat_id is not None:
            row.telegram_private_chat_id = private_chat_id
            row.private_chat_available = True
            row.last_private_interaction_at = utcnow()
            row.private_delivery_failure_category = None
            row.bot_blocked_at = None

    async def get_user_by_telegram_id(self, telegram_user_id: int) -> User | None:
        """Fetch a ``users`` row by Telegram id."""
        result = await self._session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()
