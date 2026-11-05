"""Đội agent (0091): agents, conversations, messages - everything but the stream.

The answer itself streams from the relay in :mod:`.turns`; this service
prepares a turn (checks, the question row, an empty answer row) and records
how it ended, each on whatever session it is handed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.agents.access import AgentAccess
from meobot.application.audit_service import AuditService
from meobot.core.time import utcnow
from meobot.db.models.agent_chat import AgentConversation, AgentMessage, AgentProfile
from meobot.db.models.department import Department
from meobot.db.models.user import User
from meobot.domain.agents.errors import (
    AgentAccessInvalidError,
    AgentMessageInvalidError,
    AgentNotFoundError,
    AgentRateLimitedError,
    ConversationNotFoundError,
)
from meobot.domain.agents.models import (
    DEFAULT_AGENTS,
    MAX_MESSAGE_CHARS,
    RATE_LIMIT,
    RATE_WINDOW_SECONDS,
    MessageRole,
    MessageStatus,
    conversation_title,
    hermes_session_id,
    hermes_session_key,
)
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.org.models import Seat, seat_label

NEW_TITLE = "Cuộc trò chuyện mới"


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    """What the stream needs once the request's transaction is committed."""

    conversation_id: uuid.UUID
    slug: str
    user_message: AgentMessage
    answer_id: uuid.UUID
    prompt: str
    session_id: str
    session_key: str


@dataclass(frozen=True, slots=True)
class AgentSummary:
    agent: AgentProfile
    last_message_at: datetime | None


class AgentChatService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- agents ---------------------------------------------------------------------

    async def agents(self, access: AgentAccess) -> list[AgentSummary]:
        """The agents this person chats with, and when they last talked to each."""
        rows = (
            await self._session.scalars(select(AgentProfile).order_by(AgentProfile.sort_order))
        ).all()
        visible = [row for row in rows if access.may_chat(row)]
        last: dict[uuid.UUID, datetime] = {}
        if access.user_id is not None and visible:
            result = await self._session.execute(
                select(AgentConversation.agent_id, func.max(AgentConversation.last_message_at))
                .where(
                    AgentConversation.user_id == access.user_id,
                    AgentConversation.archived_at.is_(None),
                )
                .group_by(AgentConversation.agent_id)
            )
            last = {agent_id: at for agent_id, at in result.all() if at is not None}
        return [AgentSummary(agent=row, last_message_at=last.get(row.id)) for row in visible]

    async def agent(self, access: AgentAccess, slug: str) -> AgentProfile:
        row = await self._session.scalar(select(AgentProfile).where(AgentProfile.slug == slug))
        if row is None or not access.may_chat(row):
            raise AgentNotFoundError()
        return row

    # --- conversations --------------------------------------------------------------

    async def conversations(self, access: AgentAccess, slug: str) -> Sequence[AgentConversation]:
        agent = await self.agent(access, slug)
        return (
            await self._session.scalars(
                select(AgentConversation)
                .where(
                    AgentConversation.agent_id == agent.id,
                    AgentConversation.user_id == access.user_id,
                    AgentConversation.archived_at.is_(None),
                )
                .order_by(
                    func.coalesce(
                        AgentConversation.last_message_at, AgentConversation.created_at
                    ).desc()
                )
            )
        ).all()

    async def create_conversation(
        self, access: AgentAccess, slug: str, *, request_id: uuid.UUID
    ) -> AgentConversation:
        agent = await self.agent(access, slug)
        if access.user_id is None:
            raise AgentNotFoundError()
        conversation_id = uuid.uuid4()
        row = AgentConversation(
            id=conversation_id,
            agent_id=agent.id,
            user_id=access.user_id,
            title=NEW_TITLE,
            hermes_session_id=hermes_session_id(conversation_id),
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=AuditAction.AGENT_CONVERSATION_CREATED,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="agent_conversation",
                entity_id=str(row.id),
                after_data={"agent": agent.slug},
            )
        )
        return row

    async def conversation(
        self, access: AgentAccess, conversation_id: uuid.UUID
    ) -> tuple[AgentConversation, AgentProfile]:
        """The caller's own conversation with an agent they still chat with."""
        row = await self._session.get(AgentConversation, conversation_id)
        if row is None or access.user_id is None or row.user_id != access.user_id:
            raise ConversationNotFoundError()
        agent = await self._session.get(AgentProfile, row.agent_id)
        if agent is None or not access.may_chat(agent):
            raise ConversationNotFoundError()
        return row, agent

    async def rename(
        self, access: AgentAccess, conversation_id: uuid.UUID, title: str
    ) -> AgentConversation:
        row, _ = await self.conversation(access, conversation_id)
        clean = " ".join(title.split())
        if not clean or len(clean) > 120:
            raise AgentMessageInvalidError("Tên cuộc trò chuyện từ 1 đến 120 ký tự.")
        row.title = clean
        await self._session.flush()
        return row

    async def archive(self, access: AgentAccess, conversation_id: uuid.UUID) -> None:
        row, _ = await self.conversation(access, conversation_id)
        row.archived_at = utcnow()
        await self._session.flush()

    async def messages(
        self,
        access: AgentAccess,
        conversation_id: uuid.UUID,
        *,
        before: datetime | None = None,
        limit: int = 50,
    ) -> list[AgentMessage]:
        """Oldest first: the ``limit`` messages before ``before`` (else the newest)."""
        row, _ = await self.conversation(access, conversation_id)
        query = select(AgentMessage).where(AgentMessage.conversation_id == row.id)
        if before is not None:
            query = query.where(AgentMessage.created_at < before)
        newest = (
            await self._session.scalars(query.order_by(AgentMessage.created_at.desc()).limit(limit))
        ).all()
        return list(reversed(newest))

    # --- a turn -----------------------------------------------------------------------

    async def prepare_turn(
        self,
        access: AgentAccess,
        conversation_id: uuid.UUID,
        text: str,
        *,
        asker: str,
        seats: Sequence[Seat],
        request_id: uuid.UUID,
    ) -> PreparedTurn:
        """Check and record a question, and an empty answer the stream fills.

        ``agent_busy`` (one answer at a time) is the caller's check: it knows
        which answers are still running in this process.
        """
        conversation, agent = await self.conversation(access, conversation_id)
        body = text.strip()
        if not body:
            raise AgentMessageInvalidError("Tin nhắn trống.")
        if len(body) > MAX_MESSAGE_CHARS:
            raise AgentMessageInvalidError(
                f"Tin nhắn dài quá {MAX_MESSAGE_CHARS:,} ký tự.".replace(",", ".")
            )
        now = utcnow()
        recent = await self._session.scalar(
            select(func.count(AgentMessage.id))
            .join(AgentConversation, AgentConversation.id == AgentMessage.conversation_id)
            .where(
                AgentConversation.user_id == access.user_id,
                AgentMessage.role == MessageRole.USER,
                AgentMessage.created_at >= now - timedelta(seconds=RATE_WINDOW_SECONDS),
            )
        )
        if (recent or 0) >= RATE_LIMIT:
            raise AgentRateLimitedError()
        first = not await self._session.scalar(
            select(func.count(AgentMessage.id)).where(
                AgentMessage.conversation_id == conversation.id
            )
        )
        question = AgentMessage(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=body,
            status=MessageStatus.DONE,
            tools_used=[],
            created_at=now,
            finished_at=now,
        )
        answer = AgentMessage(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="",
            status=MessageStatus.STREAMING,
            tools_used=[],
            created_at=now + timedelta(microseconds=1),
        )
        self._session.add_all([question, answer])
        if first and conversation.title in ("", NEW_TITLE):
            conversation.title = conversation_title(body)
        conversation.last_message_at = now
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=AuditAction.AGENT_MESSAGE_SENT,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="agent_conversation",
                entity_id=str(conversation.id),
                after_data={"agent": agent.slug, "chars": len(body)},  # never the text itself
            )
        )
        prompt = body
        if first:
            who = ", ".join(seat_label(seat) for seat in seats) or "chưa thuộc phòng ban nào"
            prompt = f"[Người hỏi: {asker} - {who}. Hỏi qua APM Management.]\n\n{body}"
        return PreparedTurn(
            conversation_id=conversation.id,
            slug=agent.slug,
            user_message=question,
            answer_id=answer.id,
            prompt=prompt,
            session_id=conversation.hermes_session_id,
            session_key=hermes_session_key(access.user_id, agent.slug),
        )

    async def fail_orphans(self, conversation_id: uuid.UUID, running: set[uuid.UUID]) -> None:
        """Answers left STREAMING by a restart (not running in this process): FAILED."""
        rows = (
            await self._session.scalars(
                select(AgentMessage).where(
                    AgentMessage.conversation_id == conversation_id,
                    AgentMessage.status == MessageStatus.STREAMING,
                )
            )
        ).all()
        for row in rows:
            if row.id not in running:
                row.status = MessageStatus.FAILED
                row.error_code = row.error_code or "interrupted"
                row.finished_at = row.finished_at or utcnow()
        await self._session.flush()

    # --- managing -------------------------------------------------------------------

    async def all_agents(self, access: AgentAccess) -> Sequence[AgentProfile]:
        access.require_manage()
        return (
            await self._session.scalars(select(AgentProfile).order_by(AgentProfile.sort_order))
        ).all()

    async def update_agent(
        self,
        access: AgentAccess,
        slug: str,
        *,
        active: bool | None,
        everyone: bool | None,
        department_ids: list[uuid.UUID] | None,
        user_ids: list[uuid.UUID] | None,
        request_id: uuid.UUID,
    ) -> AgentProfile:
        access.require_manage()
        row = await self._session.scalar(select(AgentProfile).where(AgentProfile.slug == slug))
        if row is None:
            raise AgentNotFoundError()
        before = _access_data(row)
        if department_ids is not None:
            known = set(
                (
                    await self._session.scalars(
                        select(Department.id).where(Department.id.in_(department_ids))
                    )
                ).all()
            )
            if known != set(department_ids):
                raise AgentAccessInvalidError("Có phòng ban không tồn tại.")
            row.allowed_department_ids = sorted(str(v) for v in set(department_ids))
        if user_ids is not None:
            known_users = set(
                (await self._session.scalars(select(User.id).where(User.id.in_(user_ids)))).all()
            )
            if known_users != set(user_ids):
                raise AgentAccessInvalidError("Có tài khoản không tồn tại.")
            row.allowed_user_ids = sorted(str(v) for v in set(user_ids))
        if active is not None:
            row.active = active
        if everyone is not None:
            row.everyone = everyone
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=AuditAction.AGENT_ACCESS_UPDATED,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="agent_profile",
                entity_id=row.slug,
                before_data=before,
                after_data=_access_data(row),
            )
        )
        return row


def _access_data(row: AgentProfile) -> dict[str, object]:
    return {
        "active": row.active,
        "everyone": row.everyone,
        "departments": list(row.allowed_department_ids),
        "users": list(row.allowed_user_ids),
    }


async def finish_answer(
    session: AsyncSession,
    answer_id: uuid.UUID,
    *,
    content: str,
    status: MessageStatus,
    error_code: str | None,
    tools: list[str],
    started_at: datetime,
) -> AgentMessage | None:
    """Record how an answer ended (on the stream's own transaction)."""
    row = await session.get(AgentMessage, answer_id)
    if row is None:
        return None
    now = utcnow()
    row.content = content
    row.status = status
    row.error_code = error_code
    row.tools_used = tools
    row.finished_at = now
    row.duration_ms = int((now - started_at).total_seconds() * 1000)
    conversation = await session.get(AgentConversation, row.conversation_id)
    if conversation is not None:
        conversation.last_message_at = now
    await session.flush()
    return row


async def seed_default_agents(session: AsyncSession) -> list[AgentProfile]:
    """The 15 agents 0091 seeds, for a database built without migrations (tests)."""
    codes = {
        code: dept_id
        for dept_id, code in (await session.execute(select(Department.id, Department.code))).all()
    }
    rows = [
        AgentProfile(
            slug=seed.slug,
            name=seed.name,
            title=seed.title,
            description=seed.description,
            badge=seed.badge,
            group=seed.group,
            sort_order=index,
            everyone=seed.everyone,
            allowed_department_ids=[str(codes[c]) for c in seed.departments if c in codes],
            allowed_user_ids=[],
        )
        for index, seed in enumerate(DEFAULT_AGENTS)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


__all__ = [
    "AgentChatService",
    "AgentSummary",
    "PreparedTurn",
    "finish_answer",
    "seed_default_agents",
]
