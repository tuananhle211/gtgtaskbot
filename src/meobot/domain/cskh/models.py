"""Hộp chat Chăm sóc khách hàng (0102, 2026-11-11): the pure rules.

Ported from the prototype ``hop-chat-apexmed`` (``server.py``, 10/11/2026):
customers write to ~90 Facebook pages managed in **Pancake**; APM reads them
every 20 s through Pancake's Public API, staff answer from one inbox, confirm a
phone number / a QR code into a **POS Cake** order (Pancake then reports the
Purchase to Facebook), and the telesales' company **Zalo** nicks are read
through a QR-linked bridge and matched to the page conversation by the
customer code (Mã KH) or the phone number.

Everything here is pure: phone numbers, attachments, QR kinds, work hours,
quick-reply import, the order note, the Zalo message shape.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from enum import StrEnum
from types import MappingProxyType
from typing import Any

#: Pancake's Public API root (``pages.fm``) and POS Cake's (``pos.pages.fm``).
PANCAKE_API_BASE = "https://pages.fm/api"
POS_API_BASE = "https://pos.pages.fm/api/v1"

#: How often the worker reads Pancake (seconds) and refreshes tags / staff / shops.
POLL_SECONDS = 20
META_SECONDS = 600
#: A page's first read (just connected) loads this much and counts nothing as new.
#: Pancake returns at most 60 conversations per call.
MESSAGES_PER_CONVERSATION = 30
#: One out-of-hours answer per conversation per this many hours.
AUTO_REPLY_COOLDOWN_HOURS = 12
#: Only a message this fresh triggers the out-of-hours answer.
AUTO_REPLY_FRESH_MINUTES = 15
#: A staff edit of tags / assignee wins over Pancake's (older) list for this long.
RECENT_EDIT_SECONDS = 60
#: A Pancake page token is not shown, only its last characters.
TOKEN_HINT_CHARS = 4
#: Biggest picture read for a QR code.
MAX_QR_IMAGE_BYTES = 12 * 1024 * 1024
#: A customer code shorter than this never auto-links a Zalo thread (too many false hits).
MIN_CODE_LENGTH = 4
PLATFORMS: tuple[str, ...] = ("Facebook", "TikTok", "Google")
DEFAULT_PLATFORM = "Facebook"

#: POS Cake order statuses (``status`` code -> Vietnamese; the API answers in English).
POS_STATUS: Mapping[int, str] = MappingProxyType(
    {
        0: "Mới",
        17: "Chờ xác nhận",
        11: "Chờ hàng",
        12: "Chờ in",
        13: "Đã in",
        20: "Đã đặt hàng",
        1: "Đã xác nhận",
        8: "Đang đóng hàng",
        9: "Chờ chuyển hàng",
        2: "Đã gửi hàng",
        3: "Đã nhận",
        16: "Đã thu tiền",
        4: "Đang hoàn",
        15: "Hoàn một phần",
        5: "Đã hoàn",
        6: "Đã huỷ",
        7: "Đã xoá",
    }
)
#: A POS order still waiting for its confirmation (Pancake makes one when a
#: customer leaves a number).
PENDING_POS: frozenset[int] = frozenset({0, 17, 11, 12, 13, 20})
CONFIRMED_POS = 1


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class MessageKind(StrEnum):
    #: The customer wrote.
    CUSTOMER = "customer"
    #: Somebody answered from APM.
    STAFF = "staff"
    #: The page answered elsewhere (Pancake, Business Suite, the phone).
    PAGE = "page"
    #: The out-of-hours answer.
    AUTO = "auto"


class MemberRole(StrEnum):
    """Who a page belongs to ("Chủ page") and who is on duty ("Nhân viên trực")."""

    OWNER = "OWNER"
    STAFF = "STAFF"


MEMBER_ROLE_LABELS: Mapping[MemberRole, str] = MappingProxyType(
    {MemberRole.OWNER: "Chủ page", MemberRole.STAFF: "Nhân viên trực"}
)

LABEL_COLORS: tuple[str, ...] = (
    "blue",
    "teal",
    "green",
    "amber",
    "coral",
    "pink",
    "purple",
    "gray",
)


class ConfirmType(StrEnum):
    PHONE = "phone"
    QR = "qr"


class LinkKind(StrEnum):
    """How a Zalo thread was matched to a page conversation."""

    #: By the customer code: the page side's phone numbers are masked for telesales.
    CODE = "code"
    PHONE = "phone"


class ZaloStatus(StrEnum):
    CONNECTED = "connected"
    CLOSED = "closed"
    EXPIRED = "expired"
    LOGOUT = "logout"


ZALO_STATUS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        ZaloStatus.CONNECTED.value: "Đang kết nối",
        ZaloStatus.CLOSED.value: "Mất kết nối",
        ZaloStatus.EXPIRED.value: "Hết phiên, cần quét lại",
        ZaloStatus.LOGOUT.value: "Đã ngắt",
    }
)


class CskhUpstreamError(Exception):
    """Pancake / POS / the Zalo bridge refused or could not be reached."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- text ------------------------------------------------------------------------------------


def fold(value: object) -> str:
    """Accents off, lower case: ``"Nguyễn Đức"`` -> ``"nguyen duc"`` (search keys)."""
    text = unicodedata.normalize("NFD", str(value or "")).replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").lower()


def mapping_of(value: object) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def unescape(value: object) -> str:
    """Pancake escapes HTML in titles (``-&gt;``)."""
    return html.unescape(str(value or "")).strip()


def clip(value: object, size: int) -> str:
    text = str(value or "")
    return text if len(text) <= size else text[: size - 1] + "…"


def token_hint(token: str) -> str:
    return "••••" + token[-TOKEN_HINT_CHARS:] if len(token) >= 8 else "••••"


# --- phone numbers ---------------------------------------------------------------------------

PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){8,10}(?!\d)")


def find_phones(text: str | None) -> list[str]:
    """Vietnamese numbers a customer typed (``0912 345 678``, ``+84...``), as digits."""
    out: list[str] = []
    for match in PHONE_RE.findall(text or ""):
        digits = re.sub(r"[^\d+]", "", match)
        if 10 <= len(digits.lstrip("+")) <= 12 and digits not in out:
            out.append(digits)
    return out


def phone_key(phone: str | None) -> str:
    """The last 9 digits: ``0912...`` and ``+84912...`` are one number."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-9:] if len(digits) >= 9 else ""


def mask_phone(phone: str) -> str:
    """Hide the last 4 digits, keeping spaces / dots: ``0912 345 678`` -> ``0912 34* ***``."""
    chars, hidden = list(phone), 0
    for index in range(len(chars) - 1, -1, -1):
        if chars[index].isdigit():
            chars[index] = "*"
            hidden += 1
            if hidden == 4:
                break
    return "".join(chars)


def mask_phones_in(text: str | None) -> str | None:
    if not text:
        return text
    return PHONE_RE.sub(lambda match: mask_phone(match.group(0)), text)


def mask_deep(value: Any) -> Any:
    """Every phone number inside a JSON value (attachments, QR texts) masked; links kept."""
    if isinstance(value, str):
        return mask_phones_in(value)
    if isinstance(value, list):
        return [mask_deep(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (item if key in ("url", "image", "kind", "ad_id") else mask_deep(item))
            for key, item in value.items()
        }
    return value


def merge_phones(old: Sequence[str], new: Iterable[str]) -> list[str]:
    merged = list(old)
    keys = {phone_key(item) for item in merged}
    for phone in new:
        key = phone_key(phone)
        if phone and key and key not in keys:
            merged.append(phone)
            keys.add(key)
    return merged


def phones_text(phones: Iterable[str]) -> str:
    """The searchable form kept on a conversation: every number's digits, space separated."""
    return " ".join(re.sub(r"\D", "", phone) for phone in phones if phone)


# --- attachments -----------------------------------------------------------------------------

ATTACHMENT_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "image": "[Ảnh]",
        "sticker": "[Nhãn dán]",
        "video": "[Video]",
        "audio": "[Ghi âm]",
        "file": "[Tệp]",
        "card": "[Thẻ]",
        "ad": "[Khách bấm quảng cáo]",
        "address": "[Địa chỉ]",
        "link": "[Liên kết]",
    }
)


def _url(item: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    return str(item.get("url") or payload.get("url") or "")


def normalize_attachments(items: object) -> list[dict[str, Any]]:
    """Pancake's attachments as ``{kind, url, title, subtitle, image, buttons, ad_id}``."""
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        payload = mapping_of(item.get("payload"))
        if kind in ("photo", "image", "sticker", "animated_image_share"):
            out.append(
                {"kind": "sticker" if kind == "sticker" else "image", "url": _url(item, payload)}
            )
        elif kind == "video":
            video = mapping_of(item.get("video_data"))
            out.append(
                {
                    "kind": "video",
                    "url": str(video.get("url") or _url(item, payload)),
                    "image": str(item.get("preview_url") or ""),
                }
            )
        elif kind in ("audio", "file"):
            out.append(
                {
                    "kind": kind,
                    "url": _url(item, payload),
                    "title": unescape(item.get("name") or item.get("title")),
                }
            )
        elif kind == "template":
            elements = payload.get("elements") or ([payload] if payload.get("text") else [])
            for element in elements if isinstance(elements, list) else []:
                if not isinstance(element, dict):
                    continue
                buttons = element.get("buttons") or payload.get("buttons") or []
                out.append(
                    {
                        "kind": "card",
                        "title": unescape(element.get("title") or element.get("text")),
                        "subtitle": unescape(element.get("subtitle")),
                        "image": str(element.get("image_url") or ""),
                        "buttons": [
                            {"title": unescape(b.get("title")), "url": str(b.get("url") or "")}
                            for b in buttons
                            if isinstance(b, dict)
                        ],
                    }
                )
        elif kind == "ad_click":
            posts = (
                item.get("post_attachments")
                if isinstance(item.get("post_attachments"), list)
                else []
            )
            post = posts[0] if posts and isinstance(posts[0], dict) else {}
            media = mapping_of(post.get("media"))
            image = (
                (media.get("image") or {}).get("src")
                if isinstance(media.get("image"), dict)
                else ""
            )
            if not image and "photo" in str(post.get("type") or ""):
                image = post.get("url") or ""
            out.append(
                {
                    "kind": "ad",
                    "ad_id": str(item.get("ad_id") or ""),
                    "subtitle": unescape(post.get("description") or post.get("title")),
                    "image": str(image or ""),
                }
            )
        elif kind == "address":
            out.append(
                {
                    "kind": "address",
                    "title": unescape(item.get("full_address") or item.get("address")),
                }
            )
        elif kind in ("fallback", "share", "link"):
            out.append(
                {
                    "kind": "link",
                    "url": _url(item, payload),
                    "title": unescape(item.get("title") or payload.get("title")),
                }
            )
        elif kind == "system_message":
            continue
        elif _url(item, payload):
            out.append({"kind": "file", "url": _url(item, payload), "title": kind or "Tệp"})
    return [
        entry
        for entry in out
        if entry.get("url") or entry.get("title") or entry.get("subtitle") or entry.get("image")
    ]


def attachment_label(attachments: Sequence[Mapping[str, Any]]) -> str:
    """A short preview for the conversation list: ``[Ảnh]``, ``[Tệp] báo giá.pdf``..."""
    if not attachments:
        return ""
    first = attachments[0]
    label = ATTACHMENT_LABELS.get(str(first.get("kind")), "[Tệp]")
    return f"{label} {first.get('title') or ''}".strip()


def has_image(attachments: Sequence[Mapping[str, Any]] | None) -> bool:
    return any(a.get("kind") == "image" and a.get("url") for a in attachments or [])


# --- QR codes ---------------------------------------------------------------------------------


def qr_kind(text: str) -> str:
    lower = (text or "").lower()
    if "zalo.me" in lower or "zaloapp.com" in lower or "zalo.vn" in lower:
        return "Zalo"
    if lower.startswith("000201"):
        return "Ngân hàng"  # VietQR / EMVCo
    if lower.startswith(("http://", "https://")):
        return "Liên kết"
    return "Văn bản"


def merge_qrs(
    old: Sequence[Mapping[str, Any]], new: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """A conversation's QR list ``[{text, kind, mid, at, url}]``, one per (text, message)."""
    merged = [dict(item) for item in old]
    seen = {(item.get("text"), item.get("mid")) for item in merged}
    for item in new:
        if (item.get("text"), item.get("mid")) not in seen:
            merged.append(dict(item))
            seen.add((item.get("text"), item.get("mid")))
    return merged


# --- time -------------------------------------------------------------------------------------


def pancake_time(value: object) -> datetime | None:
    """Pancake and POS answer UTC without a zone (``2026-11-08T05:52:46.000000``)."""
    text = str(value or "")[:19]
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class InboxSettings:
    """``system_settings`` key ``cskh``: what MANAGE holders edit on "Cài đặt hộp chat"."""

    auto_reply_enabled: bool = True
    work_start: str = "08:00"
    work_end: str = "21:00"
    #: 0 = Monday ... 6 = Sunday.
    work_days: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    auto_reply_text: str = (
        "Cảm ơn anh/chị đã nhắn tin. Hiện đã ngoài giờ làm việc, "
        "anh/chị vui lòng để lại số điện thoại, bên em sẽ gọi lại sớm nhất ạ."
    )
    #: Apexmed's own order labels ("Trạng thái" in the confirmation; POS still goes to 1).
    order_statuses: tuple[str, ...] = (
        "Đã chốt lịch",
        "Đã đặt cọc",
        "Đã đến khám",
        "Đã làm dịch vụ",
        "Hẹn gọi lại",
    )
    #: A repeated number keeps the oldest data's marketer; only MANAGE changes it for this long.
    mkt_lock_days: int = 30
    #: Pancake pages / POS shops whose name holds one of these are not Apexmed's.
    skip_keywords: tuple[str, ...] = ()
    #: POS shops left out of the duplicate-number check.
    pos_dup_skip_keywords: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "auto_reply_enabled": self.auto_reply_enabled,
            "work_start": self.work_start,
            "work_end": self.work_end,
            "work_days": list(self.work_days),
            "auto_reply_text": self.auto_reply_text,
            "order_statuses": list(self.order_statuses),
            "mkt_lock_days": self.mkt_lock_days,
            "skip_keywords": list(self.skip_keywords),
            "pos_dup_skip_keywords": list(self.pos_dup_skip_keywords),
        }

    def skipped(self, name: str | None) -> bool:
        lower = (name or "").lower()
        return any(word.lower() in lower for word in self.skip_keywords if word.strip())

    def dup_skipped(self, name: str | None) -> bool:
        lower = (name or "").lower()
        words = (*self.skip_keywords, *self.pos_dup_skip_keywords)
        return any(word.lower() in lower for word in words if word.strip())


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def valid_hhmm(value: str) -> bool:
    return bool(_HHMM.match(value or ""))


def settings_from(value: Mapping[str, Any] | None) -> InboxSettings:
    """Whatever is stored, read leniently (a bad value = the default)."""
    data = dict(value or {})
    base = InboxSettings()

    def words(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return default
        return tuple(str(item).strip() for item in raw if str(item).strip())

    days_raw = data.get("work_days")
    days = (
        tuple(sorted({int(d) for d in days_raw if isinstance(d, int) and 0 <= d <= 6}))
        if isinstance(days_raw, list)
        else base.work_days
    )
    start = str(data.get("work_start") or base.work_start)
    end = str(data.get("work_end") or base.work_end)
    lock = data.get("mkt_lock_days")
    return InboxSettings(
        auto_reply_enabled=bool(data.get("auto_reply_enabled", base.auto_reply_enabled)),
        work_start=start if valid_hhmm(start) else base.work_start,
        work_end=end if valid_hhmm(end) else base.work_end,
        work_days=days,
        auto_reply_text=str(data.get("auto_reply_text") or base.auto_reply_text),
        order_statuses=words("order_statuses", base.order_statuses),
        mkt_lock_days=lock if isinstance(lock, int) and 0 <= lock <= 3650 else base.mkt_lock_days,
        skip_keywords=words("skip_keywords", base.skip_keywords),
        pos_dup_skip_keywords=words("pos_dup_skip_keywords", base.pos_dup_skip_keywords),
    )


def is_work_time(settings: InboxSettings, local: datetime) -> bool:
    """``local`` = Vietnam time."""
    hm = local.strftime("%H:%M")
    return local.weekday() in settings.work_days and settings.work_start <= hm < settings.work_end


def hhmm(value: time) -> str:
    return value.strftime("%H:%M")


# --- quick replies ----------------------------------------------------------------------------


def clean_shortcut(value: object) -> str:
    return str(value or "").strip().lstrip("/").replace(" ", "_")[:40]


def parse_quick_replies(text: str) -> list[tuple[str, str]]:
    """``/chao | Dạ em chào {ten}`` blocks; following lines join the text above."""
    items: list[list[str]] = []
    current: list[str] | None = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("/") and "|" in stripped:
            shortcut, _, body = stripped[1:].partition("|")
            current = [clean_shortcut(shortcut), body.strip()]
            items.append(current)
        elif current is not None:
            current[1] += "\n" + line.rstrip()
    return [(shortcut, body.strip()) for shortcut, body in items if shortcut and body.strip()]


# --- orders -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderInfo:
    """What the confirmation dialog sends (names already resolved)."""

    customer_name: str = ""
    address: str = ""
    content_name: str = ""
    order_status: str = ""
    platform: str = DEFAULT_PLATFORM
    marketer: str = ""
    creator: str = ""
    telesale: str = ""
    customer_code: str = ""
    note: str = ""


def order_note_text(info: OrderInfo) -> str:
    """The note put on the POS order (name / phone / address have their own fields)."""
    parts = (
        ("Trạng thái", info.order_status),
        ("Dịch vụ", info.content_name),
        ("Nền tảng", info.platform),
        ("Marketing", info.marketer),
        ("Người tạo", info.creator),
        ("Telesale", info.telesale),
        ("Mã KH", info.customer_code),
        ("Ghi chú", info.note),
    )
    return " · ".join(f"{label}: {value}" for label, value in parts if value)


def normalize_code(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()[:40]


def code_in_text(code: str, text: str) -> bool:
    """A customer code as a whole word in a message (``KH1241242``)."""
    if len(code) < MIN_CODE_LENGTH:
        return False
    return bool(
        re.search(
            r"(?<![A-Z0-9])" + re.escape(code.upper()) + r"(?![A-Z0-9])", (text or "").upper()
        )
    )


# --- Zalo -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ZaloMessage:
    """One message the bridge pushed (Salework's shape: ``uidFrom "0"`` = the nick wrote)."""

    msg_id: str
    peer_uid: str
    outgoing: bool
    group: bool
    text: str
    kind: str
    at: datetime
    sender_name: str = ""
    peer_name: str = ""
    peer_avatar: str = ""


def zalo_text(content: object, msg_type: object) -> tuple[str, str]:
    if isinstance(content, str):
        return content, "text"
    if isinstance(content, dict):
        text = content.get("title") or content.get("description") or content.get("href") or "[tệp]"
        return str(text), str(msg_type or "attach")
    return "", str(msg_type or "other")


def parse_zalo_message(
    account_id: str, raw: Mapping[str, Any], *, group: bool, now: datetime
) -> ZaloMessage | None:
    uid_from = str(raw.get("uidFrom") or "")
    outgoing = uid_from in ("0", account_id)
    peer = str(raw.get("idTo") or "") if outgoing else uid_from
    if group:
        peer = "g" + str(raw.get("idTo") or "")
    if not peer or peer == "g":
        return None
    text, kind = zalo_text(raw.get("content"), raw.get("msgType"))
    try:
        stamp = int(str(raw.get("ts") or "0"))
    except ValueError:
        stamp = 0
    at = datetime.fromtimestamp(stamp / 1000, tz=UTC) if stamp > 0 else now
    info = mapping_of(raw.get("peer"))
    msg_id = str(raw.get("msgId") or raw.get("cliMsgId") or "") or f"{account_id}:{peer}:{stamp}"
    return ZaloMessage(
        msg_id=msg_id[:120],
        peer_uid=peer[:60],
        outgoing=outgoing,
        group=group,
        text=text[:8000],
        kind=kind[:40],
        at=at,
        sender_name=str(raw.get("dName") or "")[:200],
        peer_name=str(info.get("name") or ("" if outgoing or group else raw.get("dName") or ""))[
            :200
        ],
        peer_avatar=str(info.get("avatar") or "")[:1000],
    )


@dataclass(frozen=True, slots=True)
class ZaloPush:
    """One item of the bridge's push (status / account info / messages)."""

    account_id: str
    status: str | None = None
    reason: str = ""
    name: str = ""
    phone: str = ""
    avatar: str = ""
    history: bool = False
    messages: tuple[ZaloMessage, ...] = field(default_factory=tuple)


def parse_zalo_push(payload: object, now: datetime) -> list[ZaloPush]:
    items = payload if isinstance(payload, list) else [payload]
    out: list[ZaloPush] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        account = str(item.get("zaloAccountId") or item.get("accountId") or "")[:40]
        if not account:
            continue
        data = mapping_of(item.get("data"))
        info = mapping_of(data.get("info"))
        history = bool(data.get("history"))
        messages: list[ZaloMessage] = []
        for key, group in (("msgs", False), ("groupMsgs", True)):
            for raw in data.get(key) or []:
                if isinstance(raw, dict):
                    parsed = parse_zalo_message(account, raw, group=group, now=now)
                    if parsed is not None:
                        messages.append(parsed)
        status = str(data.get("status")) if data.get("status") else None
        out.append(
            ZaloPush(
                account_id=account,
                status=status if status in {s.value for s in ZaloStatus} else None,
                reason=str(data.get("reason") or "")[:300],
                name=str(info.get("displayName") or info.get("name") or "")[:200],
                phone=str(info.get("phoneNumber") or "")[:30],
                avatar=str(info.get("avatar") or "")[:1000],
                history=history,
                messages=tuple(messages),
            )
        )
    return out


def looks_like_phone(term: str) -> bool:
    digits = re.sub(r"\D", "", term or "")
    return len(digits) >= 8 and len(digits) >= len((term or "").replace(" ", "")) - 3


__all__ = [
    "ATTACHMENT_LABELS",
    "AUTO_REPLY_COOLDOWN_HOURS",
    "AUTO_REPLY_FRESH_MINUTES",
    "CONFIRMED_POS",
    "DEFAULT_PLATFORM",
    "LABEL_COLORS",
    "MAX_QR_IMAGE_BYTES",
    "MEMBER_ROLE_LABELS",
    "MESSAGES_PER_CONVERSATION",
    "META_SECONDS",
    "MIN_CODE_LENGTH",
    "PANCAKE_API_BASE",
    "PENDING_POS",
    "PLATFORMS",
    "POLL_SECONDS",
    "POS_API_BASE",
    "POS_STATUS",
    "RECENT_EDIT_SECONDS",
    "ZALO_STATUS_LABELS",
    "ConfirmType",
    "CskhUpstreamError",
    "Direction",
    "InboxSettings",
    "LinkKind",
    "MemberRole",
    "MessageKind",
    "OrderInfo",
    "ZaloMessage",
    "ZaloPush",
    "ZaloStatus",
    "attachment_label",
    "clean_shortcut",
    "clip",
    "code_in_text",
    "find_phones",
    "fold",
    "has_image",
    "hhmm",
    "is_work_time",
    "looks_like_phone",
    "mapping_of",
    "mask_deep",
    "mask_phone",
    "mask_phones_in",
    "merge_phones",
    "merge_qrs",
    "normalize_attachments",
    "normalize_code",
    "order_note_text",
    "pancake_time",
    "parse_quick_replies",
    "parse_zalo_message",
    "parse_zalo_push",
    "phone_key",
    "phones_text",
    "qr_kind",
    "settings_from",
    "token_hint",
    "unescape",
    "valid_hhmm",
]
