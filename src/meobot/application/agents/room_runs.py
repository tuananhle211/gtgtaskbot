"""Phòng họp agent (0093): the rounds running now, and who is watching each room.

A round runs as its own task in the API process (like a single answer, see
:mod:`.turns`): the chair opens, every other agent speaks, the chair closes,
one after another, each turn saved on its own transaction. Everybody with the
room open listens to the room's events (``GET /rooms/{id}/events``):

* ``state`` - on connecting: whether a round runs, the turn being written and
  what it said so far;
* ``message`` - a new message (a person's, or an agent's turn that starts);
* ``tool`` / ``delta`` - the agent speaking uses a tool / says more;
* ``done`` - a turn ended (the message as saved, whatever its status);
* ``round`` - a round started (``running: true``) or ended.

Process-local (the API runs one worker): a turn left STREAMING by a restart is
marked FAILED ``interrupted`` the next time the room is read.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from meobot.application.agents.rooms import (
    PreparedRoomTurn,
    begin_round,
    finish_turn,
    open_turn,
)
from meobot.application.agents.turns import KEEPALIVE_SECONDS, TransactionFactory
from meobot.core.time import utcnow
from meobot.db.models.agent_room import AgentRoomMessage
from meobot.domain.agents.models import ANSWER_TIMEOUT_SECONDS, MessageStatus
from meobot.domain.agents.rooms import STEP_LABELS
from meobot.integrations.hermes.relay import AgentRelay, RelayDelta, RelayError, RelayTool

logger = logging.getLogger(__name__)


def room_message_payload(row: AgentRoomMessage, agent_slug: str | None = None) -> dict[str, Any]:
    """One room message as the API sends it (also inside the events)."""
    return {
        "id": str(row.id),
        "speaker": row.speaker.value,
        "user_id": str(row.user_id) if row.user_id else None,
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "agent_slug": agent_slug,
        "speaker_label": row.speaker_label,
        "round_no": row.round_no,
        "step": row.step.value if row.step else None,
        "step_label": STEP_LABELS[row.step] if row.step else None,
        "content": row.content,
        "status": row.status.value,
        "error_code": row.error_code,
        "tools_used": list(row.tools_used or []),
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@dataclass
class RoomRun:
    room_id: uuid.UUID
    round_no: int | None = None
    message_id: uuid.UUID | None = None
    text: list[str] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    stopping: bool = False
    task: asyncio.Task[None] | None = None

    @property
    def partial(self) -> str:
        return "".join(self.text)


class RoomRunner:
    """The rounds running in this process, and the rooms' listeners."""

    def __init__(self) -> None:
        self._runs: dict[uuid.UUID, RoomRun] = {}
        self._listeners: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any]]]] = {}

    # --- listening ----------------------------------------------------------------------

    def publish(self, room_id: uuid.UUID, event: dict[str, Any]) -> None:
        for queue in list(self._listeners.get(room_id, ())):
            queue.put_nowait(event)

    def state(self, room_id: uuid.UUID) -> dict[str, Any]:
        run = self._runs.get(room_id)
        return {
            "type": "state",
            "running": run is not None,
            "round": run.round_no if run else None,
            "message_id": str(run.message_id) if run and run.message_id else None,
            "text": run.partial if run else "",
            "tools": list(run.tool_events) if run else [],
        }

    async def listen(self, room_id: uuid.UUID) -> AsyncIterator[dict[str, Any] | None]:
        """``state`` first, then each event; ``None`` = nothing for a while (keepalive)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._listeners.setdefault(room_id, set()).add(queue)
        try:
            yield self.state(room_id)
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield None
        finally:
            listeners = self._listeners.get(room_id)
            if listeners is not None:
                listeners.discard(queue)
                if not listeners:
                    self._listeners.pop(room_id, None)

    # --- rounds -------------------------------------------------------------------------

    def running(self, room_id: uuid.UUID) -> RoomRun | None:
        return self._runs.get(room_id)

    def running_message(self, room_id: uuid.UUID) -> uuid.UUID | None:
        run = self._runs.get(room_id)
        return run.message_id if run else None

    def start(
        self, room_id: uuid.UUID, relay: AgentRelay, transactions: TransactionFactory
    ) -> RoomRun | None:
        """Start a round; None = one is already running."""
        if room_id in self._runs:
            return None
        run = RoomRun(room_id=room_id)
        self._runs[room_id] = run
        run.task = asyncio.create_task(self.run_round(run, relay, transactions))
        return run

    async def stop(self, room_id: uuid.UUID) -> bool:
        run = self._runs.get(room_id)
        if run is None or run.task is None:
            return False
        run.stopping = True
        run.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run.task
        return True

    async def run_round(
        self, run: RoomRun, relay: AgentRelay, transactions: TransactionFactory
    ) -> None:
        """The chair opens, the others speak, the chair closes - one turn at a time."""
        room_id = run.room_id
        self._runs.setdefault(room_id, run)
        try:
            async with transactions() as session:
                start = await begin_round(session, room_id)
            if start is None:
                return
            run.round_no = start.round_no
            self.publish(room_id, {"type": "round", "running": True, "round": start.round_no})
            for turn, step in start.plan:
                if run.stopping:
                    break
                async with transactions() as session:
                    prepared = await open_turn(session, room_id, turn, step, start.round_no)
                    payload = (
                        room_message_payload(prepared.message, prepared.slug) if prepared else None
                    )
                if prepared is None or payload is None:
                    continue
                run.message_id, run.text, run.tool_events = prepared.message.id, [], []
                self.publish(room_id, {"type": "message", "message": payload})
                await self._speak(run, prepared.message.id, prepared, relay, transactions)
        except asyncio.CancelledError:
            run.stopping = True
        except Exception:
            logger.exception("agent room round failed", extra={"room_id": str(room_id)})
        finally:
            self._runs.pop(room_id, None)
            self.publish(room_id, {"type": "round", "running": False, "round": run.round_no})

    async def _speak(
        self,
        run: RoomRun,
        message_id: uuid.UUID,
        prepared: PreparedRoomTurn,
        relay: AgentRelay,
        transactions: TransactionFactory,
    ) -> None:
        started: datetime = utcnow()
        tools: list[str] = []
        status, error = MessageStatus.DONE, None
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT_SECONDS):
                async for event in relay.stream_chat(
                    prepared.slug,
                    text=prepared.prompt,
                    session_id=prepared.session_id,
                    session_key=prepared.session_key,
                    idempotency_key=str(message_id),
                ):
                    if isinstance(event, RelayDelta):
                        run.text.append(event.text)
                        self.publish(
                            run.room_id,
                            {"type": "delta", "message_id": str(message_id), "text": event.text},
                        )
                    elif isinstance(event, RelayTool):
                        if event.tool not in tools:
                            tools.append(event.tool)
                        tool_event = {
                            "type": "tool",
                            "message_id": str(message_id),
                            "tool": event.tool,
                            "label": event.label,
                            "emoji": event.emoji,
                        }
                        run.tool_events.append(tool_event)
                        self.publish(run.room_id, tool_event)
            if not run.partial.strip():
                status, error = MessageStatus.FAILED, "empty_answer"
        except asyncio.CancelledError:
            status, error = MessageStatus.CANCELLED, None
            run.stopping = True
        except TimeoutError:
            status, error = MessageStatus.FAILED, "timeout"
        except RelayError as exc:
            status, error = MessageStatus.FAILED, exc.code
        except Exception:  # never leave the row STREAMING
            logger.exception("agent room turn failed", extra={"message_id": str(message_id)})
            status, error = MessageStatus.FAILED, "agent_error"
        final: dict[str, Any] | None = None
        try:
            async with transactions() as session:
                row = await finish_turn(
                    session,
                    message_id,
                    content=run.partial,
                    status=status,
                    error_code=error,
                    tools=tools,
                    started_at=started,
                )
                final = room_message_payload(row, prepared.slug) if row is not None else None
        except Exception:
            logger.exception(
                "saving an agent room turn failed", extra={"message_id": str(message_id)}
            )
        run.message_id, run.text, run.tool_events = None, [], []
        if final is not None:
            self.publish(run.room_id, {"type": "done", "message": final})


__all__ = ["RoomRun", "RoomRunner", "room_message_payload"]
