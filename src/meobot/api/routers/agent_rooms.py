"""Phòng họp agent (0093): several agents talk in turns, people read and write.

Everybody signed in reaches the router; a room is seen only by its owner and
the people they invited (anybody else: 404 ``room_not_found``), and only while
they may chat with every agent of the room. The owner runs it (403
``room_forbidden`` for the others).

The owner's message starts a round when none runs; ``POST /rounds`` starts one
without a message ("Họp tiếp"). A round runs in the API process whatever the
browsers do; ``GET /{id}/events`` is the live view (see
:mod:`meobot.application.agents.room_runs`).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.api.deps import CurrentActorDep, RequestIdDep, UnitMembershipDep, get_session
from meobot.api.routers.agents import RelayDep, TransactionsDep
from meobot.api.schemas.agent_rooms import (
    AddMemberRequest,
    CandidateListResponse,
    CandidateResponse,
    CreateRoomRequest,
    PostRoomMessageRequest,
    PostRoomMessageResponse,
    RoomListResponse,
    RoomMessageListResponse,
    RoomResponse,
    RoomSummaryResponse,
    RoundResponse,
    UpdateRoomRequest,
)
from meobot.api.schemas.agents import StopResponse, sse
from meobot.application.agents.access import AgentAccess, agent_access
from meobot.application.agents.room_runs import RoomRunner, room_message_payload
from meobot.application.agents.rooms import AgentRoomService, RoomView
from meobot.application.audit_service import AuditService
from meobot.domain.agents.errors import (
    AgentsNotConfiguredError,
    RoomBusyError,
    RoomForbiddenError,
)
from meobot.domain.org.models import seat_label
from meobot.integrations.hermes.relay import AgentRelay

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents/rooms", tags=["agents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
PAGE = 80


def get_room_runner(request: Request) -> RoomRunner:
    """The rounds running in this process (one runner per app)."""
    runner: RoomRunner | None = getattr(request.app.state, "agent_rooms", None)
    if runner is None:
        runner = RoomRunner()
        request.app.state.agent_rooms = runner
    return runner


RunnerDep = Annotated[RoomRunner, Depends(get_room_runner)]


def _context(
    session: AsyncSession, membership: UnitMembershipDep
) -> tuple[AgentRoomService, AgentAccess]:
    return AgentRoomService(session, AuditService(session)), agent_access(membership)


def _room(view: RoomView, runner: RoomRunner) -> RoomResponse:
    return RoomResponse.from_view(view, running=runner.running(view.room.id) is not None)


async def _forget(relay: AgentRelay, transcripts: list[tuple[str, str]]) -> None:
    """Delete the Hermes transcripts of agents that left (APM's side is gone either way)."""
    if not relay.configured:
        return
    for slug, session_id in transcripts:
        if not await relay.delete_session(slug, session_id):
            logger.warning("hermes room transcript not deleted", extra={"agent": slug})


@router.get("", response_model=RoomListResponse)
async def list_rooms(
    session: SessionDep, membership: UnitMembershipDep, relay: RelayDep, runner: RunnerDep
) -> RoomListResponse:
    service, access = _context(session, membership)
    return RoomListResponse(
        rooms=[
            RoomSummaryResponse.from_summary(item, running=runner.running(item.room.id) is not None)
            for item in await service.rooms(access)
        ],
        configured=relay.configured,
    )


@router.post("", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    body: CreateRoomRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    runner: RunnerDep,
) -> RoomResponse:
    service, access = _context(session, membership)
    room = await service.create(
        access,
        title=body.title,
        agent_slugs=body.agents,
        chair_slug=body.chair,
        request_id=request_id,
    )
    return _room(await service.view(access, room.id), runner)


@router.get("/{room_id}/messages", response_model=RoomMessageListResponse)
async def list_messages(
    room_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    runner: RunnerDep,
    before: datetime | None = Query(default=None),
) -> RoomMessageListResponse:
    """The room and its messages, oldest first; ``before`` pages back. The turn
    being written carries what it said so far."""
    service, access = _context(session, membership)
    view = await service.view(access, room_id)
    await service.fail_orphans(view.room.id, runner.running_message(view.room.id))
    rows, agents = await service.messages(access, room_id, before=before, limit=PAGE + 1)
    has_more = len(rows) > PAGE
    rows = rows[-PAGE:]
    run = runner.running(view.room.id)
    return RoomMessageListResponse(
        room=_room(view, runner),
        messages=[
            RoomMessageListResponse.message(
                row,
                agents[row.agent_id].slug if row.agent_id in agents else None,
                run.partial if run is not None and run.message_id == row.id else None,
            )
            for row in rows
        ],
        has_more=has_more,
    )


@router.patch("/{room_id}", response_model=RoomResponse)
async def update_room(
    room_id: uuid.UUID,
    body: UpdateRoomRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    runner: RunnerDep,
    relay: RelayDep,
) -> RoomResponse:
    """(Owner) the name; the agents in speaking order and the chair (not mid-round)."""
    service, access = _context(session, membership)
    view = await service.owned(access, room_id)
    if body.title is not None:
        await service.rename(access, room_id, body.title)
    if body.agents is not None:
        if runner.running(view.room.id) is not None:
            raise RoomBusyError()
        view, gone = await service.set_agents(
            access, room_id, agent_slugs=body.agents, chair_slug=body.chair, request_id=request_id
        )
        await session.commit()
        await _forget(relay, gone)
    return _room(await service.view(access, room_id), runner)


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(
    room_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    runner: RunnerDep,
    relay: RelayDep,
) -> Response:
    """(Owner) the room, its people, its messages, and the agents' transcripts - for good."""
    service, access = _context(session, membership)
    await service.owned(access, room_id)
    await runner.stop(room_id)
    transcripts = await service.delete(access, room_id, request_id=request_id)
    await session.commit()
    runner.publish(room_id, {"type": "deleted"})
    await _forget(relay, transcripts)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{room_id}/candidates", response_model=CandidateListResponse)
async def candidates(
    room_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep
) -> CandidateListResponse:
    """(Owner) who might be invited, and why somebody may not."""
    service, access = _context(session, membership)
    return CandidateListResponse(
        people=[
            CandidateResponse.from_candidate(item)
            for item in await service.candidates(access, room_id)
        ]
    )


@router.post("/{room_id}/members", response_model=RoomResponse)
async def add_member(
    room_id: uuid.UUID,
    body: AddMemberRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    runner: RunnerDep,
) -> RoomResponse:
    service, access = _context(session, membership)
    view = await service.add_member(access, room_id, body.user_id, request_id=request_id)
    runner.publish(room_id, {"type": "people"})
    return _room(view, runner)


@router.delete("/{room_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    room_id: uuid.UUID,
    user_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    runner: RunnerDep,
) -> Response:
    """The owner takes somebody out; anybody invited leaves (their own id)."""
    service, access = _context(session, membership)
    await service.remove_member(access, room_id, user_id, request_id=request_id)
    runner.publish(room_id, {"type": "people"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{room_id}/messages", response_model=PostRoomMessageResponse)
async def post_message(
    room_id: uuid.UUID,
    body: PostRoomMessageRequest,
    session: SessionDep,
    membership: UnitMembershipDep,
    actor: CurrentActorDep,
    request_id: RequestIdDep,
    runner: RunnerDep,
    relay: RelayDep,
    transactions: TransactionsDep,
) -> PostRoomMessageResponse:
    """Write in the room. The owner's message starts a round when none runs;
    anybody else's is read by the agents who speak next."""
    service, access = _context(session, membership)
    seats = ", ".join(seat_label(seat) for seat in membership.seats)
    label = f"{actor.full_name} - {seats}" if seats else actor.full_name
    view, row = await service.post(
        access, room_id, body.text, speaker_label=label, request_id=request_id
    )
    start = view.is_owner and relay.configured and runner.running(view.room.id) is None
    if start:
        await service.record_round(access, view.room.id, request_id)
    payload = room_message_payload(row)
    # The round saves on its own transactions: the message must be committed first.
    await session.commit()
    runner.publish(view.room.id, {"type": "message", "message": payload})
    started = start and runner.start(view.room.id, relay, transactions) is not None
    return PostRoomMessageResponse(message=payload, round_started=started)


@router.post("/{room_id}/rounds", response_model=RoundResponse)
async def start_round(
    room_id: uuid.UUID,
    session: SessionDep,
    membership: UnitMembershipDep,
    request_id: RequestIdDep,
    runner: RunnerDep,
    relay: RelayDep,
    transactions: TransactionsDep,
) -> RoundResponse:
    """(Owner) "Họp tiếp": one more round without writing anything."""
    service, access = _context(session, membership)
    view = await service.owned(access, room_id)
    if not relay.configured:
        raise AgentsNotConfiguredError()
    if runner.running(view.room.id) is not None:
        raise RoomBusyError()
    await service.record_round(access, view.room.id, request_id)
    await session.commit()
    return RoundResponse(started=runner.start(view.room.id, relay, transactions) is not None)


@router.post("/{room_id}/stop", response_model=StopResponse)
async def stop_round(
    room_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep, runner: RunnerDep
) -> StopResponse:
    """(Owner) "Dừng": the turn being written keeps what it said (CANCELLED), the
    rest of the round is skipped."""
    service, access = _context(session, membership)
    view = await service.view(access, room_id)
    if not view.is_owner:
        raise RoomForbiddenError()
    return StopResponse(stopped=await runner.stop(view.room.id))


@router.get("/{room_id}/events")
async def room_events(
    room_id: uuid.UUID, session: SessionDep, membership: UnitMembershipDep, runner: RunnerDep
) -> StreamingResponse:
    """The room live: ``state`` first, then ``message`` / ``tool`` / ``delta`` /
    ``done`` / ``round`` / ``people`` / ``deleted``, ``: keepalive`` while quiet."""
    service, access = _context(session, membership)
    view = await service.view(access, room_id)  # 404 before streaming anything
    await session.commit()

    async def stream() -> AsyncIterator[str]:
        async for event in runner.listen(view.room.id):
            if event is None:
                yield ": keepalive\n\n"
            else:
                yield sse(str(event["type"]), event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


__all__ = ["get_room_runner", "router"]
