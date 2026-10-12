"""Every column that holds a Telegram id is 64-bit.

Telegram account and chat ids passed 2**31 long ago. A 32-bit column takes an
old account's id and refuses a newer one ("integer out of range"), which is how
/create_invite answered one Leader and stayed silent for the others (0055).
SQLite does not enforce the width, so the offline suite would never notice:
this reads the models instead.
"""

from __future__ import annotations

import sqlalchemy as sa

import meobot.db.models  # noqa: F401 - registers every table
from meobot.db.base import Base


def _holds_a_telegram_id(name: str) -> bool:
    return (
        name.endswith(("telegram_id", "telegram_user_id", "chat_id"))
        or name == "telegram_private_chat_id"
    )


def test_every_telegram_id_column_is_bigint() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if _holds_a_telegram_id(column.name)
        and isinstance(column.type, sa.Integer)
        and not isinstance(column.type, sa.BigInteger)
    ]
    assert offenders == []
