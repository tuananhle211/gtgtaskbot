"""Đội agent (0091): agents, conversations, messages - everything but the stream.

The answer itself streams from the relay in :mod:`.turns`; this service
prepares a turn (checks, the question row, an empty answer row) and records
how it ended, each on whatever session it is handed.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from meobot.application.agents.access import AgentAccess
from meobot.application.agents.files import clean_name, read_upload
from meobot.application.audit_service import AuditService
from meobot.core.time import utcnow
from meobot.db.models.agent_chat import (
    AgentAttachment,
    AgentConversation,
    AgentMessage,
    AgentProfile,
)
from meobot.db.models.department import Department
from meobot.db.models.user import User
from meobot.domain.agents.errors import (
    AgentAccessInvalidError,
    AgentFileInvalidError,
    AgentMessageInvalidError,
    AgentNotFoundError,
    AgentRateLimitedError,
    AttachmentNotFoundError,
    ConversationNotFoundError,
)
from meobot.domain.agents.models import (
    DEFAULT_AGENTS,
    MAX_ATTACHED_TEXT_CHARS,
    MAX_ATTACHMENTS,
    MAX_IMAGE_BYTES_PER_MESSAGE,
    MAX_MESSAGE_CHARS,
    RATE_LIMIT,
    RATE_WINDOW_SECONDS,
    UNSENT_UPLOAD_HOURS,
    UPLOAD_RATE_LIMIT,
    AttachmentKind,
    MessageRole,
    MessageStatus,
    conversation_title,
    hermes_session_id,
    hermes_session_key,
)
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult
from meobot.domain.org.models import Seat, seat_label

NEW_TITLE = "Cuộc trò chuyện mới"
#: What the agent reads when a question is only files.
FILES_ONLY = "(Người hỏi gửi tệp đính kèm, không kèm lời nhắn.)"
SKILL_HINT = (
    "Người hỏi gửi skill này để bạn dùng trong cuộc trò chuyện. Muốn giữ lâu dài thì "
    "lưu skill như thường lệ: skill mới vào thư mục riêng của bạn (tự duyệt), sửa skill "
    "dùng chung thì Emma duyệt."
)


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
    #: The pictures, as ``data:`` URLs (Hermes sees them).
    images: tuple[str, ...] = ()
    attachments: list[AgentAttachment] = field(default_factory=list)


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

    async def delete_conversation(
        self, access: AgentAccess, conversation_id: uuid.UUID, *, request_id: uuid.UUID
    ) -> tuple[str, str]:
        """Delete a conversation with its messages and files, for good.

        Returns the agent's slug and the Hermes transcript id, for the caller
        to delete that one too.
        """
        row, agent = await self.conversation(access, conversation_id)
        messages = await self._session.scalar(
            select(func.count(AgentMessage.id)).where(AgentMessage.conversation_id == row.id)
        )
        await self._session.execute(
            delete(AgentAttachment).where(AgentAttachment.conversation_id == row.id)
        )
        await self._session.execute(
            delete(AgentMessage).where(AgentMessage.conversation_id == row.id)
        )
        session_id = row.hermes_session_id
        await self._session.delete(row)
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=AuditAction.AGENT_CONVERSATION_DELETED,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="agent_conversation",
                entity_id=str(conversation_id),
                after_data={"agent": agent.slug, "messages": messages or 0},
            )
        )
        return agent.slug, session_id

    # --- attachments ----------------------------------------------------------------

    async def upload(
        self, access: AgentAccess, conversation_id: uuid.UUID, *, name: str, data: bytes
    ) -> AgentAttachment:
        """Keep a file for the next question (read now: a refusal comes at once)."""
        conversation, _ = await self.conversation(access, conversation_id)
        assert access.user_id is not None  # conversation() guarantees it
        now = utcnow()
        await self._session.execute(
            delete(AgentAttachment).where(
                AgentAttachment.user_id == access.user_id,
                AgentAttachment.message_id.is_(None),
                AgentAttachment.created_at < now - timedelta(hours=UNSENT_UPLOAD_HOURS),
            )
        )
        recent = await self._session.scalar(
            select(func.count(AgentAttachment.id)).where(
                AgentAttachment.user_id == access.user_id,
                AgentAttachment.created_at >= now - timedelta(seconds=RATE_WINDOW_SECONDS),
            )
        )
        if (recent or 0) >= UPLOAD_RATE_LIMIT:
            raise AgentRateLimitedError()
        waiting = await self._session.scalar(
            select(func.count(AgentAttachment.id)).where(
                AgentAttachment.conversation_id == conversation.id,
                AgentAttachment.message_id.is_(None),
            )
        )
        if (waiting or 0) >= MAX_ATTACHMENTS:
            raise AgentFileInvalidError(f"Mỗi tin nhắn gửi tối đa {MAX_ATTACHMENTS} tệp.")
        name = clean_name(name)
        read = await asyncio.to_thread(read_upload, name, data)
        kept = read.data if read.data is not None else data  # a page drawn as PNG replaces the file
        row = AgentAttachment(
            conversation_id=conversation.id,
            user_id=access.user_id,
            name=read.name or name,
            content_type=read.content_type,
            kind=read.kind,
            size_bytes=len(kept),
            data=kept,
            content=read.text,
            pages=read.pages,
            truncated=read.truncated,
            note=read.note,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def attachment(
        self, access: AgentAccess, attachment_id: uuid.UUID, *, with_data: bool = False
    ) -> AgentAttachment:
        """One file of the caller's own conversations (somebody else's = 404)."""
        query = select(AgentAttachment).where(AgentAttachment.id == attachment_id)
        if with_data:
            query = query.options(undefer(AgentAttachment.data))
        row = await self._session.scalar(query)
        if row is None or row.user_id != access.user_id:
            raise AttachmentNotFoundError()
        try:
            await self.conversation(access, row.conversation_id)
        except ConversationNotFoundError as exc:
            raise AttachmentNotFoundError() from exc
        return row

    async def remove_attachment(self, access: AgentAccess, attachment_id: uuid.UUID) -> None:
        """Take a file back out of the composer (not once it went with a question)."""
        row = await self.attachment(access, attachment_id)
        if row.message_id is not None:
            raise AgentFileInvalidError("Tệp đã gửi đi, không gỡ được.")
        await self._session.delete(row)
        await self._session.flush()

    async def attachments_for(
        self, message_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[AgentAttachment]]:
        """The files of these messages (no bytes), in the order they were added."""
        if not message_ids:
            return {}
        rows = (
            await self._session.scalars(
                select(AgentAttachment)
                .where(AgentAttachment.message_id.in_(list(message_ids)))
                .order_by(AgentAttachment.created_at, AgentAttachment.id)
            )
        ).all()
        out: dict[uuid.UUID, list[AgentAttachment]] = {}
        for row in rows:
            assert row.message_id is not None
            out.setdefault(row.message_id, []).append(row)
        return out

    async def _unsent(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[AgentAttachment]:
        if not ids:
            return []
        if len(set(ids)) > MAX_ATTACHMENTS:
            raise AgentFileInvalidError(f"Mỗi tin nhắn gửi tối đa {MAX_ATTACHMENTS} tệp.")
        rows = (
            await self._session.scalars(
                select(AgentAttachment)
                .options(undefer(AgentAttachment.data), undefer(AgentAttachment.content))
                .where(
                    AgentAttachment.id.in_(list(set(ids))),
                    AgentAttachment.conversation_id == conversation_id,
                    AgentAttachment.user_id == user_id,
                    AgentAttachment.message_id.is_(None),
                )
            )
        ).all()
        if len(rows) != len(set(ids)):
            raise AgentFileInvalidError("Có tệp không còn (đã gửi hoặc đã gỡ). Đính kèm lại.")
        order = {attachment_id: index for index, attachment_id in enumerate(ids)}
        found = sorted(rows, key=lambda row: order[row.id])
        if (
            sum(row.size_bytes for row in found if row.kind == AttachmentKind.IMAGE)
            > MAX_IMAGE_BYTES_PER_MESSAGE
        ):
            raise AgentFileInvalidError(
                f"Ảnh của một tin nhắn cộng lại tối đa "
                f"{MAX_IMAGE_BYTES_PER_MESSAGE // (1024 * 1024)} MB. Gửi bớt ảnh."
            )
        if sum(len(row.content or "") for row in found) > MAX_ATTACHED_TEXT_CHARS:
            raise AgentFileInvalidError("Tài liệu đính kèm dài quá. Gửi bớt tệp, mỗi lần vài tệp.")
        return found

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
        attachment_ids: Sequence[uuid.UUID] = (),
    ) -> PreparedTurn:
        """Check and record a question, and an empty answer the stream fills.

        ``agent_busy`` (one answer at a time) is the caller's check: it knows
        which answers are still running in this process.
        """
        conversation, agent = await self.conversation(access, conversation_id)
        assert access.user_id is not None  # conversation() guarantees it
        body = text.strip()
        files = await self._unsent(conversation.id, access.user_id, attachment_ids)
        if not body and not files:
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
            conversation.title = conversation_title(body or files[0].name)
        conversation.last_message_at = now
        await self._session.flush()
        for row in files:
            row.message_id = question.id
        await self._session.flush()
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=AuditAction.AGENT_MESSAGE_SENT,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="agent_conversation",
                entity_id=str(conversation.id),
                # never the text itself, nor the files' names
                after_data={
                    "agent": agent.slug,
                    "chars": len(body),
                    "files": sorted(row.kind.value for row in files),
                },
            )
        )
        prompt = "\n\n".join([body or FILES_ONLY, *(attached_block(row) for row in files)])
        if first:
            who = ", ".join(seat_label(seat) for seat in seats) or "chưa thuộc phòng ban nào"
            prompt = f"[Người hỏi: {asker} - {who}. Hỏi qua APM Management.]\n\n{prompt}"
        images = tuple(
            f"data:{row.content_type};base64,{base64.b64encode(row.data).decode('ascii')}"
            for row in files
            if row.kind == AttachmentKind.IMAGE
        )
        return PreparedTurn(
            conversation_id=conversation.id,
            slug=agent.slug,
            user_message=question,
            answer_id=answer.id,
            prompt=prompt,
            session_id=conversation.hermes_session_id,
            session_key=hermes_session_key(access.user_id, agent.slug),
            images=images,
            attachments=list(files),
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


def attached_block(row: AgentAttachment) -> str:
    """How a file is introduced to the agent, inside the question's text."""
    if row.kind == AttachmentKind.IMAGE:
        return f"[Ảnh đính kèm: {row.name}]"
    about = f" - {row.note}" if row.note else ""
    if row.kind == AttachmentKind.SKILL:
        return (
            f"[Skill đính kèm: {row.name}{about}]\n{SKILL_HINT}\n\n"
            f"{row.content or ''}\n[Hết skill: {row.name}]"
        )
    return f"[Tệp đính kèm: {row.name}{about}]\n{row.content or ''}\n[Hết tệp: {row.name}]"


def _access_data(row: AgentProfile) -> dict[str, object]:
    return {
        "active": row.active,
        "everyone": row.everyone,
        "departments": list(row.allowed_department_ids),
        "users": list(row.allowed_user_ids),
    }


@dataclass(frozen=True, slots=True)
class AnswerFile:
    """A picture or a web page the agent put in its answer, kept as a file of the answer."""

    name: str
    content_type: str
    data: bytes
    kind: AttachmentKind = AttachmentKind.IMAGE
    note: str = "Ảnh agent gửi"


async def finish_answer(
    session: AsyncSession,
    answer_id: uuid.UUID,
    *,
    content: str,
    status: MessageStatus,
    error_code: str | None,
    tools: list[str],
    started_at: datetime,
    files: Sequence[AnswerFile] = (),
) -> tuple[AgentMessage, list[AgentAttachment]] | None:
    """Record how an answer ended (on the stream's own transaction), with the
    pictures and pages the agent made as the answer's files (owned by the conversation's owner)."""
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
    kept: list[AgentAttachment] = []
    if conversation is not None:
        conversation.last_message_at = now
        for item in files:
            file = AgentAttachment(
                conversation_id=conversation.id,
                message_id=row.id,
                user_id=conversation.user_id,
                name=item.name,
                content_type=item.content_type,
                kind=item.kind,
                size_bytes=len(item.data),
                data=item.data,
                note=item.note,
            )
            session.add(file)
            kept.append(file)
    await session.flush()
    return row, kept


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
    "AnswerFile",
    "PreparedTurn",
    "attached_block",
    "finish_answer",
    "seed_default_agents",
]
