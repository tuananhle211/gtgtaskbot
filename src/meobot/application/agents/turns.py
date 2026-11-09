"""Answers being written right now (Đội agent, 0091).

Each answer runs as its own task in the API process, apart from the browser's
connection: closing the tab or losing the network does not stop it - the
answer is still saved when the agent finishes. "Dừng" cancels the task, and
what came so far is kept as CANCELLED.

The registry is process-local (the API runs one worker): an answer row left
STREAMING after a restart is not here, and is marked FAILED ``interrupted``
the next time its conversation is read.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.agents.files import clean_name, image_type
from meobot.application.agents.service import AnswerFile, PreparedTurn, finish_answer
from meobot.core.time import utcnow
from meobot.db.models.agent_chat import AgentAttachment, AgentMessage
from meobot.domain.agents.media import (
    MAX_ANSWER_IMAGE_BYTES,
    file_type,
    find_html_pages,
    find_media,
    has_media,
    replace_media,
)
from meobot.domain.agents.models import ANSWER_TIMEOUT_SECONDS, AttachmentKind, MessageStatus
from meobot.integrations.hermes.relay import AgentRelay, RelayDelta, RelayError, RelayTool

logger = logging.getLogger(__name__)

TransactionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: The browser gets a comment line this often while the agent thinks, so no
#: proxy on the way closes an idle connection.
KEEPALIVE_SECONDS = 15.0


def attachment_url(attachment_id: object) -> str:
    return f"/api/agents/attachments/{attachment_id}"


def attachment_payload(row: AgentAttachment) -> dict[str, Any]:
    """One attached file as the API sends it (never its bytes nor its words)."""
    return {
        "id": str(row.id),
        "name": row.name,
        "kind": row.kind.value,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "pages": row.pages,
        "truncated": row.truncated,
        "note": row.note,
        "url": attachment_url(row.id),
    }


def message_payload(
    row: AgentMessage, attachments: list[AgentAttachment] | None = None
) -> dict[str, Any]:
    """One message as the API sends it (also inside the stream's last event)."""
    return {
        "id": str(row.id),
        "role": row.role.value,
        "content": row.content,
        "status": row.status.value,
        "error_code": row.error_code,
        "tools_used": list(row.tools_used or []),
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "attachments": [attachment_payload(item) for item in attachments or []],
    }


#: How long fetching an answer's pictures from the VPS may take, all together.
MEDIA_TIMEOUT_SECONDS = 60.0


async def answer_files(relay: AgentRelay, slug: str, text: str) -> tuple[str, list[AnswerFile]]:
    """Take the pictures and whole web pages out of an answer: ``MEDIA:`` paths
    fetched through the relay, ``data:`` URLs decoded, ```` ```html ```` pages kept
    as ``.html`` files. Returns the text without them, and the files. A picture
    the relay will not hand stays as a short note in the text; a page leaves a
    line naming its file."""
    replacements: dict[str, str] = {}
    files: list[AnswerFile] = []
    for number, page in enumerate(find_html_pages(text), start=1):
        name = f"giao-dien-{number}.html"
        files.append(
            AnswerFile(
                name=name,
                content_type="text/html",
                data=page.html.encode(),
                kind=AttachmentKind.DOCUMENT,
                note="Trang HTML · bấm Xem trước",
            )
        )
        replacements[page.raw] = f"*(Trang HTML: {name} - xem trước ở tệp bên dưới)*"
    text = replace_media(text, replacements) if replacements else text
    replacements = {}
    for ref in find_media(text):
        data, content_type = ref.data, None
        if ref.path is not None:
            media = await relay.fetch_media(slug, ref.path)
            data = media.data if media is not None else None
        elif data is None:  # a repeated path: shown once already
            replacements[ref.raw] = ""
            continue
        if data:
            content_type = file_type(ref.extension, data) if ref.is_file else image_type(data)
        if not data or content_type is None or len(data) > MAX_ANSWER_IMAGE_BYTES:
            what = "tệp" if ref.is_file else "ảnh"
            replacements[ref.raw] = f"(Không lấy được {what} {ref.name})"
            continue
        name = clean_name(ref.name)
        if "." not in name:
            name = f"{name}.{content_type.split('/', 1)[1].replace('jpeg', 'jpg')}"
        if ref.is_file:
            files.append(
                AnswerFile(
                    name=name,
                    content_type=content_type,
                    data=data,
                    kind=AttachmentKind.DOCUMENT,
                    note="Tệp agent gửi",
                )
            )
        else:
            files.append(AnswerFile(name=name, content_type=content_type, data=data))
        replacements[ref.raw] = ""
    return replace_media(text, replacements), files


@dataclass
class RunningTurn:
    answer_id: uuid.UUID
    conversation_id: uuid.UUID
    started_at: datetime
    text: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    #: The ``tool`` events so far, replayed to a listener who comes late.
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    final: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = None
    _queues: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)

    @property
    def partial(self) -> str:
        return "".join(self.text)

    def _final(self) -> dict[str, Any] | None:
        return self.final

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._queues):
            queue.put_nowait(event)

    def close(self) -> None:
        for queue in list(self._queues):
            queue.put_nowait(None)

    async def events(self) -> AsyncIterator[dict[str, Any] | None]:
        """What happened so far (the tools used, the text as one delta), then
        each new event; ``None`` = nothing for :data:`KEEPALIVE_SECONDS`
        (send a keepalive)."""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._queues.add(queue)
        try:
            for tool_event in list(self.tool_events):
                yield tool_event
            if self.partial:
                yield {"type": "delta", "text": self.partial}
            if self.final is not None:
                yield {"type": "done", "message": self.final}
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield None
                    continue
                if event is None:
                    final = self._final()  # set by the answer task meanwhile
                    if final is not None:
                        yield {"type": "done", "message": final}
                    return
                yield event
        finally:
            self._queues.discard(queue)


class TurnRegistry:
    """The answers running in this process."""

    def __init__(self) -> None:
        self._turns: dict[uuid.UUID, RunningTurn] = {}
        #: Conversations whose question is being recorded (before the answer starts).
        self._reserved: set[uuid.UUID] = set()

    def reserve(self, conversation_id: uuid.UUID) -> bool:
        """Claim the conversation for one new answer; False = one is already on its way."""
        if conversation_id in self._reserved or self.running_for(conversation_id) is not None:
            return False
        self._reserved.add(conversation_id)
        return True

    def release(self, conversation_id: uuid.UUID) -> None:
        self._reserved.discard(conversation_id)

    def running_for(self, conversation_id: uuid.UUID) -> RunningTurn | None:
        for turn in self._turns.values():
            if turn.conversation_id == conversation_id and turn.final is None:
                return turn
        return None

    def get(self, answer_id: uuid.UUID) -> RunningTurn | None:
        return self._turns.get(answer_id)

    def running_ids(self) -> set[uuid.UUID]:
        return set(self._turns)

    def start(
        self, prepared: PreparedTurn, relay: AgentRelay, transactions: TransactionFactory
    ) -> RunningTurn:
        turn = RunningTurn(
            answer_id=prepared.answer_id,
            conversation_id=prepared.conversation_id,
            started_at=utcnow(),
        )
        self._turns[turn.answer_id] = turn
        self._reserved.discard(prepared.conversation_id)
        turn.task = asyncio.create_task(self._run(turn, prepared, relay, transactions))
        return turn

    async def stop(self, conversation_id: uuid.UUID) -> bool:
        turn = self.running_for(conversation_id)
        if turn is None or turn.task is None:
            return False
        turn.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await turn.task
        return True

    async def _run(
        self,
        turn: RunningTurn,
        prepared: PreparedTurn,
        relay: AgentRelay,
        transactions: TransactionFactory,
    ) -> None:
        status, error = MessageStatus.DONE, None
        try:
            async with asyncio.timeout(ANSWER_TIMEOUT_SECONDS):
                async for event in relay.stream_chat(
                    prepared.slug,
                    text=prepared.prompt,
                    session_id=prepared.session_id,
                    session_key=prepared.session_key,
                    idempotency_key=str(prepared.user_message.id),
                    images=prepared.images,
                ):
                    if isinstance(event, RelayDelta):
                        turn.text.append(event.text)
                        turn.publish({"type": "delta", "text": event.text})
                    elif isinstance(event, RelayTool):
                        if event.tool not in turn.tools:
                            turn.tools.append(event.tool)
                        tool_event = {
                            "type": "tool",
                            "tool": event.tool,
                            "label": event.label,
                            "emoji": event.emoji,
                        }
                        turn.tool_events.append(tool_event)
                        turn.publish(tool_event)
            if not turn.partial.strip():
                status, error = MessageStatus.FAILED, "empty_answer"
        except asyncio.CancelledError:
            status, error = MessageStatus.CANCELLED, None
        except TimeoutError:
            status, error = MessageStatus.FAILED, "timeout"
        except RelayError as exc:
            status, error = MessageStatus.FAILED, exc.code
        except Exception:  # never leave the row STREAMING
            logger.exception("agent answer failed", extra={"answer_id": str(turn.answer_id)})
            status, error = MessageStatus.FAILED, "agent_error"
        content: str = turn.partial
        files: list[AnswerFile] = []
        if status != MessageStatus.FAILED and has_media(content):
            try:
                async with asyncio.timeout(MEDIA_TIMEOUT_SECONDS):
                    content, files = await answer_files(relay, prepared.slug, content)
            except Exception:  # the words are still worth keeping
                logger.exception(
                    "taking an answer's files failed", extra={"answer_id": str(turn.answer_id)}
                )
                content, files = turn.partial, []
        try:
            async with transactions() as session:
                saved = await finish_answer(
                    session,
                    turn.answer_id,
                    content=content,
                    status=status,
                    error_code=error,
                    tools=turn.tools,
                    started_at=turn.started_at,
                    files=files,
                )
                turn.final = message_payload(*saved) if saved is not None else None
        except Exception:
            logger.exception(
                "saving an agent answer failed", extra={"answer_id": str(turn.answer_id)}
            )
        if turn.final is None:
            turn.final = {
                "id": str(turn.answer_id),
                "role": "ASSISTANT",
                "content": content,
                "status": status.value,
                "error_code": error,
                "tools_used": list(turn.tools),
                "duration_ms": None,
                "created_at": None,
                "finished_at": None,
                "attachments": [],
            }
        turn.close()
        self._turns.pop(turn.answer_id, None)


__all__ = [
    "KEEPALIVE_SECONDS",
    "RunningTurn",
    "TransactionFactory",
    "TurnRegistry",
    "answer_files",
    "attachment_payload",
    "attachment_url",
    "message_payload",
]
