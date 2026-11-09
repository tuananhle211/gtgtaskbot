"""Chăm sóc khách hàng (0094): Pancake pages answered by a Hermes agent.

* who: ``CSKH REPLY`` reads / sends suggestions (OWNER, ADMIN by role, or
  ticked), ``CSKH MANAGE`` wires pages (the OWNER);
* pages: the token stored encrypted (only ``••••abcd`` leaves), the webhook
  address with its secret key, "Kiểm tra token", no switching on without a token;
* the webhook: unknown key 404, a customer's inbox message queued once, comments
  ignored, a staff reply on Pancake pauses the bot there;
* answering: the agent gets the briefing on the first turn and only the new
  messages after; SUGGEST keeps the answer, AUTO sends it only when the AI
  check passed and the agent did not ask for a person; a burst is answered once;
* the inbox: the live thread, send (edited) / dismiss once, resume a pause.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from meobot.api.routers.cskh import get_cskh_runner, get_cskh_secret_box, get_pancake_client
from meobot.application.cskh.pipeline import CskhDeps, Review, answer_conversation
from meobot.core.secrets import SecretBox, build_secret_box, generate_key
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.pancake import PancakeConversation, PancakePage, PancakeReply
from meobot.domain.identity.models import Role
from meobot.domain.pancake.models import (
    HANDOFF_MARKER,
    ReplyStatus,
    ThreadMessage,
    WebhookConversation,
    WebhookMessage,
    parse_thread,
    parse_webhook,
    read_answer,
)
from meobot.integrations.hermes.relay import FakeAgentRelay, RelayDelta
from meobot.integrations.pancake.client import FakePancakeClient
from tests.unit.pr_world import World
from tests.unit.test_agent_chat import seeded
from tests.unit.test_order_commands import person

pytestmark = pytest.mark.asyncio


def error_reason(body: dict[str, Any]) -> str | None:
    details = body.get("details") or body.get("error", {}).get("details") or {}
    return details.get("reason")


TOKEN = "pk-token-abcdef123456"
PAGE = "1122334455"


class FakeRunner:
    def __init__(self) -> None:
        self.scheduled: list[uuid.UUID] = []

    def schedule(self, conv_id: uuid.UUID, deps: Any) -> None:
        self.scheduled.append(conv_id)


class Kit:
    def __init__(self, world: World) -> None:
        settings = world.settings.model_copy(
            update={
                "pr_secret_encryption_key": SecretStr(generate_key()),
                "pr_secret_encryption_key_file": None,
            }
        )
        self.world = world
        self.box: SecretBox = build_secret_box(settings)
        self.pancake = FakePancakeClient()
        self.runner = FakeRunner()
        self.relay = FakeAgentRelay([[RelayDelta("Dạ, "), RelayDelta("em chào anh/chị ạ.")]])
        app = world.client.app  # type: ignore[attr-defined]
        app.dependency_overrides[get_cskh_secret_box] = lambda: lambda: self.box
        app.dependency_overrides[get_pancake_client] = lambda: self.pancake
        app.dependency_overrides[get_cskh_runner] = lambda: self.runner

    def deps(self, review: Review | None = None) -> CskhDeps:
        world = self.world

        @asynccontextmanager
        async def same_session() -> AsyncIterator[Any]:
            yield world.session
            await world.session.flush()

        async def reviewer(text: str, rules: str) -> Review:
            return review or Review(passed=True)

        return CskhDeps(
            relay=self.relay,
            client=self.pancake,
            transactions=same_session,
            secret_box=lambda: self.box,
            reviewer=reviewer,
            debounce=0,
        )


def create_page(world: World, **body: Any) -> dict[str, Any]:
    world.act_as(world.owner)
    payload = {"page_id": PAGE, "name": "Apexmed HN", "access_token": TOKEN, "active": True}
    payload.update(body)
    made = world.client.post("/api/cskh/pages", json=payload)
    assert made.status_code == 201, made.json()
    return dict(made.json())


def key_of(page: dict[str, Any]) -> str:
    return str(page["webhook_url"]).rsplit("/", 1)[-1]


def customer(message_id: str, conv: str = "psid-1", *, kind: str = "INBOX") -> dict[str, Any]:
    return {
        "event_type": "messaging",
        "page_id": PAGE,
        "data": {
            "conversation": {"id": conv, "type": kind, "tags": [], "assignee_ids": []},
            "message": {
                "id": message_id,
                "conversation_id": conv,
                "type": kind,
                "from": {"id": "cust-9", "name": "Khách"},
                "message": "Giá nâng mũi bao nhiêu ạ?",
                "inserted_at": "2026-11-09T02:00:00.000000",
                "page_id": PAGE,
            },
        },
    }


def staff(message_id: str, conv: str = "psid-1") -> dict[str, Any]:
    body = customer(message_id, conv)
    body["data"]["message"]["from"] = {"id": PAGE, "name": "Apexmed HN"}
    return body


def said(message_id: str, text: str, *, page: bool = False, minute: int = 0) -> ThreadMessage:
    return ThreadMessage(
        id=message_id,
        from_page=page,
        text=text,
        at=datetime(2026, 11, 9, 2, minute, tzinfo=UTC),
    )


async def replies(world: World) -> list[PancakeReply]:
    return list(
        (await world.session.scalars(select(PancakeReply).order_by(PancakeReply.created_at))).all()
    )


async def only_conversation(world: World) -> PancakeConversation:
    conv = await world.session.scalar(select(PancakeConversation))
    assert conv is not None
    return conv


# --- the domain -------------------------------------------------------------------------


async def test_01_reading_pancake() -> None:
    event = parse_webhook(customer("m1"))
    assert isinstance(event, WebhookMessage)
    assert (event.page_id, event.conversation_id, event.message_id) == (PAGE, "psid-1", "m1")
    assert event.conversation_type == "INBOX" and not event.from_page
    assert event.at == datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    page_event = parse_webhook(staff("m2"))
    assert isinstance(page_event, WebhookMessage) and page_event.from_page
    tags = parse_webhook(
        {
            "event_type": "conversation",
            "page_id": PAGE,
            "data": {
                "conversation": {
                    "id": "psid-1",
                    "tags": [{"id": 7}, None, {"id": 9, "is_deactive": True}],
                    "assignee_ids": ["u-1"],
                }
            },
        }
    )
    assert isinstance(tags, WebhookConversation)
    assert tags.tag_ids == ("7",) and tags.assignee_ids == ("u-1",)
    assert parse_webhook({"event_type": "post"}) is None
    assert parse_webhook("rác") is None

    thread = parse_thread(
        {
            "can_inbox": False,
            "messages": [
                {
                    "id": "b",
                    "from": {"id": PAGE},
                    "message": "<div>Dạ&nbsp;em đây</div>",
                    "inserted_at": "2026-11-09T02:05:00",
                },
                {
                    "id": "a",
                    "from": {"id": "c"},
                    "original_message": "Chào",
                    "inserted_at": "2026-11-09T02:01:00",
                },
                {"id": "x", "from": {"id": "c"}, "message": "", "is_removed": True},
            ],
        },
        PAGE,
    )
    assert [m.id for m in thread.messages] == ["a", "b"]
    assert thread.messages[1].from_page and thread.messages[1].text == "Dạ\xa0em đây"
    assert thread.can_inbox is False

    answer = read_answer(f"{HANDOFF_MARKER} khách muốn đặt lịch\n**Dạ** em chuyển tư vấn viên ạ.")
    assert answer.handoff and answer.handoff_reason == "khách muốn đặt lịch"
    assert answer.text == "Dạ em chuyển tư vấn viên ạ."
    assert read_answer("x" * 3000).text == "x" * 2000


# --- who --------------------------------------------------------------------------------


async def test_02_who_uses_the_module(world: World) -> None:
    Kit(world)
    await seeded(world)
    world.act_as(world.member)
    assert error_reason(world.client.get("/api/cskh/replies").json()) == "cskh_forbidden"
    assert world.client.get("/api/units/me").json()["cskh"]["reply"] is False
    # The OWNER ticks CSKH REPLY for them: the inbox, not the pages.
    world.act_as(world.owner)
    granted = world.client.post(
        f"/api/permissions/people/{world.member.id}/grants",
        json={"module": "CSKH", "permission": "REPLY", "scope": "ALL"},
    )
    assert granted.status_code == 201, granted.json()
    world.act_as(world.member)
    assert world.client.get("/api/cskh/replies").status_code == 200
    assert world.client.get("/api/cskh/pages").status_code == 403
    me = world.client.get("/api/units/me").json()["cskh"]
    assert me == {"reply": True, "manage": False, "waiting": 0}
    # An ADMIN replies by role; the pages are the OWNER's.
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    assert world.client.get("/api/cskh/access").json()["reply"] is True
    assert world.client.get("/api/cskh/pages").status_code == 403
    world.act_as(world.owner)
    assert world.client.get("/api/cskh/access").json()["manage"] is True


# --- pages ------------------------------------------------------------------------------


async def test_03_pages_keep_the_token_secret(world: World) -> None:
    kit = Kit(world)
    await seeded(world)
    world.act_as(world.owner)
    # No switching on without a token; an unknown agent is refused.
    off = world.client.post(
        "/api/cskh/pages", json={"page_id": PAGE, "name": "Apexmed HN", "active": True}
    )
    assert error_reason(off.json()) == "cskh_token_missing"
    bad_agent = world.client.post(
        "/api/cskh/pages", json={"page_id": PAGE, "name": "A", "agent_slug": "khong-co"}
    )
    assert error_reason(bad_agent.json()) == "cskh_agent_invalid"
    page = create_page(world)
    assert page["access_token_hint"] == "••••3456" and page["has_token"] is True
    assert TOKEN not in str(page)
    assert page["agent_slug"] == "chatpage" and page["mode"] == "SUGGEST"
    assert page["webhook_url"].startswith(
        f"{world.settings.web_base_url.rstrip('/')}/api/cskh/pancake/webhook/"
    )
    stored = await world.session.get(PancakePage, uuid.UUID(page["id"]))
    assert stored is not None and stored.access_token_encrypted
    assert TOKEN not in stored.access_token_encrypted
    again = world.client.post("/api/cskh/pages", json={"page_id": PAGE, "name": "B"})
    assert error_reason(again.json()) == "cskh_page_exists"
    # "Kiểm tra token" reads Pancake with the real token.
    checked = world.client.post(f"/api/cskh/pages/{page['id']}/check").json()
    assert checked["last_check_ok"] is True and kit.pancake.checked == [PAGE]
    # Editing without a token keeps it; a new address replaces the old one.
    edited = world.client.patch(
        f"/api/cskh/pages/{page['id']}",
        json={"mode": "AUTO", "handoff_tag_ids": ["12", "12", "15"], "rotate_webhook_key": True},
    ).json()
    assert edited["has_token"] and edited["mode"] == "AUTO"
    assert edited["handoff_tag_ids"] == ["12", "15"]
    assert edited["webhook_url"] != page["webhook_url"]
    assert (
        world.client.post(f"/api/cskh/pancake/webhook/{key_of(page)}", json={}).status_code == 404
    )
    listed = world.client.get("/api/cskh/pages").json()
    assert [p["page_id"] for p in listed["pages"]] == [PAGE]
    assert "chatpage" in [a["slug"] for a in listed["agents"]]
    audit = await world.session.scalars(
        select(AuditLog.action).where(AuditLog.action.like("cskh.page.%"))
    )
    assert set(audit.all()) == {"cskh.page.created", "cskh.page.updated"}
    assert world.client.delete(f"/api/cskh/pages/{page['id']}").status_code == 204
    assert world.client.get("/api/cskh/pages").json()["pages"] == []


# --- the webhook ------------------------------------------------------------------------


async def test_04_the_webhook_queues_a_customer_message_once(world: World) -> None:
    kit = Kit(world)
    await seeded(world)
    page = create_page(world, active=False)
    url = f"/api/cskh/pancake/webhook/{key_of(page)}"
    assert world.client.post("/api/cskh/pancake/webhook/sai-key", json={}).status_code == 404
    # Switched off: taken (the page shows the last event), nothing answered.
    assert world.client.post(url, json=customer("m1")).status_code == 200
    assert await replies(world) == [] and kit.runner.scheduled == []
    stored = await world.session.get(PancakePage, uuid.UUID(page["id"]))
    assert stored is not None and stored.last_event_at is not None
    world.client.patch(f"/api/cskh/pages/{page['id']}", json={"active": True})
    world.client.post(url, json=customer("m2"))
    world.client.post(url, json=customer("m2"))  # delivered twice
    world.client.post(url, json=customer("c1", "post_1", kind="COMMENT"))
    rows = await replies(world)
    assert [(r.customer_message_id, r.status) for r in rows] == [("m2", ReplyStatus.WAITING)]
    assert len(kit.runner.scheduled) == 1
    # A staff member answers on Pancake: the bot keeps quiet there.
    world.client.post(url, json=staff("s1"))
    await world.session.refresh(rows[0])
    assert rows[0].status == ReplyStatus.SKIPPED and rows[0].reason == "staff_replied"
    conv = await only_conversation(world)
    assert conv.pause_reason == "staff_replied" and conv.paused_until is not None
    # Tags arrive with a conversation event.
    world.client.post(
        url,
        json={
            "event_type": "conversation",
            "page_id": PAGE,
            "data": {"conversation": {"id": "psid-1", "tags": [{"id": 5}], "assignee_ids": []}},
        },
    )
    await world.session.refresh(conv)
    assert conv.tag_ids == ["5"]


# --- answering --------------------------------------------------------------------------


async def test_05_suggest_mode_keeps_the_answer_for_a_person(world: World) -> None:
    kit = Kit(world)
    await seeded(world)
    page = create_page(world)
    url = f"/api/cskh/pancake/webhook/{key_of(page)}"
    kit.pancake.say("psid-1", said("m1", "Chào em"), said("m2", "Giá nâng mũi?", minute=1))
    world.client.post(url, json=customer("m1"))
    world.client.post(url, json=customer("m2"))
    conv = await only_conversation(world)
    await answer_conversation(conv.id, kit.deps())
    first, second = await replies(world)
    # A burst is answered once, by the newest row.
    assert (first.status, first.reason) == (ReplyStatus.SKIPPED, "merged")
    assert second.status == ReplyStatus.SUGGESTED
    assert second.suggested_text == "Dạ, em chào anh/chị ạ."
    assert second.review_passed is True and kit.pancake.sent == []
    call = kit.relay.calls[0]
    assert call["slug"] == "chatpage"
    assert call["session_id"] == f"apm-pk-{conv.id}"
    assert "[Kênh: Pancake" in call["text"] and "- Khách: Giá nâng mũi?" in call["text"]
    await world.session.refresh(conv)
    assert conv.seen_message_id == "m2"
    # The next turn: only what is new, no briefing.
    kit.pancake.say("psid-1", said("m3", "Ở Hà Nội có không?", minute=2))
    world.client.post(url, json=customer("m3"))
    await answer_conversation(conv.id, kit.deps())
    text = kit.relay.calls[1]["text"]
    assert "[Kênh:" not in text and "Giá nâng mũi" not in text and "Ở Hà Nội" in text
    world.act_as(world.owner)
    listed = world.client.get("/api/cskh/replies").json()
    assert [r["suggested_text"] for r in listed["replies"]] == ["Dạ, em chào anh/chị ạ."] * 2
    assert world.client.get("/api/units/me").json()["cskh"]["waiting"] == 2


async def test_06_auto_mode_sends_only_a_clean_answer(world: World) -> None:
    kit = Kit(world)
    await seeded(world)
    page = create_page(world, mode="AUTO")
    url = f"/api/cskh/pancake/webhook/{key_of(page)}"
    kit.pancake.say("psid-1", said("m1", "Giá?"))
    world.client.post(url, json=customer("m1"))
    conv = await only_conversation(world)
    await answer_conversation(conv.id, kit.deps())
    (row,) = await replies(world)
    assert row.status == ReplyStatus.SENT and row.decided_by_user_id is None
    assert kit.pancake.sent[0]["text"] == "Dạ, em chào anh/chị ạ."
    assert kit.pancake.sent[0]["token"] == TOKEN
    # Its echo is not a staff reply.
    world.client.post(url, json=staff("sent-1"))
    await world.session.refresh(conv)
    assert conv.paused_until is None
    # The check found a broken rule: kept for a person.
    kit.pancake.say("psid-1", said("m2", "Có cam kết không?", minute=1))
    world.client.post(url, json=customer("m2"))
    broken = Review(passed=False, violations=[{"rule": "Cam kết", "quote": "x"}])
    await answer_conversation(conv.id, kit.deps(broken))
    # The check could not run: kept too (never fails open).
    kit.pancake.say("psid-1", said("m3", "Còn gì nữa?", minute=2))
    world.client.post(url, json=customer("m3"))
    await answer_conversation(conv.id, kit.deps(Review(passed=None, note="AI đang lỗi")))
    rows = await replies(world)
    assert [r.status for r in rows] == [
        ReplyStatus.SENT,
        ReplyStatus.SUGGESTED,
        ReplyStatus.SUGGESTED,
    ]
    assert rows[1].violations == [{"rule": "Cam kết", "quote": "x"}]
    assert rows[2].review_passed is None and len(kit.pancake.sent) == 1


async def test_07_the_agent_asks_for_a_person(world: World) -> None:
    kit = Kit(world)
    kit.relay.answers = [
        [RelayDelta(f"{HANDOFF_MARKER} khách muốn đặt lịch\nDạ em chuyển tư vấn viên ạ.")]
    ]
    await seeded(world)
    page = create_page(world, mode="AUTO")
    url = f"/api/cskh/pancake/webhook/{key_of(page)}"
    kit.pancake.say("psid-1", said("m1", "Đặt lịch mai được không?"))
    world.client.post(url, json=customer("m1"))
    conv = await only_conversation(world)
    await answer_conversation(conv.id, kit.deps())
    (row,) = await replies(world)
    assert row.status == ReplyStatus.SUGGESTED and row.handoff
    assert row.handoff_reason == "khách muốn đặt lịch" and kit.pancake.sent == []
    # Paused: the next message is not answered...
    kit.pancake.say("psid-1", said("m2", "Alo?", minute=1))
    world.client.post(url, json=customer("m2"))
    await answer_conversation(conv.id, kit.deps())
    rows = await replies(world)
    assert (rows[1].status, rows[1].reason) == (ReplyStatus.SKIPPED, "paused")
    # ...until a person lifts the pause.
    world.act_as(world.owner)
    assert world.client.post(f"/api/cskh/conversations/{conv.id}/resume").status_code == 204
    await world.session.refresh(conv)
    assert conv.paused_until is None
    # A hand-off tag stops it too.
    world.client.patch(f"/api/cskh/pages/{page['id']}", json={"handoff_tag_ids": ["5"]})
    conv.tag_ids = ["5"]
    kit.pancake.say("psid-1", said("m3", "Alo?", minute=2))
    world.client.post(url, json=customer("m3"))
    await answer_conversation(conv.id, kit.deps())
    assert (await replies(world))[2].reason == "handoff_tag"


async def test_08_the_inbox_sends_or_dismisses_once(world: World) -> None:
    kit = Kit(world)
    await seeded(world)
    page = create_page(world)
    url = f"/api/cskh/pancake/webhook/{key_of(page)}"
    kit.pancake.say("psid-1", said("m1", "Giá?"), said("p1", "Dạ", page=True, minute=1))
    world.client.post(url, json=customer("m1"))
    conv = await only_conversation(world)
    await answer_conversation(conv.id, kit.deps())
    (row,) = await replies(world)
    world.act_as(world.owner)
    thread = world.client.get(f"/api/cskh/replies/{row.id}/thread").json()
    assert [(m["id"], m["from_page"]) for m in thread["messages"]] == [("m1", False), ("p1", True)]
    empty = world.client.post(f"/api/cskh/replies/{row.id}/send", json={"text": "  "})
    assert error_reason(empty.json()) == "cskh_text_invalid"
    sent = world.client.post(
        f"/api/cskh/replies/{row.id}/send", json={"text": "Dạ giá 30 triệu ạ."}
    )
    assert sent.status_code == 200, sent.json()
    assert sent.json()["status"] == "SENT" and sent.json()["decided_by"] == world.owner.full_name
    assert kit.pancake.sent[-1]["text"] == "Dạ giá 30 triệu ạ."
    again = world.client.post(f"/api/cskh/replies/{row.id}/send", json={"text": "x"})
    assert error_reason(again.json()) == "cskh_reply_decided"
    assert world.client.get("/api/cskh/replies?tab=sent").json()["replies"][0]["sent_text"] == (
        "Dạ giá 30 triệu ạ."
    )
    # Pancake refuses: the suggestion stays to try again.
    kit.pancake.say("psid-1", said("m2", "Ở đâu?", minute=2))
    world.client.post(url, json=customer("m2"))
    await answer_conversation(conv.id, kit.deps())
    second = (await replies(world))[1]
    kit.pancake.fail_send = "pancake_failed"
    refused = world.client.post(f"/api/cskh/replies/{second.id}/send", json={"text": "Dạ"})
    assert refused.status_code == 422 and error_reason(refused.json()) == "pancake_failed"
    await world.session.refresh(second)
    assert second.status == ReplyStatus.SUGGESTED
    dismissed = world.client.post(f"/api/cskh/replies/{second.id}/dismiss")
    assert dismissed.json()["status"] == "DISMISSED"
    actions = (
        await world.session.scalars(
            select(AuditLog.action).where(AuditLog.action.like("cskh.reply.%"))
        )
    ).all()
    assert sorted(actions) == ["cskh.reply.dismissed", "cskh.reply.sent"]


async def test_09_rows_cut_by_a_restart_fail_when_read(world: World) -> None:
    Kit(world)
    await seeded(world)
    page = create_page(world)
    world.client.post(f"/api/cskh/pancake/webhook/{key_of(page)}", json=customer("m1"))
    (row,) = await replies(world)
    row.updated_at = row.updated_at - timedelta(hours=1)
    await world.session.flush()
    world.act_as(world.owner)
    other = world.client.get("/api/cskh/replies?tab=other").json()["replies"]
    assert [(r["status"], r["reason"]) for r in other] == [("FAILED", "interrupted")]
