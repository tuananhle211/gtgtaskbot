"""Ghim menu: the nav branches a person pinned (2026-11-12, no migration).

A pinned branch ("Phòng PR", "Nhân sự"...) is drawn at the top of the sidebar,
outside "Phòng Media", and always open. The list is the person's own, kept on
the server so it follows them from one device to another (the web app keeps
nothing in browser storage): one ``system_settings`` row per person, key
``nav_pins:<user id>``, value ``{"pins": [...]}`` in pin order.

Keys are the frontend's branch keys (``pr``, ``ads``, ``orders``, ``hr``...).
The server only checks their shape: a key the menu does not know (a branch
renamed or gone) is ignored when the menu is drawn.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.errors import ValidationError
from meobot.db.models.system_setting import SystemSetting

KEY_PREFIX = "nav_pins:"
MAX_PINS = 20
_PIN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class NavPinsInvalidError(ValidationError):
    code = "nav_pins_invalid"

    def __init__(self) -> None:
        super().__init__(
            "Danh sách ghim không hợp lệ.",
            details={"reason": "nav_pins_invalid", "max": MAX_PINS},
        )


def clean_pins(pins: list[str]) -> list[str]:
    """Every key well formed, at most :data:`MAX_PINS`, duplicates dropped
    (the first place kept)."""
    if len(pins) > MAX_PINS * 2 or any(
        not isinstance(pin, str) or not _PIN.match(pin) for pin in pins
    ):
        raise NavPinsInvalidError()
    unique = list(dict.fromkeys(pins))
    if len(unique) > MAX_PINS:
        raise NavPinsInvalidError()
    return unique


async def _row(session: AsyncSession, user_id: uuid.UUID) -> SystemSetting | None:
    row: SystemSetting | None = await session.scalar(
        select(SystemSetting).where(SystemSetting.key == f"{KEY_PREFIX}{user_id}")
    )
    return row


async def read_pins(session: AsyncSession, user_id: uuid.UUID | None) -> list[str]:
    if user_id is None:
        return []
    row = await _row(session, user_id)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    pins = value.get("pins")
    if not isinstance(pins, list):
        return []
    return [pin for pin in pins if isinstance(pin, str) and _PIN.match(pin)][:MAX_PINS]


async def save_pins(session: AsyncSession, user_id: uuid.UUID, pins: list[str]) -> list[str]:
    cleaned = clean_pins(pins)
    row = await _row(session, user_id)
    if row is None:
        session.add(
            SystemSetting(
                key=f"{KEY_PREFIX}{user_id}",
                value={"pins": cleaned},
                description="Ghim menu của một người",
                updated_by=user_id,
            )
        )
    else:
        row.value = {"pins": cleaned}
        row.version = (row.version or 1) + 1
        row.updated_by = user_id
    await session.flush()
    return cleaned


__all__ = ["MAX_PINS", "NavPinsInvalidError", "clean_pins", "read_pins", "save_pins"]
