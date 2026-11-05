"""Đội agent (0091) request / response bodies."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from meobot.application.agents.service import AgentSummary
from meobot.application.agents.turns import attachment_url
from meobot.db.models.agent_chat import (
    AgentAttachment,
    AgentConversation,
    AgentMessage,
    AgentProfile,
)
from meobot.domain.agents.models import (
    AGENT_GROUP_LABELS,
    MAX_ATTACHMENTS,
    MAX_FILE_BYTES,
    MAX_IMAGE_BYTES,
    MAX_MESSAGE_CHARS,
    AgentGroup,
)


class AgentGroupResponse(BaseModel):
    key: str
    label: str


class AgentResponse(BaseModel):
    slug: str
    name: str
    title: str
    description: str
    badge: str
    group: str
    last_message_at: datetime | None = None

    @classmethod
    def from_summary(cls, summary: AgentSummary) -> AgentResponse:
        agent = summary.agent
        return cls(
            slug=agent.slug,
            name=agent.name,
            title=agent.title,
            description=agent.description,
            badge=agent.badge,
            group=agent.group.value,
            last_message_at=summary.last_message_at,
        )


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
    groups: list[AgentGroupResponse] = Field(
        default_factory=lambda: [
            AgentGroupResponse(key=group.value, label=AGENT_GROUP_LABELS[group])
            for group in AgentGroup
        ]
    )
    #: False = the relay is not configured: the screen says so, sending is off.
    configured: bool
    manage: bool = False
    max_message_chars: int = MAX_MESSAGE_CHARS
    max_attachments: int = MAX_ATTACHMENTS
    max_file_bytes: int = MAX_FILE_BYTES
    max_image_bytes: int = MAX_IMAGE_BYTES


class ConversationResponse(BaseModel):
    id: uuid.UUID
    agent: str
    title: str
    created_at: datetime | None = None
    last_message_at: datetime | None = None
    #: An answer is being written right now.
    running: bool = False

    @classmethod
    def from_row(
        cls, row: AgentConversation, slug: str, *, running: bool = False
    ) -> ConversationResponse:
        return cls(
            id=row.id,
            agent=slug,
            title=row.title,
            created_at=row.created_at,
            last_message_at=row.last_message_at,
            running=running,
        )


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]


class AttachmentResponse(BaseModel):
    """A file attached to a question. ``url`` opens it (the owner only)."""

    id: uuid.UUID
    name: str
    kind: str
    content_type: str
    size_bytes: int
    pages: int | None = None
    truncated: bool = False
    note: str | None = None
    url: str

    @classmethod
    def from_row(cls, row: AgentAttachment) -> AttachmentResponse:
        return cls(
            id=row.id,
            name=row.name,
            kind=row.kind.value,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            pages=row.pages,
            truncated=row.truncated,
            note=row.note,
            url=attachment_url(row.id),
        )


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    status: str
    error_code: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    attachments: list[AttachmentResponse] = Field(default_factory=list)

    @classmethod
    def from_row(
        cls,
        row: AgentMessage,
        *,
        partial: str | None = None,
        attachments: list[AgentAttachment] | None = None,
    ) -> MessageResponse:
        return cls(
            id=row.id,
            role=row.role.value,
            content=partial if partial is not None else row.content,
            status=row.status.value,
            error_code=row.error_code,
            tools_used=[str(tool) for tool in row.tools_used or []],
            duration_ms=row.duration_ms,
            created_at=row.created_at,
            finished_at=row.finished_at,
            attachments=[AttachmentResponse.from_row(item) for item in attachments or []],
        )


class MessageListResponse(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]
    has_more: bool


class SendMessageRequest(BaseModel):
    # The service gives the friendly error for length; this only stops a huge body.
    text: str = Field(default="", max_length=MAX_MESSAGE_CHARS * 2)
    #: Files uploaded before (``POST .../attachments``), in the order shown.
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class RenameConversationRequest(BaseModel):
    title: str = Field(max_length=400)


class StopResponse(BaseModel):
    stopped: bool


class AgentAdminResponse(BaseModel):
    slug: str
    name: str
    title: str
    badge: str
    group: str
    active: bool
    everyone: bool
    department_ids: list[uuid.UUID]
    user_ids: list[uuid.UUID]

    @classmethod
    def from_row(cls, row: AgentProfile) -> AgentAdminResponse:
        return cls(
            slug=row.slug,
            name=row.name,
            title=row.title,
            badge=row.badge,
            group=row.group.value,
            active=row.active,
            everyone=row.everyone,
            department_ids=[uuid.UUID(str(v)) for v in row.allowed_department_ids or []],
            user_ids=[uuid.UUID(str(v)) for v in row.allowed_user_ids or []],
        )


class AgentAdminListResponse(BaseModel):
    agents: list[AgentAdminResponse]
    configured: bool


class UpdateAgentAccessRequest(BaseModel):
    active: bool | None = None
    everyone: bool | None = None
    department_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    user_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)


def sse(event: str, payload: dict[str, Any]) -> str:
    """One Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


__all__ = [
    "AgentAdminListResponse",
    "AgentAdminResponse",
    "AgentListResponse",
    "AgentResponse",
    "AttachmentResponse",
    "ConversationListResponse",
    "ConversationResponse",
    "MessageListResponse",
    "MessageResponse",
    "RenameConversationRequest",
    "SendMessageRequest",
    "StopResponse",
    "UpdateAgentAccessRequest",
    "sse",
]
