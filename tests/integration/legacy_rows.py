"""Rows written into a database held at an older revision.

The ORM models always describe the newest schema, so ``User(...)`` at, say,
revision 0030 asks for columns that do not exist yet (0045 added the password
ones). Tests that seed users below the head go through this plain INSERT,
which names only the columns every revision has had.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_legacy_user(session: AsyncSession, *, full_name: str, role: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO users (id, full_name, role, active) VALUES (:id, :full_name, :role, true)"
        ),
        {"id": user_id, "full_name": full_name, "role": role},
    )
    return user_id
