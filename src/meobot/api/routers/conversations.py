"""Conversation-thread endpoints.

Operational only: whether a thread is open, how large it is, and the ability to
start a fresh one or archive the current one. Message *contents* are
deliberately not exposed - this API has no authentication yet, and somebody's
conversation is not operational data.

Archiving never deletes.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from meobot.api.deps import ChatMemoryServiceDep
from meobot.api.schemas.conversations import (
    ConversationActionResponse,
    ConversationTargetRequest,
    ConversationThreadResponse,
)
from meobot.core.errors import NotFoundError

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get(
    "/current",
    response_model=ConversationThreadResponse,
    summary="Describe the active conversation thread",
)
async def current_thread(
    memory: ChatMemoryServiceDep,
    bot_id: int = Query(ge=1),
    chat_id: int = Query(),
    telegram_user_id: int = Query(ge=1),
) -> ConversationThreadResponse:
    """Describe the open thread for one conversation.

    Raises:
        NotFoundError: When no thread is open. Reading this endpoint must not
            create one - only a real message does that.
    """
    thread = await memory.active_thread(
        bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id
    )
    if thread is None:
        raise NotFoundError("No active conversation thread for this chat.")
    return ConversationThreadResponse(
        id=thread.id,
        chat_id=thread.chat_id,
        telegram_user_id=thread.telegram_user_id,
        title=thread.title,
        active=thread.active,
        message_count=await memory.message_count(thread.id),
        has_summary=await memory.summary_for(thread.id) is not None,
        last_message_at=thread.last_message_at,
        archived_at=thread.archived_at,
        created_at=thread.created_at,
    )


@router.post(
    "/new",
    response_model=ConversationActionResponse,
    summary="Start a fresh conversation thread",
)
async def new_thread(
    payload: ConversationTargetRequest,
    memory: ChatMemoryServiceDep,
) -> ConversationActionResponse:
    """Archive whatever is open and start a new thread."""
    thread = await memory.start_thread(
        bot_id=payload.bot_id,
        chat_id=payload.chat_id,
        telegram_user_id=payload.telegram_user_id,
    )
    return ConversationActionResponse(
        thread_id=thread.id,
        message="Started a new conversation thread.",
    )


@router.post(
    "/current/archive",
    response_model=ConversationActionResponse,
    summary="Archive the active conversation thread",
)
async def archive_thread(
    payload: ConversationTargetRequest,
    memory: ChatMemoryServiceDep,
) -> ConversationActionResponse:
    """Archive the open thread. Messages are kept, they stop being replayed."""
    archived = await memory.archive_active(
        bot_id=payload.bot_id,
        chat_id=payload.chat_id,
        telegram_user_id=payload.telegram_user_id,
    )
    return ConversationActionResponse(
        archived=archived,
        message=("Archived the active thread." if archived else "There was no active thread."),
    )
