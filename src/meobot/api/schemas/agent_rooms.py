"""Phòng họp agent (0093) request / response bodies."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from meobot.application.agents.room_runs import room_message_payload
from meobot.application.agents.rooms import Candidate, RoomSummary, RoomView
from meobot.db.models.agent_chat import AgentProfile
from meobot.db.models.agent_room import AgentRoomMessage
from meobot.domain.agents.models import MAX_MESSAGE_CHARS
from meobot.domain.agents.rooms import MAX_ROOM_AGENTS, MAX_ROOM_TITLE, MIN_ROOM_AGENTS


class RoomAgentResponse(BaseModel):
    agent_id: uuid.UUID
    slug: str
    name: str
    title: str
    badge: str
    active: bool
    chair: bool = False

    @classmethod
    def from_agent(cls, agent: AgentProfile, *, chair: bool = False) -> RoomAgentResponse:
        return cls(
            agent_id=agent.id,
            slug=agent.slug,
            name=agent.name,
            title=agent.title,
            badge=agent.badge,
            active=agent.active,
            chair=chair,
        )


class RoomPersonResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    owner: bool = False


class RoomSummaryResponse(BaseModel):
    id: uuid.UUID
    title: str
    owner_name: str
    is_owner: bool
    agents: list[RoomAgentResponse]
    people: int
    rounds: int
    created_at: datetime | None = None
    last_message_at: datetime | None = None
    running: bool = False

    @classmethod
    def from_summary(cls, item: RoomSummary, *, running: bool) -> RoomSummaryResponse:
        return cls(
            id=item.room.id,
            title=item.room.title,
            owner_name=item.owner_name,
            is_owner=item.is_owner,
            agents=[
                RoomAgentResponse.from_agent(agent, chair=agent.id == item.room.chair_agent_id)
                for agent in item.agents
            ],
            people=item.people,
            rounds=item.room.rounds,
            created_at=item.room.created_at,
            last_message_at=item.room.last_message_at,
            running=running,
        )


class RoomListResponse(BaseModel):
    rooms: list[RoomSummaryResponse]
    configured: bool
    min_agents: int = MIN_ROOM_AGENTS
    max_agents: int = MAX_ROOM_AGENTS
    max_message_chars: int = MAX_MESSAGE_CHARS


class RoomResponse(BaseModel):
    id: uuid.UUID
    title: str
    is_owner: bool
    rounds: int
    running: bool = False
    agents: list[RoomAgentResponse]
    people: list[RoomPersonResponse]
    created_at: datetime | None = None
    last_message_at: datetime | None = None

    @classmethod
    def from_view(cls, view: RoomView, *, running: bool) -> RoomResponse:
        return cls(
            id=view.room.id,
            title=view.room.title,
            is_owner=view.is_owner,
            rounds=view.room.rounds,
            running=running,
            agents=[
                RoomAgentResponse.from_agent(
                    item.agent, chair=item.agent.id == view.room.chair_agent_id
                )
                for item in view.agents
            ],
            people=[
                RoomPersonResponse(user_id=view.owner.id, name=view.owner.full_name, owner=True),
                *(
                    RoomPersonResponse(user_id=user.id, name=user.full_name)
                    for _, user in view.members
                ),
            ],
            created_at=view.room.created_at,
            last_message_at=view.room.last_message_at,
        )


class RoomMessageListResponse(BaseModel):
    room: RoomResponse
    messages: list[dict[str, Any]]
    has_more: bool

    @staticmethod
    def message(row: AgentRoomMessage, slug: str | None, partial: str | None) -> dict[str, Any]:
        payload = room_message_payload(row, slug)
        if partial is not None:
            payload["content"] = partial
        return payload


class CandidateResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    allowed: bool
    missing: list[str]

    @classmethod
    def from_candidate(cls, item: Candidate) -> CandidateResponse:
        return cls(
            user_id=item.user.id,
            name=item.user.full_name,
            allowed=item.allowed,
            missing=item.missing,
        )


class CandidateListResponse(BaseModel):
    people: list[CandidateResponse]


class CreateRoomRequest(BaseModel):
    title: str = Field(default="", max_length=MAX_ROOM_TITLE * 2)
    #: In speaking order.
    agents: list[str] = Field(max_length=MAX_ROOM_AGENTS * 2)
    chair: str | None = Field(default=None, max_length=40)


class UpdateRoomRequest(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_ROOM_TITLE * 2)
    agents: list[str] | None = Field(default=None, max_length=MAX_ROOM_AGENTS * 2)
    chair: str | None = Field(default=None, max_length=40)


class AddMemberRequest(BaseModel):
    user_id: uuid.UUID


class PostRoomMessageRequest(BaseModel):
    text: str = Field(default="", max_length=MAX_MESSAGE_CHARS * 2)


class PostRoomMessageResponse(BaseModel):
    message: dict[str, Any]
    #: The owner's message started a round.
    round_started: bool


class RoundResponse(BaseModel):
    started: bool


__all__ = [
    "AddMemberRequest",
    "CandidateListResponse",
    "CandidateResponse",
    "CreateRoomRequest",
    "PostRoomMessageRequest",
    "PostRoomMessageResponse",
    "RoomAgentResponse",
    "RoomListResponse",
    "RoomMessageListResponse",
    "RoomPersonResponse",
    "RoomResponse",
    "RoomSummaryResponse",
    "RoundResponse",
    "UpdateRoomRequest",
]
