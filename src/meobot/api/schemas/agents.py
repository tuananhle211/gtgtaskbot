"""Đội agent (0091) request / response bodies."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from meobot.application.agents.service import AgentSummary
from meobot.db.models.agent_chat import AgentConversation, AgentMessage, AgentProfile
from meobot.domain.agents.models import AGENT_GROUP_LABELS, MAX_MESSAGE_CHARS, AgentGroup


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

    @classmethod
    def from_row(cls, row: AgentMessage, *, partial: str | None = None) -> MessageResponse:
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
        )


class MessageListResponse(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]
    has_more: bool


class SendMessageRequest(BaseModel):
    # The service gives the friendly error for length; this only stops a huge body.
    text: str = Field(max_length=MAX_MESSAGE_CHARS * 2)


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
