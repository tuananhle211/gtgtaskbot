"""Per-group response policy, including Guest grants and their counters.

One row per (bot, chat, telegram user). Absence of a row means
:attr:`~meobot.domain.access.models.GroupPolicyMode.INHERIT`, so this table
changes nothing until somebody sets a policy - which is what makes the whole
feature additive.

Two rules are enforced here rather than left to callers:

* a **mute expires by arithmetic, not by a task**. ``resolve`` compares
  ``muted_until`` to the current time and reports ``INHERIT`` once it has
  passed, so nothing has to sweep the table for a mute to end on time;
* a **Guest is a policy row in mode ``guest``**. The grant and the counters
  live together, so "may they ask" and "how many are left" are answered from
  the same row in the same transaction, and the two can never disagree.

Consuming a Guest question is *not* here - it is in
:class:`~meobot.application.quota_service.QuotaService`, because reserving a
slot and converting it after delivery is the same problem for a Guest and for a
Member and must not be solved twice.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, format_local, utcnow
from meobot.db.models.access import GroupMemberResponsePolicy
from meobot.domain.access.models import (
    GUEST_DEFAULT_DURATION,
    GUEST_DEFAULT_QUESTION_LIMIT,
    GroupPolicyMode,
    GuestPrincipal,
    guest_deadline,
)
from meobot.domain.identity.models import Actor

logger = get_logger(__name__)

#: Mute presets accepted by ``/mute_user``.
MUTE_PRESETS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

DEFAULT_MUTE = MUTE_PRESETS["1d"]


def guest_deadline_text(row: GroupMemberResponsePolicy, timezone: ZoneInfo) -> str:
    """The wall-clock end of a Guest window, in the deployment's timezone.

    The column is nullable because a policy row exists for non-Guest modes too,
    so every caller would otherwise have to repeat the same ``None`` check
    before formatting a date that is always set for an actual grant.
    """
    if row.guest_expires_at is None:
        return "không rõ"
    return format_local(row.guest_expires_at, timezone, fmt="%H:%M %d/%m")


def parse_duration(text: str | None) -> timedelta | None:
    """Read ``1h`` / ``1d`` / ``7d`` / ``30d``, or ``None`` when unrecognised.

    Deliberately a closed set. A free-form duration parser would happily accept
    "10y" and quietly mute somebody for a decade.
    """
    if text is None:
        return None
    return MUTE_PRESETS.get(text.strip().lower())


class GroupPolicyService:
    """Reads and writes :class:`GroupMemberResponsePolicy` rows.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Reading ----------------------------------------------------------
    async def get(
        self, *, bot_id: int, chat_id: int, telegram_user_id: int
    ) -> GroupMemberResponsePolicy | None:
        """The stored row for one person in one chat, if there is one."""
        result = await self._session.execute(
            select(GroupMemberResponsePolicy).where(
                GroupMemberResponsePolicy.bot_id == bot_id,
                GroupMemberResponsePolicy.telegram_chat_id == chat_id,
                GroupMemberResponsePolicy.telegram_user_id == telegram_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def resolve_mode(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        now: datetime | None = None,
    ) -> GroupPolicyMode:
        """The mode that actually applies right now.

        Expiry is arithmetic: a ``MUTE_UNTIL`` whose timestamp has passed reads
        as ``INHERIT``, and a ``GUEST`` whose window has closed reads as
        ``INHERIT`` too - so the next mention goes back through the approval
        flow instead of being silently answered forever.
        """
        moment = now or utcnow()
        row = await self.get(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        if row is None:
            return GroupPolicyMode.INHERIT
        return self.effective_mode(row, now=moment)

    @staticmethod
    def effective_mode(
        row: GroupMemberResponsePolicy, *, now: datetime | None = None
    ) -> GroupPolicyMode:
        """Apply time-based expiry to a stored row."""
        moment = now or utcnow()
        if row.revoked_at is not None:
            return GroupPolicyMode.INHERIT
        if row.mode is GroupPolicyMode.MUTE_UNTIL:
            if row.muted_until is None or moment >= ensure_utc(row.muted_until):
                return GroupPolicyMode.INHERIT
            return GroupPolicyMode.MUTE_UNTIL
        if row.mode is GroupPolicyMode.GUEST:
            if row.guest_expires_at is None or moment >= ensure_utc(row.guest_expires_at):
                return GroupPolicyMode.INHERIT
            if row.guest_questions_used + row.guest_reserved_count >= row.guest_question_limit:
                # Allowance spent. Not an error - just no longer a Guest.
                return GroupPolicyMode.INHERIT
            return GroupPolicyMode.GUEST
        return row.mode

    async def active_guest(
        self,
        *,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        display_name: str = "Khách",
        username: str | None = None,
        now: datetime | None = None,
    ) -> GuestPrincipal | None:
        """The Guest principal for this person in this chat, if still valid."""
        moment = now or utcnow()
        row = await self.get(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        if row is None or self.effective_mode(row, now=moment) is not GroupPolicyMode.GUEST:
            return None
        if row.guest_granted_at is None or row.guest_expires_at is None:  # pragma: no cover
            return None
        return GuestPrincipal(
            policy_id=row.id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=chat_id,
            display_name=display_name,
            telegram_username=username,
            granted_at=ensure_utc(row.guest_granted_at),
            expires_at=ensure_utc(row.guest_expires_at),
            question_limit=row.guest_question_limit,
            questions_used=row.guest_questions_used,
            reserved_count=row.guest_reserved_count,
        )

    async def list_for_chat(
        self, *, bot_id: int, chat_id: int
    ) -> Sequence[GroupMemberResponsePolicy]:
        """Every stored policy in one chat, newest first."""
        result = await self._session.execute(
            select(GroupMemberResponsePolicy)
            .where(
                GroupMemberResponsePolicy.bot_id == bot_id,
                GroupMemberResponsePolicy.telegram_chat_id == chat_id,
            )
            .order_by(GroupMemberResponsePolicy.created_at.desc())
        )
        return result.scalars().all()

    # --- Writing ----------------------------------------------------------
    async def _upsert(
        self, *, bot_id: int, chat_id: int, telegram_user_id: int
    ) -> GroupMemberResponsePolicy:
        """Find the row for this triple or create a fresh inherit-mode one."""
        row = await self.get(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        if row is not None:
            return row
        row = GroupMemberResponsePolicy(
            bot_id=bot_id,
            telegram_chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            mode=GroupPolicyMode.INHERIT,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_mode(
        self,
        *,
        actor: Actor,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        mode: GroupPolicyMode,
        muted_until: datetime | None = None,
        reason: str | None = None,
    ) -> GroupMemberResponsePolicy:
        """Set a plain (non-Guest) mode for one person in one chat."""
        row = await self._upsert(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        row.mode = mode
        row.muted_until = muted_until
        row.reason = reason
        row.revoked_at = None
        row.revoked_by_user_id = None
        row.created_by_user_id = actor.user_id
        row.created_by_telegram_id = actor.telegram_user_id
        if mode is not GroupPolicyMode.GUEST:
            # Leaving guest mode ends the allowance; it is not kept in reserve
            # for a future grant.
            row.guest_reserved_count = 0
        await self._session.flush()
        logger.info(
            "group_policy_set",
            extra={"chat_id": chat_id, "target_telegram_id": telegram_user_id, "mode": mode.value},
        )
        return row

    async def reset_to_inherit(
        self, *, actor: Actor, bot_id: int, chat_id: int, telegram_user_id: int
    ) -> GroupMemberResponsePolicy:
        """Return this person to the default rules in this chat.

        The row is kept and marked revoked rather than deleted: who granted
        what, and who took it away, is exactly the history an audit needs.
        """
        row = await self._upsert(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        row.mode = GroupPolicyMode.INHERIT
        row.muted_until = None
        row.guest_reserved_count = 0
        row.revoked_at = utcnow()
        row.revoked_by_user_id = actor.user_id
        await self._session.flush()
        return row

    async def grant_guest(
        self,
        *,
        actor: Actor,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        question_limit: int = GUEST_DEFAULT_QUESTION_LIMIT,
        duration: timedelta = GUEST_DEFAULT_DURATION,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> GroupMemberResponsePolicy:
        """Start a fresh Guest window in this chat.

        Both limits start now - at the owner's confirmation - not at whenever
        the stranger first spoke. A previous, spent allowance is reset rather
        than added to: this is a new grant, not an extension.
        """
        moment = now or utcnow()
        row = await self._upsert(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        row.mode = GroupPolicyMode.GUEST
        row.muted_until = None
        row.guest_granted_at = moment
        row.guest_expires_at = guest_deadline(moment, duration)
        row.guest_question_limit = question_limit
        row.guest_questions_used = 0
        row.guest_reserved_count = 0
        row.guest_exhausted_at = None
        row.reason = reason
        row.revoked_at = None
        row.revoked_by_user_id = None
        row.created_by_user_id = actor.user_id
        row.created_by_telegram_id = actor.telegram_user_id
        await self._session.flush()
        logger.info(
            "guest_granted",
            extra={
                "chat_id": chat_id,
                "target_telegram_id": telegram_user_id,
                "question_limit": question_limit,
            },
        )
        return row

    async def extend_guest(
        self,
        *,
        actor: Actor,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        extra_questions: int = 0,
        extra_duration: timedelta | None = None,
        now: datetime | None = None,
    ) -> GroupMemberResponsePolicy | None:
        """Add questions and/or time to an existing Guest window.

        Adding questions preserves the existing deadline, which is the
        behaviour the owner's "+10 câu" button promises.
        """
        moment = now or utcnow()
        row = await self.get(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        if row is None or row.guest_granted_at is None:
            return None
        row.mode = GroupPolicyMode.GUEST
        row.revoked_at = None
        if extra_questions:
            row.guest_question_limit += extra_questions
            row.guest_exhausted_at = None
        if extra_duration is not None:
            base = (
                ensure_utc(row.guest_expires_at)
                if row.guest_expires_at is not None and ensure_utc(row.guest_expires_at) > moment
                else moment
            )
            row.guest_expires_at = base + extra_duration
        row.created_by_user_id = actor.user_id or row.created_by_user_id
        row.created_by_telegram_id = actor.telegram_user_id or row.created_by_telegram_id
        await self._session.flush()
        return row

    async def reset_guest(
        self,
        *,
        actor: Actor,
        bot_id: int,
        chat_id: int,
        telegram_user_id: int,
        now: datetime | None = None,
    ) -> GroupMemberResponsePolicy:
        """Put a Guest back to a full default allowance and a fresh 24 hours."""
        return await self.grant_guest(
            actor=actor,
            bot_id=bot_id,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            question_limit=GUEST_DEFAULT_QUESTION_LIMIT,
            duration=GUEST_DEFAULT_DURATION,
            now=now,
        )

    async def revoke_guest(
        self, *, actor: Actor, bot_id: int, chat_id: int, telegram_user_id: int
    ) -> GroupMemberResponsePolicy | None:
        """End Guest access immediately, keeping the record of it."""
        row = await self.get(bot_id=bot_id, chat_id=chat_id, telegram_user_id=telegram_user_id)
        if row is None:
            return None
        row.mode = GroupPolicyMode.INHERIT
        row.guest_expires_at = utcnow()
        row.guest_reserved_count = 0
        row.revoked_at = utcnow()
        row.revoked_by_user_id = actor.user_id
        await self._session.flush()
        return row

    async def policy_by_id(self, policy_id: uuid.UUID) -> GroupMemberResponsePolicy | None:
        """Load one policy row by primary key."""
        return await self._session.get(GroupMemberResponsePolicy, policy_id)
