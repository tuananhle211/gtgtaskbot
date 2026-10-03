"""Persistent state for multi-step Telegram conversations.

Scope is deliberately narrow: enough structured state to finish a workflow the
user already started (adding a sheet, correcting a mapping, typing a revision
comment, creating an invite), with an expiry so an abandoned flow disappears
on its own. This is not a memory of what was said.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger
from meobot.core.time import utc_in, utcnow
from meobot.db.models.conversation_state import ConversationState

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class ConversationKey:
    """Identifies one conversation. Mirrors aiogram's ``StorageKey``."""

    bot_id: int
    chat_id: int
    telegram_user_id: int
    destiny: str = "default"


class ConversationStateService:
    """Stores and expires conversation state rows.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        ttl_seconds: How long an untouched conversation survives.
    """

    def __init__(self, session: AsyncSession, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._session = session
        self._ttl = ttl_seconds

    async def load(self, key: ConversationKey) -> ConversationState | None:
        """Return the stored row, or ``None`` when absent or expired."""
        row = await self._find(key)
        if row is None:
            return None
        if self._expired(row, utcnow()):
            await self._session.delete(row)
            await self._session.flush()
            return None
        return row

    async def set_state(self, key: ConversationKey, state: str | None) -> ConversationState:
        """Set the FSM state, creating the row if needed."""
        row = await self._upsert(key)
        row.state = state
        row.expires_at = utc_in(self._ttl)
        await self._session.flush()
        return row

    async def set_data(self, key: ConversationKey, data: dict[str, Any]) -> ConversationState:
        """Replace the stored payload."""
        row = await self._upsert(key)
        row.data = dict(data)
        row.expires_at = utc_in(self._ttl)
        await self._session.flush()
        return row

    async def get_data(self, key: ConversationKey) -> dict[str, Any]:
        """Return the stored payload, or an empty dict."""
        row = await self.load(key)
        return dict(row.data) if row is not None else {}

    async def clear(self, key: ConversationKey) -> None:
        """Forget a conversation entirely."""
        row = await self._find(key)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete every expired conversation. Returns the row count."""
        moment = now or utcnow()
        result = await self._session.execute(
            delete(ConversationState).where(ConversationState.expires_at <= moment)
        )
        # A DELETE always yields a CursorResult; the async facade types it as
        # the wider Result, which has no rowcount.
        deleted = int(getattr(result, "rowcount", 0) or 0)
        if deleted:
            logger.info("conversations_purged", extra={"deleted": deleted})
        return deleted

    async def _find(self, key: ConversationKey) -> ConversationState | None:
        result = await self._session.execute(
            select(ConversationState).where(
                ConversationState.bot_id == key.bot_id,
                ConversationState.chat_id == key.chat_id,
                ConversationState.telegram_user_id == key.telegram_user_id,
                ConversationState.destiny == key.destiny,
            )
        )
        return result.scalar_one_or_none()

    async def _upsert(self, key: ConversationKey) -> ConversationState:
        row = await self._find(key)
        if row is not None:
            return row
        row = ConversationState(
            bot_id=key.bot_id,
            chat_id=key.chat_id,
            telegram_user_id=key.telegram_user_id,
            destiny=key.destiny,
            state=None,
            data={},
            expires_at=utc_in(self._ttl),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    @staticmethod
    def _expired(row: ConversationState, now: datetime) -> bool:
        from meobot.core.time import ensure_utc

        return now >= ensure_utc(row.expires_at)
