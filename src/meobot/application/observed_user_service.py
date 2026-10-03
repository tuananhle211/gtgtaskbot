"""Recording the Telegram accounts MeoBot has seen.

Being observed is not being registered. This service writes a row that lets an
approval message say *who* is asking ("Nguyễn Văn A (@nva)") without that person
gaining a role, a permission, or any presence in ``users``.

The Telegram numeric id is the identity and the unique key. Usernames and
display names change; folding a rename into a new row would split one person's
history in two, so the row is found by id and its metadata is overwritten.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.observed_user import ObservedTelegramUser

logger = get_logger(__name__)

MAX_DISPLAY_NAME = 300
MAX_USERNAME = 100


class ObservedUserService:
    """Upserts :class:`ObservedTelegramUser` rows.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        telegram_user_id: int,
        username: str | None = None,
        display_name: str | None = None,
        is_bot: bool = False,
        chat_id: int | None = None,
    ) -> ObservedTelegramUser:
        """Create or refresh the row for one Telegram account.

        Never raises for ordinary data: this runs on the hot path of every
        update, and failing to note a username must not cost a reply.
        """
        now = utcnow()
        existing = await self.get(telegram_user_id)
        if existing is None:
            row = ObservedTelegramUser(
                telegram_user_id=telegram_user_id,
                latest_username=(username or None) and username[:MAX_USERNAME],
                latest_display_name=(display_name or None) and display_name[:MAX_DISPLAY_NAME],
                is_bot=is_bot,
                first_seen_at=now,
                last_seen_at=now,
                last_seen_chat_id=chat_id,
                extra_metadata={},
            )
            self._session.add(row)
            await self._session.flush()
            return row

        # Same person, possibly a new name. Overwrite in place - a rename must
        # never produce a second identity.
        if username:
            existing.latest_username = username[:MAX_USERNAME]
        if display_name:
            existing.latest_display_name = display_name[:MAX_DISPLAY_NAME]
        existing.is_bot = is_bot
        existing.last_seen_at = now
        if chat_id is not None:
            existing.last_seen_chat_id = chat_id
        await self._session.flush()
        return existing

    async def get(self, telegram_user_id: int) -> ObservedTelegramUser | None:
        """Fetch one observed account by Telegram id."""
        result = await self._session.execute(
            select(ObservedTelegramUser).where(
                ObservedTelegramUser.telegram_user_id == telegram_user_id
            )
        )
        return result.scalar_one_or_none()

    async def describe(self, telegram_user_id: int) -> dict[str, Any]:
        """Safe, display-ready facts about one account.

        Only what an approval message needs. No message content ever passes
        through here.
        """
        row = await self.get(telegram_user_id)
        if row is None:
            return {"telegram_user_id": telegram_user_id, "display_name": None, "username": None}
        return {
            "telegram_user_id": row.telegram_user_id,
            "display_name": row.latest_display_name,
            "username": row.latest_username,
            "is_bot": row.is_bot,
            "first_seen_at": row.first_seen_at,
            "last_seen_at": row.last_seen_at,
        }
