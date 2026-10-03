"""Request and response models for the conversation endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationThreadResponse(BaseModel):
    """One conversation thread.

    Message *contents* are not exposed. The endpoints exist for operating the
    bot (is there an open thread, how big is it), not for reading somebody's
    conversation out of an unauthenticated localhost API.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    chat_id: int
    telegram_user_id: int
    title: str | None
    active: bool
    message_count: int
    has_summary: bool
    last_message_at: datetime | None
    archived_at: datetime | None
    created_at: datetime | None


class ConversationTargetRequest(BaseModel):
    """Identifies whose conversation an endpoint acts on."""

    model_config = ConfigDict(extra="forbid")

    bot_id: int = Field(ge=1)
    chat_id: int
    telegram_user_id: int = Field(ge=1)


class ConversationActionResponse(BaseModel):
    """Result of starting or archiving a thread."""

    model_config = ConfigDict(frozen=True)

    thread_id: uuid.UUID | None = None
    archived: int = 0
    message: str
