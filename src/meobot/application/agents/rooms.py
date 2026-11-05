"""Phòng họp agent (0093): rooms, their agents and people, messages, and the
database half of a round (the stream itself runs in :mod:`.room_runs`).

Rules (see :mod:`meobot.domain.agents.rooms`):

* a room is read by its owner and the people they invited - and only while the
  reader may chat with every active agent of the room (else 404, like a room
  that does not exist);
* the owner alone renames it, picks its agents and chair, invites / removes
  people, runs and stops rounds, deletes it; an invited person reads, writes,
  and may leave;
* a person is invited only if they may chat with every agent of the room, an
  agent joins only if every person of the room may chat with it
  (422 ``room_access_mismatch`` names who / which).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.agents.access import AgentAccess, agent_access_for
from meobot.application.audit_service import AuditService
from meobot.application.org.seats import load_seats
from meobot.core.time import utcnow
from meobot.db.models.agent_chat import AgentProfile
from meobot.db.models.agent_room import (
    AgentRoom,
    AgentRoomAgent,
    AgentRoomMember,
    AgentRoomMessage,
)
from meobot.db.models.user import User
from meobot.domain.agents.errors import (
    AgentMessageInvalidError,
    AgentRateLimitedError,
    RoomAccessMismatchError,
    RoomForbiddenError,
    RoomInvalidError,
    RoomNotFoundError,
)
from meobot.domain.agents.models import MAX_MESSAGE_CHARS, RATE_WINDOW_SECONDS, MessageStatus
from meobot.domain.agents.rooms import (
    MAX_ROOM_AGENTS,
    MAX_ROOM_PEOPLE,
    MAX_ROOM_TITLE,
    MIN_ROOM_AGENTS,
    ROOM_RATE_LIMIT,
    RoomSpeaker,
    RoomStep,
    Said,
    TurnAgent,
    room_briefing,
    room_session_id,
    room_session_key,
    round_plan,
    turn_prompt,
)
from meobot.domain.audit.models import AuditAction, AuditEntry, AuditResult

NEW_ROOM = "Phòng họp mới"
#: An agent's turn that failed or was stopped still reaches the others when it said something.
_HEARD = (MessageStatus.DONE, MessageStatus.CANCELLED, MessageStatus.FAILED)


@dataclass(frozen=True, slots=True)
class RoomAgentView:
    seat: AgentRoomAgent
    agent: AgentProfile


@dataclass(frozen=True, slots=True)
class RoomView:
    room: AgentRoom
    owner: User
    agents: list[RoomAgentView]
    members: list[tuple[AgentRoomMember, User]]
    is_owner: bool

    @property
    def chair(self) -> AgentProfile | None:
        return next(
            (item.agent for item in self.agents if item.agent.id == self.room.chair_agent_id), None
        )


@dataclass(frozen=True, slots=True)
class RoomSummary:
    room: AgentRoom
    owner_name: str
    agents: list[AgentProfile]
    people: int
    is_owner: bool


@dataclass(frozen=True, slots=True)
class Candidate:
    user: User
    allowed: bool
    #: The room's agents this person may not chat with.
    missing: list[str]


@dataclass(frozen=True, slots=True)
class RoundStart:
    round_no: int
    plan: list[tuple[TurnAgent, RoomStep]]


@dataclass(frozen=True, slots=True)
class PreparedRoomTurn:
    message: AgentRoomMessage
    slug: str
    prompt: str
    session_id: str
    session_key: str


def _clean_title(title: str) -> str:
    clean = " ".join(title.split())
    if not clean or len(clean) > MAX_ROOM_TITLE:
        raise RoomInvalidError(f"Tên phòng từ 1 đến {MAX_ROOM_TITLE} ký tự.")
    return clean


class AgentRoomService:
    def __init__(self, session: AsyncSession, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # --- reading ----------------------------------------------------------------------

    async def _agents_of(self, room_id: uuid.UUID) -> list[RoomAgentView]:
        rows = (
            await self._session.execute(
                select(AgentRoomAgent, AgentProfile)
                .join(AgentProfile, AgentProfile.id == AgentRoomAgent.agent_id)
                .where(AgentRoomAgent.room_id == room_id)
                .order_by(AgentRoomAgent.position, AgentRoomAgent.added_at)
            )
        ).all()
        return [RoomAgentView(seat=seat, agent=agent) for seat, agent in rows]

    async def _members_of(self, room_id: uuid.UUID) -> list[tuple[AgentRoomMember, User]]:
        rows = (
            await self._session.execute(
                select(AgentRoomMember, User)
                .join(User, User.id == AgentRoomMember.user_id)
                .where(AgentRoomMember.room_id == room_id)
                .order_by(AgentRoomMember.added_at)
            )
        ).all()
        return [(member, user) for member, user in rows]

    @staticmethod
    def _readable(access: AgentAccess, agents: Sequence[AgentProfile]) -> bool:
        return all(access.may_chat(agent) for agent in agents if agent.active)

    async def view(self, access: AgentAccess, room_id: uuid.UUID) -> RoomView:
        """The room for one of its people (anybody else: 404)."""
        room = await self._session.get(AgentRoom, room_id)
        if room is None or access.user_id is None:
            raise RoomNotFoundError()
        members = await self._members_of(room.id)
        is_owner = room.owner_user_id == access.user_id
        if not is_owner and all(user.id != access.user_id for _, user in members):
            raise RoomNotFoundError()
        agents = await self._agents_of(room.id)
        if not self._readable(access, [item.agent for item in agents]):
            raise RoomNotFoundError()
        owner = await self._session.get(User, room.owner_user_id)
        assert owner is not None  # RESTRICT foreign key
        return RoomView(room=room, owner=owner, agents=agents, members=members, is_owner=is_owner)

    async def owned(self, access: AgentAccess, room_id: uuid.UUID) -> RoomView:
        view = await self.view(access, room_id)
        if not view.is_owner:
            raise RoomForbiddenError()
        return view

    async def rooms(self, access: AgentAccess) -> list[RoomSummary]:
        """The rooms this person owns or was invited to, last spoken in first."""
        if access.user_id is None:
            return []
        invited = select(AgentRoomMember.room_id).where(AgentRoomMember.user_id == access.user_id)
        rooms = (
            await self._session.scalars(
                select(AgentRoom)
                .where(or_(AgentRoom.owner_user_id == access.user_id, AgentRoom.id.in_(invited)))
                .order_by(func.coalesce(AgentRoom.last_message_at, AgentRoom.created_at).desc())
            )
        ).all()
        if not rooms:
            return []
        ids = [room.id for room in rooms]
        agent_rows = (
            await self._session.execute(
                select(AgentRoomAgent.room_id, AgentProfile)
                .join(AgentProfile, AgentProfile.id == AgentRoomAgent.agent_id)
                .where(AgentRoomAgent.room_id.in_(ids))
                .order_by(AgentRoomAgent.position, AgentRoomAgent.added_at)
            )
        ).all()
        by_room: dict[uuid.UUID, list[AgentProfile]] = {}
        for room_id, agent in agent_rows:
            by_room.setdefault(room_id, []).append(agent)
        people: dict[uuid.UUID, int] = {
            room_id: int(count)
            for room_id, count in (
                await self._session.execute(
                    select(AgentRoomMember.room_id, func.count(AgentRoomMember.id))
                    .where(AgentRoomMember.room_id.in_(ids))
                    .group_by(AgentRoomMember.room_id)
                )
            ).all()
        }
        owners = {
            user.id: user.full_name
            for user in (
                await self._session.scalars(
                    select(User).where(User.id.in_({room.owner_user_id for room in rooms}))
                )
            ).all()
        }
        out = []
        for room in rooms:
            agents = by_room.get(room.id, [])
            if not self._readable(access, agents):
                continue
            out.append(
                RoomSummary(
                    room=room,
                    owner_name=owners.get(room.owner_user_id, ""),
                    agents=agents,
                    people=int(people.get(room.id, 0)) + 1,
                    is_owner=room.owner_user_id == access.user_id,
                )
            )
        return out

    async def messages(
        self,
        access: AgentAccess,
        room_id: uuid.UUID,
        *,
        before: datetime | None = None,
        limit: int = 50,
    ) -> tuple[list[AgentRoomMessage], dict[uuid.UUID, AgentProfile]]:
        """Oldest first: the ``limit`` messages before ``before`` (else the newest),
        and the agents who spoke in them."""
        view = await self.view(access, room_id)
        query = select(AgentRoomMessage).where(AgentRoomMessage.room_id == view.room.id)
        if before is not None:
            query = query.where(AgentRoomMessage.created_at < before)
        newest = (
            await self._session.scalars(
                query.order_by(AgentRoomMessage.created_at.desc()).limit(limit)
            )
        ).all()
        rows = list(reversed(newest))
        agent_ids = {row.agent_id for row in rows if row.agent_id is not None}
        agents = (
            {
                agent.id: agent
                for agent in (
                    await self._session.scalars(
                        select(AgentProfile).where(AgentProfile.id.in_(agent_ids))
                    )
                ).all()
            }
            if agent_ids
            else {}
        )
        return rows, agents

    async def fail_orphans(self, room_id: uuid.UUID, running: uuid.UUID | None) -> None:
        """Turns left STREAMING by a restart (not the one running now): FAILED."""
        rows = (
            await self._session.scalars(
                select(AgentRoomMessage).where(
                    AgentRoomMessage.room_id == room_id,
                    AgentRoomMessage.status == MessageStatus.STREAMING,
                )
            )
        ).all()
        for row in rows:
            if row.id != running:
                row.status = MessageStatus.FAILED
                row.error_code = row.error_code or "interrupted"
                row.finished_at = row.finished_at or utcnow()
        await self._session.flush()

    # --- the owner's controls ---------------------------------------------------------

    async def _pick_agents(
        self, access: AgentAccess, slugs: Sequence[str], chair: str | None
    ) -> tuple[list[AgentProfile], AgentProfile]:
        wanted = list(dict.fromkeys(slug.strip() for slug in slugs if slug.strip()))
        if not MIN_ROOM_AGENTS <= len(wanted) <= MAX_ROOM_AGENTS:
            raise RoomInvalidError(
                f"Phòng họp cần từ {MIN_ROOM_AGENTS} đến {MAX_ROOM_AGENTS} agent."
            )
        rows = {
            row.slug: row
            for row in (
                await self._session.scalars(
                    select(AgentProfile).where(AgentProfile.slug.in_(wanted))
                )
            ).all()
        }
        agents = []
        for slug in wanted:
            row = rows.get(slug)
            if row is None or not access.may_chat(row):
                raise RoomInvalidError(
                    "Có agent bạn không được chat cùng, không đưa vào phòng được."
                )
            agents.append(row)
        chair_slug = chair or wanted[0]
        chosen = next((row for row in agents if row.slug == chair_slug), None)
        if chosen is None:
            raise RoomInvalidError("Chủ trì phải là một agent trong phòng.")
        return agents, chosen

    async def _people_who_cannot(
        self, users: Sequence[User], agents: Sequence[AgentProfile]
    ) -> list[tuple[User, list[AgentProfile]]]:
        if not users:
            return []
        seats = await load_seats(self._session, [user.id for user in users])
        out = []
        for user in users:
            access = await agent_access_for(self._session, user, seats.get(user.id, ()))
            missing = [agent for agent in agents if agent.active and not access.may_chat(agent)]
            if missing or user.deleted_at is not None or not user.active:
                out.append((user, missing))
        return out

    async def create(
        self,
        access: AgentAccess,
        *,
        title: str,
        agent_slugs: Sequence[str],
        chair_slug: str | None,
        request_id: uuid.UUID,
    ) -> AgentRoom:
        if access.user_id is None:
            raise RoomForbiddenError()
        agents, chair = await self._pick_agents(access, agent_slugs, chair_slug)
        room = AgentRoom(
            id=uuid.uuid4(),
            owner_user_id=access.user_id,
            title=_clean_title(title or NEW_ROOM),
            chair_agent_id=chair.id,
            rounds=0,
        )
        self._session.add(room)
        await self._session.flush()
        now = utcnow()
        self._session.add_all(
            AgentRoomAgent(room_id=room.id, agent_id=agent.id, position=index, added_at=now)
            for index, agent in enumerate(agents)
        )
        await self._session.flush()
        await self._record(
            AuditAction.AGENT_ROOM_CREATED,
            access,
            room.id,
            request_id,
            {"agents": [agent.slug for agent in agents], "chair": chair.slug},
        )
        return room

    async def rename(self, access: AgentAccess, room_id: uuid.UUID, title: str) -> AgentRoom:
        view = await self.owned(access, room_id)
        view.room.title = _clean_title(title)
        await self._session.flush()
        return view.room

    async def set_agents(
        self,
        access: AgentAccess,
        room_id: uuid.UUID,
        *,
        agent_slugs: Sequence[str],
        chair_slug: str | None,
        request_id: uuid.UUID,
    ) -> tuple[RoomView, list[tuple[str, str]]]:
        """Which agents, in which order, and the chair. Returns the Hermes transcripts
        of the agents taken out (``(slug, session id)``) for the caller to delete."""
        view = await self.owned(access, room_id)
        agents, chair = await self._pick_agents(access, agent_slugs, chair_slug)
        cannot = await self._people_who_cannot([user for _, user in view.members], agents)
        if cannot:
            names = sorted({agent.name for _, missing in cannot for agent in missing})
            raise RoomAccessMismatchError(
                "Có người trong phòng không được chat với "
                + ", ".join(names)
                + ". Mời họ ra trước hoặc bỏ agent đó.",
                people=[user.full_name for user, _ in cannot],
                agents=names,
            )
        keep = {agent.id: index for index, agent in enumerate(agents)}
        gone = []
        for item in view.agents:
            if item.agent.id in keep:
                item.seat.position = keep.pop(item.agent.id)
            else:
                gone.append((item.agent.slug, room_session_id(item.seat.id)))
                await self._session.delete(item.seat)
        now = utcnow()
        for agent in agents:
            if agent.id in keep:
                self._session.add(
                    AgentRoomAgent(
                        room_id=view.room.id,
                        agent_id=agent.id,
                        position=keep[agent.id],
                        added_at=now,
                    )
                )
        view.room.chair_agent_id = chair.id
        await self._session.flush()
        await self._record(
            AuditAction.AGENT_ROOM_UPDATED,
            access,
            view.room.id,
            request_id,
            {"agents": [agent.slug for agent in agents], "chair": chair.slug},
        )
        return await self.view(access, room_id), gone

    async def candidates(self, access: AgentAccess, room_id: uuid.UUID) -> list[Candidate]:
        """Everybody the owner might invite, with whether they may join and why not."""
        view = await self.owned(access, room_id)
        present = {view.room.owner_user_id, *(user.id for _, user in view.members)}
        users = (
            await self._session.scalars(
                select(User)
                .where(User.deleted_at.is_(None), User.active.is_(True), User.id.not_in(present))
                .order_by(User.full_name)
            )
        ).all()
        agents = [item.agent for item in view.agents]
        refused = {
            user.id: missing for user, missing in await self._people_who_cannot(users, agents)
        }
        return [
            Candidate(
                user=user,
                allowed=user.id not in refused,
                missing=[agent.name for agent in refused.get(user.id, [])],
            )
            for user in users
        ]

    async def add_member(
        self, access: AgentAccess, room_id: uuid.UUID, user_id: uuid.UUID, *, request_id: uuid.UUID
    ) -> RoomView:
        view = await self.owned(access, room_id)
        if user_id == view.room.owner_user_id or any(
            user.id == user_id for _, user in view.members
        ):
            return view
        if len(view.members) + 1 >= MAX_ROOM_PEOPLE:
            raise RoomInvalidError(f"Một phòng tối đa {MAX_ROOM_PEOPLE} người.")
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None or not user.active:
            raise RoomInvalidError("Không tìm thấy tài khoản này.")
        cannot = await self._people_who_cannot([user], [item.agent for item in view.agents])
        if cannot:
            names = [agent.name for agent in cannot[0][1]]
            raise RoomAccessMismatchError(
                f"{user.full_name} không được chat với {', '.join(names)}, "
                "nên không vào phòng này được.",
                people=[user.full_name],
                agents=names,
            )
        assert access.user_id is not None
        self._session.add(
            AgentRoomMember(room_id=view.room.id, user_id=user.id, added_by_user_id=access.user_id)
        )
        await self._session.flush()
        await self._record(
            AuditAction.AGENT_ROOM_MEMBER_ADDED,
            access,
            view.room.id,
            request_id,
            {"user": str(user.id)},
        )
        return await self.view(access, room_id)

    async def remove_member(
        self, access: AgentAccess, room_id: uuid.UUID, user_id: uuid.UUID, *, request_id: uuid.UUID
    ) -> None:
        """The owner takes somebody out; anybody invited may leave."""
        view = await self.view(access, room_id)
        if not view.is_owner and user_id != access.user_id:
            raise RoomForbiddenError()
        member = next((row for row, user in view.members if user.id == user_id), None)
        if member is None:
            raise RoomInvalidError("Người này không ở trong phòng.")
        await self._session.delete(member)
        await self._session.flush()
        await self._record(
            AuditAction.AGENT_ROOM_MEMBER_REMOVED,
            access,
            view.room.id,
            request_id,
            {"user": str(user_id), "left": user_id == access.user_id},
        )

    async def delete(
        self, access: AgentAccess, room_id: uuid.UUID, *, request_id: uuid.UUID
    ) -> list[tuple[str, str]]:
        """The room and all it holds, for good. Returns its Hermes transcripts."""
        view = await self.owned(access, room_id)
        transcripts = [(item.agent.slug, room_session_id(item.seat.id)) for item in view.agents]
        messages = await self._session.scalar(
            select(func.count(AgentRoomMessage.id)).where(AgentRoomMessage.room_id == view.room.id)
        )
        for table in (AgentRoomMessage, AgentRoomMember, AgentRoomAgent):
            await self._session.execute(delete(table).where(table.room_id == view.room.id))
        await self._session.delete(view.room)
        await self._session.flush()
        await self._record(
            AuditAction.AGENT_ROOM_DELETED, access, room_id, request_id, {"messages": messages or 0}
        )
        return transcripts

    # --- writing ------------------------------------------------------------------------

    async def post(
        self,
        access: AgentAccess,
        room_id: uuid.UUID,
        text: str,
        *,
        speaker_label: str,
        request_id: uuid.UUID,
    ) -> tuple[RoomView, AgentRoomMessage]:
        view = await self.view(access, room_id)
        assert access.user_id is not None
        body = text.strip()
        if not body:
            raise AgentMessageInvalidError("Tin nhắn trống.")
        if len(body) > MAX_MESSAGE_CHARS:
            raise AgentMessageInvalidError(
                f"Tin nhắn dài quá {MAX_MESSAGE_CHARS:,} ký tự.".replace(",", ".")
            )
        now = utcnow()
        recent = await self._session.scalar(
            select(func.count(AgentRoomMessage.id)).where(
                AgentRoomMessage.user_id == access.user_id,
                AgentRoomMessage.created_at >= now - timedelta(seconds=RATE_WINDOW_SECONDS),
            )
        )
        if (recent or 0) >= ROOM_RATE_LIMIT:
            raise AgentRateLimitedError()
        first = not await self._session.scalar(
            select(func.count(AgentRoomMessage.id)).where(AgentRoomMessage.room_id == view.room.id)
        )
        row = AgentRoomMessage(
            room_id=view.room.id,
            speaker=RoomSpeaker.PERSON,
            user_id=access.user_id,
            speaker_label=speaker_label[:200],
            content=body,
            status=MessageStatus.DONE,
            tools_used=[],
            created_at=now,
            finished_at=now,
        )
        self._session.add(row)
        if first and view.is_owner and view.room.title == NEW_ROOM:
            view.room.title = _clean_title(body.splitlines()[0][:60])
        view.room.last_message_at = now
        await self._session.flush()
        await self._record(
            AuditAction.AGENT_ROOM_MESSAGE_SENT,
            access,
            view.room.id,
            request_id,
            {"chars": len(body)},  # never the text
        )
        return view, row

    async def record_round(
        self, access: AgentAccess, room_id: uuid.UUID, request_id: uuid.UUID
    ) -> None:
        await self._record(AuditAction.AGENT_ROOM_ROUND_STARTED, access, room_id, request_id, {})

    async def _record(
        self,
        action: AuditAction,
        access: AgentAccess,
        room_id: uuid.UUID,
        request_id: uuid.UUID,
        data: dict[str, object],
    ) -> None:
        await self._audit.record(
            AuditEntry(
                request_id=request_id,
                action=action,
                result=AuditResult.SUCCESS,
                actor_user_id=access.user_id,
                entity_type="agent_room",
                entity_id=str(room_id),
                after_data=data,
            )
        )


# --- a round, on the runner's own transactions --------------------------------------------


async def _turn_agents(session: AsyncSession, room_id: uuid.UUID) -> list[TurnAgent]:
    rows = (
        await session.execute(
            select(AgentRoomAgent, AgentProfile)
            .join(AgentProfile, AgentProfile.id == AgentRoomAgent.agent_id)
            .where(AgentRoomAgent.room_id == room_id, AgentProfile.active.is_(True))
            .order_by(AgentRoomAgent.position, AgentRoomAgent.added_at)
        )
    ).all()
    return [
        TurnAgent(room_agent_id=str(seat.id), slug=agent.slug, name=agent.name, title=agent.title)
        for seat, agent in rows
    ]


async def begin_round(session: AsyncSession, room_id: uuid.UUID) -> RoundStart | None:
    """Number the round and say who speaks; None = the room is gone or has no agent."""
    room = await session.get(AgentRoom, room_id)
    if room is None:
        return None
    agents = await _turn_agents(session, room_id)
    if not agents:
        return None
    chair = await session.get(AgentProfile, room.chair_agent_id) if room.chair_agent_id else None
    room.rounds += 1
    await session.flush()
    return RoundStart(round_no=room.rounds, plan=round_plan(agents, chair.slug if chair else ""))


async def open_turn(
    session: AsyncSession, room_id: uuid.UUID, turn: TurnAgent, step: RoomStep, round_no: int
) -> PreparedRoomTurn | None:
    """Record the agent's (empty) turn and build what it reads; None = it left the room."""
    room = await session.get(AgentRoom, room_id)
    seat = await session.get(AgentRoomAgent, uuid.UUID(turn.room_agent_id))
    if room is None or seat is None:
        return None
    agent = await session.get(AgentProfile, seat.agent_id)
    if agent is None or not agent.active:
        return None
    now = utcnow()
    query = select(AgentRoomMessage).where(
        AgentRoomMessage.room_id == room_id,
        AgentRoomMessage.created_at < now,
        AgentRoomMessage.status.in_(_HEARD),
        or_(AgentRoomMessage.agent_id.is_(None), AgentRoomMessage.agent_id != agent.id),
    )
    if seat.seen_until is not None:
        query = query.where(AgentRoomMessage.created_at > seat.seen_until)
    said = [
        Said(speaker=row.speaker_label or "?", content=row.content)
        for row in (await session.scalars(query.order_by(AgentRoomMessage.created_at))).all()
        if row.content.strip()
    ]
    briefing = None
    if seat.seen_until is None:
        agents = await _turn_agents(session, room_id)
        chair_agent = (
            await session.get(AgentProfile, room.chair_agent_id) if room.chair_agent_id else None
        )
        chair = next(
            (item for item in agents if chair_agent is not None and item.slug == chair_agent.slug),
            None,
        )
        owner = await session.get(User, room.owner_user_id)
        members = (
            await session.scalars(
                select(User)
                .join(AgentRoomMember, AgentRoomMember.user_id == User.id)
                .where(AgentRoomMember.room_id == room_id)
                .order_by(AgentRoomMember.added_at)
            )
        ).all()
        people = [user.full_name for user in [owner, *members] if user is not None]
        briefing = room_briefing(
            title=room.title, seat=turn, chair=chair, agents=agents, people=people
        )
    message = AgentRoomMessage(
        room_id=room_id,
        speaker=RoomSpeaker.AGENT,
        agent_id=agent.id,
        speaker_label=agent.name,
        round_no=round_no,
        step=step,
        content="",
        status=MessageStatus.STREAMING,
        tools_used=[],
        created_at=now,
    )
    session.add(message)
    seat.seen_until = now
    room.last_message_at = now
    await session.flush()
    return PreparedRoomTurn(
        message=message,
        slug=agent.slug,
        prompt=turn_prompt(briefing=briefing, said=said, step=step, seat=turn),
        session_id=room_session_id(seat.id),
        session_key=room_session_key(room_id, agent.slug),
    )


async def finish_turn(
    session: AsyncSession,
    message_id: uuid.UUID,
    *,
    content: str,
    status: MessageStatus,
    error_code: str | None,
    tools: list[str],
    started_at: datetime,
) -> AgentRoomMessage | None:
    row = await session.get(AgentRoomMessage, message_id)
    if row is None:
        return None
    now = utcnow()
    row.content = content
    row.status = status
    row.error_code = error_code
    row.tools_used = tools
    row.finished_at = now
    row.duration_ms = int((now - started_at).total_seconds() * 1000)
    room = await session.get(AgentRoom, row.room_id)
    if room is not None:
        room.last_message_at = now
    await session.flush()
    return row


__all__ = [
    "AgentRoomService",
    "Candidate",
    "PreparedRoomTurn",
    "RoomAgentView",
    "RoomSummary",
    "RoomView",
    "RoundStart",
    "begin_round",
    "finish_turn",
    "open_turn",
]
