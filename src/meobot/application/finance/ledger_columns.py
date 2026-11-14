"""Bảng kê: the columns a person hides (14/11/2026, no migration).

User: "Bảng kê BCLT có thêm config tắt bật hiển thị cho các cột". The table
has ~45 columns; each viewer picks which to see, kept on the server so it
follows them from one device to another (like the menu pins): one
``system_settings`` row per person, key ``finance_ledger_columns:<user id>``,
value ``{"hidden": [...]}``. The HIDDEN keys are stored, so a column added
later shows until one hides it. Keys are the frontend's column keys
(``customer_name``, ``method:4``, ``fee:cost``…); the server only checks their
shape, an unknown key is ignored when the table is drawn. The Excel export
keeps every column (the accounting workbook's layout).
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models.system_setting import SystemSetting
from meobot.domain.finance.errors import FinanceInvalidError

KEY_PREFIX = "finance_ledger_columns:"
MAX_COLUMNS = 80
_KEY = re.compile(r"^[a-z][a-z0-9_:]{0,39}$")


def clean_hidden(keys: list[str]) -> list[str]:
    shapes = (isinstance(key, str) and _KEY.match(key) for key in keys)
    if len(keys) > MAX_COLUMNS or not all(shapes):
        raise FinanceInvalidError("ledger_columns_invalid", "Danh sách cột không hợp lệ.")
    return list(dict.fromkeys(keys))


async def _row(session: AsyncSession, user_id: uuid.UUID) -> SystemSetting | None:
    row: SystemSetting | None = await session.scalar(
        select(SystemSetting).where(SystemSetting.key == f"{KEY_PREFIX}{user_id}")
    )
    return row


async def read_hidden(session: AsyncSession, user_id: uuid.UUID | None) -> list[str]:
    if user_id is None:
        return []
    row = await _row(session, user_id)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    hidden = value.get("hidden")
    if not isinstance(hidden, list):
        return []
    return [key for key in hidden if isinstance(key, str) and _KEY.match(key)][:MAX_COLUMNS]


async def save_hidden(session: AsyncSession, user_id: uuid.UUID, keys: list[str]) -> list[str]:
    cleaned = clean_hidden(keys)
    row = await _row(session, user_id)
    if row is None:
        session.add(
            SystemSetting(
                key=f"{KEY_PREFIX}{user_id}",
                value={"hidden": cleaned},
                description="Cột ẩn trên Bảng kê của một người",
                updated_by=user_id,
            )
        )
    else:
        row.value = {"hidden": cleaned}
        row.version = (row.version or 1) + 1
        row.updated_by = user_id
    await session.flush()
    return cleaned


__all__ = ["MAX_COLUMNS", "clean_hidden", "read_hidden", "save_hidden"]
