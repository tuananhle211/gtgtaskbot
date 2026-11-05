"""Đội agent (0091): chat with the Hermes agents inside APM.

* who sees which agent: the agent's departments / people / everyone, ``AGENTS
  CHAT`` = every agent, the OWNER / an ADMIN every agent; only the OWNER
  manages them;
* a conversation is its owner's alone (somebody else's is a 404);
* sending streams the answer (SSE) and saves it, with the tools used; the
  first question tells the agent who asks; a failure keeps what came;
* checks: empty / too long, relay not configured, too many messages, one
  answer at a time, "Dừng" keeps what came as CANCELLED, an answer orphaned
  by a restart becomes FAILED;
* the SSE reader of the relay's stream.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from meobot.api.routers.agents import get_agent_relay, get_answer_transactions
from meobot.application.agents.access import agent_access
from meobot.application.agents.service import AgentChatService, seed_default_agents
from meobot.application.agents.turns import TurnRegistry
from meobot.application.audit_service import AuditService
from meobot.core.time import utcnow
from meobot.db.models.agent_chat import AgentConversation, AgentMessage, AgentProfile
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.department import Department
from meobot.domain.agents.models import RATE_LIMIT, MessageRole, MessageStatus
from meobot.domain.identity.models import Role
from meobot.domain.units.models import UnitMembership
from meobot.integrations.hermes.relay import (
    FakeAgentRelay,
    RelayDelta,
    RelayError,
    RelayEvent,
    RelayTool,
    parse_sse,
)
from tests.unit.pr_world import World
from tests.unit.test_order_commands import person
from tests.unit.test_units_gating import error_reason

pytestmark = pytest.mark.asyncio

ANSWER: list[RelayEvent] = [
    RelayTool(tool="mcp_secondbrain_search_files", label="Tìm trong kho", emoji="🔎"),
    RelayDelta("Dạ, "),
    RelayDelta("em là Pháp chế."),
]


def setup(world: World, relay: FakeAgentRelay | None = None) -> FakeAgentRelay:
    relay = relay or FakeAgentRelay([list(ANSWER)])

    @asynccontextmanager
    async def same_session() -> AsyncIterator[Any]:
        yield world.session
        await world.session.flush()

    app = world.client.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_agent_relay] = lambda: relay
    app.dependency_overrides[get_answer_transactions] = lambda: same_session
    return relay


async def seeded(world: World) -> list[AgentProfile]:
    return await seed_default_agents(world.session)


def slugs(world: World) -> list[str]:
    response = world.client.get("/api/agents")
    assert response.status_code == 200, response.json()
    return [agent["slug"] for agent in response.json()["agents"]]


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for frame in body.split("\n\n"):
        lines = [line for line in frame.splitlines() if not line.startswith(":")]
        kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if kind and data:
            out.append((kind, json.loads(data)))
    return out


def open_conversation(world: World, slug: str = "phapche") -> str:
    made = world.client.post(f"/api/agents/{slug}/conversations")
    assert made.status_code == 201, made.json()
    return str(made.json()["id"])


# --- who sees which agent -------------------------------------------------------------


async def test_01_the_lists_decide_who_sees_an_agent(world: World) -> None:
    setup(world)
    await seeded(world)
    # The member sits in PR Thương Hiệu: the agents listing PR, and Pháp chế (everyone).
    world.act_as(world.member)
    assert slugs(world) == ["cmo", "mktnghiencuu", "mktnoidung", "phapche"]
    # Somebody in no department: Pháp chế only.
    outsider = await person(world, "Người ngoài")
    world.act_as(outsider)
    assert slugs(world) == ["phapche"]
    # The OWNER and an ADMIN: all fifteen.
    world.act_as(world.owner)
    assert len(slugs(world)) == 15
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    assert len(slugs(world)) == 15
    # AGENTS CHAT (ticked by the OWNER) = every agent.
    world.act_as(world.owner)
    made = world.client.post(
        f"/api/permissions/people/{outsider.id}/grants",
        json={"module": "AGENTS", "permission": "CHAT", "scope": "ALL"},
    )
    assert made.status_code == 201, made.json()
    world.act_as(outsider)
    assert len(slugs(world)) == 15
    # An agent switched off is gone for everybody.
    phapche = await world.session.scalar(select(AgentProfile).where(AgentProfile.slug == "phapche"))
    assert phapche is not None
    phapche.active = False
    await world.session.flush()
    world.act_as(world.owner)
    assert "phapche" not in slugs(world)


async def test_02_only_the_owner_manages_the_agents(world: World) -> None:
    setup(world)
    await seeded(world)
    admin = await person(world, "Quản trị", Role.ADMIN)
    for refused_for in (admin, world.member):
        world.act_as(refused_for)
        for call in (
            lambda: world.client.get("/api/agents/admin"),
            lambda: world.client.put("/api/agents/admin/mktads", json={"everyone": True}),
        ):
            refused = call()
            assert refused.status_code == 403, refused.json()
            assert error_reason(refused.json()) == "agents_forbidden"
    world.act_as(world.owner)
    listing = world.client.get("/api/agents/admin")
    assert listing.status_code == 200 and len(listing.json()["agents"]) == 15
    # Give Marketing Ads to the member by name.
    updated = world.client.put(
        "/api/agents/admin/mktads", json={"user_ids": [str(world.member.id)]}
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["user_ids"] == [str(world.member.id)]
    world.act_as(world.member)
    assert "mktads" in slugs(world)
    # An unknown department is refused.
    world.act_as(world.owner)
    bad = world.client.put("/api/agents/admin/mktads", json={"department_ids": [str(uuid.uuid4())]})
    assert bad.status_code == 422 and error_reason(bad.json()) == "agent_access_invalid"
    logs = (
        await world.session.scalars(
            select(AuditLog).where(AuditLog.action == "agent.access.updated")
        )
    ).all()
    assert len(logs) == 1 and logs[0].entity_id == "mktads"


async def test_03_units_me_tells_the_nav(world: World) -> None:
    setup(world)
    await seeded(world)
    world.act_as(world.member)
    assert world.client.get("/api/units/me").json()["agents"] == {"chat": True, "manage": False}
    world.act_as(world.owner)
    assert world.client.get("/api/units/me").json()["agents"] == {"chat": True, "manage": True}
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    assert world.client.get("/api/units/me").json()["agents"] == {"chat": True, "manage": False}


# --- conversations ----------------------------------------------------------------------


async def test_04_a_conversation_is_its_owners_alone(world: World) -> None:
    setup(world)
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    # An agent the member may not chat with: 404, nothing created.
    refused = world.client.post("/api/agents/ceo/conversations")
    assert refused.status_code == 404 and error_reason(refused.json()) == "agent_not_found"
    # Somebody else - even the OWNER - gets a 404 for it.
    world.act_as(world.owner)
    for call in (
        lambda: world.client.get(f"/api/agents/conversations/{conversation}/messages"),
        lambda: world.client.post(
            f"/api/agents/conversations/{conversation}/messages", json={"text": "Xin chào"}
        ),
        lambda: world.client.patch(
            f"/api/agents/conversations/{conversation}", json={"title": "Của tôi"}
        ),
    ):
        response = call()
        assert response.status_code == 404, response.json()
        assert error_reason(response.json()) == "conversation_not_found"
    assert world.client.get("/api/agents/phapche/conversations").json()["conversations"] == []


async def test_05_rename_and_archive(world: World) -> None:
    setup(world)
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    renamed = world.client.patch(
        f"/api/agents/conversations/{conversation}", json={"title": "  Hợp đồng KOL  "}
    )
    assert renamed.status_code == 200 and renamed.json()["title"] == "Hợp đồng KOL"
    listed = world.client.get("/api/agents/phapche/conversations").json()["conversations"]
    assert [item["title"] for item in listed] == ["Hợp đồng KOL"]
    assert world.client.delete(f"/api/agents/conversations/{conversation}").status_code == 204
    assert world.client.get("/api/agents/phapche/conversations").json()["conversations"] == []


# --- sending ------------------------------------------------------------------------------


async def test_06_the_answer_streams_and_is_saved(world: World) -> None:
    relay = setup(world)
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages",
        json={"text": "Soát giúp em câu: cam kết hết mụn sau 7 ngày"},
    )
    assert sent.status_code == 200, sent.text
    assert sent.headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in sent.headers["cache-control"]
    got = events(sent.text)
    kinds = [kind for kind, _ in got]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "tool" in kinds  # replayed even when the answer was already done
    streamed = "".join(data["text"] for kind, data in got if kind == "delta")
    assert streamed == "Dạ, em là Pháp chế."
    done = got[-1][1]["message"]
    assert done["status"] == "DONE" and done["content"] == "Dạ, em là Pháp chế."
    assert done["tools_used"] == ["mcp_secondbrain_search_files"]
    # Saved, the conversation titled from the first question.
    rows = (
        await world.session.scalars(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == uuid.UUID(conversation))
            .order_by(AgentMessage.created_at)
        )
    ).all()
    assert [(row.role, row.status) for row in rows] == [
        (MessageRole.USER, MessageStatus.DONE),
        (MessageRole.ASSISTANT, MessageStatus.DONE),
    ]
    assert rows[1].content == "Dạ, em là Pháp chế."
    row = await world.session.get(AgentConversation, uuid.UUID(conversation))
    assert row is not None and row.title.startswith("Soát giúp em câu")
    # What the agent was sent: who asks (first question only), its session, its memory scope.
    call = relay.calls[0]
    assert call["slug"] == "phapche"
    assert call["text"].startswith("[Người hỏi: Hảo - Nhân viên PR Thương Hiệu.")
    assert call["text"].endswith("cam kết hết mụn sau 7 ngày")
    assert call["session_id"] == f"apm-{conversation}"
    assert call["session_key"] == f"apm:{world.member.id}:phapche"
    assert call["idempotency_key"] == str(rows[0].id)
    world.client.post(
        f"/api/agents/conversations/{conversation}/messages", json={"text": "Còn câu khác?"}
    )
    assert relay.calls[1]["text"] == "Còn câu khác?"
    # The message list reads them back, oldest first; the audit never holds the text.
    listed = world.client.get(f"/api/agents/conversations/{conversation}/messages").json()
    assert [m["role"] for m in listed["messages"]] == ["USER", "ASSISTANT", "USER", "ASSISTANT"]
    logs = (
        await world.session.scalars(select(AuditLog).where(AuditLog.action == "agent.message.sent"))
    ).all()
    assert len(logs) == 2 and all("mụn" not in str(log.after_data) for log in logs)


async def test_07_a_failure_keeps_what_came(world: World) -> None:
    setup(world, FakeAgentRelay([[RelayDelta("Một phần")]], fail="agent_unreachable"))
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages", json={"text": "Hỏi"}
    )
    done = events(sent.text)[-1][1]["message"]
    assert done["status"] == "FAILED" and done["error_code"] == "agent_unreachable"
    assert done["content"] == "Một phần"
    # Nothing at all from the agent is a failure too.
    setup(world, FakeAgentRelay([[]]))
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages", json={"text": "Hỏi lại"}
    )
    done = events(sent.text)[-1][1]["message"]
    assert done["status"] == "FAILED" and done["error_code"] == "empty_answer"


async def test_08_what_a_message_needs(world: World) -> None:
    setup(world)
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    url = f"/api/agents/conversations/{conversation}/messages"
    for text in ("   ", "x" * 8001):
        refused = world.client.post(url, json={"text": text})
        assert refused.status_code == 422, refused.json()
        assert error_reason(refused.json()) == "agent_message_invalid"
    # The relay not configured: 503, nothing saved.
    setup(world, FakeAgentRelay(configured=False))
    refused = world.client.post(url, json={"text": "Xin chào"})
    assert refused.status_code == 503 and error_reason(refused.json()) == "agents_not_configured"
    assert world.client.get("/api/agents").json()["configured"] is False
    count = await world.session.scalar(
        select(AgentMessage.id).where(AgentMessage.conversation_id == uuid.UUID(conversation))
    )
    assert count is None
    # Too many questions in five minutes.
    setup(world)
    now = utcnow()
    world.session.add_all(
        AgentMessage(
            conversation_id=uuid.UUID(conversation),
            role=MessageRole.USER,
            content="q",
            status=MessageStatus.DONE,
            tools_used=[],
            created_at=now - timedelta(seconds=10),
        )
        for _ in range(RATE_LIMIT)
    )
    await world.session.flush()
    refused = world.client.post(url, json={"text": "Thêm một câu"})
    assert refused.status_code == 429 and error_reason(refused.json()) == "agent_rate_limited"


# --- one answer at a time, "Dừng", orphans -------------------------------------------


class SlowRelay(FakeAgentRelay):
    """Says a little, then waits until told to go on."""

    def __init__(self) -> None:
        super().__init__([[]])
        self.go_on = asyncio.Event()
        self.started = asyncio.Event()

    async def stream_chat(self, slug: str, **kwargs: str) -> AsyncIterator[RelayEvent]:  # type: ignore[override]
        self.calls.append({"slug": slug, **kwargs})
        yield RelayDelta("Đang viết")
        self.started.set()
        await self.go_on.wait()
        yield RelayDelta(" xong.")


async def prepared_turn(world: World) -> tuple[AgentChatService, Any, uuid.UUID]:
    await seeded(world)
    membership = UnitMembership(user_id=world.owner.id, is_owner=True)
    access = agent_access(membership)
    service = AgentChatService(world.session, AuditService(world.session))
    conversation = await service.create_conversation(access, "ceo", request_id=uuid.uuid4())
    prepared = await service.prepare_turn(
        access,
        conversation.id,
        "Báo cáo tuần",
        asker="Chủ sở hữu",
        seats=(),
        request_id=uuid.uuid4(),
    )
    return service, prepared, conversation.id


async def test_09_one_answer_at_a_time_and_stop_keeps_what_came(world: World) -> None:
    _, prepared, conversation_id = await prepared_turn(world)

    @asynccontextmanager
    async def same_session() -> AsyncIterator[Any]:
        yield world.session
        await world.session.flush()

    registry = TurnRegistry()
    relay = SlowRelay()
    assert registry.reserve(conversation_id)
    turn = registry.start(prepared, relay, same_session)
    await asyncio.wait_for(relay.started.wait(), timeout=5)
    assert not registry.reserve(conversation_id)  # busy
    assert registry.running_for(conversation_id) is turn
    assert await registry.stop(conversation_id) is True
    row = await world.session.get(AgentMessage, prepared.answer_id)
    assert row is not None
    assert row.status is MessageStatus.CANCELLED and row.content == "Đang viết"
    assert registry.running_for(conversation_id) is None and registry.reserve(conversation_id)


async def test_10_an_answer_left_streaming_by_a_restart_fails(world: World) -> None:
    service, prepared, conversation_id = await prepared_turn(world)
    # Nothing runs it in this process (the API restarted): reading it fails it.
    await service.fail_orphans(conversation_id, running=set())
    row = await world.session.get(AgentMessage, prepared.answer_id)
    assert row is not None
    assert row.status is MessageStatus.FAILED and row.error_code == "interrupted"


# --- the relay's stream ----------------------------------------------------------------


async def test_11_reading_the_relay_stream() -> None:
    lines = [
        ": keepalive",
        "",
        "event: hermes.tool.progress",
        'data: {"tool": "web_search", "emoji": "🔍", "label": "Tìm: giá đối thủ",'
        ' "status": "running"}',
        "",
        "event: hermes.tool.progress",
        'data: {"tool": "web_search", "status": "completed"}',
        "",
        'data: {"choices": [{"delta": {"role": "assistant"}}]}',
        "",
        'data: {"choices": [{"delta": {"content": "Xin "}}]}',
        "",
        'data: {"choices": [{"delta": {"content": "chào"}}]}',
        "",
        "data: [DONE]",
        "",
    ]
    assert parse_sse(lines) == [
        RelayTool(tool="web_search", label="Tìm: giá đối thủ", emoji="🔍"),
        RelayDelta("Xin "),
        RelayDelta("chào"),
    ]
    with pytest.raises(RelayError) as failed:
        parse_sse(['data: {"error": {"message": "quota"}}', ""])
    assert failed.value.code == "agent_error"


async def test_12_seeded_departments_follow_the_codes(world: World) -> None:
    await seeded(world)
    codes = {
        row.code: str(row.id) for row in (await world.session.scalars(select(Department))).all()
    }
    cfo = await world.session.scalar(select(AgentProfile).where(AgentProfile.slug == "cfo"))
    assert cfo is not None
    # BGD exists in the test database; TCKT does not and is skipped.
    assert cfo.allowed_department_ids == [codes["BGD"]]
    phapche = await world.session.scalar(select(AgentProfile).where(AgentProfile.slug == "phapche"))
    assert phapche is not None and phapche.everyone and phapche.allowed_department_ids == []
