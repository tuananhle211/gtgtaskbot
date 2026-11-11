"""Reading Pancake into the inbox, sending, and the background jobs (0102).

Ported from ``hop-chat-apexmed`` ``server.py`` (``pancake_poll_page``,
``save_message``, ``send_message``, ``maybe_auto_reply``, ``pancake_sync_meta``,
``ad_fetch_loop``, ``qr_loop``, ``pos_load_shops``, ``_run_connect_job``):

* ``poll`` (Celery ``cskh.poll_pancake``, every 20 s): each connected page in
  its own transaction - Pancake's 60 newest conversations; tags, assignees,
  phone numbers and ads refreshed every time; the thread read again only when
  Pancake's ``updated_at`` moved. A page's first read (``polled_at`` None)
  loads the history and counts nothing as unread nor answers it.
* new customer pictures are read for QR codes; unknown ads are looked up.
* ``refresh`` (``cskh.refresh_meta``, every 10 min): Pancake tags + staff per
  page, the accounts' page lists (new pages not connected), the POS shops.
* ``connect`` (``cskh.connect_pages``): connect pages, progress in
  ``system_settings`` ``cskh_job``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.cskh.common import (
    JOB_KEY,
    STATE_KEY,
    CskhClients,
    account_token,
    load_settings,
    page_aad,
    page_token,
    pos_key,
    read_state,
    vn_now,
    write_state,
)
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.cskh import (
    CskhAd,
    CskhConversation,
    CskhMessage,
    CskhPage,
    CskhPageLabel,
    CskhPageMember,
    CskhPancakeAccount,
    CskhPancakeTag,
    CskhPancakeUser,
    CskhPosKey,
    CskhPosShop,
)
from meobot.domain.cskh.errors import CskhInvalidError, CskhUpstreamRefusedError
from meobot.domain.cskh.models import (
    AUTO_REPLY_COOLDOWN_HOURS,
    AUTO_REPLY_FRESH_MINUTES,
    MAX_QR_IMAGE_BYTES,
    RECENT_EDIT_SECONDS,
    CskhUpstreamError,
    Direction,
    InboxSettings,
    MemberRole,
    MessageKind,
    attachment_label,
    clip,
    find_phones,
    fold,
    has_image,
    is_work_time,
    mapping_of,
    merge_phones,
    merge_qrs,
    normalize_attachments,
    pancake_time,
    phones_text,
    qr_kind,
    token_hint,
)
from meobot.integrations.pancake.client import PancakePageInfo

logger = logging.getLogger(__name__)

Transactions = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: Pancake allows 5 calls / s / page: a pause between two thread reads.
CALL_PAUSE_SECONDS = 0.2
#: Pages read at once (each its own transaction).
PARALLEL_PAGES = 5
#: Per page and run: QR pictures read, ads looked up.
QR_PER_RUN = 10
ADS_PER_RUN = 10
#: A job "running" for longer than this was killed with its worker.
JOB_STALE = timedelta(minutes=45)

try:  # pragma: no cover - depends on the image's wheels
    import zxingcpp
    from PIL import Image, ImageOps

    QR_READY, QR_ERROR = True, ""
except Exception as _error:  # pragma: no cover
    QR_READY, QR_ERROR = False, str(_error)


def decode_qr(data: bytes) -> list[str]:
    """QR texts in a picture (grey, rotated, a small one doubled)."""
    if not QR_READY:
        return []
    opened = Image.open(io.BytesIO(data))
    with contextlib.suppress(EOFError):
        opened.seek(0)
    image: Image.Image = ImageOps.exif_transpose(opened).convert("L")
    width, height = image.size
    tries: list[Image.Image] = [image]
    if max(width, height) < 900:
        tries.append(image.resize((width * 2, height * 2)))
    for candidate in tries:
        try:
            found = zxingcpp.read_barcodes(
                candidate,
                formats=zxingcpp.BarcodeFormat.QRCode,
                try_rotate=True,
                try_downscale=True,
            )
        except TypeError:
            found = zxingcpp.read_barcodes(candidate)
        texts = [item.text for item in found if item.text]
        if texts:
            return list(dict.fromkeys(texts))
    return []


QrDecoder = Callable[[bytes], list[str]]


# --- the store -------------------------------------------------------------------------------


async def conversation_of(session: AsyncSession, page_id: str, psid: str) -> CskhConversation:
    row: CskhConversation | None = await session.scalar(
        select(CskhConversation).where(
            CskhConversation.page_id == page_id, CskhConversation.psid == psid
        )
    )
    if row is not None:
        return row
    row = CskhConversation(
        id=uuid.uuid4(),
        page_id=page_id,
        psid=psid,
        customer_name="",
        customer_key="",
        last_text="",
        unread=0,
        pancake_tags=[],
        assignee_ids=[],
        phones=[],
        phone_digits="",
        ads=[],
        qrs=[],
        qr_texts="",
    )
    session.add(row)
    await session.flush()
    return row


def set_name(conv: CskhConversation, name: str | None) -> None:
    clean = " ".join(str(name or "").split())[:300]
    if clean and clean != conv.customer_name:
        conv.customer_name = clean
        conv.customer_key = fold(clean)[:300]


def set_phones(conv: CskhConversation, phones: Iterable[str]) -> bool:
    """Add numbers; True when the conversation had none before."""
    old = [str(p) for p in conv.phones or []]
    merged = merge_phones(old, [p for p in phones if p])
    if merged != old:
        conv.phones = merged
        conv.phone_digits = phones_text(merged)
    return not old and bool(merged)


async def save_message(
    session: AsyncSession,
    conv: CskhConversation,
    *,
    mid: str,
    direction: Direction,
    kind: MessageKind,
    text: str,
    at: datetime,
    attachments: Sequence[dict[str, Any]] = (),
    status: str = "ok",
    sender_user_id: uuid.UUID | None = None,
    sender_name: str = "",
) -> tuple[CskhMessage | None, bool]:
    """``(row, new)``. A known id gets its attachments if it had none (old rows)."""
    existing: CskhMessage | None = await session.scalar(
        select(CskhMessage).where(CskhMessage.mid == mid[:200])
    )
    if existing is not None:
        if attachments and not existing.attachments:
            existing.attachments = list(attachments)
            existing.text = text or existing.text
            if direction is Direction.IN and has_image(attachments):
                existing.qr = None
        return existing, False
    row = CskhMessage(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        mid=mid[:200],
        direction=direction.value,
        kind=kind.value,
        text=text,
        text_key=fold(text),
        at=at,
        status=status[:300],
        attachments=list(attachments),
        qr=None,
        sender_user_id=sender_user_id,
        sender_name=sender_name[:200],
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:  # the same id saved by a concurrent run
        return None, False
    return row, True


def aware(value: datetime | None) -> datetime | None:
    """DB times as aware UTC (SQLite in tests drops the offset)."""
    return ensure_utc(value) if value is not None else None


def touch_last(conv: CskhConversation, text: str, at: datetime, *, outgoing: bool) -> None:
    last = aware(conv.last_at)
    if last is None or at >= last:
        conv.last_text = clip(text, 500)
        conv.last_at = at
        conv.last_out = outgoing


# --- sending ---------------------------------------------------------------------------------


async def send_text(
    session: AsyncSession,
    clients: CskhClients,
    conv: CskhConversation,
    page: CskhPage,
    text: str,
    *,
    kind: MessageKind = MessageKind.STAFF,
    sender_user_id: uuid.UUID | None = None,
    sender_name: str = "",
) -> CskhMessage:
    """Send through Pancake (``reply_inbox``) and keep the message. Refused = 422, nothing kept."""
    token = page_token(clients, page)
    if not token or not page.active:
        raise CskhInvalidError(
            "cskh_page_not_connected",
            "Page này chưa nối Pancake (hoặc đang tắt) nên chưa gửi được.",
        )
    try:
        mid = await clients.pancake.send(page.id, f"{page.id}_{conv.psid}", token, text)
    except CskhUpstreamError as error:
        raise CskhUpstreamRefusedError(error.code, _send_refusal(error.message)) from error
    now = utcnow()
    row, _new = await save_message(
        session,
        conv,
        mid=mid or f"apm-{uuid.uuid4().hex}",
        direction=Direction.OUT,
        kind=kind,
        text=text,
        at=now,
        sender_user_id=sender_user_id,
        sender_name=sender_name,
    )
    touch_last(conv, text, now, outgoing=True)
    if row is None:  # pragma: no cover - the poll saved it first
        row = await session.scalar(select(CskhMessage).where(CskhMessage.mid == mid))
        assert row is not None
    if sender_name and not row.sender_name:
        row.sender_name = sender_name[:200]
        row.sender_user_id = sender_user_id
    return row


def _send_refusal(message: str) -> str:
    lowered = message.lower()
    if "24" in lowered or "window" in lowered or "outside" in lowered:
        return (
            "Pancake không gửi được: khách đã quá 24 giờ chưa nhắn page "
            f"(quy tắc của Facebook). Chi tiết: {message}"
        )
    return f"Pancake không gửi được: {message}"


async def maybe_auto_reply(
    session: AsyncSession,
    clients: CskhClients,
    conv: CskhConversation,
    page: CskhPage,
    settings: InboxSettings,
) -> bool:
    """Out of hours: one answer per conversation per 12 h, the conversation marked "Chờ gọi lại"."""
    if not settings.auto_reply_enabled or is_work_time(settings, vn_now()):
        return False
    now = utcnow()
    last_auto = aware(conv.last_auto_at)
    if last_auto and now - last_auto < timedelta(hours=AUTO_REPLY_COOLDOWN_HOURS):
        return False
    conv.last_auto_at = now
    conv.callback = True
    try:
        await send_text(
            session, clients, conv, page, settings.auto_reply_text, kind=MessageKind.AUTO
        )
    except (CskhInvalidError, CskhUpstreamRefusedError) as error:
        logger.warning("cskh_auto_reply_failed", extra={"page": page.id, "error": str(error)})
        return False
    return True


# --- reading Pancake ---------------------------------------------------------------------


@dataclass
class PollReport:
    pages: int = 0
    conversations: int = 0
    messages: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        status = "error" if self.errors and not self.pages else "ok"
        return {
            "status": status,
            "message": (
                f"{self.pages} page, {self.conversations} hội thoại đọc lại, "
                f"{self.messages} tin mới, {len(self.errors)} lỗi"
            ),
            "errors": self.errors[:5],
        }


class CskhSync:
    def __init__(
        self,
        transactions: Transactions,
        clients: CskhClients,
        *,
        qr_decoder: QrDecoder | None = None,
        pause: float = CALL_PAUSE_SECONDS,
    ) -> None:
        self._transactions = transactions
        self._clients = clients
        self._decode = qr_decoder or (decode_qr if QR_READY else None)
        self._pause = pause

    # --- polling ----------------------------------------------------------------------

    async def poll(self, *, page_ids: Sequence[str] | None = None) -> PollReport:
        async with self._transactions() as session:
            query = select(CskhPage.id).where(
                CskhPage.active.is_(True), CskhPage.token_encrypted.is_not(None)
            )
            if page_ids is not None:
                query = query.where(CskhPage.id.in_(list(page_ids)))
            ids = list((await session.scalars(query.order_by(CskhPage.id))).all())
        report = PollReport()
        gate = asyncio.Semaphore(PARALLEL_PAGES)

        async def one(page_id: str) -> None:
            async with gate:
                try:
                    convs, new = await self.poll_page(page_id)
                    report.pages += 1
                    report.conversations += convs
                    report.messages += new
                except Exception as error:  # one page's failure never stops the others
                    report.errors.append(f"{page_id}: {error}")
                    logger.warning("cskh_poll_failed", extra={"page": page_id, "error": str(error)})
                    await self._note_error(page_id, str(error))

        await asyncio.gather(*(one(page_id) for page_id in ids))
        return report

    async def _note_error(self, page_id: str, message: str) -> None:
        with contextlib.suppress(Exception):
            async with self._transactions() as session:
                page = await session.get(CskhPage, page_id)
                if page is not None:
                    page.poll_error = clip(message, 300)

    async def poll_page(self, page_id: str, *, force: bool = False) -> tuple[int, int]:
        """``(conversations read again, new customer messages)`` for one page."""
        async with self._transactions() as session:
            page = await session.get(CskhPage, page_id)
            token = page_token(self._clients, page) if page is not None else None
            if page is None or not token:
                return 0, 0
            settings = await load_settings(session)
            live = page.polled_at is not None
            touched, new_total, images, ad_ids = await self._read_page(
                session, page, token, settings, live=live, force=force
            )
            page.polled_at = utcnow()
            page.poll_error = None
        if images:
            await self.scan_qr(images)
        if ad_ids:
            await self.fetch_ads(page_id, ad_ids)
        return touched, new_total

    async def _read_page(
        self,
        session: AsyncSession,
        page: CskhPage,
        token: str,
        settings: InboxSettings,
        *,
        live: bool,
        force: bool,
    ) -> tuple[int, int, list[uuid.UUID], set[str]]:
        conversations = await self._clients.pancake.conversations(page.id, token)
        now = utcnow()
        touched = new_total = 0
        images: list[uuid.UUID] = []
        ad_ids: set[str] = set()
        for item in conversations:
            if item.get("type") != "INBOX":
                continue
            sender = mapping_of(item.get("from"))
            psid = str(sender.get("id") or "")[:80]
            if not psid:
                continue
            conv = await conversation_of(session, page.id, psid)
            set_name(conv, sender.get("name"))
            updated = pancake_time(item.get("updated_at"))
            edited = aware(conv.edited_at)
            fresh_edit = edited is not None and now - edited < timedelta(
                seconds=RECENT_EDIT_SECONDS
            )
            if not fresh_edit:
                conv.pancake_tags = [str(t) for t in item.get("tags") or [] if t is not None]
                conv.assignee_ids = [str(a) for a in item.get("assignee_ids") or [] if a]
            set_phones(
                conv,
                [
                    str(p.get("captured") or p.get("phone_number") or "")
                    for p in item.get("recent_phone_numbers") or []
                    if isinstance(p, dict)
                ],
            )
            ads = _ads_of(item)
            if ads:
                conv.ads = ads
                conv.ad_id = ads[0]["ad_id"]
                ad_ids.update(a["ad_id"] for a in ads)
            if (
                not force
                and conv.pancake_updated_at is not None
                and updated is not None
                and ensure_utc(conv.pancake_updated_at) >= updated
            ):
                continue
            pancake_id = str(item.get("id") or f"{page.id}_{psid}")
            messages = await self._clients.pancake.messages(page.id, pancake_id, token)
            touched += 1
            if self._pause:
                await asyncio.sleep(self._pause)
            new_in: list[datetime] = []
            last_customer = False
            for raw in messages:
                text = str(raw.get("original_message") or "").strip()
                attachments = normalize_attachments(raw.get("attachments"))
                if not text and not attachments:
                    continue
                at = pancake_time(raw.get("inserted_at")) or now
                outgoing = str((raw.get("from") or {}).get("id")) == str(page.id)
                row, new = await save_message(
                    session,
                    conv,
                    mid=str(raw.get("id") or f"pc-{page.id}-{psid}-{at.timestamp()}"),
                    direction=Direction.OUT if outgoing else Direction.IN,
                    kind=MessageKind.PAGE if outgoing else MessageKind.CUSTOMER,
                    text=text,
                    at=at,
                    attachments=attachments,
                )
                preview = text or attachment_label(attachments)
                if (
                    row is not None
                    and not outgoing
                    and row.qr is None
                    and has_image(row.attachments)
                ):
                    images.append(row.id)
                if new and not outgoing:
                    new_in.append(at)
                    # Numbers the customer typed (Pancake's list misses some).
                    set_phones(conv, find_phones(text))
                last_at = aware(conv.last_at)
                if last_at is None or at >= last_at:
                    last_customer = not outgoing
                touch_last(conv, preview, at, outgoing=outgoing)
            if updated is not None:
                conv.pancake_updated_at = updated
            if live and new_in:
                new_total += len(new_in)
                conv.unread = (conv.unread or 0) + len(new_in)
                conv.last_in_at = max(new_in)
                if last_customer and now - max(new_in) < timedelta(
                    minutes=AUTO_REPLY_FRESH_MINUTES
                ):
                    await maybe_auto_reply(session, self._clients, conv, page, settings)
            elif new_in:
                previous = aware(conv.last_in_at)
                conv.last_in_at = max(new_in + ([previous] if previous else []))
        return touched, new_total, images, ad_ids

    # --- QR codes ---------------------------------------------------------------------

    async def scan_qr(self, message_ids: Sequence[uuid.UUID], *, force: bool = False) -> int:
        """Read the QR codes in the customers' pictures; returns how many were found."""
        if self._decode is None:
            return 0
        found_total = 0
        for message_id in list(message_ids)[: QR_PER_RUN * 5]:
            async with self._transactions() as session:
                message = await session.get(CskhMessage, message_id)
                if message is None or message.direction != Direction.IN.value:
                    continue
                if message.qr is not None and not force:
                    continue
                found: list[dict[str, Any]] = []
                for attachment in message.attachments or []:
                    url = str(attachment.get("url") or "")
                    if attachment.get("kind") != "image" or not url:
                        continue
                    try:
                        data = await self._clients.pancake.download(url, MAX_QR_IMAGE_BYTES)
                        texts = await asyncio.to_thread(self._decode, data)
                    except Exception as error:  # a dead CDN link, a broken picture
                        logger.info("cskh_qr_unreadable", extra={"error": str(error)[:150]})
                        continue
                    found += [
                        {
                            "text": text[:1000],
                            "kind": qr_kind(text),
                            "mid": message.mid,
                            "at": ensure_utc(message.at).isoformat(),
                            "url": url,
                        }
                        for text in texts
                    ]
                message.qr = [item["text"] for item in found]
                if found:
                    conv = await session.get(CskhConversation, message.conversation_id)
                    if conv is not None:
                        add_qrs(conv, found)
                    found_total += len(found)
        return found_total

    # --- ads --------------------------------------------------------------------------

    async def fetch_ads(self, page_id: str, ad_ids: Iterable[str]) -> int:
        async with self._transactions() as session:
            known = set(
                (
                    await session.scalars(
                        select(CskhAd.ad_id).where(CskhAd.ad_id.in_(list(ad_ids)))
                    )
                ).all()
            )
            wanted = [a for a in ad_ids if a and a not in known][:ADS_PER_RUN]
            page = await session.get(CskhPage, page_id)
            token = page_token(self._clients, page) if page is not None else None
            if not wanted or not token:
                return 0
            saved = 0
            for ad_id in wanted:
                try:
                    items = await self._clients.pancake.ads(page_id, token, ad_id)
                except CskhUpstreamError as error:
                    logger.info(
                        "cskh_ad_lookup_failed", extra={"ad": ad_id, "error": error.message}
                    )
                    continue
                info = next(
                    (a for a in items if str(a.get("id")) == ad_id), items[0] if items else None
                )
                session.add(
                    CskhAd(
                        ad_id=ad_id,
                        page_id=page_id,
                        name=clip((info or {}).get("name") or "", 500),
                        image_url=str((info or {}).get("image_url") or ""),
                        campaign_id=str((info or {}).get("campaign_id") or "")[:60],
                        campaign_name=clip((info or {}).get("campaign_name") or "", 500),
                        account_id=str((info or {}).get("account_id") or "")[:60],
                        found=info is not None,
                        fetched_at=utcnow(),
                    )
                )
                saved += 1
                if self._pause:
                    await asyncio.sleep(self._pause)
            await session.flush()
            return saved

    # --- every 10 minutes -------------------------------------------------------------

    async def refresh(self) -> dict[str, Any]:
        """Tags + staff of every connected page, new pages on Pancake, POS shops."""
        async with self._transactions() as session:
            ids = list(
                (
                    await session.scalars(
                        select(CskhPage.id).where(
                            CskhPage.active.is_(True), CskhPage.token_encrypted.is_not(None)
                        )
                    )
                ).all()
            )
        errors: list[str] = []
        for page_id in ids:
            try:
                async with self._transactions() as session:
                    await self.sync_meta(session, page_id)
            except Exception as error:
                errors.append(f"{page_id}: {error}")
        new_pages = 0
        try:
            async with self._transactions() as session:
                listed = await pancake_pages(session, self._clients)
                settings = await load_settings(session)
                connected = set((await session.scalars(select(CskhPage.id))).all())
                new_pages = sum(
                    1
                    for info, _account in listed
                    if info.status == "activated"
                    and info.id not in connected
                    and not settings.skipped(info.name)
                )
                state = await read_state(session, STATE_KEY)
                await write_state(
                    session,
                    STATE_KEY,
                    {**state, "new_pages": new_pages, "checked_at": utcnow().isoformat()},
                    description="Hộp chat CSKH: page mới trên Pancake chưa nối.",
                )
        except Exception as error:
            errors.append(f"Pancake: {error}")
        try:
            async with self._transactions() as session:
                await load_pos_shops(session, self._clients)
        except Exception as error:
            errors.append(f"POS: {error}")
        return {
            "status": "ok" if not errors or ids else "error",
            "message": f"{len(ids)} page, {new_pages} page mới chưa nối, {len(errors)} lỗi",
            "errors": errors[:5],
        }

    async def sync_meta(self, session: AsyncSession, page_id: str) -> tuple[int, int]:
        page = await session.get(CskhPage, page_id)
        token = page_token(self._clients, page) if page is not None else None
        if page is None or not token:
            return 0, 0
        tags = await self._clients.pancake.tags(page_id, token)
        users = await self._clients.pancake.users(page_id, token)
        await session.execute(delete(CskhPancakeTag).where(CskhPancakeTag.page_id == page_id))
        await session.execute(delete(CskhPancakeUser).where(CskhPancakeUser.page_id == page_id))
        seen_tags: set[str] = set()
        for tag in tags:
            tag_id = str(tag.get("id") or "")[:60]
            if tag_id and tag_id not in seen_tags:
                seen_tags.add(tag_id)
                session.add(
                    CskhPancakeTag(
                        page_id=page_id,
                        tag_id=tag_id,
                        text=clip(tag.get("text") or "", 200),
                        color=str(tag.get("color") or "#888780")[:20],
                    )
                )
        seen_users: set[str] = set()
        for user in users:
            user_id = str(user.get("id") or "")[:80]
            if (
                user_id
                and user_id not in seen_users
                and (user.get("status_in_page") or "active") == "active"
            ):
                seen_users.add(user_id)
                session.add(
                    CskhPancakeUser(
                        page_id=page_id, user_id=user_id, name=clip(user.get("name") or "", 200)
                    )
                )
        page.meta_at = utcnow()
        await session.flush()
        return len(seen_tags), len(seen_users)

    # --- connecting pages -------------------------------------------------------------

    async def connect(
        self,
        page_ids: Sequence[str],
        *,
        label_ids: Sequence[uuid.UUID] = (),
        members: Sequence[tuple[uuid.UUID, MemberRole]] = (),
        import_history: bool = True,
    ) -> dict[str, Any]:
        """The connect job: each page found on Pancake, its token made, read once."""
        async with self._transactions() as session:
            job = await read_state(session, JOB_KEY)
            job.setdefault("errors", [])
            try:
                listed = {
                    info.id: (info, account)
                    for info, account in await pancake_pages(session, self._clients)
                }
            except Exception as error:
                listed = {}
                job["errors"].append(f"Không lấy được danh sách page từ Pancake: {error}")
            account_ids = {pid: account.id for pid, (_info, account) in listed.items()}
            infos = {pid: info for pid, (info, _account) in listed.items()}
            pos_on = bool(await session.scalar(select(func.count()).select_from(CskhPosKey)))
            if pos_on:
                try:
                    await load_pos_shops(session, self._clients)
                except Exception as error:
                    pos_on = False
                    job["errors"].append(f"POS Cake: không lấy được danh sách cửa hàng ({error})")
            job.update(pos_on=pos_on)
            await write_state(session, JOB_KEY, job)
        for page_id in page_ids:
            info = infos.get(page_id)
            name = info.name if info else page_id
            await self._progress(current=name)
            try:
                if info is None:
                    raise CskhInvalidError(
                        "cskh_page_missing",
                        "không có trong tài khoản Pancake "
                        "(cần thêm page vào Pancake và cấp quyền quản trị)",
                    )
                if info.status != "activated":
                    raise CskhInvalidError(
                        "cskh_page_inactive",
                        "chưa kích hoạt trên Pancake (cần kích hoạt trong gói cước)",
                    )
                generated = await self._connect_one(info, account_ids[page_id], label_ids, members)
                conversations = 0
                if import_history:
                    conversations, _ = await self.poll_page(page_id, force=True)
                async with self._transactions() as session:
                    page = await session.get(CskhPage, page_id)
                    try:
                        await self.sync_meta(session, page_id)
                    except CskhUpstreamError as error:
                        await self._progress(
                            error=f"{name}: không lấy được thẻ/nhân viên Pancake ({error.message})"
                        )
                    has_shop = bool(page and page.pos_shop_id)
                await self._progress(
                    connected=1,
                    generated=1 if generated else 0,
                    conversations=conversations,
                    pos_linked=1 if pos_on and has_shop else 0,
                    pos_missing=name if pos_on and not has_shop else None,
                )
            except Exception as error:
                message = getattr(error, "message", None) or str(error)
                await self._progress(error=f"{name}: {message}")
            await self._progress(done=1)
        async with self._transactions() as session:
            job = await read_state(session, JOB_KEY)
            job.update(running=False, current="", finished_at=utcnow().isoformat())
            await write_state(session, JOB_KEY, job)
        return job

    async def _connect_one(
        self,
        info: PancakePageInfo,
        account_id: uuid.UUID,
        label_ids: Sequence[uuid.UUID],
        members: Sequence[tuple[uuid.UUID, MemberRole]],
    ) -> bool:
        async with self._transactions() as session:
            page = await session.get(CskhPage, info.id)
            if page is None:
                page = CskhPage(
                    id=info.id, name=info.name[:300], username=info.username[:200], token_hint=""
                )
                session.add(page)
            page.name = info.name[:300]
            page.username = info.username[:200]
            page.active = True
            generated = False
            if not page.token_encrypted:
                account = await session.get(CskhPancakeAccount, account_id)
                assert account is not None
                token = await self._clients.pancake.generate_page_token(
                    account_token(self._clients, account), info.id
                )
                page.token_encrypted = self._clients.box().encrypt(token, aad=page_aad(info.id))
                page.token_hint = token_hint(token)
                page.pancake_account_id = account_id
                generated = True
            await session.flush()
            for label_id in label_ids:
                if await session.get(CskhPageLabel, (info.id, label_id)) is None:
                    session.add(CskhPageLabel(page_id=info.id, label_id=label_id))
            for user_id, role in members:
                member = await session.get(CskhPageMember, (info.id, user_id))
                if member is None:
                    session.add(CskhPageMember(page_id=info.id, user_id=user_id, role=role.value))
                else:
                    member.role = role.value
            await _link_shop(session, page)
            await session.flush()
            return generated

    async def _progress(
        self,
        *,
        current: str | None = None,
        done: int = 0,
        connected: int = 0,
        generated: int = 0,
        conversations: int = 0,
        pos_linked: int = 0,
        pos_missing: str | None = None,
        error: str | None = None,
    ) -> None:
        async with self._transactions() as session:
            job = await read_state(session, JOB_KEY)
            if current is not None:
                job["current"] = current
            for key, value in (
                ("done", done),
                ("connected", connected),
                ("generated", generated),
                ("conversations", conversations),
                ("pos_linked", pos_linked),
            ):
                job[key] = int(job.get(key) or 0) + value
            if pos_missing:
                job["pos_missing"] = [*job.get("pos_missing", []), pos_missing][:200]
            if error:
                job["errors"] = [*job.get("errors", []), clip(error, 300)][:200]
            await write_state(session, JOB_KEY, job)


def add_qrs(conv: CskhConversation, items: Sequence[dict[str, Any]]) -> None:
    merged = merge_qrs(conv.qrs or [], items)
    if merged != list(conv.qrs or []):
        conv.qrs = merged
        conv.qr_texts = "\n".join(str(item.get("text") or "") for item in merged)
    set_phones(conv, [p for item in items for p in find_phones(str(item.get("text") or ""))])


def _ads_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    ads = [
        {
            "ad_id": str(a.get("ad_id")),
            "post_id": str(a.get("post_id") or ""),
            "at": (pancake_time(a.get("inserted_at")) or utcnow()).isoformat()
            if a.get("inserted_at")
            else None,
        }
        for a in item.get("ads") or []
        if isinstance(a, dict) and a.get("ad_id")
    ]
    if not ads:
        ads = [{"ad_id": str(x), "post_id": "", "at": None} for x in item.get("ad_ids") or [] if x]
    ads.sort(key=lambda a: a["at"] or "", reverse=True)
    return ads


async def job_running(session: AsyncSession) -> bool:
    job = await read_state(session, JOB_KEY)
    if not job.get("running"):
        return False
    started = job.get("started_at")
    try:
        when = ensure_utc(datetime.fromisoformat(str(started)))
    except ValueError:
        return False
    return utcnow() - when < JOB_STALE


async def start_job(
    session: AsyncSession, *, label: str, total: int, user_id: uuid.UUID | None
) -> dict[str, Any]:
    job = {
        "running": True,
        "label": label,
        "total": total,
        "done": 0,
        "connected": 0,
        "generated": 0,
        "conversations": 0,
        "current": "",
        "errors": [],
        "started_at": utcnow().isoformat(),
        "finished_at": None,
        "pos_on": False,
        "pos_linked": 0,
        "pos_missing": [],
    }
    await write_state(
        session, JOB_KEY, job, user_id=user_id, description="Hộp chat CSKH: tiến độ kết nối page."
    )
    return job


# --- Pancake accounts' pages ---------------------------------------------------------------


async def pancake_pages(
    session: AsyncSession, clients: CskhClients
) -> list[tuple[PancakePageInfo, CskhPancakeAccount]]:
    """Every account's pages, a page two accounts hold kept once (the activating one, else
    the earlier account). Each account's counts / error are refreshed."""
    accounts = list(
        (
            await session.scalars(
                select(CskhPancakeAccount).order_by(
                    CskhPancakeAccount.sort_order, CskhPancakeAccount.created_at
                )
            )
        ).all()
    )
    merged: dict[str, tuple[PancakePageInfo, CskhPancakeAccount]] = {}
    order: list[str] = []
    failures = 0
    for account in accounts:
        account.checked_at = utcnow()
        try:
            listed = await clients.pancake.list_pages(account_token(clients, account))
        except Exception as error:
            failures += 1
            account.last_error = clip(getattr(error, "message", None) or str(error), 300)
            continue
        account.last_error = None
        account.page_count = len(listed)
        account.active_count = sum(1 for p in listed if p.status == "activated")
        for info in listed:
            old = merged.get(info.id)
            if old is None:
                merged[info.id] = (info, account)
                order.append(info.id)
            elif old[0].status != "activated" and info.status == "activated":
                merged[info.id] = (info, account)
    await session.flush()
    if accounts and failures == len(accounts):
        raise CskhUpstreamRefusedError(
            "pancake_token_invalid",
            "Không đọc được tài khoản Pancake nào (token sai hoặc đã hết hạn 90 ngày).",
        )
    return [merged[page_id] for page_id in order]


# --- POS Cake shops ------------------------------------------------------------------------


async def _link_shop(session: AsyncSession, page: CskhPage) -> None:
    shops = (await session.scalars(select(CskhPosShop))).all()
    page.pos_shop_id = next(
        (
            s.id
            for s in _shop_order(shops)
            if any(str(p.get("id")) == page.id for p in s.pages or [])
        ),
        None,
    )


def _shop_order(shops: Iterable[CskhPosShop]) -> list[CskhPosShop]:
    return sorted(shops, key=lambda s: (s.created_at or utcnow(), s.id))


async def load_pos_shops(session: AsyncSession, clients: CskhClients) -> int:
    """Every key's shops (a shop two keys see: the earlier key), then each page's shop."""
    keys = list(
        (
            await session.scalars(
                select(CskhPosKey).order_by(CskhPosKey.sort_order, CskhPosKey.created_at)
            )
        ).all()
    )
    seen: dict[str, tuple[CskhPosKey, dict[str, Any]]] = {}
    failures = 0
    for key in keys:
        key.checked_at = utcnow()
        try:
            shops = await clients.pos.shops(pos_key(clients, key))
        except Exception as error:
            failures += 1
            key.last_error = clip(getattr(error, "message", None) or str(error), 300)
            continue
        key.last_error = None
        key.shop_count = len(shops)
        for shop in shops:
            shop_id = str(shop.get("id") or "")[:40]
            if shop_id and shop_id not in seen:
                seen[shop_id] = (key, shop)
    if keys and failures == len(keys):
        await session.flush()
        raise CskhUpstreamRefusedError("pos_token_invalid", "Không đọc được API key POS nào.")
    existing = {s.id: s for s in (await session.scalars(select(CskhPosShop))).all()}
    for shop_id, (key, shop) in seen.items():
        pages = [
            {"id": str(p.get("id")), "name": str(p.get("name") or "")}
            for p in shop.get("pages") or []
            if isinstance(p, dict) and p.get("id")
        ]
        row = existing.get(shop_id)
        if row is None:
            session.add(
                CskhPosShop(
                    id=shop_id, name=clip(shop.get("name") or "", 300), key_id=key.id, pages=pages
                )
            )
        else:
            row.name = clip(shop.get("name") or "", 300)
            row.key_id = key.id
            row.pages = pages
    for shop_id, row in existing.items():
        if shop_id not in seen:
            await session.delete(row)
    await session.flush()
    page_shop: dict[str, str] = {}
    for row in _shop_order((await session.scalars(select(CskhPosShop))).all()):
        for page in row.pages or []:
            page_shop.setdefault(str(page.get("id")), row.id)
    for page in (await session.scalars(select(CskhPage))).all():
        page.pos_shop_id = page_shop.get(page.id)
    await session.flush()
    return len(seen)


@contextlib.asynccontextmanager
async def same_session(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """A ``Transactions`` over one session (the API's request, tests)."""
    yield session
    await session.flush()


__all__ = [
    "QR_ERROR",
    "QR_READY",
    "CskhSync",
    "PollReport",
    "Transactions",
    "add_qrs",
    "aware",
    "conversation_of",
    "decode_qr",
    "job_running",
    "load_pos_shops",
    "maybe_auto_reply",
    "pancake_pages",
    "same_session",
    "save_message",
    "send_text",
    "set_name",
    "set_phones",
    "start_job",
    "touch_last",
]
