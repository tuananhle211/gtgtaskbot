"""Signed Telegram callback data.

Telegram hands ``callback_data`` back to us verbatim, and a client can send any
string it likes. Anything that names an entity and an action therefore carries
a short HMAC over its own payload: a tampered button (a different script id, a
different action) fails :func:`parse_callback` instead of executing.

The signature is truncated to 10 hex characters. That is not a secret-grade
MAC, but forging one requires ~2^40 guesses against a bot that also re-checks
permissions, workflow state and version identity server-side - the button is a
convenience, never the authority.

Telegram caps ``callback_data`` at 64 bytes, which is why ids are packed as
bare hex and the payload is pipe-separated rather than JSON.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

#: Bytes of the HMAC kept in the callback string.
SIGNATURE_LENGTH = 10
SEPARATOR = "|"
MAX_CALLBACK_BYTES = 64


class CallbackAction(StrEnum):
    """Inline-button actions. Values are short on purpose - 64 bytes total."""

    APPROVE_PRODUCTION = "ap"
    REQUEST_REVISION = "rr"
    REVIEW_AGAIN = "ra"
    VIEW_SCRIPT = "vs"
    SKIP = "sk"
    MAPPING_CONFIRM = "mc"
    MAPPING_EDIT = "me"
    MAPPING_CANCEL = "mx"
    WORKSHEET_PICK = "wp"
    SYNC_NOW = "sn"


class CallbackPayload(BaseModel):
    """Decoded, signature-verified callback data."""

    model_config = ConfigDict(frozen=True)

    action: CallbackAction
    entity_id: uuid.UUID | None = None
    #: Free-form short argument (a version number, a worksheet index).
    argument: str = ""


def _signature(secret: str, material: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), material.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:SIGNATURE_LENGTH]


def build_callback(
    action: CallbackAction,
    *,
    secret: str,
    entity_id: uuid.UUID | None = None,
    argument: str = "",
) -> str:
    """Build signed callback data.

    Raises:
        ValueError: When the result would exceed Telegram's 64-byte limit
            (a caller passing an oversized ``argument``).
    """
    if SEPARATOR in argument:
        raise ValueError("Callback argument must not contain the separator")
    material = SEPARATOR.join([action.value, entity_id.hex if entity_id else "", argument])
    data = SEPARATOR.join([material, _signature(secret, material)])
    if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise ValueError(f"Callback data too long: {len(data)} bytes")
    return data


def parse_callback(data: str, *, secret: str) -> CallbackPayload | None:
    """Verify and decode callback data.

    Returns ``None`` for anything malformed, unknown or badly signed - the
    caller shows a neutral 'button expired' message rather than guessing.
    """
    parts = data.split(SEPARATOR)
    if len(parts) != 4:
        return None
    action_value, entity_hex, argument, signature = parts

    material = SEPARATOR.join([action_value, entity_hex, argument])
    if not hmac.compare_digest(_signature(secret, material), signature):
        return None

    try:
        action = CallbackAction(action_value)
    except ValueError:
        return None

    entity_id: uuid.UUID | None = None
    if entity_hex:
        try:
            entity_id = uuid.UUID(hex=entity_hex)
        except ValueError:
            return None

    return CallbackPayload(action=action, entity_id=entity_id, argument=argument)
