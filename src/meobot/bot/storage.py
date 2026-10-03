"""PostgreSQL-backed aiogram FSM storage.

aiogram ships a memory storage (lost on restart) and a Redis one. MeoBot uses
PostgreSQL because the conversation state it keeps - a half-configured sheet
profile, a proposed mapping awaiting confirmation - is operational data that
belongs next to the profile it will become, with the same backup story.

Each call is its own short transaction: an FSM write must not be entangled
with whatever transaction a handler happens to be running.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from meobot.application.conversation_state_service import (
    ConversationKey,
    ConversationStateService,
)
from meobot.core.logging import get_logger
from meobot.db.session import Database

logger = get_logger(__name__)


class PostgresStorage(BaseStorage):
    """FSM storage persisting to ``conversation_states``.

    Args:
        database: Source of sessions.
        ttl_seconds: How long an untouched conversation survives.
    """

    def __init__(self, database: Database, *, ttl_seconds: int = 3600) -> None:
        self._database = database
        self._ttl = ttl_seconds

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        """Store the FSM state, or forget the conversation when it is cleared."""
        resolved = state.state if isinstance(state, State) else state
        async with self._database.transaction() as session:
            service = ConversationStateService(session, ttl_seconds=self._ttl)
            conversation = self._key(key)
            if resolved is None:
                # aiogram clears the state at the end of a flow; keeping the
                # row would only leave stale data to expire later.
                await service.clear(conversation)
                return
            await service.set_state(conversation, resolved)

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._database.transaction() as session:
            row = await ConversationStateService(session, ttl_seconds=self._ttl).load(
                self._key(key)
            )
            return row.state if row is not None else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self._database.transaction() as session:
            await ConversationStateService(session, ttl_seconds=self._ttl).set_data(
                self._key(key), dict(data)
            )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._database.transaction() as session:
            return await ConversationStateService(session, ttl_seconds=self._ttl).get_data(
                self._key(key)
            )

    async def close(self) -> None:
        """The database is owned by the process, not by this storage."""
        return None

    @staticmethod
    def _key(key: StorageKey) -> ConversationKey:
        return ConversationKey(
            bot_id=key.bot_id,
            chat_id=key.chat_id,
            telegram_user_id=key.user_id,
            destiny=key.destiny,
        )
