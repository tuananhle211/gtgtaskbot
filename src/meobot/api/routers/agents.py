"""Đội agent (0091): chat with the company's Hermes agents.

Everybody signed in reaches the router; each route shows only the agents the
caller may chat with (``application/agents/access.py``) and only their own
conversations (somebody else's is a 404). Managing the agents is the OWNER's
(403 ``agents_forbidden``).

Sending a message answers with a Server-Sent Events stream: ``start`` (the
question as saved), ``delta`` (a piece of the answer), ``tool`` (the agent
started a tool), ``done`` (the answer as saved, whatever its status), and
``: keepalive`` comment lines while the agent thinks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import (
    CurrentActorDep,
    RequestIdDep,
    SettingsDep,
    UnitMembershipDep,
    get_session,
)
from meobot.api.schemas.agents import (
    AgentAdminListResponse,
    AgentAdminResponse,
    AgentListResponse,
    AgentResponse,
    ConversationListResponse,
    ConversationResponse,
    MessageListResponse,
    MessageResponse,
    RenameConversationRequest,
    SendMessageRequest,
    StopResponse,
    UpdateAgentAccessRequest,
    sse,
)
from meobot.application.agents.access import AgentAccess, agent_access
from meobot.application.agents.service import AgentChatService
from meobot.application.agents.turns import TransactionFactory, TurnRegistry, message_payload
from meobot.application.audit_service import AuditService
from meobot.db.session import Database
from meobot.domain.agents.errors import AgentBusyError, AgentsNotConfiguredError
from meobot.integrations.hermes.relay import AgentRelay, HttpAgentRelay

router = APIRouter(prefix="/api/agents", tags=["agents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
PAGE = 50


def get_agent_relay(app_settings: SettingsDep) -> AgentRelay:
    """The relay in front of the Hermes agents (a fake in tests)."""
    return HttpAgentRelay.from_settings(app_settings)


def get_turn_registry(request: Request) -> TurnRegistry:
    """The answers running in this process (one registry per app)."""
    registry: TurnRegistry | None = getattr(request.app.state, "agent_turns", None)
    if registry is None:
        registry = TurnRegistry()
        request.app.state.agent_turns = registry
    return registry


def get_answer_transactions(request: Request) -> TransactionFactory:
    """Where a finished answer is saved: its own transaction, after the request's."""
    database: Database = request.app.state.database

    def factory() -> AbstractAsyncContextManager[AsyncSession]:
        return database.transaction()

    return factory


RelayDep = Annotated[AgentRelay, Depends(get_agent_relay)]
TurnsDep = Annotated[TurnRegistry, Depends(get_turn_registry)]
TransactionsDep = Annotated[
    Callable[[], AbstractAsyncContextManager[AsyncSession]], Depends(get_answer_transactions)
]


def _context(
    session: AsyncSession, membership: UnitMembershipDep
) -> tuple[AgentChatService, AgentAccess]:
    return AgentChatService(session, AuditService(session)), agent_access(membership)


@router.get("", response_model=AgentListResponse)
async def list_agents(
    session: SessionDep, membership: UnitMembershipDep, relay: RelayDep
) -> AgentListResponse:
    """The agents the caller chats with, grouped on the screen."""
    service, access = _context(session, membership)
    return AgentListResponse(
        agents=[AgentResponse.from_summary(item) for item in await service.agents(access)],
        configured=relay.configured,
        manage=access.manage,
    )


@router.get("/admin", response_model=AgentAdminListResponse)
async def admin_list(
    session: SessionDep, membership: UnitMembershipDep, relay: RelayDep
) -> AgentAdminListResponse:
    """(OWNER) every agent with who may chat with it."""
    service, access = _context(session, membership)
    rows = await service.all_agents(access)
    return AgentAdminListResponse(
        agents=[AgentAdminResponse.from_row(row) for row in rows], configured=relay.configured
    )


@router.put("/admin/{slug}", response_model=AgentAdminResponse)
async def admin_update(
    slug: str,
    body: UpdateAgentAccessRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
) -> AgentAdminResponse:
    """(OWNER) turn an agent on / off, and who may chat with it."""
    service, access = _context(session, membership)
    row = await service.update_agent(
        access,
        slug,
        active=body.active,
        everyone=body.everyone,
        department_ids=body.department_ids,
        user_ids=body.user_ids,
        request_id=request_id,
    )
    return AgentAdminResponse.from_row(row)


@router.get("/{slug}/conversations", response_model=ConversationListResponse)
async def list_conversations(
    slug: str, session: SessionDep, membership: UnitMembershipDep, turns: TurnsDep
) -> ConversationListResponse:
    service, access = _context(session, membership)
    rows = await service.conversations(access, slug)
    return ConversationListResponse(
        conversations=[
            ConversationResponse.from_row(row, slug, running=turns.running_for(row.id) is not None)
            for row in rows
        ]
    )


@router.post(
    "/{slug}/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    slug: str, session: SessionDep, membership: UnitMembershipDep, request_id: RequestIdDep
) -> ConversationResponse:
    service, access = _context(session, membership)
    row = await service.create_conversation(access, slug, request_id=request_id)
    return ConversationResponse.from_row(row, slug)


@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def list_messages(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    turns: TurnsDep,
    before: datetime | None = Query(default=None),
) -> MessageListResponse:
    """Oldest first; ``before`` pages back. An answer still being written
    carries what came so far."""
    service, access = _context(session, membership)
    conversation, agent = await service.conversation(access, conversation_id)
    await service.fail_orphans(conversation.id, turns.running_ids())
    rows = await service.messages(access, conversation_id, before=before, limit=PAGE + 1)
    has_more = len(rows) > PAGE
    rows = rows[-PAGE:]
    messages = []
    for row in rows:
        running = turns.get(row.id)
        messages.append(MessageResponse.from_row(row, partial=running.partial if running else None))
    return MessageListResponse(
        conversation=ConversationResponse.from_row(
            conversation, agent.slug, running=turns.running_for(conversation.id) is not None
        ),
        messages=messages,
        has_more=has_more,
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    relay: RelayDep,
    turns: TurnsDep,
    transactions: TransactionsDep,
) -> StreamingResponse:
    """Ask the agent; the answer streams back (see the module docstring)."""
    service, access = _context(session, membership)
    await service.conversation(access, conversation_id)  # 404 before anything else
    if not relay.configured:
        raise AgentsNotConfiguredError()
    if not turns.reserve(conversation_id):
        raise AgentBusyError()
    try:
        await service.fail_orphans(conversation_id, turns.running_ids())
        prepared = await service.prepare_turn(
            access,
            conversation_id,
            body.text,
            asker=actor.full_name,
            seats=membership.seats,
            request_id=request_id,
        )
        # The answer task saves on its own transaction: the question and the
        # empty answer must be committed before it starts.
        await session.commit()
    except BaseException:
        turns.release(conversation_id)
        raise
    turn = turns.start(prepared, relay, transactions)
    start = {
        "user_message": message_payload(prepared.user_message),
        "answer_id": str(prepared.answer_id),
    }

    async def stream() -> AsyncIterator[str]:
        yield sse("start", start)
        async for event in turn.events():
            if event is None:
                yield ": keepalive\n\n"
            else:
                yield sse(str(event["type"]), event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversation_id}/stop", response_model=StopResponse)
async def stop_answer(
    conversation_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep, turns: TurnsDep
) -> StopResponse:
    """ "Dừng": what came so far is kept, as CANCELLED."""
    service, access = _context(session, membership)
    await service.conversation(access, conversation_id)
    return StopResponse(stopped=await turns.stop(conversation_id))


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: RenameConversationRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
) -> ConversationResponse:
    service, access = _context(session, membership)
    row = await service.rename(access, conversation_id, body.title)
    _, agent = await service.conversation(access, conversation_id)
    return ConversationResponse.from_row(row, agent.slug)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_conversation(
    conversation_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep, turns: TurnsDep
) -> Response:
    """ "Lưu trữ": out of the list, kept. A running answer is stopped first."""
    service, access = _context(session, membership)
    await service.conversation(access, conversation_id)
    await turns.stop(conversation_id)
    await service.archive(access, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["get_agent_relay", "get_answer_transactions", "get_turn_registry", "router"]
