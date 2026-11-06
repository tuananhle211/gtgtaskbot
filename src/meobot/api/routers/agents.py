"""Đội agent (0091): chat with the company's Hermes agents.

Everybody signed in reaches the router; each route shows only the agents the
caller may chat with (``application/agents/access.py``) and only their own
conversations (somebody else's is a 404). Managing the agents is the OWNER's
(403 ``agents_forbidden``).

Sending a message answers with a Server-Sent Events stream: ``start`` (the
question as saved), ``delta`` (a piece of the answer), ``tool`` (the agent
started a tool), ``done`` (the answer as saved, whatever its status), and
``: keepalive`` comment lines while the agent thinks.

Files (0092) are uploaded one by one first - the raw bytes as the body of
``POST /conversations/{id}/attachments?name=`` - and sent with the question by
id (``attachment_ids``). Deleting a conversation deletes it for good, its
files, and its transcript on the Hermes side.

``GET /skills`` = the skills each visible agent carries (approved by Emma in the
Second Brain, its own not yet approved, Hermes' bundled), for the agents screen.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

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
    AgentSkillSetResponse,
    AgentSkillsResponse,
    AttachmentResponse,
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
from meobot.domain.agents.errors import (
    AgentBusyError,
    AgentFileInvalidError,
    AgentsNotConfiguredError,
    AttachmentNotFoundError,
)
from meobot.domain.agents.models import MAX_FILE_BYTES, AttachmentKind
from meobot.integrations.hermes.relay import AgentRelay, HttpAgentRelay

logger = logging.getLogger(__name__)

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


@router.get("/skills", response_model=AgentSkillsResponse)
async def list_skills(
    session: SessionDep, membership: UnitMembershipDep, relay: RelayDep
) -> AgentSkillsResponse:
    """The skills of every agent the caller chats with (read on the agents' VPS)."""
    service, access = _context(session, membership)
    visible = [item.agent.slug for item in await service.agents(access)]
    book = await relay.skills()
    if book is None:
        return AgentSkillsResponse(available=False, agents={})
    return AgentSkillsResponse(
        available=True,
        agents={slug: AgentSkillSetResponse.from_skills(book.get(slug, [])) for slug in visible},
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
    files = await service.attachments_for([row.id for row in rows])
    messages = []
    for row in rows:
        running = turns.get(row.id)
        messages.append(
            MessageResponse.from_row(
                row, partial=running.partial if running else None, attachments=files.get(row.id)
            )
        )
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
            attachment_ids=body.attachment_ids,
        )
        # The answer task saves on its own transaction: the question and the
        # empty answer must be committed before it starts.
        await session.commit()
    except BaseException:
        turns.release(conversation_id)
        raise
    turn = turns.start(prepared, relay, transactions)
    start = {
        "user_message": message_payload(prepared.user_message, prepared.attachments),
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
async def delete_conversation(
    conversation_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    turns: TurnsDep,
    relay: RelayDep,
    request_id: RequestIdDep,
) -> Response:
    """ "Xoá": the conversation, its messages and files, for good - and its
    transcript on the Hermes side. A running answer is stopped first."""
    service, access = _context(session, membership)
    await service.conversation(access, conversation_id)
    await turns.stop(conversation_id)
    slug, hermes_id = await service.delete_conversation(
        access, conversation_id, request_id=request_id
    )
    if relay.configured and not await relay.delete_session(slug, hermes_id):
        # APM's copy is gone either way; the agents' VPS keeps an orphan transcript.
        logger.warning("hermes transcript not deleted", extra={"agent": slug})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    conversation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    membership: UnitMembershipDep,
    name: str = Query(min_length=1, max_length=400),
) -> AttachmentResponse:
    """A file for the next question: the raw bytes are the body."""
    service, access = _context(session, membership)
    await service.conversation(access, conversation_id)  # 404 before reading anything
    limit = MAX_FILE_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise AgentFileInvalidError(f"Tệp lớn quá {limit // (1024 * 1024)} MB.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise AgentFileInvalidError(f"Tệp lớn quá {limit // (1024 * 1024)} MB.")
    row = await service.upload(access, conversation_id, name=name, data=bytes(data))
    return AttachmentResponse.from_row(row)


@router.get("/attachments/{attachment_id}")
async def open_attachment(
    attachment_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep
) -> Response:
    """The file itself, for its owner: a picture shows, anything else downloads."""
    service, access = _context(session, membership)
    row = await service.attachment(access, attachment_id, with_data=True)
    inline = row.kind == AttachmentKind.IMAGE
    disposition = "inline" if inline else "attachment"
    return Response(
        content=row.data,
        media_type=row.content_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(row.name)}",
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


#: A web page an agent wrote, opened in its own tab: sandboxed without
#: ``allow-same-origin`` (an opaque origin: no APM cookie, no APM storage), no
#: requests of its own (``connect-src 'none'``), pictures / fonts / styles from
#: https only; the Next middleware leaves this path's CSP alone.
PAGE_PREVIEW_CSP = (
    "sandbox allow-scripts allow-popups; default-src 'none'; "
    "script-src 'unsafe-inline' 'unsafe-eval' https:; style-src 'unsafe-inline' https:; "
    "img-src https: data: blob:; font-src https: data:; media-src https: data:; "
    "connect-src 'none'; form-action 'none'; frame-src https:; base-uri 'none'"
)


@router.get("/attachments/{attachment_id}/view")
async def view_page(
    attachment_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep
) -> Response:
    """A web page the agent wrote (``.html`` file of an answer), shown as a page."""
    service, access = _context(session, membership)
    row = await service.attachment(access, attachment_id, with_data=True)
    if row.content_type != "text/html":
        raise AttachmentNotFoundError()
    return Response(
        content=row.data,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(row.name)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": PAGE_PREVIEW_CSP,
            "Referrer-Policy": "no-referrer",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_attachment(
    attachment_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep
) -> Response:
    """Take a file back out of the composer (not one already sent)."""
    service, access = _context(session, membership)
    await service.remove_attachment(access, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["get_agent_relay", "get_answer_transactions", "get_turn_registry", "router"]
