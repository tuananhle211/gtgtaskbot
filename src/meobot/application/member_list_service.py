"""Making "việc số 2" mean exactly what the person saw.

A Member reads a numbered list and then refers to a line by its number. The
failure this prevents is specific and easy to hit: they open a list, get
distracted, a newer list arrives, and *then* they say "số 2". Without binding,
that resolves against whatever is newest and acts on the wrong thing.

So a list is stored with a **version**, and a numbered reference only resolves
when it is read against the version that produced it. A stale reference
resolves to nothing and MeoBot asks again - which is the correct outcome, and
the reason this is a table rather than an in-memory dict that a restart would
silently empty.

Scoped to (bot, chat, person, kind), so two people numbering different lists in
the same group never see each other's items.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.hr import MemberListContext

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ListReference:
    """A resolved "số N": which item, from which version of which list."""

    kind: str
    version: int
    position: int
    item_id: str


class MemberListService:
    """Stores and resolves the numbered lists a Member has been shown.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Supplies the context lifetime.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def remember(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        kind: str,
        item_ids: Sequence[uuid.UUID | str],
    ) -> int:
        """Record the list just shown and return its version.

        The version increments every time, so the number a person is about to
        read is only ever valid against this exact rendering.
        """
        now = utcnow()
        row = await self._row(
            bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id, kind=kind
        )
        ids = [str(item) for item in item_ids]
        if row is None:
            row = MemberListContext(
                bot_id=bot_id,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                kind=kind,
                version=1,
                item_ids=ids,
                expires_at=now + timedelta(seconds=self._settings.member_list_context_ttl_seconds),
                created_at=now,
            )
            self._session.add(row)
        else:
            row.version += 1
            row.item_ids = ids
            row.created_at = now
            row.expires_at = now + timedelta(seconds=self._settings.member_list_context_ttl_seconds)
        await self._session.flush()
        return row.version

    async def resolve(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        kind: str,
        position: int,
        expected_version: int | None = None,
    ) -> ListReference | None:
        """Turn "số N" into an item id, or ``None`` when it cannot be trusted.

        Returns ``None`` for an expired context, an out-of-range position, or a
        version mismatch - all three mean "ask again", never "guess".
        """
        row = await self._row(
            bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id, kind=kind
        )
        if row is None:
            return None
        if utcnow() >= ensure_utc(row.expires_at):
            logger.info("member_list_context_expired", extra={"kind": kind})
            return None
        if expected_version is not None and row.version != expected_version:
            logger.info(
                "member_list_context_stale",
                extra={"kind": kind, "expected": expected_version, "actual": row.version},
            )
            return None
        if position < 1 or position > len(row.item_ids):
            return None
        return ListReference(
            kind=kind,
            version=row.version,
            position=position,
            item_id=str(row.item_ids[position - 1]),
        )

    async def current_version(
        self, *, bot_id: int, chat_id: int, telegram_user_id: int, kind: str
    ) -> int | None:
        """Version of the list this person is currently looking at."""
        row = await self._row(
            bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id, kind=kind
        )
        if row is None or utcnow() >= ensure_utc(row.expires_at):
            return None
        return row.version

    async def only_item(
        self, *, bot_id: int, chat_id: int, telegram_user_id: int, kind: str
    ) -> str | None:
        """The single item, when the last list had exactly one.

        This is what lets "tôi làm xong rồi" work without a follow-up question
        when there is genuinely nothing else it could mean.
        """
        row = await self._row(
            bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id, kind=kind
        )
        if row is None or utcnow() >= ensure_utc(row.expires_at):
            return None
        return str(row.item_ids[0]) if len(row.item_ids) == 1 else None

    async def _row(
        self, *, bot_id: int, chat_id: int, telegram_user_id: int, kind: str
    ) -> MemberListContext | None:
        result = await self._session.execute(
            select(MemberListContext).where(
                MemberListContext.bot_id == bot_id,
                MemberListContext.chat_id == chat_id,
                MemberListContext.telegram_user_id == telegram_user_id,
                MemberListContext.kind == kind,
            )
        )
        return result.scalar_one_or_none()


#: Numbered references people actually write.
_POSITION_WORDS: dict[str, int] = {
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "nam": 5,
}


def read_position(matchable: str) -> int | None:
    """Read "việc số 2" / "số 3" / "cái thứ hai" out of a folded message."""
    import re

    digits = re.search(r"\b(?:so|thu)\s+(\d{1,2})\b", matchable)
    if digits is not None:
        return int(digits.group(1))
    words = re.search(r"\b(?:so|thu)\s+(mot|hai|ba|bon|nam)\b", matchable)
    if words is not None:
        return _POSITION_WORDS[words.group(1)]
    return None
