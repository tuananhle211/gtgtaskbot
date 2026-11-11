"""Hộp chat CSKH (0102): Pancake pages + POS Cake + Zalo telesale in one inbox.

* the rules: phone numbers, masking, attachments, QR kinds, quick-reply import,
  the order note, the bridge's push, settings, work hours;
* who: ``INBOX`` OWN = the pages one is a member of / ALL; ``ZALO`` OWN = one's
  nicks; ``MANAGE`` = connections; OWNER / ADMIN all; the menu's badges;
* connections: Pancake accounts and POS keys stored encrypted (only the hint
  leaves), connecting a page (token made, members / labels, POS shop linked);
* reading Pancake: a page's first read counts nothing; a new customer message
  is unread and, out of hours, answered once; tags / phones / ads refreshed;
  QR codes read from the customer's pictures;
* the inbox: list, filters, search (code / phone / name / words), detail marks
  read, send through Pancake, Pancake tags, assignee, "Chờ gọi lại";
* confirming: a new number creates a confirmed POS order; Pancake's pending
  order is confirmed instead (note appended); a repeated number needs
  ``dup_ack``; a locked marketer is MANAGE's to change;
* Zalo: the bridge's door (secret), threads matched by code (page side masked)
  or phone, a telesale sees their nicks only, answers on the page from Zalo,
  QR login hands the nick to whoever scanned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from meobot.api.deps import get_app_settings
from meobot.api.routers.cskh import get_cskh_clients, get_cskh_task_sender, get_zalo_bridge
from meobot.application.cskh import inbox as inbox_module
from meobot.application.cskh.common import VN, CskhClients, write_state
from meobot.application.cskh.sync import CskhSync
from meobot.core.secrets import SecretBox, build_secret_box, generate_key
from meobot.db.models.audit_log import AuditLog
from meobot.db.models.cskh import (
    CskhConversation,
    CskhMessage,
    CskhOrder,
    CskhPage,
    CskhPageMember,
    CskhPancakeAccount,
    CskhZaloAccount,
    CskhZaloThread,
)
from meobot.domain.cskh.models import (
    InboxSettings,
    OrderInfo,
    code_in_text,
    find_phones,
    is_work_time,
    mask_phone,
    mask_phones_in,
    normalize_attachments,
    order_note_text,
    parse_quick_replies,
    parse_zalo_push,
    phone_key,
    qr_kind,
    settings_from,
)
from meobot.domain.identity.models import Role
from meobot.integrations.pancake.client import (
    FakePancakeClient,
    FakePosClient,
    PancakePageInfo,
    image_url_allowed,
)
from meobot.integrations.zalo_bridge.client import FakeZaloBridge
from tests.unit.pr_world import World
from tests.unit.test_order_commands import person

pytestmark = pytest.mark.asyncio

PAGE = "421752257690653"
PAGE_B = "102859049227605"
USER_TOKEN = "pancake-user-token-0001"
POS_KEY = "pos_user_key_123456"
BRIDGE_SECRET = "bridge-secret-xyz"


def reason(body: dict[str, Any]) -> str | None:
    details = body.get("details") or body.get("error", {}).get("details") or {}
    return details.get("reason")


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000000")


class Kit:
    def __init__(self, world: World) -> None:
        settings = world.settings.model_copy(
            update={
                "pr_secret_encryption_key": SecretStr(generate_key()),
                "pr_secret_encryption_key_file": None,
                "zalo_bridge_secret": SecretStr(BRIDGE_SECRET),
            }
        )
        self.world = world
        self.box: SecretBox = build_secret_box(settings)
        self.pancake = FakePancakeClient(
            pages={
                USER_TOKEN: [
                    PancakePageInfo(id=PAGE, name="BS Tiến - Thẩm mỹ", username="bstien"),
                    PancakePageInfo(id=PAGE_B, name="Apexmed Sài Gòn"),
                    PancakePageInfo(id="999", name="Page tắt", status="inactivated"),
                ]
            },
            page_tags={PAGE: [{"id": 1, "text": "Đã chốt", "color": "#f00"}]},
            page_users={PAGE: [{"id": "pc-u1", "name": "Lan trực page"}]},
        )
        self.pos = FakePosClient(
            shops_by_key={
                POS_KEY: [
                    {
                        "id": 430144632,
                        "name": "Shop BS Tiến",
                        "pages": [{"id": PAGE, "name": "BS"}],
                    },
                    {"id": 430144633, "name": "Shop SG", "pages": [{"id": PAGE_B, "name": "SG"}]},
                ]
            }
        )
        self.bridge = FakeZaloBridge()
        self.sent_tasks: list[tuple[str, dict[str, Any]]] = []
        self.clients = CskhClients(pancake=self.pancake, pos=self.pos, secret_box=lambda: self.box)
        inbox_module._POS_CACHE.clear()
        app = world.client.app  # type: ignore[attr-defined]
        app.dependency_overrides[get_cskh_clients] = lambda: self.clients
        app.dependency_overrides[get_zalo_bridge] = lambda: self.bridge
        app.dependency_overrides[get_cskh_task_sender] = lambda: (
            lambda name, kwargs: self.sent_tasks.append((name, kwargs))
        )
        app.dependency_overrides[get_app_settings] = lambda: settings

    def sync(self, decoder: Any = None) -> CskhSync:
        session = self.world.session

        @asynccontextmanager
        async def same() -> AsyncIterator[Any]:
            yield session
            await session.flush()

        return CskhSync(same, self.clients, qr_decoder=decoder, pause=0)

    def say(
        self,
        page: str,
        psid: str,
        name: str,
        messages: list[tuple[str, str, bool, datetime]],
        **conv: Any,
    ) -> None:
        """``messages`` = ``(id, text, from_page, at)``; the conversation's
        ``updated_at`` = its newest message."""
        conv_id = f"{page}_{psid}"
        items = self.pancake.convs.setdefault(page, [])
        newest = max(at for *_, at in messages)
        item = next((c for c in items if c["id"] == conv_id), None)
        if item is None:
            item = {"id": conv_id, "type": "INBOX", "from": {"id": psid, "name": name}}
            items.append(item)
        item.update({"updated_at": stamp(newest), **conv})
        thread = self.pancake.threads.setdefault(conv_id, [])
        for mid, text, from_page, at in messages:
            thread.append(
                {
                    "id": mid,
                    "from": {"id": page if from_page else psid},
                    "original_message": text,
                    "inserted_at": stamp(at),
                    "attachments": [],
                }
            )

    async def connect(self, *page_ids: str) -> None:
        world = self.world
        world.act_as(world.owner)
        added = world.client.post(
            "/api/cskh/pancake-accounts", json={"label": "Ngô", "token": USER_TOKEN}
        )
        assert added.status_code == 201, added.json()
        key = world.client.post("/api/cskh/pos-keys", json={"label": "POS", "key": POS_KEY})
        assert key.status_code == 201, key.json()
        started = world.client.post("/api/cskh/pages/connect", json={"page_ids": list(page_ids)})
        assert started.status_code == 202, started.json()
        name, kwargs = self.sent_tasks[-1]
        assert name == "cskh.connect_pages"
        await self.sync().connect(kwargs["page_ids"], pages=kwargs["pages"], import_history=True)


async def no_work_hours(world: World) -> None:
    await write_state(world.session, "cskh", {**InboxSettings().as_dict(), "work_days": []})


async def conversation(world: World, psid: str) -> CskhConversation:
    row = await world.session.scalar(select(CskhConversation).where(CskhConversation.psid == psid))
    assert row is not None
    return row


async def grant(world: World, user: Any, permission: str, scope: str) -> None:
    world.act_as(world.owner)
    made = world.client.post(
        f"/api/permissions/people/{user.id}/grants",
        json={"module": "CSKH", "permission": permission, "scope": scope},
    )
    assert made.status_code == 201, made.json()


# --- the rules -----------------------------------------------------------------------------


async def test_01_the_rules() -> None:
    assert find_phones("SĐT em 0912 345 678 hoặc +84.987.654.321 nhé") == [
        "0912345678",
        "+84987654321",
    ]
    assert find_phones("mã 123456") == []
    assert phone_key("+84912345678") == phone_key("0912.345.678") == "912345678"
    assert mask_phone("0912 345 678") == "0912 34* ***"
    assert mask_phones_in("gọi 0912345678 nhé") == "gọi 091234**** nhé"
    atts = normalize_attachments(
        [
            {"type": "photo", "url": "https://content.pancake.vn/a.jpg"},
            {
                "type": "template",
                "payload": {
                    "elements": [
                        {
                            "title": "Để lại SĐT -&gt; nhận ưu đãi",
                            "image_url": "https://x/y.png",
                            "buttons": [{"title": "Gọi", "url": "tel:1"}],
                        }
                    ]
                },
            },
            {"type": "ad_click", "ad_id": 77, "post_attachments": [{"description": "Nâng mũi"}]},
            {"type": "address", "full_address": "12 Lê Lợi"},
            {"type": "system_message"},
        ]
    )
    assert [a["kind"] for a in atts] == ["image", "card", "ad", "address"]
    assert atts[1]["title"] == "Để lại SĐT -> nhận ưu đãi" and atts[2]["ad_id"] == "77"
    assert qr_kind("https://zalo.me/g/abc") == "Zalo" and qr_kind("000201010212") == "Ngân hàng"
    assert parse_quick_replies("/chao | Dạ chào {ten}\nem là Lan\n/xin so|Cho em xin SĐT") == [
        ("chao", "Dạ chào {ten}\nem là Lan"),
        ("xin_so", "Cho em xin SĐT"),
    ]
    note = order_note_text(
        OrderInfo(order_status="Đã chốt lịch", platform="Facebook", customer_code="KH1")
    )
    assert note == "Trạng thái: Đã chốt lịch · Nền tảng: Facebook · Mã KH: KH1"
    assert code_in_text("KH1241242", "mã khách của anh là kh1241242 ạ")
    assert not code_in_text("KH12", "AKH12B") and not code_in_text("KH1", "KH1")
    now = datetime(2026, 11, 11, 3, tzinfo=UTC)
    pushes = parse_zalo_push(
        {
            "zaloAccountId": "acc1",
            "data": {
                "msgs": [
                    {
                        "msgId": "z1",
                        "uidFrom": "0",
                        "idTo": "cust",
                        "content": "Chào chị",
                        "ts": "1794000000000",
                    },
                    {
                        "msgId": "z2",
                        "uidFrom": "cust",
                        "idTo": "acc1",
                        "content": {"title": "ảnh"},
                        "peer": {"name": "Hà"},
                    },
                ],
                "status": "connected",
            },
        },
        now,
    )
    first, second = pushes[0].messages
    assert first.outgoing and first.peer_uid == "cust" and first.text == "Chào chị"
    assert not second.outgoing and second.peer_uid == "cust" and second.peer_name == "Hà"
    assert second.at == now and pushes[0].status == "connected"
    settings = settings_from({"work_start": "25:00", "work_days": [0, 9], "mkt_lock_days": -1})
    assert (
        settings.work_start == "08:00"
        and settings.work_days == (0,)
        and settings.mkt_lock_days == 30
    )
    monday_9 = datetime(2026, 11, 9, 9, 0, tzinfo=VN)
    assert is_work_time(settings, monday_9) and not is_work_time(
        settings, monday_9 + timedelta(days=1)
    )
    assert image_url_allowed("https://content.pancake.vn/1/a.jpg")
    assert not image_url_allowed("http://content.pancake.vn/a.jpg")
    assert not image_url_allowed("https://169.254.169.254/latest")


# --- who ------------------------------------------------------------------------------------


async def test_02_who_sees_what(world: World) -> None:
    kit = Kit(world)
    await kit.connect(PAGE, PAGE_B)
    world.act_as(world.member)
    assert reason(world.client.get("/api/cskh/state").json()) == "cskh_forbidden"
    assert world.client.get("/api/units/me").json()["cskh"]["inbox"] is False
    # INBOX OWN: only the pages one is a member of.
    await grant(world, world.member, "INBOX", "OWN")
    world.act_as(world.member)
    assert world.client.get("/api/cskh/state").json()["pages"] == []
    world.act_as(world.owner)
    updated = world.client.post(
        "/api/cskh/pages/update",
        json={"page_ids": [PAGE], "members": [{"user_id": str(world.member.id), "role": "STAFF"}]},
    )
    assert updated.status_code == 200, updated.json()
    world.act_as(world.member)
    pages = world.client.get("/api/cskh/state").json()["pages"]
    assert [p["id"] for p in pages] == [PAGE]
    assert pages[0]["members"][0]["role_label"] == "Nhân viên trực"
    assert world.client.get("/api/cskh/connections").status_code == 403
    assert world.client.get("/api/cskh/zalo/threads").status_code == 403
    me = world.client.get("/api/units/me").json()["cskh"]
    assert me["inbox"] is True and me["zalo"] is False and me["manage"] is False
    # An ADMIN manages by role.
    admin = await person(world, "Quản trị", Role.ADMIN)
    world.act_as(admin)
    assert world.client.get("/api/cskh/access").json() == {
        "inbox": "ALL",
        "zalo": "ALL",
        "manage": True,
        "sees": True,
    }


# --- connections ----------------------------------------------------------------------------


async def test_03_connecting_pages_keeps_secrets(world: World) -> None:
    kit = Kit(world)
    world.act_as(world.owner)
    bad = world.client.post("/api/cskh/pancake-accounts", json={"token": "bad"})
    assert bad.status_code == 422 and reason(bad.json()) == "pancake_token_invalid"
    await kit.connect(PAGE)
    account = await world.session.scalar(select(CskhPancakeAccount))
    assert account is not None and USER_TOKEN not in account.token_encrypted
    assert account.token_hint == "••••0001" and account.page_count == 3
    page = await world.session.get(CskhPage, PAGE)
    assert page is not None and page.token_encrypted and "pt-" not in page.token_encrypted
    assert page.pos_shop_id == "430144632" and page.polled_at is not None
    overview = world.client.get("/api/cskh/connections").json()
    assert USER_TOKEN not in str(overview) and POS_KEY not in str(overview)
    assert overview["job"]["connected"] == 1 and overview["job"]["running"] is False
    row = next(p for p in overview["pages"] if p["id"] == PAGE)
    assert row["connected"] and row["pos_shop_name"] == "Shop BS Tiến"
    # Tags / staff from Pancake.
    state = world.client.get("/api/cskh/state").json()
    assert state["pages"][0]["pancake_tags"][0]["text"] == "Đã chốt"
    assert state["pages"][0]["pancake_users"][0]["name"] == "Lan trực page"
    # An inactive page and an unknown one are reported, not connected.
    await kit.sync().connect(["999", "nope"])
    assert await world.session.get(CskhPage, "999") is None
    listing = world.client.get("/api/cskh/pancake/pages").json()["pages"]
    assert {p["id"]: p["connected"] for p in listing}[PAGE] is True
    # A second connect while one runs is refused.
    from meobot.application.cskh.sync import start_job

    await start_job(world.session, label="x", total=1, user_id=None)
    busy = world.client.post("/api/cskh/pages/connect", json={"page_ids": [PAGE_B]})
    assert busy.status_code == 409 and reason(busy.json()) == "cskh_job_running"
    # Ngắt kết nối forgets the token.
    assert world.client.post(f"/api/cskh/pages/{PAGE}/disconnect").status_code == 204
    await world.session.refresh(page)
    assert page.token_encrypted is None and page.active is False
    actions = set((await world.session.scalars(select(AuditLog.action))).all())
    assert {"cskh.pancake_account.saved", "cskh.pos_key.saved", "cskh.page.deleted"} <= actions


# --- reading Pancake --------------------------------------------------------------------------


async def test_04_reading_pancake_and_answering_out_of_hours(world: World) -> None:
    kit = Kit(world)
    now = datetime.now(UTC)
    kit.say(
        PAGE,
        "psid1",
        "Ngô Trọng Huy",
        [("m_1", "Cho em hỏi giá nâng mũi", False, now - timedelta(hours=2))],
        tags=[1],
        recent_phone_numbers=[{"captured": "0912 345 678"}],
        ads=[{"ad_id": "ad1", "post_id": "p1", "inserted_at": stamp(now - timedelta(hours=3))}],
    )
    kit.pancake.ad_info["ad1"] = {
        "id": "ad1",
        "name": "QC nâng mũi",
        "campaign_id": "c1",
        "campaign_name": "Chiến dịch 11",
    }
    await kit.connect(PAGE)
    conv = await conversation(world, "psid1")
    # The first read: history, nothing unread, nobody answered.
    assert conv.unread == 0 and conv.customer_name == "Ngô Trọng Huy"
    assert conv.phones == ["0912 345 678"] and conv.pancake_tags == ["1"] and conv.ad_id == "ad1"
    assert kit.pancake.sent == []
    # A new message out of hours: unread, one automatic answer, "Chờ gọi lại".
    await no_work_hours(world)
    kit.say(PAGE, "psid1", "Ngô Trọng Huy", [("m_2", "Số em 0987654321 ạ", False, now)])
    report = await kit.sync().poll()
    assert report.errors == []
    await world.session.refresh(conv)
    assert conv.unread == 1 and conv.callback is True
    assert len(kit.pancake.sent) == 1 and "ngoài giờ" in kit.pancake.sent[0]["text"]
    assert "0987654321" in conv.phones
    auto = await world.session.scalar(select(CskhMessage).where(CskhMessage.kind == "auto"))
    assert auto is not None and auto.direction == "out"
    # Another message: no second automatic answer within 12 h.
    kit.say(PAGE, "psid1", "Ngô Trọng Huy", [("m_3", "Alo", False, now + timedelta(seconds=5))])
    await kit.sync().poll()
    await world.session.refresh(conv)
    assert conv.unread == 2 and len(kit.pancake.sent) == 1
    # The list, the ad name, the campaign.
    world.act_as(world.owner)
    listed = world.client.get("/api/cskh/conversations", params={"tab": "unread"}).json()
    row = listed["conversations"][0]
    assert row["customer_name"] == "Ngô Trọng Huy" and row["ad_name"] == "QC nâng mũi"
    assert world.client.get("/api/cskh/state").json()["campaigns"] == [
        {"id": "c1", "name": "Chiến dịch 11"}
    ]
    assert world.client.get("/api/cskh/conversations", params={"campaign_id": "c1"}).json()[
        "conversations"
    ]
    assert world.client.get("/api/units/me").json()["cskh"]["unread"] == 2


async def test_05_qr_codes_in_pictures(world: World) -> None:
    kit = Kit(world)
    now = datetime.now(UTC)
    url = "https://content.pancake.vn/2/qr.jpg"
    kit.pancake.images[url] = b"picture"
    await kit.connect(PAGE)
    kit.say(PAGE, "psid9", "Hà", [("m_9", "", False, now)])
    kit.pancake.threads[f"{PAGE}_psid9"][-1]["attachments"] = [{"type": "photo", "url": url}]
    await kit.sync(decoder=lambda data: ["https://zalo.me/0911222333"]).poll()
    conv = await conversation(world, "psid9")
    assert conv.qrs[0]["kind"] == "Zalo" and "0911222333" in conv.phones
    message = await world.session.scalar(select(CskhMessage).where(CskhMessage.mid == "m_9"))
    assert message is not None and message.qr == ["https://zalo.me/0911222333"]
    world.act_as(world.owner)
    row = world.client.get(f"/api/cskh/conversations/{conv.id}").json()["conversation"]
    assert row["has_qr"] is True and row["qrs"][0]["text"] == "https://zalo.me/0911222333"


# --- the inbox ----------------------------------------------------------------------------------


async def test_06_inbox_reads_sends_tags(world: World) -> None:
    kit = Kit(world)
    now = datetime.now(UTC)
    kit.say(PAGE, "psid1", "Nguyễn Văn Đức", [("m_1", "Dạ em muốn đặt lịch", False, now)])
    kit.say(PAGE_B, "psid2", "Trần Lan", [("m_2", "giá bao nhiêu", False, now)])
    await kit.connect(PAGE, PAGE_B)
    conv = await conversation(world, "psid1")
    conv.unread = 3
    world.act_as(world.owner)
    # Search: accents off, by message words, by name.
    hits = world.client.get("/api/cskh/conversations", params={"q": "dat lich"}).json()
    assert [c["psid"] for c in hits["conversations"]] == ["psid1"]
    assert hits["conversations"][0]["match"]["why"] == "Tin nhắn"
    by_name = world.client.get("/api/cskh/conversations", params={"q": "nguyen van duc"}).json()
    assert by_name["conversations"][0]["match"]["why"] == "Tên khách"
    only_b = world.client.get("/api/cskh/conversations", params={"page_id": PAGE_B}).json()
    assert [c["psid"] for c in only_b["conversations"]] == ["psid2"]
    # The detail marks it read.
    detail = world.client.get(f"/api/cskh/conversations/{conv.id}").json()
    assert detail["messages"][0]["text"] == "Dạ em muốn đặt lịch"
    assert detail["page"]["pos_shop_name"] == "Shop BS Tiến"
    await world.session.refresh(conv)
    assert conv.unread == 0
    # Send through Pancake with the sender's name.
    sent = world.client.post(
        f"/api/cskh/conversations/{conv.id}/messages",
        json={"text": "Dạ em chào anh", "clear_callback": True},
    )
    assert sent.status_code == 200, sent.json()
    assert sent.json()["sender_name"] == world.owner.full_name and sent.json()["kind"] == "staff"
    assert kit.pancake.sent[-1]["conversation_id"] == f"{PAGE}_psid1"
    kit.pancake.fail_send = "Tin nhắn ngoài 24h"
    refused = world.client.post(f"/api/cskh/conversations/{conv.id}/messages", json={"text": "x"})
    assert refused.status_code == 422 and "24 giờ" in refused.json()["error"]["message"]
    # Pancake tag + assignee: kept against Pancake's older list for a minute.
    tagged = world.client.post(
        f"/api/cskh/conversations/{conv.id}/pancake-tags", json={"tag_id": "5"}
    )
    assert tagged.json() == {"pancake_tags": ["5"]}
    assigned = world.client.post(
        f"/api/cskh/conversations/{conv.id}/assign", json={"user_id": "pc-u1"}
    )
    assert assigned.json() == {"assignee_ids": ["pc-u1"]}
    kit.pancake.convs[PAGE][0]["tags"] = []
    kit.pancake.convs[PAGE][0]["updated_at"] = stamp(now + timedelta(minutes=1))
    await kit.sync().poll()
    await world.session.refresh(conv)
    assert conv.pancake_tags == ["5"]
    assert (
        world.client.post(
            f"/api/cskh/conversations/{conv.id}/callback", json={"on": True}
        ).status_code
        == 204
    )
    assert world.client.get("/api/cskh/conversations", params={"tab": "callback"}).json()[
        "conversations"
    ]
    # A member without the page: not found.
    await grant(world, world.member, "INBOX", "OWN")
    world.act_as(world.member)
    assert world.client.get(f"/api/cskh/conversations/{conv.id}").status_code == 404


# --- confirming into POS ---------------------------------------------------------------------


async def test_07_confirm_phone_creates_or_confirms_pos_orders(world: World) -> None:
    kit = Kit(world)
    now = datetime.now(UTC)
    kit.say(PAGE, "psid1", "Huy", [("m_1", "sđt em 0912345678", False, now - timedelta(hours=1))])
    kit.say(PAGE_B, "psid2", "Huy SG", [("m_2", "0912.345.678 nhé", False, now)])
    await kit.connect(PAGE, PAGE_B)
    marketer = await person(world, "Marketer HN")
    other = await person(world, "Marketer SG")
    world.act_as(world.owner)
    world.client.post(
        "/api/cskh/pages/update", json={"page_ids": [PAGE], "marketer_user_id": str(marketer.id)}
    )
    world.client.post(
        "/api/cskh/pages/update", json={"page_ids": [PAGE_B], "marketer_user_id": str(other.id)}
    )
    first = await conversation(world, "psid1")
    second = await conversation(world, "psid2")
    # The same number on another page: the check names it.
    check = world.client.get(f"/api/cskh/conversations/{first.id}/phone-check").json()
    assert check["need_ack"] is True and check["seen"][0]["page_name"] == "Apexmed Sài Gòn"
    refused = world.client.post(
        f"/api/cskh/conversations/{first.id}/confirm",
        json={"phone": "0912345678", "content_name": "Nâng mũi", "customer_code": "kh1241242"},
    )
    assert refused.status_code == 409 and reason(refused.json()) == "cskh_duplicate"
    assert refused.json()["error"]["details"]["dup"]["need_ack"] is True
    people = {p["name"] for p in world.client.get("/api/cskh/state").json()["people"]}
    assert {"Marketer HN", "Marketer SG"} <= people
    made = world.client.post(
        f"/api/cskh/conversations/{first.id}/confirm",
        json={
            "phone": "0912345678",
            "content_name": "Nâng mũi",
            "customer_code": "kh1241242",
            "order_status": "Đã chốt lịch",
            "dup_ack": True,
        },
    )
    assert made.status_code == 200, made.json()
    body = made.json()
    assert body["action"] == "create" and body["order"]["customer_code"] == "KH1241242"
    assert body["order"]["marketer_name"] == "Marketer HN" and body["order"]["dup_info"].startswith(
        "Trùng số"
    )
    created = kit.pos.created[-1]
    assert created["status"] == 1 and created["conversation_id"] == f"{PAGE}_psid1"
    assert "Mã KH: KH1241242" in created["note"] and created["shop_id"] == 430144632
    # Pancake's own pending order of the second customer gets confirmed (note appended).
    kit.pos.store["430144633"] = [
        {
            "id": 1082,
            "system_id": 1082,
            "status": 0,
            "page_id": PAGE_B,
            "conversation_id": f"{PAGE_B}_psid2",
            "bill_phone_number": "0912345678",
            "bill_full_name": "Huy SG",
            "note": "Khách hỏi giá",
        }
    ]
    inbox_module._POS_CACHE.clear()
    # The number belongs to Marketer HN (oldest data, 30 days): a member may not change it.
    await grant(world, world.member, "INBOX", "ALL")
    world.act_as(world.member)
    locked = world.client.post(
        f"/api/cskh/conversations/{second.id}/confirm",
        json={"phone": "0912345678", "marketer_user_id": str(other.id), "dup_ack": True},
    )
    assert locked.status_code == 403
    ok = world.client.post(
        f"/api/cskh/conversations/{second.id}/confirm",
        json={"phone": "0912345678", "dup_ack": True},
    )
    assert ok.status_code == 200, ok.json()
    assert ok.json()["action"] == "confirm" and ok.json()["order"]["marketer_name"] == "Marketer HN"
    update = kit.pos.updates[-1]
    assert update["status"] == 1 and update["note"].startswith("Khách hỏi giá\n")
    orders = (await world.session.scalars(select(CskhOrder))).all()
    assert len(orders) == 2
    # MANAGE changes the marketer (noted).
    world.act_as(world.owner)
    changed = world.client.post(
        f"/api/cskh/conversations/{first.id}/confirm",
        json={"phone": "0912345678", "marketer_user_id": str(other.id), "dup_ack": True},
    )
    assert changed.status_code == 200, changed.json()
    assert "Đổi marketing Marketer HN → Marketer SG" in changed.json()["order"]["dup_info"]
    # Search by customer code.
    found = world.client.get("/api/cskh/conversations", params={"q": "KH1241242"}).json()
    assert found["conversations"][0]["match"]["why"] == "Mã KH"


# --- Zalo -------------------------------------------------------------------------------------


def push(account: str, *messages: dict[str, Any], status: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"msgs": list(messages)}
    if status:
        data["status"] = status
    return {"type": "Notification", "zaloAccountId": account, "source": "zca", "data": data}


async def test_08_zalo_bridge_matching_and_masking(world: World) -> None:
    kit = Kit(world)
    now = datetime.now(UTC)
    kit.say(PAGE, "psid1", "Huy", [("m_1", "sđt em 0912345678", False, now)])
    await kit.connect(PAGE)
    conv = await conversation(world, "psid1")
    world.act_as(world.owner)
    made = world.client.post(
        f"/api/cskh/conversations/{conv.id}/confirm",
        json={"phone": "0912345678", "customer_code": "KH1241242"},
    )
    assert made.status_code == 200, made.json()
    client = world.client
    # The door: wrong secret 403.
    assert (
        client.post("/api/cskh/zalo/bridge", json={}, headers={"x-bridge-secret": "no"}).status_code
        == 403
    )
    pushed = client.post(
        "/api/cskh/zalo/bridge",
        json=push(
            "acc1",
            {
                "msgId": "z1",
                "uidFrom": "0",
                "idTo": "cust1",
                "content": "Chào anh, mã khách của anh là KH1241242 ạ",
                "ts": "1794000000000",
            },
            {
                "msgId": "z2",
                "uidFrom": "cust1",
                "idTo": "acc1",
                "content": "Ok em",
                "peer": {"name": "Anh Huy"},
            },
            status="connected",
        ),
        headers={"x-bridge-secret": BRIDGE_SECRET},
    )
    assert pushed.status_code == 200 and pushed.json()["saved"] == 2
    thread = await world.session.scalar(select(CskhZaloThread))
    assert thread is not None and thread.conversation_id == conv.id and thread.link_kind == "code"
    assert thread.unread == 1 and thread.peer_name == "Anh Huy"
    # The same push again saves nothing.
    again = client.post(
        "/api/cskh/zalo/bridge",
        json=push("acc1", {"msgId": "z2", "uidFrom": "cust1", "idTo": "acc1", "content": "Ok em"}),
        headers={"x-bridge-secret": BRIDGE_SECRET},
    )
    assert again.json()["saved"] == 0
    # A telesale sees their own nick only, the page side masked (matched by code).
    sale = await person(world, "Lan Telesale")
    await grant(world, sale, "ZALO", "OWN")
    world.act_as(sale)
    assert client.get("/api/cskh/zalo/threads").json()["threads"] == []
    world.act_as(world.owner)
    assigned = client.post(
        "/api/cskh/zalo/accounts/assign", json={"account_ids": ["acc1"], "user_id": str(sale.id)}
    )
    assert assigned.json() == {"changed": 1}
    world.act_as(sale)
    threads = client.get("/api/cskh/zalo/threads").json()["threads"]
    assert len(threads) == 1 and threads[0]["masked"] is True
    assert threads[0]["conversation_phones"] == ["091234****"]
    page_side = client.get(f"/api/cskh/zalo/threads/{thread.id}/page").json()
    assert page_side["masked"] is True and "091234****" in page_side["messages"][0]["text"]
    assert "0912345678" not in str(page_side)
    # The telesale answers on the page from Zalo (no page access needed).
    sent = client.post(
        f"/api/cskh/zalo/threads/{thread.id}/page-send", json={"text": "Dạ em gọi lại anh nhé"}
    )
    assert sent.status_code == 200, sent.json()
    assert sent.json()["sender_name"] == "Lan Telesale (từ hộp chat Zalo)"
    assert kit.pancake.sent[-1]["text"] == "Dạ em gọi lại anh nhé"
    # Unlink, then match by phone (not masked).
    assert (
        client.post(f"/api/cskh/zalo/threads/{thread.id}/unlink").json()["conversation_id"] is None
    )
    relinked = client.post(
        f"/api/cskh/zalo/threads/{thread.id}/link", json={"term": "0912 345 678"}
    )
    assert relinked.status_code == 200, relinked.json()
    assert relinked.json()["masked"] is False and relinked.json()["conversation_phones"] == [
        "0912345678"
    ]
    missing = client.post(f"/api/cskh/zalo/threads/{thread.id}/link", json={"term": "0999000111"})
    assert missing.status_code == 404
    # The page inbox's Zalo tab: the matched thread with its messages.
    world.act_as(world.owner)
    tab = client.get(f"/api/cskh/conversations/{conv.id}/zalo").json()
    assert tab["threads"][0]["messages"][-1]["text"] == "Ok em" and tab["codes"] == ["KH1241242"]
    assert client.get("/api/units/me").json()["cskh"]["zalo_unread"] == 1


async def test_09_zalo_qr_login_and_accounts(world: World) -> None:
    kit = Kit(world)
    sale = await person(world, "Minh Telesale")
    await grant(world, sale, "ZALO", "OWN")
    world.act_as(sale)
    started = world.client.post("/api/cskh/zalo/qr", json={"user_id": str(world.owner.id)})
    assert started.status_code == 200 and started.json()["state"] == "qr"
    session_id = started.json()["session"]
    kit.bridge.sessions[session_id] = {"state": "ok", "accountId": "acc9", "name": "Apexmed - Ekip"}
    done = world.client.get(f"/api/cskh/zalo/qr/{session_id}").json()
    assert done["state"] == "ok"
    account = await world.session.get(CskhZaloAccount, "acc9")
    # A telesale's own scan: the nick is theirs (not who they named).
    assert account is not None and account.user_id == sale.id and account.name == "Apexmed - Ekip"
    kit.bridge.live = [
        {"id": "acc9", "connected": False, "reason": "Nick đang mở Zalo Web ở nơi khác"}
    ]
    listed = world.client.get("/api/cskh/zalo/accounts").json()
    assert (
        listed["accounts"][0]["status"] == "closed"
        and "Zalo Web" in listed["accounts"][0]["status_note"]
    )
    assert listed["may_manage"] is False
    assert world.client.post("/api/cskh/zalo/accounts/acc9/logout").status_code == 403
    # Somebody else's QR session is not theirs to read.
    other = await person(world, "Khác")
    await grant(world, other, "ZALO", "OWN")
    world.act_as(other)
    assert world.client.get(f"/api/cskh/zalo/qr/{session_id}").status_code in (403, 404)
    world.act_as(world.owner)
    assert world.client.post("/api/cskh/zalo/accounts/acc9/logout").status_code == 204
    assert kit.bridge.logged_out == ["acc9"]


# --- labels, quick replies, settings -----------------------------------------------------------


async def test_10_labels_quick_replies_settings(world: World) -> None:
    kit = Kit(world)
    await kit.connect(PAGE)
    world.act_as(world.owner)
    label = world.client.post("/api/cskh/labels", json={"name": "BS Tiến", "color": "teal"}).json()
    assert world.client.post("/api/cskh/labels", json={"name": "bs tiến"}).status_code == 422
    world.client.post(
        "/api/cskh/pages/update",
        json={"page_ids": [PAGE], "label_ids": [label["id"]], "label_mode": "add"},
    )
    assert world.client.get("/api/cskh/state").json()["pages"][0]["label_ids"] == [label["id"]]
    shared = world.client.post(
        "/api/cskh/quick-replies", json={"shortcut": "/chao", "text": "Dạ chào {ten}"}
    )
    assert shared.status_code == 201
    own = world.client.post(
        "/api/cskh/quick-replies", json={"shortcut": "chao", "text": "Chào riêng", "page_id": PAGE}
    )
    assert own.status_code == 201
    clash = world.client.post("/api/cskh/quick-replies", json={"shortcut": "chao", "text": "x"})
    assert clash.status_code == 422 and reason(clash.json()) == "cskh_quick_reply_exists"
    imported = world.client.post(
        "/api/cskh/quick-replies/import", json={"text": "/chao | trùng\n/diachi | 12 Lê Lợi"}
    ).json()
    assert imported == {"added": 1, "skipped": ["chao"]}
    share = world.client.post("/api/cskh/quick-replies/share", json={}).json()
    assert share == {"moved": 0, "conflict": ["chao"]}
    share = world.client.post("/api/cskh/quick-replies/share", json={"overwrite": True}).json()
    assert share == {"moved": 1, "conflict": []}
    replies = world.client.get("/api/cskh/quick-replies").json()["quick_replies"]
    assert {r["shortcut"]: r["text"] for r in replies}["chao"] == "Chào riêng"
    bad = world.client.put("/api/cskh/settings", json={"work_start": "22:00", "work_end": "08:00"})
    assert bad.status_code == 422
    saved = world.client.put(
        "/api/cskh/settings",
        json={
            "work_start": "07:30",
            "order_statuses": ["Đã chốt lịch", " ", "Hẹn gọi lại"],
            "mkt_lock_days": 15,
        },
    ).json()
    assert saved["work_start"] == "07:30" and saved["order_statuses"] == [
        "Đã chốt lịch",
        "Hẹn gọi lại",
    ]
    assert world.client.get("/api/cskh/state").json()["order_statuses"] == [
        "Đã chốt lịch",
        "Hẹn gọi lại",
    ]
    members = (await world.session.scalars(select(CskhPageMember))).all()
    assert members == []


async def test_11_reads_a_real_qr_screenshot() -> None:
    """A Zalo QR in the middle of a phone screenshot, halved, JPEG 60 (the prototype's case)."""
    import io

    import zxingcpp
    from PIL import Image

    from meobot.application.cskh.sync import QR_READY, decode_qr

    assert QR_READY
    code = zxingcpp.write_barcode(
        zxingcpp.BarcodeFormat.QRCode, "https://zalo.me/0911222333", width=300, height=300
    )
    picture = code if isinstance(code, Image.Image) else Image.fromarray(code)
    canvas = Image.new("L", (1080, 2000), 255)
    canvas.paste(picture.resize((400, 400)), (340, 800))
    buffer = io.BytesIO()
    canvas.convert("RGB").resize((540, 1000)).save(buffer, "JPEG", quality=60)
    assert decode_qr(buffer.getvalue()) == ["https://zalo.me/0911222333"]


async def test_12_pancake_rate_limit_is_not_hit_twice(world: World) -> None:
    """11/11 on prod: "Thêm tài khoản" -> "Đồng bộ toàn bộ page" -> the job asked Pancake's
    page list three times in a minute, got 429, and called all 84 pages "not on the account".
    Now the list is kept 5 minutes and handed to the job; a refusal is said once."""
    from meobot.application.cskh.sync import pancake_pages
    from meobot.domain.cskh.models import CskhUpstreamError

    kit = Kit(world)
    calls: list[str] = []
    real = kit.pancake.list_pages

    async def counted(token: str) -> list[PancakePageInfo]:
        calls.append(token)
        if len(calls) > 1:
            raise CskhUpstreamError("pancake_rate_limited", "Pancake đang giới hạn số lần gọi.")
        return await real(token)

    kit.pancake.list_pages = counted  # type: ignore[method-assign]
    await kit.connect(PAGE, PAGE_B)
    assert len(calls) == 1  # the account's add; connect + job reused it
    _name, kwargs = kit.sent_tasks[-1]
    assert {p["id"] for p in kwargs["pages"]} == {PAGE, PAGE_B}
    assert (await world.session.get(CskhPage, PAGE_B)) is not None
    # Rate-limited with a list older than 5 minutes: the last list (≤ 12 h) is used.
    listed = await pancake_pages(world.session, kit.clients, max_age=timedelta(0))
    assert {info.id for info, _ in listed} >= {PAGE, PAGE_B}
    account = await world.session.scalar(select(CskhPancakeAccount))
    assert account is not None and account.last_error == "Pancake đang giới hạn số lần gọi."
    # No list at all and Pancake refuses: the job stops with one clear line.
    await write_state(world.session, "cskh_pancake_pages", {})
    job = await kit.sync().connect(["123"])
    assert job["running"] is False and len(job["errors"]) == 1
    assert "giới hạn số lần gọi" in job["errors"][0]
