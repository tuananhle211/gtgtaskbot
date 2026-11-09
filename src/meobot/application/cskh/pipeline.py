"""From a Pancake webhook to an answer (0094). See :mod:`meobot.domain.pancake.models`.

1. :func:`take_webhook` (in the webhook request): a customer's inbox message
   becomes a ``WAITING`` reply (once per Pancake message id); a page message
   that APM did not send = a staff member answered on Pancake → the bot keeps
   quiet in that conversation for ``STAFF_PAUSE_HOURS`` and drops what it had
   pending there; a ``conversation`` event refreshes the tags / assignees.
2. :class:`CskhRunner` (a task in the API process, like the agents' answers):
   waits ``DEBOUNCE_SECONDS`` after the last message of a conversation, folds the
   waiting rows into the newest, reads the thread from Pancake, asks the agent,
   checks the answer against the ORD script rules, then sends it (``AUTO`` with
   nothing in the way) or keeps it as a suggestion.
3. :class:`CskhInbox`: a person reads the thread (live from Pancake, never
   stored), sends the suggestion as is or edited, or dismisses it.

Each step saves on its own transaction (``transactions``); Pancake and the
agent are called between them, never inside one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.cskh.access import CskhAccess
from meobot.application.cskh.pages import read_token
from meobot.core.errors import ConfigurationError
from meobot.core.secrets import SecretBox
from meobot.core.time import utcnow
from meobot.db.models.org_unit import OrgUnit
from meobot.db.models.pancake import PancakeConversation, PancakePage, PancakeReply
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.cskh.errors import (
    CskhInvalidError,
    CskhPancakeError,
    CskhReplyDecidedError,
    CskhReplyNotFoundError,
)
from meobot.domain.pancake.models import (
    ANSWER_TIMEOUT_SECONDS,
    DEBOUNCE_SECONDS,
    FIRST_TURN_MESSAGES,
    HANDOFF_PAUSE_HOURS,
    MAX_REPLY_CHARS,
    STAFF_PAUSE_HOURS,
    STALE_MINUTES,
    THREAD_MESSAGES,
    PageRules,
    PancakeError,
    ReplyMode,
    ReplyStatus,
    Thread,
    ThreadMessage,
    WebhookConversation,
    WebhookMessage,
    customer_turn,
    parse_webhook,
    read_answer,
)
from meobot.domain.units.models import UnitCode, UnitSettings
from meobot.integrations.hermes.relay import AgentRelay, RelayDelta, RelayError
from meobot.integrations.pancake.client import PancakeClient

logger = logging.getLogger(__name__)

TransactionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
#: How many of its own sent message ids a conversation remembers.
SENT_IDS_KEPT = 50
#: A page message this soon after APM sent one is taken for its echo.
ECHO_SECONDS = 60


@dataclass(frozen=True, slots=True)
class Review:
    """The AI check of an answer: passed True / False, None = could not run."""

    passed: bool | None
    violations: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None


#: answer text, rules -> review.
Reviewer = Callable[[str, str], Awaitable[Review]]


def script_reviewer(llm: Any) -> Reviewer:
    """The ORD script check (same rules, same AI route) on a customer answer."""
    from meobot.application.orders.script_review import OrderScriptReviewer

    checker = OrderScriptReviewer(llm, timeout_seconds=60.0)

    async def review(text: str, rules: str) -> Review:
        verdict = await checker.review(
            title="Tin nhắn trả lời khách trên fanpage", script=text, rules=rules
        )
        if not verdict.reviewed:
            return Review(passed=None, note=f"AI chưa kiểm tra được ({verdict.problem}).")
        return Review(
            passed=verdict.passed,
            violations=[item.to_json() for item in verdict.violations],
            note=verdict.note,
        )

    return review


@dataclass(slots=True)
class CskhDeps:
    relay: AgentRelay
    client: PancakeClient
    transactions: TransactionFactory
    secret_box: Callable[[], SecretBox]
    reviewer: Reviewer | None = None
    debounce: float = DEBOUNCE_SECONDS


# --- 1. the webhook -----------------------------------------------------------------------


async def _conversation(
    session: AsyncSession, page: PancakePage, conversation_id: str
) -> PancakeConversation:
    row = await session.scalar(
        select(PancakeConversation).where(
            PancakeConversation.page_row_id == page.id,
            PancakeConversation.conversation_id == conversation_id,
        )
    )
    if row is not None:
        return row
    row = PancakeConversation(
        id=uuid.uuid4(),
        page_row_id=page.id,
        conversation_id=conversation_id,
        tag_ids=[],
        assignee_ids=[],
        sent_message_ids=[],
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(PancakeConversation).where(
                PancakeConversation.page_row_id == page.id,
                PancakeConversation.conversation_id == conversation_id,
            )
        )
        assert existing is not None
        return existing
    return row


async def _is_echo(session: AsyncSession, conv: PancakeConversation, message_id: str) -> bool:
    """A page message APM sent itself (its id, or the moments while it was sending)."""
    if message_id in {str(v) for v in conv.sent_message_ids or []}:
        return True
    recent = await session.scalar(
        select(PancakeReply.id).where(
            PancakeReply.conversation_row_id == conv.id,
            (PancakeReply.status == ReplyStatus.ANSWERING)
            | (PancakeReply.sent_at >= utcnow() - timedelta(seconds=ECHO_SECONDS)),
        )
    )
    return recent is not None


async def take_webhook(session: AsyncSession, key: str, payload: Any) -> uuid.UUID | None:
    """Pancake's event; the conversation to answer, or None.

    Raises ``LookupError`` for an unknown key (the route answers 404).
    """
    page = await session.scalar(select(PancakePage).where(PancakePage.webhook_key == key))
    if page is None:
        raise LookupError(key)
    page.last_event_at = utcnow()
    event = parse_webhook(payload)
    if event is None or event.page_id != page.page_id:
        return None
    conv = await _conversation(session, page, event.conversation_id)
    if event.tag_ids or isinstance(event, WebhookConversation):
        conv.tag_ids = list(event.tag_ids)
    if event.assignee_ids or isinstance(event, WebhookConversation):
        conv.assignee_ids = list(event.assignee_ids)
    if isinstance(event, WebhookConversation):
        await session.flush()
        return None
    assert isinstance(event, WebhookMessage)
    if event.conversation_type and event.conversation_type != "INBOX":
        await session.flush()
        return None
    if event.from_page:
        if not await _is_echo(session, conv, event.message_id):
            await _staff_replied(session, conv)
        await session.flush()
        return None
    if not page.active:
        await session.flush()
        return None
    reply = PancakeReply(
        id=uuid.uuid4(),
        page_row_id=page.id,
        conversation_row_id=conv.id,
        customer_message_id=event.message_id,
        customer_at=event.at or utcnow(),
        status=ReplyStatus.WAITING,
        violations=[],
    )
    try:
        async with session.begin_nested():
            session.add(reply)
            await session.flush()
    except IntegrityError:
        return None  # delivered twice
    return conv.id


async def _staff_replied(session: AsyncSession, conv: PancakeConversation) -> None:
    now = utcnow()
    conv.paused_until = now + timedelta(hours=STAFF_PAUSE_HOURS)
    conv.pause_reason = "staff_replied"
    await session.execute(
        update(PancakeReply)
        .where(
            PancakeReply.conversation_row_id == conv.id,
            PancakeReply.status.in_([ReplyStatus.WAITING, ReplyStatus.SUGGESTED]),
        )
        .values(status=ReplyStatus.SKIPPED, reason="staff_replied", updated_at=now)
        .execution_options(synchronize_session="fetch")
    )


# --- 2. answering ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Job:
    reply_id: uuid.UUID
    conv_id: uuid.UUID
    page_id: str
    page_name: str
    conversation_id: str
    agent_slug: str
    mode: ReplyMode
    token: str
    seen_message_id: str | None
    sent_ids: frozenset[str]
    rules: str


def _rules_of(unit: OrgUnit | None) -> str:
    return UnitSettings.model_validate((unit.settings if unit else None) or {}).script_review_rules


def _rules(page: PancakePage) -> PageRules:
    return PageRules(
        handoff_tag_ids=frozenset(str(t) for t in page.handoff_tag_ids or []),
        stop_when_assigned=page.stop_when_assigned,
    )


async def _close(reply: PancakeReply, status: ReplyStatus, reason: str | None) -> None:
    reply.status, reply.reason = status, reason


async def _begin(session: AsyncSession, conv_id: uuid.UUID, deps: CskhDeps) -> _Job | None:
    conv = await session.get(PancakeConversation, conv_id)
    if conv is None:
        return None
    page = await session.get(PancakePage, conv.page_row_id)
    waiting = list(
        (
            await session.scalars(
                select(PancakeReply)
                .where(
                    PancakeReply.conversation_row_id == conv_id,
                    PancakeReply.status == ReplyStatus.WAITING,
                )
                .order_by(PancakeReply.customer_at, PancakeReply.created_at)
            )
        ).all()
    )
    if not waiting or page is None:
        return None
    latest = waiting[-1]
    for older in waiting[:-1]:
        await _close(older, ReplyStatus.SKIPPED, "merged")
    if not page.active:
        await _close(latest, ReplyStatus.SKIPPED, "page_off")
        return None
    paused = _rules(page).paused_by(
        tag_ids=[str(t) for t in conv.tag_ids or []],
        assignee_ids=[str(a) for a in conv.assignee_ids or []],
        paused_until=conv.paused_until,
        now=utcnow(),
    )
    if paused is not None:
        await _close(latest, ReplyStatus.SKIPPED, paused.reason)
        return None
    try:
        token = read_token(page, deps.secret_box())
    except ConfigurationError:
        token = None
    if not token:
        await _close(latest, ReplyStatus.FAILED, "token_missing")
        return None
    latest.status = ReplyStatus.ANSWERING
    unit = await session.scalar(select(OrgUnit).where(OrgUnit.code == UnitCode.ADS))
    await session.flush()
    return _Job(
        reply_id=latest.id,
        conv_id=conv.id,
        page_id=page.page_id,
        page_name=page.name,
        conversation_id=conv.conversation_id,
        agent_slug=page.agent_slug,
        mode=page.mode,
        token=token,
        seen_message_id=conv.seen_message_id,
        sent_ids=frozenset(str(v) for v in conv.sent_message_ids or []),
        rules=_rules_of(unit),
    )


def _new_messages(thread: Thread, job: _Job) -> list[ThreadMessage]:
    """What the agent has not seen: after its last message, else the last few."""
    messages = [m for m in thread.messages if m.id not in job.sent_ids]
    ids = [m.id for m in thread.messages]
    if job.seen_message_id and job.seen_message_id in ids:
        cut = ids.index(job.seen_message_id) + 1
        seen = {m.id for m in thread.messages[:cut]}
        return [m for m in messages if m.id not in seen]
    return messages[-FIRST_TURN_MESSAGES:]


async def _ask_agent(deps: CskhDeps, job: _Job, text: str) -> str:
    parts: list[str] = []

    async def read() -> None:
        async for event in deps.relay.stream_chat(
            job.agent_slug,
            text=text,
            session_id=f"apm-pk-{job.conv_id}",
            session_key=f"apm:cskh:{job.conv_id}",
            idempotency_key=f"pk-{job.reply_id}",
        ):
            if isinstance(event, RelayDelta):
                parts.append(event.text)

    await asyncio.wait_for(read(), timeout=ANSWER_TIMEOUT_SECONDS)
    return "".join(parts)


async def _fail(deps: CskhDeps, reply_id: uuid.UUID, status: ReplyStatus, reason: str) -> None:
    async with deps.transactions() as session:
        reply = await session.get(PancakeReply, reply_id)
        if reply is not None and reply.status == ReplyStatus.ANSWERING:
            reply.status, reply.reason = status, reason
            await session.flush()


def _remember_sent(conv: PancakeConversation, message_id: str) -> None:
    if message_id:
        conv.sent_message_ids = [*list(conv.sent_message_ids or []), message_id][-SENT_IDS_KEPT:]


async def answer_conversation(conv_id: uuid.UUID, deps: CskhDeps) -> None:
    """One pass over a conversation's waiting messages (see the module doc)."""
    async with deps.transactions() as session:
        job = await _begin(session, conv_id, deps)
    if job is None:
        return
    try:
        thread = await deps.client.thread(job.page_id, job.conversation_id, job.token)
    except PancakeError as error:
        await _fail(deps, job.reply_id, ReplyStatus.FAILED, error.code[:40])
        return
    if not thread.can_inbox:
        await _fail(deps, job.reply_id, ReplyStatus.SKIPPED, "outside_window")
        return
    fresh = _new_messages(thread, job)
    if not any(not m.from_page for m in fresh):
        await _fail(deps, job.reply_id, ReplyStatus.SKIPPED, "no_customer_message")
        return
    question = customer_turn(fresh, page_name=job.page_name, first=job.seen_message_id is None)
    try:
        raw = await _ask_agent(deps, job, question)
    except TimeoutError:
        await _fail(deps, job.reply_id, ReplyStatus.FAILED, "agent_timeout")
        return
    except RelayError as error:
        reason = (
            "agents_not_configured" if error.code == "agents_not_configured" else "agent_failed"
        )
        await _fail(deps, job.reply_id, ReplyStatus.FAILED, reason)
        return
    answer = read_answer(raw)
    if not answer.text:
        await _fail(deps, job.reply_id, ReplyStatus.FAILED, "empty_answer")
        return
    review = (
        await deps.reviewer(answer.text, job.rules)
        if deps.reviewer is not None
        else Review(passed=None, note="Chưa có AI kiểm tra luật.")
    )
    send_now = False
    async with deps.transactions() as session:
        reply = await session.get(PancakeReply, job.reply_id)
        conv = await session.get(PancakeConversation, job.conv_id)
        if reply is None or conv is None or reply.status != ReplyStatus.ANSWERING:
            return  # staff answered meanwhile, or the page was removed
        now = utcnow()
        last = fresh[-1] if fresh else None
        if last is not None:
            conv.seen_message_id, conv.seen_at = last.id, last.at or now
        reply.answered_at = now
        reply.suggested_text = answer.text
        reply.handoff = answer.handoff
        reply.handoff_reason = answer.handoff_reason or None
        reply.violations = review.violations
        reply.review_passed = review.passed
        reply.review_note = (review.note or "")[:300] or None
        if answer.handoff:
            conv.paused_until = now + timedelta(hours=HANDOFF_PAUSE_HOURS)
            conv.pause_reason = "handoff"
        send_now = job.mode is ReplyMode.AUTO and not answer.handoff and review.passed is True
        if not send_now:
            reply.status = ReplyStatus.SUGGESTED
        await session.flush()
    if not send_now:
        return
    try:
        message_id = await deps.client.send(
            job.page_id, job.conversation_id, job.token, answer.text
        )
    except PancakeError as error:
        async with deps.transactions() as session:
            reply = await session.get(PancakeReply, job.reply_id)
            if reply is not None:
                reply.status, reply.reason = ReplyStatus.SUGGESTED, error.code[:40]
                await session.flush()
        return
    async with deps.transactions() as session:
        reply = await session.get(PancakeReply, job.reply_id)
        conv = await session.get(PancakeConversation, job.conv_id)
        if reply is not None:
            reply.status, reply.reason = ReplyStatus.SENT, None
            reply.sent_text, reply.sent_at, reply.sent_message_id = (
                answer.text,
                utcnow(),
                message_id,
            )
        if conv is not None:
            _remember_sent(conv, message_id)
        await session.flush()


class CskhRunner:
    """One answering task per conversation in this API process.

    A message arriving while the conversation is being answered is answered
    right after (``_again``), so nothing waits for the next webhook.
    """

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._again: set[uuid.UUID] = set()

    def running(self, conv_id: uuid.UUID) -> bool:
        return conv_id in self._tasks

    def schedule(self, conv_id: uuid.UUID, deps: CskhDeps) -> None:
        if conv_id in self._tasks:
            self._again.add(conv_id)
            return
        self._tasks[conv_id] = asyncio.create_task(self._run(conv_id, deps))

    async def _run(self, conv_id: uuid.UUID, deps: CskhDeps) -> None:
        try:
            while True:
                self._again.discard(conv_id)
                if deps.debounce > 0:
                    await asyncio.sleep(deps.debounce)
                try:
                    await answer_conversation(conv_id, deps)
                except Exception:
                    logger.exception("cskh answer failed", extra={"conversation": str(conv_id)})
                if conv_id not in self._again:
                    break
        finally:
            self._tasks.pop(conv_id, None)

    async def wait(self) -> None:
        """Every running task to its end (tests)."""
        while self._tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*list(self._tasks.values()))


# --- 3. the inbox ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplyRow:
    reply: PancakeReply
    page: PancakePage
    conversation: PancakeConversation
    decided_by: str | None = None


class CskhInbox:
    def __init__(
        self, session: AsyncSession, audit: AuditService, secret_box: Callable[[], SecretBox]
    ) -> None:
        self._session = session
        self._audit = audit
        self._secret_box = secret_box

    async def _expire_stale(self) -> None:
        """Rows a restart cut off (no task is running for them any more)."""
        cutoff = utcnow() - timedelta(minutes=STALE_MINUTES)
        await self._session.execute(
            update(PancakeReply)
            .where(
                PancakeReply.status.in_([ReplyStatus.WAITING, ReplyStatus.ANSWERING]),
                PancakeReply.updated_at < cutoff,
            )
            .values(status=ReplyStatus.FAILED, reason="interrupted", updated_at=utcnow())
            .execution_options(synchronize_session="fetch")
        )

    async def rows(
        self,
        access: CskhAccess,
        *,
        tab: str = "waiting",
        page_row_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[ReplyRow], dict[str, int]]:
        access.require_reply()
        await self._expire_stale()
        statuses = {
            "waiting": [ReplyStatus.SUGGESTED],
            "sent": [ReplyStatus.SENT],
            "other": [
                ReplyStatus.WAITING,
                ReplyStatus.ANSWERING,
                ReplyStatus.DISMISSED,
                ReplyStatus.SKIPPED,
                ReplyStatus.FAILED,
            ],
        }.get(tab, [ReplyStatus.SUGGESTED])
        query = (
            select(PancakeReply, PancakePage, PancakeConversation, User.full_name)
            .join(PancakePage, PancakePage.id == PancakeReply.page_row_id)
            .join(PancakeConversation, PancakeConversation.id == PancakeReply.conversation_row_id)
            .outerjoin(User, User.id == PancakeReply.decided_by_user_id)
            .where(PancakeReply.status.in_(statuses))
        )
        if page_row_id is not None:
            query = query.where(PancakeReply.page_row_id == page_row_id)
        newest_first = (
            PancakeReply.updated_at.desc() if tab != "waiting" else PancakeReply.created_at
        )
        rows = (await self._session.execute(query.order_by(newest_first).limit(limit))).all()
        counts: dict[str, int] = {}
        for status, count in (
            await self._session.execute(
                select(PancakeReply.status, func.count()).group_by(PancakeReply.status)
            )
        ).all():
            counts[str(getattr(status, "value", status))] = int(count)
        return [ReplyRow(r, p, c, name) for r, p, c, name in rows], counts

    async def _row(
        self, reply_id: uuid.UUID
    ) -> tuple[PancakeReply, PancakePage, PancakeConversation]:
        reply = await self._session.get(PancakeReply, reply_id)
        if reply is None:
            raise CskhReplyNotFoundError()
        page = await self._session.get(PancakePage, reply.page_row_id)
        conv = await self._session.get(PancakeConversation, reply.conversation_row_id)
        if page is None or conv is None:
            raise CskhReplyNotFoundError()
        return reply, page, conv

    def _token(self, page: PancakePage) -> str:
        try:
            token = read_token(page, self._secret_box())
        except ConfigurationError:
            token = None
        if not token:
            raise CskhPancakeError("token_missing", "Page chưa có token, chưa đọc / gửi được.")
        return token

    async def thread(
        self, access: CskhAccess, reply_id: uuid.UUID, client: PancakeClient
    ) -> list[ThreadMessage]:
        """The conversation's last messages, read now from Pancake (never stored)."""
        access.require_reply()
        _reply, page, conv = await self._row(reply_id)
        try:
            thread = await client.thread(page.page_id, conv.conversation_id, self._token(page))
        except PancakeError as error:
            raise CskhPancakeError(error.code, error.message) from error
        return list(thread.messages[-THREAD_MESSAGES:])

    async def send(
        self,
        access: CskhAccess,
        reply_id: uuid.UUID,
        text: str,
        client: PancakeClient,
        *,
        request_id: uuid.UUID,
    ) -> PancakeReply:
        access.require_reply()
        reply, page, conv = await self._row(reply_id)
        if reply.status != ReplyStatus.SUGGESTED:
            raise CskhReplyDecidedError()
        body = (text or "").strip()
        if not body or len(body) > MAX_REPLY_CHARS:
            raise CskhInvalidError(
                "cskh_text_invalid",
                f"Nội dung gửi khách không được trống, tối đa {MAX_REPLY_CHARS} ký tự.",
                field="text",
            )
        # Claimed before calling Pancake: a second click / person gets 409.
        claimed = await self._session.execute(
            update(PancakeReply)
            .where(PancakeReply.id == reply.id, PancakeReply.status == ReplyStatus.SUGGESTED)
            .values(status=ReplyStatus.ANSWERING, updated_at=utcnow())
            .execution_options(synchronize_session="fetch")
        )
        if getattr(claimed, "rowcount", 1) == 0:
            raise CskhReplyDecidedError()
        try:
            message_id = await client.send(
                page.page_id, conv.conversation_id, self._token(page), body
            )
        except (PancakeError, CskhPancakeError) as error:
            reply.status = ReplyStatus.SUGGESTED
            await self._session.flush()
            if isinstance(error, CskhPancakeError):
                raise
            raise CskhPancakeError(error.code, error.message) from error
        await self._session.refresh(reply)
        reply.status, reply.reason = ReplyStatus.SENT, None
        reply.sent_text, reply.sent_at, reply.sent_message_id = body, utcnow(), message_id
        reply.decided_by_user_id = access.user_id
        _remember_sent(conv, message_id)
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=AuditAction.CSKH_REPLY_SENT,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="pancake_reply",
                entity_id=str(reply.id),
                after_data={"page_id": page.page_id, "edited": body != reply.suggested_text},
            )
        )
        return reply

    async def dismiss(
        self, access: CskhAccess, reply_id: uuid.UUID, *, request_id: uuid.UUID
    ) -> PancakeReply:
        access.require_reply()
        reply, page, _conv = await self._row(reply_id)
        if reply.status != ReplyStatus.SUGGESTED:
            raise CskhReplyDecidedError()
        reply.status, reply.decided_by_user_id = ReplyStatus.DISMISSED, access.user_id
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=AuditAction.CSKH_REPLY_DISMISSED,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="pancake_reply",
                entity_id=str(reply.id),
                after_data={"page_id": page.page_id},
            )
        )
        return reply

    async def resume(self, access: CskhAccess, conversation_row_id: uuid.UUID) -> None:
        """ "Cho bot trả lời lại": lift a conversation's pause."""
        access.require_reply()
        conv = await self._session.get(PancakeConversation, conversation_row_id)
        if conv is None:
            raise CskhReplyNotFoundError()
        conv.paused_until, conv.pause_reason = None, None
        await self._session.flush()


def paused_label(conv: PancakeConversation, now: datetime | None = None) -> str | None:
    now = now or utcnow()
    if conv.paused_until is not None and conv.paused_until > now:
        return conv.pause_reason or "paused"
    return None


__all__ = [
    "CskhDeps",
    "CskhInbox",
    "CskhRunner",
    "ReplyRow",
    "Review",
    "Reviewer",
    "answer_conversation",
    "paused_label",
    "script_reviewer",
    "take_webhook",
]
