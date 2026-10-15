"""The person's member code (0063): one per account, on every open tag too.

``users.member_code`` is the person's; each open stream tag carries a copy
(``org_unit_members.member_code``) because order codes, rosters and the board
read the tag. Every write goes through :func:`write_member_code`, so the two
never drift; a tag opened later takes the account's code
(``departments.sync_stream_tags``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models import OrgUnitMember, User


async def write_member_code(session: AsyncSession, user: User, code: str | None) -> None:
    """Give ``user`` this (already normalised) code, on the account and on
    every open tag. ``None`` clears it."""
    user.member_code = code
    tags = await session.scalars(
        select(OrgUnitMember).where(
            OrgUnitMember.user_id == user.id, OrgUnitMember.left_at.is_(None)
        )
    )
    for tag in tags:
        tag.member_code = code
    await session.flush()


__all__ = ["write_member_code"]
