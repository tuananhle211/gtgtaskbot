"""Shared pieces of the CSKH inbox (0102): settings, secrets, clients, names."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.errors import ConfigurationError
from meobot.core.secrets import SecretBox
from meobot.core.time import utcnow
from meobot.db.models.cskh import CskhPage, CskhPancakeAccount, CskhPosKey
from meobot.db.models.system_setting import SystemSetting
from meobot.db.models.user import User
from meobot.domain.cskh.errors import CskhInvalidError
from meobot.domain.cskh.models import InboxSettings, settings_from
from meobot.integrations.pancake.client import PancakeClient, PosClient

SETTINGS_KEY = "cskh"
#: The connect job's progress ("Đồng bộ toàn bộ page" / "+ Tạo kết nối").
JOB_KEY = "cskh_job"
#: Counters the worker keeps (new pages on Pancake not connected yet).
STATE_KEY = "cskh_state"
VN = timezone(timedelta(hours=7))


def vn_now() -> datetime:
    return utcnow().astimezone(VN)


@dataclass
class CskhClients:
    """What talks to the outside (fakes in tests)."""

    pancake: PancakeClient
    pos: PosClient
    secret_box: Callable[[], SecretBox]

    def box(self) -> SecretBox:
        try:
            return self.secret_box()
        except ConfigurationError as error:
            raise CskhInvalidError(
                "cskh_secret_not_configured",
                "Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu được token.",
            ) from error

    def secret_ready(self) -> bool:
        try:
            self.secret_box()
        except ConfigurationError:
            return False
        return True


def account_aad(row_id: uuid.UUID) -> str:
    return f"cskh_pancake_account:{row_id}"


def pos_aad(row_id: uuid.UUID) -> str:
    return f"cskh_pos_key:{row_id}"


def page_aad(page_id: str) -> str:
    return f"cskh_page:{page_id}"


def account_token(clients: CskhClients, row: CskhPancakeAccount) -> str:
    return clients.box().decrypt(row.token_encrypted, aad=account_aad(row.id))


def pos_key(clients: CskhClients, row: CskhPosKey) -> str:
    return clients.box().decrypt(row.key_encrypted, aad=pos_aad(row.id))


def page_token(clients: CskhClients, page: CskhPage) -> str | None:
    if not page.token_encrypted:
        return None
    try:
        return clients.box().decrypt(page.token_encrypted, aad=page_aad(page.id))
    except (CskhInvalidError, ConfigurationError, ValueError):
        return None


async def _setting(session: AsyncSession, key: str) -> SystemSetting | None:
    row: SystemSetting | None = await session.scalar(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    return row


async def load_settings(session: AsyncSession) -> InboxSettings:
    row = await _setting(session, SETTINGS_KEY)
    return settings_from(row.value if row is not None and isinstance(row.value, dict) else None)


async def read_state(session: AsyncSession, key: str) -> dict[str, Any]:
    row = await _setting(session, key)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


async def write_state(
    session: AsyncSession,
    key: str,
    value: dict[str, Any],
    *,
    user_id: uuid.UUID | None = None,
    description: str = "",
) -> None:
    row = await _setting(session, key)
    if row is None:
        session.add(
            SystemSetting(key=key, value=value, description=description or None, updated_by=user_id)
        )
    else:
        row.value = value
        row.version = (row.version or 1) + 1
        if user_id is not None:
            row.updated_by = user_id
    await session.flush()


def pairs(rows: Iterable[Any]) -> dict[Any, Any]:
    """Two-column rows as a dict (typed loosely: SQLAlchemy rows)."""
    return {row[0]: row[1] for row in rows}


async def names_of(session: AsyncSession, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await session.execute(select(User.id, User.full_name).where(User.id.in_(wanted)))
    return {row[0]: row[1] or "" for row in rows.all()}


__all__ = [
    "JOB_KEY",
    "SETTINGS_KEY",
    "STATE_KEY",
    "VN",
    "CskhClients",
    "account_aad",
    "account_token",
    "load_settings",
    "names_of",
    "page_aad",
    "page_token",
    "pairs",
    "pos_aad",
    "pos_key",
    "read_state",
    "vn_now",
    "write_state",
]
