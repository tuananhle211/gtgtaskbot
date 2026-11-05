"""Phòng họp agent (0093): several agents talk in turns, people read and write.

* a room needs 2-6 agents the owner chats with; the chair opens and closes;
* only its owner and invited people see it (anybody else: 404); only the owner
  runs it, invites, picks agents, deletes; an invited person writes and leaves;
* a person joins only if they may chat with every agent, an agent only if every
  person may chat with it (``room_access_mismatch``);
* a round: chair OPEN, the others SPEAK in order, chair CLOSE; each agent gets
  the briefing once, then only what was said since it last spoke; "Dừng" keeps
  what came (CANCELLED) and skips the rest; a turn left by a restart fails.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select

from meobot.application.agents.room_runs import RoomRun, RoomRunner
from meobot.core.time import utcnow
from meobot.db.models.agent_room import AgentRoomAgent, AgentRoomMessage
from meobot.db.models.audit_log import AuditLog
from meobot.domain.agents.models import MessageStatus
from meobot.domain.agents.rooms import RoomSpeaker, RoomStep, room_session_id
from meobot.integrations.hermes.relay import FakeAgentRelay, RelayDelta, RelayEvent
from tests.unit.pr_world import World
from tests.unit.test_agent_chat import seeded, setup
from tests.unit.test_order_commands import person
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio


def make_room(world: World, agents: list[str], chair: str | None = None, title: str = "") -> str:
    made = world.client.post(
        "/api/agents/rooms", json={"title": title, "agents": agents, "chair": chair}
    )
    assert made.status_code == 201, made.json()
    return str(made.json()["id"])


def same_session(world: World) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Any]:
        yield world.session
        await world.session.flush()

    return factory


async def messages(world: World, room: str) -> list[AgentRoomMessage]:
    return list(
        (
            await world.session.scalars(
                select(AgentRoomMessage)
                .where(AgentRoomMessage.room_id == uuid.UUID(room))
                .order_by(AgentRoomMessage.created_at)
            )
        ).all()
    )


async def run_round(world: World, room: str, relay: Any) -> RoomRunner:
    runner = RoomRunner()
    await runner.run_round(RoomRun(room_id=uuid.UUID(room)), relay, same_session(world))
    return runner


# --- making a room ----------------------------------------------------------------------


async def test_01_a_room_needs_agents_the_owner_chats_with(world: World) -> None:
    setup(world)
    await seeded(world)
    world.act_as(world.member)  # PR: cmo, mktnghiencuu, mktnoidung, phapche
    for agents, why in (
        (["cmo"], "room_invalid"),
        (["cmo", "mktnoidung", "mktnghiencuu", "phapche", "cmo", "x", "y", "z"], "room_invalid"),
        (["cmo", "ceo"], "room_invalid"),  # ceo: not for the member
    ):
        refused = world.client.post("/api/agents/rooms", json={"agents": agents})
        assert refused.status_code == 422, refused.json()
        assert error_reason(refused.json()) == why
    bad_chair = world.client.post(
        "/api/agents/rooms", json={"agents": ["cmo", "phapche"], "chair": "ceo"}
    )
    assert bad_chair.status_code == 422
    room = make_room(world, ["cmo", "mktnoidung", "phapche"])
    listed = world.client.get("/api/agents/rooms").json()
    assert [item["id"] for item in listed["rooms"]] == [room]
    item = listed["rooms"][0]
    assert item["title"] == "Phòng họp mới" and item["is_owner"] is True
    # No chair named: the first agent chairs.
    assert [(a["slug"], a["chair"]) for a in item["agents"]] == [
        ("cmo", True),
        ("mktnoidung", False),
        ("phapche", False),
    ]
    # Somebody else does not see it at all.
    world.act_as(world.owner)
    assert world.client.get("/api/agents/rooms").json()["rooms"] == []
    hidden = world.client.get(f"/api/agents/rooms/{room}/messages")
    assert hidden.status_code == 404 and error_reason(hidden.json()) == "room_not_found"


# --- people -------------------------------------------------------------------------------


async def test_02_only_people_who_may_chat_with_every_agent_join(world: World) -> None:
    setup(world)
    await seeded(world)
    world.act_as(world.owner)
    with_ceo = make_room(world, ["ceo", "cmo"], chair="ceo", title="Kế hoạch quý")
    refused = world.client.post(
        f"/api/agents/rooms/{with_ceo}/members", json={"user_id": str(world.member.id)}
    )
    assert refused.status_code == 422, refused.json()
    body = refused.json()
    assert error_reason(body) == "room_access_mismatch"
    assert body["error"]["details"]["people"] == [world.member.full_name]
    assert len(body["error"]["details"]["agents"]) == 1  # CEO only; CMO is the member's
    # The candidate list says so before anybody tries.
    people = world.client.get(f"/api/agents/rooms/{with_ceo}/candidates").json()["people"]
    member_row = next(item for item in people if item["user_id"] == str(world.member.id))
    assert member_row["allowed"] is False and len(member_row["missing"]) == 1

    open_room = make_room(world, ["cmo", "phapche"], chair="phapche", title="Chiến dịch T12")
    added = world.client.post(
        f"/api/agents/rooms/{open_room}/members", json={"user_id": str(world.member.id)}
    )
    assert added.status_code == 200, added.json()
    assert [p["name"] for p in added.json()["people"]] == [
        world.owner.full_name,
        world.member.full_name,
    ]
    # An agent the member may not chat with cannot join while they are in.
    widened = world.client.patch(
        f"/api/agents/rooms/{open_room}", json={"agents": ["cmo", "phapche", "ceo"]}
    )
    assert widened.status_code == 422 and error_reason(widened.json()) == "room_access_mismatch"
    assert widened.json()["error"]["details"]["people"] == [world.member.full_name]

    # The member sees and writes, but does not run the room.
    world.act_as(world.member)
    assert [r["id"] for r in world.client.get("/api/agents/rooms").json()["rooms"]] == [open_room]
    written = world.client.post(
        f"/api/agents/rooms/{open_room}/messages", json={"text": "Em góp ý: thêm video ngắn."}
    )
    assert written.status_code == 200, written.json()
    assert written.json()["round_started"] is False
    assert written.json()["message"]["speaker_label"].startswith(world.member.full_name)
    for call in (
        lambda: world.client.post(f"/api/agents/rooms/{open_room}/rounds"),
        lambda: world.client.post(f"/api/agents/rooms/{open_room}/stop"),
        lambda: world.client.patch(f"/api/agents/rooms/{open_room}", json={"title": "Của em"}),
        lambda: world.client.get(f"/api/agents/rooms/{open_room}/candidates"),
        lambda: world.client.post(
            f"/api/agents/rooms/{open_room}/members", json={"user_id": str(world.owner.id)}
        ),
        lambda: world.client.delete(f"/api/agents/rooms/{open_room}"),
    ):
        response = call()
        assert response.status_code == 403, response.json()
        assert error_reason(response.json()) == "room_forbidden"
    # ... and may leave.
    left = world.client.delete(f"/api/agents/rooms/{open_room}/members/{world.member.id}")
    assert left.status_code == 204
    assert world.client.get("/api/agents/rooms").json()["rooms"] == []
    actions = {row.action for row in (await world.session.scalars(select(AuditLog))).all()}
    assert {
        "agent.room.created",
        "agent.room.member_added",
        "agent.room.message_sent",
        "agent.room.member_removed",
    } <= actions


async def test_03_messages_need_text_and_the_relay(world: World) -> None:
    setup(world, FakeAgentRelay(configured=False))
    await seeded(world)
    world.act_as(world.owner)
    room = make_room(world, ["cmo", "phapche"])
    empty = world.client.post(f"/api/agents/rooms/{room}/messages", json={"text": "  "})
    assert empty.status_code == 422 and error_reason(empty.json()) == "agent_message_invalid"
    # Not configured: the topic is kept, no round starts; "Họp tiếp" says why.
    topic = world.client.post(
        f"/api/agents/rooms/{room}/messages", json={"text": "Ngân sách quảng cáo tháng 12"}
    )
    assert topic.status_code == 200 and topic.json()["round_started"] is False
    listing = world.client.get(f"/api/agents/rooms/{room}/messages").json()
    # The owner's first message names a room left unnamed.
    assert listing["room"]["title"] == "Ngân sách quảng cáo tháng 12"
    assert [m["content"] for m in listing["messages"]] == ["Ngân sách quảng cáo tháng 12"]
    refused = world.client.post(f"/api/agents/rooms/{room}/rounds")
    assert refused.status_code == 503 and error_reason(refused.json()) == "agents_not_configured"


# --- a round --------------------------------------------------------------------------------


def quiet(world: World) -> None:
    """The API's relay is off: posting starts no round (the test runs rounds itself)."""
    setup(world, FakeAgentRelay(configured=False))


async def test_04_the_chair_opens_the_others_speak_the_chair_closes(world: World) -> None:
    quiet(world)
    await seeded(world)
    world.act_as(world.owner)
    room = make_room(world, ["ceo", "cmo", "cfo"], chair="ceo", title="Ngân sách T12")
    world.client.post(f"/api/agents/rooms/{room}/messages", json={"text": "Có nên tăng ngân sách?"})
    replies: list[list[RelayEvent]] = [
        [RelayDelta("Mở đầu: CMO nói kênh, CFO nói tiền.")],
        [RelayDelta("CMO: tăng TikTok.")],
        [RelayDelta("CFO: giữ trần 10%.")],
        [RelayDelta("Kết luận: tăng TikTok, trần 10%.")],
    ]
    relay = FakeAgentRelay(replies)
    await run_round(world, room, relay)

    assert [call["slug"] for call in relay.calls] == ["ceo", "cmo", "cfo", "ceo"]
    first, cmo, cfo, close = (call["text"] for call in relay.calls)
    assert "[PHÒNG HỌP APM" in first and "Bạn là chủ trì" in first
    assert "Có nên tăng ngân sách?" in first and "MỞ ĐẦU" in first
    # Each agent hears what was said before it, briefing on its first turn only.
    assert "[PHÒNG HỌP APM" in cmo and "Mở đầu: CMO nói kênh" in cmo
    assert "CMO: tăng TikTok." in cfo and "Có nên tăng ngân sách?" in cfo
    assert "[PHÒNG HỌP APM" not in close and "KẾT LUẬN" in close
    assert "CFO: giữ trần 10%." in close and "Có nên tăng ngân sách?" not in close
    # Every agent has its own transcript of the room.
    seats = {
        str(row.id)
        for row in (
            await world.session.scalars(
                select(AgentRoomAgent).where(AgentRoomAgent.room_id == uuid.UUID(room))
            )
        ).all()
    }
    assert {call["session_id"] for call in relay.calls} == {room_session_id(s) for s in seats}

    rows = await messages(world, room)
    assert [(row.speaker, row.step, row.round_no) for row in rows] == [
        (RoomSpeaker.PERSON, None, None),
        (RoomSpeaker.AGENT, RoomStep.OPEN, 1),
        (RoomSpeaker.AGENT, RoomStep.SPEAK, 1),
        (RoomSpeaker.AGENT, RoomStep.SPEAK, 1),
        (RoomSpeaker.AGENT, RoomStep.CLOSE, 1),
    ]
    assert all(row.status is MessageStatus.DONE for row in rows)

    # A person writes, the next round: only what is new.
    world.client.post(f"/api/agents/rooms/{room}/messages", json={"text": "Còn Google thì sao?"})
    again = FakeAgentRelay([[RelayDelta("Ý mới.")]])
    await run_round(world, room, again)
    assert all("[PHÒNG HỌP APM" not in call["text"] for call in again.calls)
    assert "Còn Google thì sao?" in again.calls[0]["text"]
    assert "Có nên tăng ngân sách?" not in again.calls[0]["text"]
    listing = world.client.get(f"/api/agents/rooms/{room}/messages").json()
    assert listing["room"]["rounds"] == 2
    assert listing["messages"][1]["step_label"] == "Mở đầu"
    assert listing["messages"][1]["agent_slug"] == "ceo"


class SlowRelay(FakeAgentRelay):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def stream_chat(self, slug: str, **kwargs: Any) -> AsyncIterator[RelayEvent]:
        self.calls.append({"slug": slug, **kwargs})
        yield RelayDelta("Đang nói")
        self.started.set()
        await asyncio.Event().wait()  # never ends by itself


async def test_05_stop_keeps_what_came_and_skips_the_rest(world: World) -> None:
    quiet(world)
    await seeded(world)
    world.act_as(world.owner)
    room = make_room(world, ["ceo", "cmo"], chair="ceo")
    world.client.post(f"/api/agents/rooms/{room}/messages", json={"text": "Chủ đề"})
    runner = RoomRunner()
    relay = SlowRelay()
    run = runner.start(uuid.UUID(room), relay, same_session(world))
    assert run is not None
    await asyncio.wait_for(relay.started.wait(), timeout=5)
    assert runner.start(uuid.UUID(room), relay, same_session(world)) is None  # one round at a time
    assert runner.running(uuid.UUID(room)) is run
    assert await runner.stop(uuid.UUID(room)) is True
    assert runner.running(uuid.UUID(room)) is None
    rows = await messages(world, room)
    assert [row.status for row in rows] == [MessageStatus.DONE, MessageStatus.CANCELLED]
    assert rows[1].content == "Đang nói" and len(relay.calls) == 1


async def test_06_a_turn_left_by_a_restart_fails_when_read(world: World) -> None:
    setup(world)
    await seeded(world)
    world.act_as(world.owner)
    room = make_room(world, ["ceo", "cmo"])
    ceo = (await seeded_agent(world, "ceo")).id
    world.session.add(
        AgentRoomMessage(
            room_id=uuid.UUID(room),
            speaker=RoomSpeaker.AGENT,
            agent_id=ceo,
            speaker_label="CEO",
            round_no=1,
            step=RoomStep.OPEN,
            content="Nửa câu",
            status=MessageStatus.STREAMING,
            tools_used=[],
            created_at=utcnow(),
        )
    )
    await world.session.flush()
    listing = world.client.get(f"/api/agents/rooms/{room}/messages").json()
    assert listing["messages"][0]["status"] == "FAILED"
    assert listing["messages"][0]["error_code"] == "interrupted"


async def seeded_agent(world: World, slug: str) -> Any:
    from meobot.db.models.agent_chat import AgentProfile

    row = await world.session.scalar(select(AgentProfile).where(AgentProfile.slug == slug))
    assert row is not None
    return row


async def test_07_the_owner_changes_agents_and_deletes_the_room(world: World) -> None:
    relay = setup(world)
    await seeded(world)
    world.act_as(world.owner)
    room = make_room(world, ["ceo", "cmo", "cfo"], chair="ceo")
    changed = world.client.patch(
        f"/api/agents/rooms/{room}", json={"agents": ["cfo", "cmo"], "chair": "cfo", "title": "Mới"}
    )
    assert changed.status_code == 200, changed.json()
    assert changed.json()["title"] == "Mới"
    assert [(a["slug"], a["chair"]) for a in changed.json()["agents"]] == [
        ("cfo", True),
        ("cmo", False),
    ]
    # The agent taken out loses its transcript of the room.
    assert [slug for slug, _ in relay.deleted] == ["ceo"]
    outsider = await person(world, "Người ngoài")
    world.act_as(outsider)
    assert world.client.delete(f"/api/agents/rooms/{room}").status_code == 404
    world.act_as(world.owner)
    assert world.client.delete(f"/api/agents/rooms/{room}").status_code == 204
    assert world.client.get("/api/agents/rooms").json()["rooms"] == []
    assert sorted(slug for slug, _ in relay.deleted) == ["ceo", "cfo", "cmo"]
    assert await messages(world, room) == []
