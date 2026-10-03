"""Signed callback data for the owner's access and quota buttons.

These buttons grant access to a system. They are therefore bound far more
tightly than the review buttons in
:mod:`meobot.domain.conversations.callbacks`, and the binding is the point of
this module: the HMAC covers not just "which request and which action" but
*who may press it*, *about whom*, *in which chat*, *about which message*, and
*until when*.

Telegram gives us 64 bytes of ``callback_data``, which is nowhere near enough to
carry five 64-bit ids. So the wire format carries only what is needed to find
the row again::

    ga|<request uuid hex>|<action>|<expiry unix seconds>|<signature>
    2 + 1 +      32       + 1 + 2  + 1 +      10        + 1 +   10   = 60 bytes

and the bound values are read back out of the stored request at verification
time and fed into the signature material. A forged or replayed button therefore
fails for the same reason a tampered one does: the material does not match.

Consequences worth naming:

* pressing the same button twice produces the same verified payload, so
  idempotency is the handler's job and the handler can rely on the identity of
  what it is being asked to do;
* a button copied out of the owner's chat and pressed by somebody else fails,
  because the acting Telegram id is part of the material;
* a button that outlives its expiry fails, because the expiry is both inside
  the material and checked explicitly.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from meobot.domain.access.models import AccessAction, QuotaAction

SIGNATURE_LENGTH = 10
SEPARATOR = "|"
MAX_CALLBACK_BYTES = 64

#: Prefix identifying which family of buttons a payload belongs to. Two
#: characters, and distinct from every value in
#: :class:`~meobot.domain.conversations.callbacks.CallbackAction` so the two
#: parsers can never accept each other's data.
ACCESS_KIND = "ga"
QUOTA_KIND = "qa"


class CallbackBinding(BaseModel):
    """The facts a button is tied to, read from storage - never from the wire.

    Every field here is part of the signature material. Nothing here is
    transmitted, so nothing here can be tampered with by a client.
    """

    model_config = ConfigDict(frozen=True)

    #: The Telegram account allowed to press this button.
    owner_telegram_id: int
    #: The person the decision is about.
    subject_telegram_id: int
    #: Where the decision applies.
    telegram_chat_id: int
    #: The message that triggered the request, so ANSWER_ONCE answers *that*.
    source_message_id: int


class AccessCallbackPayload(BaseModel):
    """A verified press of an access or quota button."""

    model_config = ConfigDict(frozen=True)

    kind: str
    request_id: uuid.UUID
    action: AccessAction | QuotaAction
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


def _material(
    *,
    kind: str,
    request_id: uuid.UUID,
    action: str,
    expiry: int,
    binding: CallbackBinding,
) -> str:
    """Everything the signature commits to, in a fixed order."""
    return SEPARATOR.join(
        [
            kind,
            request_id.hex,
            action,
            str(expiry),
            str(binding.owner_telegram_id),
            str(binding.subject_telegram_id),
            str(binding.telegram_chat_id),
            str(binding.source_message_id),
        ]
    )


def _signature(secret: str, material: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), material.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:SIGNATURE_LENGTH]


def build_access_callback(
    action: AccessAction | QuotaAction,
    *,
    secret: str,
    request_id: uuid.UUID,
    binding: CallbackBinding,
    expires_at: datetime,
) -> str:
    """Build one signed button payload.

    Raises:
        ValueError: When the result would exceed Telegram's 64-byte limit,
            which in practice means an expiry far outside a sane range.
    """
    kind = QUOTA_KIND if isinstance(action, QuotaAction) else ACCESS_KIND
    expiry = int(expires_at.timestamp())
    material = _material(
        kind=kind, request_id=request_id, action=action.value, expiry=expiry, binding=binding
    )
    data = SEPARATOR.join(
        [kind, request_id.hex, action.value, str(expiry), _signature(secret, material)]
    )
    if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:  # pragma: no cover - guard
        raise ValueError(f"Access callback data too long: {len(data)} bytes")
    return data


def peek_request_id(data: str) -> tuple[str, uuid.UUID] | None:
    """Read ``(kind, request_id)`` without verifying anything.

    The handler needs the id to load the row that supplies the binding, and it
    cannot verify the signature until it has that row. Nothing is trusted on the
    strength of this: the row is only *read*, and
    :func:`parse_access_callback` still has to succeed before anything happens.
    """
    parts = data.split(SEPARATOR)
    if len(parts) != 5:
        return None
    kind, request_hex = parts[0], parts[1]
    if kind not in {ACCESS_KIND, QUOTA_KIND}:
        return None
    try:
        return kind, uuid.UUID(hex=request_hex)
    except ValueError:
        return None


def parse_access_callback(
    data: str,
    *,
    secret: str,
    binding: CallbackBinding,
) -> AccessCallbackPayload | None:
    """Verify a button press against the binding loaded from storage.

    Returns ``None`` for anything malformed, unknown, or signed for a different
    owner, subject, chat, message, action or expiry. The caller shows a neutral
    "this button is no longer valid" rather than explaining which check failed.
    """
    parts = data.split(SEPARATOR)
    if len(parts) != 5:
        return None
    kind, request_hex, action_value, expiry_text, signature = parts
    if kind not in {ACCESS_KIND, QUOTA_KIND}:
        return None

    try:
        request_id = uuid.UUID(hex=request_hex)
        expiry = int(expiry_text)
    except ValueError:
        return None

    material = _material(
        kind=kind, request_id=request_id, action=action_value, expiry=expiry, binding=binding
    )
    if not hmac.compare_digest(_signature(secret, material), signature):
        return None

    action: AccessAction | QuotaAction
    try:
        action = QuotaAction(action_value) if kind == QUOTA_KIND else AccessAction(action_value)
    except ValueError:
        return None

    return AccessCallbackPayload(
        kind=kind,
        request_id=request_id,
        action=action,
        expires_at=datetime.fromtimestamp(expiry, tz=UTC),
    )
