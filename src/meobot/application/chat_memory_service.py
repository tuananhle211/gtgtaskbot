"""Persistent, bounded conversation memory.

Two properties matter more than anything else here:

**Bounded.** No matter how long a thread runs, the provider receives at most
``CHAT_HISTORY_MAX_MESSAGES`` recent turns plus one rolling summary. History
does not grow the prompt; it gets compressed into the summary instead. There
is no path in this service that reads a whole thread into a prompt.

**Redacted.** Every ``content`` written here has been through
:func:`~meobot.domain.conversations.redaction.prepare_for_storage`. A pasted
service-account file is not stored at all; a pasted token is replaced. This is
the only place in MeoBot where arbitrary typed text becomes durable, so the
filter lives at the write, not at the read.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import utcnow
from meobot.db.models.conversation import (
    ConversationMessage,
    ConversationSummary,
    ConversationThread,
)
from meobot.domain.conversations.redaction import estimate_tokens, prepare_for_storage
from meobot.domain.conversations.references import (
    RecentReference,
    load_references,
    push_reference,
)
from meobot.integrations.llm.base import ChatTurn, LLMProvider, SummaryRequest

logger = get_logger(__name__)

#: Roles a stored message may carry.
VALID_ROLES: frozenset[str] = frozenset({"user", "assistant", "tool"})

#: Title derived from the first user message, so ``/chat_status`` and any
#: future thread list have something readable to show.
TITLE_MAX_CHARS = 80


@dataclass(frozen=True, slots=True)
class ChatContext:
    """The bounded context handed to a provider for one message."""

    thread_id: uuid.UUID
    history: tuple[ChatTurn, ...]
    rolling_summary: str | None
    message_count: int
    #: Entities this thread has been pointing at, so "Sheet đó" resolves. A
    #: bounded pointer list, not memory - see
    #: :mod:`meobot.domain.conversations.references`.
    references: tuple[RecentReference, ...] = ()

    @property
    def is_new(self) -> bool:
        return self.message_count == 0


class ChatMemoryService:
    """Threads, messages and rolling summaries.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Supplies the history and summarisation budgets.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- Threads ----------------------------------------------------------
    async def active_thread(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
    ) -> ConversationThread | None:
        """The open thread for this conversation, if there is one."""
        result = await self._session.execute(
            select(ConversationThread)
            .where(
                ConversationThread.bot_id == bot_id,
                ConversationThread.chat_id == chat_id,
                ConversationThread.telegram_user_id == telegram_user_id,
                ConversationThread.active.is_(True),
            )
            .order_by(desc(ConversationThread.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_or_create_thread(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        title: str | None = None,
    ) -> ConversationThread:
        """Return the active thread, opening one if none exists."""
        existing = await self.active_thread(
            bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id
        )
        if existing is not None:
            return existing
        return await self.start_thread(
            bot_id=bot_id,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            title=title,
        )

    async def start_thread(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        title: str | None = None,
    ) -> ConversationThread:
        """Archive whatever is open and start a fresh thread (``/new_chat``)."""
        await self.archive_active(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        thread = ConversationThread(
            bot_id=bot_id,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            title=(title or "")[:TITLE_MAX_CHARS] or None,
            active=True,
        )
        self._session.add(thread)
        await self._session.flush()
        logger.info("conversation_thread_started", extra={"thread_id": str(thread.id)})
        return thread

    async def archive_active(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
    ) -> int:
        """Archive the open thread. Returns how many threads were archived.

        Archiving never deletes: the messages stay, they simply stop being
        replayed. ``/clear_chat`` is "start fresh", not "destroy evidence".
        """
        now = utcnow()
        result = await self._session.execute(
            select(ConversationThread).where(
                ConversationThread.bot_id == bot_id,
                ConversationThread.chat_id == chat_id,
                ConversationThread.telegram_user_id == telegram_user_id,
                ConversationThread.active.is_(True),
            )
        )
        threads = list(result.scalars().all())
        for thread in threads:
            thread.active = False
            thread.archived_at = now
        await self._session.flush()
        return len(threads)

    # --- Messages ---------------------------------------------------------
    async def record_message(
        self,
        *,
        thread: ConversationThread,
        role: str,
        content: str,
        related_tool_name: str | None = None,
        related_entity_type: str | None = None,
        related_entity_id: str | None = None,
    ) -> ConversationMessage | None:
        """Store one turn, redacted.

        Returns ``None`` when the message must not be stored at all - a pasted
        credential file, or nothing but whitespace. The caller does not treat
        that as an error: the conversation continues, the secret does not.
        """
        if role not in VALID_ROLES:
            raise ValueError(f"Unknown conversation role: {role!r}")

        cleaned = prepare_for_storage(content)
        if cleaned is None:
            logger.info(
                "conversation_message_not_stored",
                extra={"thread_id": str(thread.id), "role": role, "reason": "redacted_empty"},
            )
            return None

        now = utcnow()
        message = ConversationMessage(
            thread_id=thread.id,
            role=role,
            content=cleaned,
            related_tool_name=related_tool_name,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            token_count_estimate=estimate_tokens(cleaned),
            created_at=now,
        )
        self._session.add(message)
        thread.last_message_at = now
        if thread.title is None and role == "user":
            thread.title = cleaned[:TITLE_MAX_CHARS]
        await self._session.flush()
        return message

    async def message_count(self, thread_id: uuid.UUID) -> int:
        """How many turns the thread holds."""
        result = await self._session.execute(
            select(func.count(ConversationMessage.id)).where(
                ConversationMessage.thread_id == thread_id
            )
        )
        return int(result.scalar_one() or 0)

    async def recent_messages(
        self,
        thread_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> list[ConversationMessage]:
        """The newest ``limit`` turns, oldest first.

        ``limit`` defaults to ``CHAT_HISTORY_MAX_MESSAGES`` and is capped at it:
        a caller cannot ask for more history than the budget allows.
        """
        budget = self._settings.chat_history_max_messages
        effective = budget if limit is None else min(limit, budget)
        result = await self._session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.thread_id == thread_id)
            .order_by(desc(ConversationMessage.created_at))
            .limit(effective)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    # --- Summary ----------------------------------------------------------
    async def summary_for(self, thread_id: uuid.UUID) -> ConversationSummary | None:
        """The rolling summary row for a thread, if one exists."""
        result = await self._session.execute(
            select(ConversationSummary).where(ConversationSummary.thread_id == thread_id)
        )
        return result.scalar_one_or_none()

    async def save_summary(self, thread_id: uuid.UUID, summary: str, message_count: int) -> None:
        """Replace the rolling summary. One row per thread, never appended."""
        existing = await self.summary_for(thread_id)
        now = utcnow()
        if existing is None:
            self._session.add(
                ConversationSummary(
                    thread_id=thread_id,
                    summary=summary[:8000],
                    message_count=message_count,
                    updated_at=now,
                )
            )
        else:
            existing.summary = summary[:8000]
            existing.message_count = message_count
            existing.updated_at = now
        await self._session.flush()

    async def load_context(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
    ) -> ChatContext:
        """Assemble the bounded context for one incoming message."""
        thread = await self.get_or_create_thread(
            bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id
        )
        messages = await self.recent_messages(thread.id)
        summary = await self.summary_for(thread.id)
        total = await self.message_count(thread.id)
        return ChatContext(
            thread_id=thread.id,
            history=tuple(
                ChatTurn(role=message.role, content=message.content[:8000]) for message in messages
            ),
            rolling_summary=summary.summary if summary is not None else None,
            message_count=total,
            references=tuple(load_references(thread.recent_references)),
        )

    # --- References -------------------------------------------------------
    def remember_reference(
        self, thread: ConversationThread, reference: RecentReference
    ) -> list[RecentReference]:
        """Record what the conversation is now pointing at.

        Bounded and de-duplicated by
        :func:`~meobot.domain.conversations.references.push_reference`, so the
        list stays a pointer list rather than becoming a second history. Stores
        identifiers and labels only - never message content.
        """
        existing = load_references(thread.recent_references)
        updated = push_reference(existing, reference)
        thread.recent_references = [item.as_dict() for item in updated]
        return updated

    def references_for(self, thread: ConversationThread) -> list[RecentReference]:
        """The pointer list for a thread, rebuilt from storage."""
        return load_references(thread.recent_references)

    def needs_summary(self, message_count: int, summarized_count: int) -> bool:
        """True when enough new turns have accumulated to be worth compressing."""
        trigger = self._settings.chat_summary_trigger_messages
        return message_count - summarized_count >= trigger

    async def summarize_if_needed(self, thread_id: uuid.UUID, llm: LLMProvider) -> bool:
        """Compress older turns into the rolling summary when it is due.

        Returns True when a summary was written. Failure is not propagated:
        summarisation is an optimisation, and losing it must never lose the
        conversation.
        """
        total = await self.message_count(thread_id)
        existing = await self.summary_for(thread_id)
        summarized = existing.message_count if existing is not None else 0
        if not self.needs_summary(total, summarized):
            return False

        turns = await self.recent_messages(
            thread_id, limit=self._settings.chat_summary_trigger_messages
        )
        try:
            summary = await llm.summarize(
                SummaryRequest(
                    turns=[ChatTurn(role=turn.role, content=turn.content[:4000]) for turn in turns],
                    previous_summary=existing.summary if existing is not None else None,
                )
            )
        except Exception:
            logger.warning("conversation_summary_failed", extra={"thread_id": str(thread_id)})
            return False

        if not summary.strip():
            return False
        await self.save_summary(thread_id, summary, total)
        logger.info(
            "conversation_summary_updated",
            extra={"thread_id": str(thread_id), "message_count": total},
        )
        return True

    # --- Views ------------------------------------------------------------
    async def thread_age_seconds(
        self, thread: ConversationThread, *, now: datetime | None = None
    ) -> int:
        """Seconds since the thread was opened. Used by ``/chat_status``."""
        reference = now or utcnow()
        return max(0, int((reference - thread.created_at).total_seconds()))

    @staticmethod
    def as_turns(messages: Sequence[ConversationMessage]) -> list[ChatTurn]:
        """Map stored rows onto the provider's turn shape."""
        return [ChatTurn(role=message.role, content=message.content[:8000]) for message in messages]
