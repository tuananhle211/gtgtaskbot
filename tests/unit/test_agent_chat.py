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


async def test_05_rename_and_delete(world: World) -> None:
    relay = setup(world)
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    renamed = world.client.patch(
        f"/api/agents/conversations/{conversation}", json={"title": "  Hợp đồng KOL  "}
    )
    assert renamed.status_code == 200 and renamed.json()["title"] == "Hợp đồng KOL"
    listed = world.client.get("/api/agents/phapche/conversations").json()["conversations"]
    assert [item["title"] for item in listed] == ["Hợp đồng KOL"]
    world.client.post(f"/api/agents/conversations/{conversation}/messages", json={"text": "Hỏi"})
    # Somebody else cannot delete it (404), its owner deletes it for good.
    world.act_as(world.owner)
    assert world.client.delete(f"/api/agents/conversations/{conversation}").status_code == 404
    world.act_as(world.member)
    assert world.client.delete(f"/api/agents/conversations/{conversation}").status_code == 204
    assert world.client.get("/api/agents/phapche/conversations").json()["conversations"] == []
    assert await world.session.get(AgentConversation, uuid.UUID(conversation)) is None
    left = (
        await world.session.scalars(
            select(AgentMessage).where(AgentMessage.conversation_id == uuid.UUID(conversation))
        )
    ).all()
    assert left == []
    # ... and its transcript on the Hermes side; the audit keeps that it happened.
    assert relay.deleted == [("phapche", f"apm-{conversation}")]
    log = await world.session.scalar(
        select(AuditLog).where(AuditLog.action == "agent.conversation.deleted")
    )
    assert log is not None and log.after_data == {"agent": "phapche", "messages": 2}
    assert world.client.get(f"/api/agents/conversations/{conversation}/messages").status_code == 404


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


# --- attachments (0092) ---------------------------------------------------------------


def _png() -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    header = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _pdf(words: str | None) -> bytes:
    """A one-page PDF saying ``words`` (None = a page with no text, like a scan)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({words}) Tj ET".encode() if words else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _zip(files: dict[str, str]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _docx(*paragraphs: str) -> bytes:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    table = (
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Gói</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Giá</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    document = f"<w:document {ns}><w:body>{body}{table}</w:body></w:document>"
    return _zip({"word/document.xml": document})


def _xlsx() -> bytes:
    import io

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Ngân sách"
    sheet.append(["Kênh", "Tiền"])
    sheet.append(["Facebook", 1200000])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _pptx() -> bytes:
    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    slide = f"<p:sld {ns} xmlns:p='x'><a:p><a:r><a:t>Chiến dịch tháng 11</a:t></a:r></a:p></p:sld>"
    return _zip({"ppt/slides/slide1.xml": slide})


SKILL_MD = "---\nname: soat-hop-dong\ndescription: Soát hợp đồng KOL\n---\n\n# Các bước\n1. Đọc"


async def test_13_reading_every_kind_of_file() -> None:
    from meobot.application.agents.files import read_upload
    from meobot.domain.agents.errors import AgentFileInvalidError
    from meobot.domain.agents.models import AttachmentKind

    image = read_upload("anh.jpg", _png())  # the bytes decide, not the name
    assert (image.kind, image.content_type, image.text) == (AttachmentKind.IMAGE, "image/png", None)
    pdf = read_upload("hop-dong.pdf", _pdf("Hop dong KOL 2026"))
    assert pdf.kind == AttachmentKind.DOCUMENT and pdf.pages == 1
    assert "Hop dong KOL 2026" in (pdf.text or "") and pdf.note == "1 trang"
    word = read_upload("brief.docx", _docx("Mục tiêu: 200 lead", "Ngân sách 50 triệu"))
    assert "Mục tiêu: 200 lead" in (word.text or "") and "Gói | Giá" in (word.text or "")
    sheet = read_upload("ngan-sach.xlsx", _xlsx())
    assert "--- Sheet: Ngân sách ---" in (sheet.text or "")
    assert "Facebook\t1200000" in (sheet.text or "")
    slides = read_upload("deck.pptx", _pptx())
    assert "Chiến dịch tháng 11" in (slides.text or "") and slides.note == "1 slide"
    text = read_upload("ghi-chu.txt", "Ghi chú họp".encode("utf-16"))
    assert text.text == "Ghi chú họp"
    skill = read_upload("SKILL.md", SKILL_MD.encode())
    assert skill.kind == AttachmentKind.SKILL and skill.note == "Skill: soat-hop-dong"
    packed = read_upload(
        "soat.zip",
        _zip(
            {
                "soat-hop-dong/SKILL.md": SKILL_MD,
                "soat-hop-dong/references/mau.md": "Mẫu điều khoản",
                "soat-hop-dong/logo.png": "x",
                "__MACOSX/soat-hop-dong/._SKILL.md": "rác",
            }
        ),
    )
    assert packed.kind == AttachmentKind.SKILL and packed.note == "Skill: soat-hop-dong · 2 tệp"
    assert "--- references/mau.md ---\nMẫu điều khoản" in (packed.text or "")
    assert "rác" not in (packed.text or "")
    # Refused, each with what to do instead.
    for name, data, says in [
        ("clip.mp4", b"\x00\x00\x00 ftypisom", "video"),
        ("scan.pdf", _pdf(None), "bản scan"),
        ("cu.doc", b"\xd0\xcf\x11\xe0", ".docx"),
        ("tai-lieu.zip", _zip({"a.txt": "x"}), "SKILL.md"),
        ("logo.svg", b"<svg/>", "PNG"),
        ("hong.docx", b"PK not a zip", "tệp hỏng"),
        ("bom.docx", _zip({"word/document.xml": '<!DOCTYPE x [<!ENTITY a "a">]><x/>'}), "hỏng"),
        ("trong.txt", b"", "trống"),
    ]:
        with pytest.raises(AgentFileInvalidError) as refused:
            read_upload(name, data)
        assert says in str(refused.value), (name, str(refused.value))


def upload(world: World, conversation: str, name: str, data: bytes) -> Any:
    return world.client.post(
        f"/api/agents/conversations/{conversation}/attachments",
        params={"name": name},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


async def test_14_files_go_with_the_question(world: World) -> None:
    relay = setup(world)
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    picture = upload(world, conversation, "banner.png", _png())
    assert picture.status_code == 201, picture.json()
    assert picture.json()["kind"] == "IMAGE"
    contract = upload(world, conversation, "../../hop-dong.pdf", _pdf("Dieu 5 cam ket"))
    assert contract.status_code == 201, contract.json()
    assert contract.json()["name"] == "hop-dong.pdf" and contract.json()["note"] == "1 trang"
    skill = upload(world, conversation, "SKILL.md", SKILL_MD.encode())
    assert skill.json()["kind"] == "SKILL"
    refused = upload(world, conversation, "clip.mov", b"\x00\x00\x00 ftypqt")
    assert refused.status_code == 422 and error_reason(refused.json()) == "agent_file_invalid"
    # A file taken back out of the composer is gone.
    extra = upload(world, conversation, "nhap.txt", b"bo di")
    assert world.client.delete(f"/api/agents/attachments/{extra.json()['id']}").status_code == 204
    ids = [picture.json()["id"], contract.json()["id"], skill.json()["id"]]
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages",
        json={"text": "Soát giúp em", "attachment_ids": ids},
    )
    assert sent.status_code == 200, sent.text
    start = events(sent.text)[0][1]["user_message"]
    assert [item["name"] for item in start["attachments"]] == [
        "banner.png",
        "hop-dong.pdf",
        "SKILL.md",
    ]
    # The agent got the picture as a picture, the PDF's words and the skill in the text.
    call = relay.calls[0]
    assert len(call["images"]) == 1 and call["images"][0].startswith("data:image/png;base64,")
    assert "[Ảnh đính kèm: banner.png]" in call["text"]
    assert "[Tệp đính kèm: hop-dong.pdf - 1 trang]" in call["text"]
    assert "Dieu 5 cam ket" in call["text"]
    assert "[Skill đính kèm: SKILL.md - Skill: soat-hop-dong]" in call["text"]
    assert "Emma duyệt" in call["text"]
    # The conversation reads them back; a sent file stays (cannot be taken back).
    listed = world.client.get(f"/api/agents/conversations/{conversation}/messages").json()
    assert [len(m["attachments"]) for m in listed["messages"]] == [3, 0]
    assert world.client.delete(f"/api/agents/attachments/{ids[0]}").status_code == 422
    # Opening a file: a picture shows, a document downloads; somebody else gets a 404.
    shown = world.client.get(f"/api/agents/attachments/{ids[0]}")
    assert shown.status_code == 200 and shown.content == _png()
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline")
    assert "sandbox" in shown.headers["content-security-policy"]
    pdf = world.client.get(f"/api/agents/attachments/{ids[1]}")
    assert pdf.headers["content-type"] == "application/octet-stream"
    assert pdf.headers["content-disposition"].startswith("attachment")
    world.act_as(world.owner)
    assert world.client.get(f"/api/agents/attachments/{ids[0]}").status_code == 404
    assert upload(world, conversation, "x.txt", b"x").status_code == 404
    # An id already sent cannot go with another question.
    world.act_as(world.member)
    again = world.client.post(
        f"/api/agents/conversations/{conversation}/messages",
        json={"text": "Lại", "attachment_ids": [ids[1]]},
    )
    assert again.status_code == 422 and error_reason(again.json()) == "agent_file_invalid"
    # Deleting the conversation deletes its files.
    from meobot.db.models.agent_chat import AgentAttachment

    assert world.client.delete(f"/api/agents/conversations/{conversation}").status_code == 204
    assert (await world.session.scalars(select(AgentAttachment))).all() == []


async def test_15_files_only_and_the_limits(world: World) -> None:
    relay = setup(world, FakeAgentRelay([list(ANSWER)] * 3))
    await seeded(world)
    world.act_as(world.member)
    conversation = open_conversation(world)
    only = upload(world, conversation, "brief.docx", _docx("Brief banner Tết"))
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages",
        json={"text": "", "attachment_ids": [only.json()["id"]]},
    )
    assert sent.status_code == 200, sent.text
    assert "(Người hỏi gửi tệp đính kèm, không kèm lời nhắn.)" in relay.calls[0]["text"]
    assert relay.calls[0]["images"] == []
    row = await world.session.get(AgentConversation, uuid.UUID(conversation))
    assert row is not None and row.title == "brief.docx"
    # At most six files wait for one question.
    for index in range(6):
        assert upload(world, conversation, f"{index}.txt", b"x").status_code == 201
    seventh = upload(world, conversation, "7.txt", b"x")
    assert seventh.status_code == 422 and "tối đa 6" in seventh.json()["error"]["message"]
    # Too big is refused before anything is read.
    huge = upload(world, conversation, "lon.pdf", b"%PDF-" + b"0" * (20 * 1024 * 1024))
    assert huge.status_code == 422 and "20 MB" in huge.json()["error"]["message"]
    # Nothing and no file: still "empty".
    empty = world.client.post(
        f"/api/agents/conversations/{conversation}/messages", json={"text": "  "}
    )
    assert empty.status_code == 422 and error_reason(empty.json()) == "agent_message_invalid"


async def test_16_the_relay_sends_pictures_as_parts() -> None:
    from meobot.integrations.hermes.relay import chat_content

    assert chat_content("Hỏi", []) == "Hỏi"
    assert chat_content("Hỏi", ["data:image/png;base64,AA=="]) == [
        {"type": "text", "text": "Hỏi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]


# --- skills (2026-11-06) ----------------------------------------------------------------


async def test_17_each_agent_shows_its_skills(world: World) -> None:
    from meobot.integrations.hermes.relay import AgentSkill, parse_skills

    book = {
        "phapche": [
            AgentSkill("hop-dong-mau", "Soát hợp đồng", "kho"),
            AgentSkill("ghi-chu-rieng", "Agent tự học", "rieng"),
            AgentSkill("arxiv", "Tìm bài báo", "co-san", "research"),
        ],
        "cfo": [AgentSkill("doc-so", "Đọc số", "kho")],
    }
    setup(world, FakeAgentRelay([list(ANSWER)], skill_book=book))
    await seeded(world)
    # Only the agents the caller chats with; an agent the relay does not list = empty.
    outsider = await person(world, "Người ngoài")
    world.act_as(outsider)
    body = world.client.get("/api/agents/skills").json()
    assert body["available"] is True
    assert list(body["agents"]) == ["phapche"]
    assert body["agents"]["phapche"] == {
        "approved": [{"name": "hop-dong-mau", "description": "Soát hợp đồng", "group": None}],
        "own": [{"name": "ghi-chu-rieng", "description": "Agent tự học", "group": None}],
        "builtin": [{"name": "arxiv", "description": "Tìm bài báo", "group": "research"}],
    }
    world.act_as(world.owner)
    agents = world.client.get("/api/agents/skills").json()["agents"]
    assert len(agents) == 15
    assert agents["cfo"]["approved"][0]["name"] == "doc-so"
    assert agents["ceo"] == {"approved": [], "own": [], "builtin": []}
    # The relay cannot say: the screen hides the counts.
    setup(world, FakeAgentRelay(configured=False))
    assert world.client.get("/api/agents/skills").json() == {"available": False, "agents": {}}
    # What the relay sends is read defensively.
    parsed = parse_skills(
        {
            "agents": {
                "x": [
                    {"name": "a", "source": "kho"},
                    {"name": "", "source": "kho"},
                    {"name": "b", "source": "la"},
                    "junk",
                ],
                "y": "junk",
            }
        }
    )
    assert parsed == {"x": [AgentSkill("a", "", "kho")]}
    assert parse_skills(["junk"]) == {}


async def test_18_pictures_the_agent_makes_come_with_its_answer(world: World) -> None:
    import base64

    from meobot.domain.agents.media import find_media
    from meobot.integrations.hermes.relay import RelayMedia

    made = "/home/hermes/.hermes/profiles/mktthietke/cache/generated/images/banner_1.png"
    lost = "/home/hermes/.hermes/profiles/mktthietke/cache/generated/images/mat.png"
    inline = base64.b64encode(_png()).decode()
    answer: list[RelayEvent] = [
        RelayTool(tool="image_generate", label="Vẽ ảnh", emoji="🎨"),
        RelayDelta("Dạ em gửi banner:\n\nMEDIA:"),
        RelayDelta(f"{made}\n\nBản nhỏ: ![image](data:image/png;base64,{inline})\n"),
        RelayDelta(f"`MEDIA:{lost}` và lặp lại MEDIA:{made}\n\nSếp xem ạ."),
    ]
    relay = setup(
        world,
        FakeAgentRelay([answer], media={made: RelayMedia(data=_png(), content_type="image/png")}),
    )
    await seeded(world)
    world.act_as(world.owner)
    conversation = open_conversation(world, "mktthietke")
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages", json={"text": "Vẽ banner"}
    )
    assert sent.status_code == 200, sent.text
    done = events(sent.text)[-1][1]["message"]
    # The tags are gone from the words; the one the relay would not hand is a note.
    assert "MEDIA:" not in done["content"] and "data:image" not in done["content"]
    assert done["content"].startswith("Dạ em gửi banner:")
    assert "(Không lấy được ảnh mat.png)" in done["content"]
    assert done["content"].endswith("Sếp xem ạ.")
    # Two pictures kept as the answer's files, the repeat fetched once.
    names = [(item["name"], item["kind"]) for item in done["attachments"]]
    assert names == [("banner_1.png", "IMAGE"), ("anh-agent.png", "IMAGE")]
    assert relay.media_asked == [("mktthietke", made), ("mktthietke", lost)]
    listed = world.client.get(f"/api/agents/conversations/{conversation}/messages").json()
    assert [len(m["attachments"]) for m in listed["messages"]] == [0, 2]
    shown = world.client.get(done["attachments"][0]["url"])
    assert shown.status_code == 200 and shown.content == _png()
    # Only the conversation's owner opens them.
    world.act_as(world.member)
    assert world.client.get(done["attachments"][0]["url"]).status_code == 404
    # The reader: a path only once; at most six pictures.
    many = "\n".join(f"MEDIA:/x/{n}.png" for n in range(9))
    assert len([ref for ref in find_media(many) if ref.path]) == 6
    assert find_media("không có ảnh") == []


async def test_19_a_whole_html_page_becomes_a_file_with_a_sandboxed_preview(world: World) -> None:
    page = (
        "<!doctype html>\n<html><head><style>h1{color:red}</style></head>"
        "<body><h1>Mới</h1></body></html>"
    )
    answer: list[RelayEvent] = [
        RelayDelta("Dạ bản thiết kế lại:\n\n```html\n"),
        RelayDelta(page + "\n```\n\nVà đoạn nhỏ:\n```html\n<button>Đặt lịch</button>\n```"),
    ]
    setup(world, FakeAgentRelay([answer]))
    await seeded(world)
    world.act_as(world.owner)
    conversation = open_conversation(world, "mktthietke")
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages", json={"text": "Làm lại trang chủ"}
    )
    done = events(sent.text)[-1][1]["message"]
    # The page left the text (a line names its file); a snippet stays as code.
    assert "<!doctype" not in done["content"]
    assert "*(Trang HTML: giao-dien-1.html - xem trước ở tệp bên dưới)*" in done["content"]
    assert "<button>Đặt lịch</button>" in done["content"]
    [file] = done["attachments"]
    assert (file["name"], file["kind"], file["content_type"]) == (
        "giao-dien-1.html",
        "DOCUMENT",
        "text/html",
    )
    # Downloading gives the file; the preview is a page in a sandbox of its own.
    assert world.client.get(file["url"]).content == page.encode()
    shown = world.client.get(f"{file['url']}/view")
    assert shown.status_code == 200 and shown.content == page.encode()
    assert shown.headers["content-type"].startswith("text/html")
    csp = shown.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp
    # Only an HTML file previews; only its owner sees it.
    from meobot.db.models.agent_chat import AgentAttachment

    picture = upload(world, conversation, "a.png", _png())
    picture_view = f"/api/agents/attachments/{picture.json()['id']}/view"
    assert world.client.get(picture_view).status_code == 404
    world.act_as(world.member)
    assert world.client.get(f"{file['url']}/view").status_code == 404
    assert (await world.session.scalars(select(AgentAttachment))).all()


async def test_20_a_brand_asset_comes_as_a_file_to_download(world: World) -> None:
    """2026-11-09: the brand library (/srv/brain/tai-san) - a logo SVG or the
    guideline PDF is a document of the answer; a PNG logo stays a picture; a
    file whose bytes do not fit its name is refused."""
    from meobot.integrations.hermes.relay import RelayMedia

    root = "/srv/brain/tai-san"
    svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>'
    pdf = b"%PDF-1.7\n%..."
    media = {
        f"{root}/logo/apexmed-ngang.svg": RelayMedia(data=svg, content_type="image/svg+xml"),
        f"{root}/logo/apexmed.png": RelayMedia(data=_png(), content_type="image/png"),
        f"{root}/guideline.pdf": RelayMedia(data=pdf, content_type="application/pdf"),
        f"{root}/font/gia.otf": RelayMedia(data=b"not a font", content_type="font/otf"),
    }
    answer: list[RelayEvent] = [
        RelayDelta(
            "Dạ logo đây ạ:\n\n"
            + "\n".join(f"MEDIA:{path}" for path in media)
            + "\n\nCần bản khác cứ bảo em."
        ),
    ]
    setup(world, FakeAgentRelay([answer], media=media))
    await seeded(world)
    world.act_as(world.owner)
    conversation = open_conversation(world, "mktthietke")
    sent = world.client.post(
        f"/api/agents/conversations/{conversation}/messages", json={"text": "Cho xin logo"}
    )
    assert sent.status_code == 200, sent.text
    done = events(sent.text)[-1][1]["message"]
    assert "MEDIA:" not in done["content"]
    assert "(Không lấy được tệp gia.otf)" in done["content"]
    kept = [(f["name"], f["kind"], f["content_type"]) for f in done["attachments"]]
    assert kept == [
        ("apexmed-ngang.svg", "DOCUMENT", "image/svg+xml"),
        ("apexmed.png", "IMAGE", "image/png"),
        ("guideline.pdf", "DOCUMENT", "application/pdf"),
    ]
    # An SVG is a download, never drawn in the app's own origin.
    got = world.client.get(done["attachments"][0]["url"])
    assert got.content == svg
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment;")
