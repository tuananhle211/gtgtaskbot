"""Signed callback data for Member and approval buttons.

Telegram allows 64 bytes and hands the string back verbatim, so a button is a
client-supplied input like any other. Each one here commits to everything that
makes the press meaningful:

* **who** may press it (the acting Telegram id);
* **where** (the chat);
* **what** (the action and the target entity);
* **which version** of that entity the button was drawn for;
* **until when**;
* **which bot** issued it.

The version is the part that is easy to leave out and expensive to miss. An
approval card shows a request as it was; if the requester amends it afterwards,
the old card must stop working, or the owner approves text they never read.

Fitting all that in 64 bytes needs two compressions: a UUID travels as 22
base64url characters rather than 36, and the expiry is minutes since the epoch
rather than seconds. A full payload comes to roughly 52 bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

SEPARATOR = "|"
SIGNATURE_LENGTH = 10
MAX_CALLBACK_BYTES = 64
KIND = "m"

#: Two-character wire codes. The readable name is what handlers use; only the
#: code travels, because 64 bytes does not stretch to "hr.request_changes".
ACTION_CODES: dict[str, str] = {
    "home.open": "ho",
    "help.open": "he",
    "quota.mine": "qm",
    "chat.open": "co",
    "work.list": "wl",
    "work.progress": "wp",
    "hr.leave": "hl",
    "hr.late": "ht",
    "hr.mine": "hm",
    "hr.stats": "hs",
    "hr.pending": "hp",
    "hr.today": "hd",
    "hr.withdraw": "hw",
    "hr.approve": "ha",
    "hr.reject": "hr",
    "hr.changes": "hc",
    "hr.period.morning": "pm",
    "hr.period.afternoon": "pa",
    "hr.period.full": "pf",
    "hr.period.hours": "ph",
    "hr.reason.skip": "rs",
    "flow.confirm": "fc",
    "flow.edit": "fe",
    "flow.discard": "fd",
    "chat.register.confirm": "cr",
    "chat.register.cancel": "cx",
    "announce.send": "as",
    "announce.cancel": "ax",
    "announce.pick": "ap",
    "announce.ack": "aa",
    "announce.ask": "aq",
    "announce.remind": "ar",
    #: Offered when a named group is not registered (0.6.0a2.1). Neither
    #: carries an entity: there is no draft, because nothing was created.
    "announce.destinations": "ad",
    "announce.retarget": "at",
    # --- Reminders (0.6.0a2) ---------------------------------------------
    "rem.confirm": "rc",
    "rem.edit": "rd",
    "rem.cancel": "rx",
    #: The two answers to "4 giờ sáng hay chiều?".
    "rem.hour.am": "ra",
    "rem.hour.pm": "rp",
    "rem.pause": "ru",
    "rem.resume": "rn",
    "rem.stop": "rt",
    "rem.list": "rl",
    # --- Group manager assignment (0.6.0a2) ------------------------------
    "group.assign.confirm": "gc",
    "group.assign.cancel": "gx",
    # --- Multi-group dispatch (0.6.0a3) ----------------------------------
    #: Ticking is expressed as two opposite actions rather than one toggle, and
    #: that is deliberate: the button drawn next to an unticked group asks to
    #: tick it, the one next to a ticked group asks to untick it, and pressing
    #: either twice is a no-op the second time. A toggle pressed twice undoes
    #: itself, which is not what a double tap on a phone means.
    "disp.select": "d1",
    "disp.deselect": "dg",
    "disp.all": "d2",
    "disp.none": "d3",
    #: Finish choosing and show the full preview.
    "disp.done": "d4",
    #: Confirm. Signed against the draft *version*, so a preview drawn for
    #: three groups cannot send to a selection that has changed since.
    "disp.send": "d5",
    "disp.cancel": "d6",
    "disp.reselect": "d7",
    "disp.detail": "d8",
    #: Retry only the destinations that failed.
    "disp.retry": "d9",
    "disp.summary": "da",
    #: Registry list paging. Carries the page number in the version field,
    #: because there is no entity to point at.
    "disp.page": "db",
    "disp.list.all": "dc",
    "disp.list.each": "dd",
    "disp.list.healthy": "de",
    "disp.edit": "df",
}
_CODE_TO_ACTION: dict[str, str] = {code: name for name, code in ACTION_CODES.items()}


def data_pattern(actions: Iterable[str]) -> str:
    """A regex matching only the callback data of ``actions``.

    Used as an aiogram filter so a router matches *its own* buttons and nothing
    else. Matching every ``m|`` payload and returning early does not work: in
    aiogram 3 a handler whose filters matched has handled the update, so the
    next router never sees it.
    """
    codes = "|".join(re.escape(ACTION_CODES[name]) for name in sorted(actions))
    return rf"^{KIND}\|(?:{codes})\|"


def pack_uuid(value: uuid.UUID | str | None) -> str:
    """A UUID as 22 URL-safe characters, losslessly."""
    if value is None or value == "":
        return ""
    identifier = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    return base64.urlsafe_b64encode(identifier.bytes).decode("ascii").rstrip("=")


def unpack_uuid(packed: str) -> uuid.UUID | None:
    """Reverse :func:`pack_uuid`, or ``None`` when it is not a UUID."""
    if not packed:
        return None
    try:
        padded = packed + "=" * (-len(packed) % 4)
        return uuid.UUID(bytes=base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class MemberBinding:
    """The facts a button is tied to. None of these travel on the wire."""

    bot_id: int
    telegram_user_id: int
    chat_id: int


@dataclass(frozen=True, slots=True)
class MemberCallback:
    """A verified button press."""

    action: str
    entity_id: uuid.UUID | None
    version: int
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def _material(*, code: str, packed: str, version: int, expiry: int, binding: MemberBinding) -> str:
    return SEPARATOR.join(
        [
            KIND,
            code,
            packed,
            str(version),
            str(expiry),
            str(binding.bot_id),
            str(binding.telegram_user_id),
            str(binding.chat_id),
        ]
    )


def _signature(secret: str, material: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), material.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:SIGNATURE_LENGTH]


def build(
    action: str,
    *,
    secret: str,
    binding: MemberBinding,
    expires_at: datetime,
    entity_id: uuid.UUID | str | None = None,
    version: int = 0,
) -> str:
    """Build one signed button payload.

    Raises:
        ValueError: For an unknown action, or a payload over Telegram's limit -
            both are programming errors and should fail at render time rather
            than produce a button that silently never works.
    """
    code = ACTION_CODES.get(action)
    if code is None:
        raise ValueError(f"Unknown member callback action: {action!r}")
    packed = pack_uuid(entity_id)
    expiry = int(expires_at.timestamp() // 60)
    material = _material(code=code, packed=packed, version=version, expiry=expiry, binding=binding)
    data = SEPARATOR.join(
        [KIND, code, packed, str(version), str(expiry), _signature(secret, material)]
    )
    if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:  # pragma: no cover - guard
        raise ValueError(f"Member callback too long: {len(data)} bytes")
    return data


def parse(data: str, *, secret: str, binding: MemberBinding) -> MemberCallback | None:
    """Verify a press against the binding built from the live update.

    Returns ``None`` for anything malformed, unknown, or signed for a different
    person, chat or bot. Expiry is reported through
    :meth:`MemberCallback.is_expired` so the caller can tell "not yours" from
    "too old" and say the right thing.
    """
    parts = data.split(SEPARATOR)
    if len(parts) != 6 or parts[0] != KIND:
        return None
    _, code, packed, version_text, expiry_text, signature = parts

    action = _CODE_TO_ACTION.get(code)
    if action is None:
        return None
    try:
        version = int(version_text)
        expiry = int(expiry_text)
    except ValueError:
        return None

    material = _material(code=code, packed=packed, version=version, expiry=expiry, binding=binding)
    if not hmac.compare_digest(_signature(secret, material), signature):
        return None

    return MemberCallback(
        action=action,
        entity_id=unpack_uuid(packed),
        version=version,
        expires_at=datetime.fromtimestamp(expiry * 60, tz=UTC),
    )
