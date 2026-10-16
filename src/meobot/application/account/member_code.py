"""The person's member code (0063): one per account, on every open tag too.

``users.member_code`` is the person's; each open stream tag carries a copy
(``org_unit_members.member_code``) because order codes, rosters and the board
read the tag. Every write goes through :func:`write_member_code`, so the two
never drift; a tag opened later takes the account's code
(``departments.sync_stream_tags``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.db.models import OrgUnitMember, User
from meobot.domain.account.errors import MemberCodeTakenError


async def member_code_holder(
    session: AsyncSession, code: str, *, exclude: uuid.UUID | None = None
) -> User | None:
    """The (other) living account that already carries ``code``, if any."""
    statement = select(User).where(User.member_code == code, User.deleted_at.is_(None))
    if exclude is not None:
        statement = statement.where(User.id != exclude)
    holder: User | None = await session.scalar(statement.limit(1))
    return holder


async def write_member_code(session: AsyncSession, user: User, code: str | None) -> None:
    """Give ``user`` this (already normalised) code, on the account and on
    every open tag. ``None`` clears it. 0068: one code per person - a code
    another living account carries is refused (``member_code_taken``)."""
    if code is not None:
        holder = await member_code_holder(session, code, exclude=user.id)
        if holder is not None:
            raise MemberCodeTakenError(code, holder.full_name)
    user.member_code = code
    tags = await session.scalars(
        select(OrgUnitMember).where(
            OrgUnitMember.user_id == user.id, OrgUnitMember.left_at.is_(None)
        )
    )
    for tag in tags:
        tag.member_code = code
    await session.flush()


__all__ = ["member_code_holder", "write_member_code"]
