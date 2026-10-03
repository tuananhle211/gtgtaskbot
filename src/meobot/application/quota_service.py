"""Reserving, committing and releasing chat-quota slots.

Members and Guests are metered by different tables but by the *same* protocol,
so it is written once:

1. **Reserve** before the provider is called. One atomic conditional ``UPDATE``
   increments the reservation counter *and* re-checks the limit in the same
   statement. Two simultaneous messages therefore cannot both take the last
   slot: the second one matches zero rows and is refused.
2. **Call** the provider and **send** the reply.
3. **Commit** once Telegram has accepted it - the reservation becomes a use.
4. **Release** on any failure - the reservation disappears and the person is
   charged nothing.

Why a conditional ``UPDATE`` rather than ``SELECT ... FOR UPDATE``: it is one
round trip, it is correct on PostgreSQL *and* on the SQLite the offline tests
run against, and the check cannot drift away from the write because they are
the same statement.

**Stale reservations.** A process that dies between reserve and commit leaves a
reservation nobody will ever settle. Rather than a sweeper task, reservations
are reconciled lazily: any counter that has not been touched for
:data:`STALE_RESERVATION_TTL` is treated as stranded and cleared on the next
reserve. A live turn writes to the row well inside that window, so nothing in
flight is ever discarded.

**What is not counted.** Only a delivered natural-language answer. Slash
commands, tools, refusals, deterministic notices, provider failures, delivery
failures and duplicate updates all cost nothing - the caller simply never
reaches :meth:`QuotaService.commit`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.core.config import Settings
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.access import GroupMemberResponsePolicy
from meobot.db.models.quota import DailyAiUsage, UserQuotaOverride
from meobot.db.models.user import User
from meobot.domain.access.models import GroupPolicyMode
from meobot.domain.access.quota import (
    MAX_DAILY_LIMIT,
    MEMBER_DEFAULT_DAILY_LIMIT,
    DailyUsageFacts,
    QuotaOutcome,
    QuotaVerdict,
    evaluate,
    next_reset_at,
    quota_date_for,
)
from meobot.domain.identity.models import Role

logger = get_logger(__name__)

#: Roles that are not metered in this release.
UNLIMITED_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.ADMIN, Role.TEAM_LEAD})

#: A reservation older than this belongs to a turn that died. One turn is
#: seconds; this is generous enough that nothing live is ever reclaimed.
STALE_RESERVATION_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Reservation:
    """A held slot, to be settled exactly once.

    ``kind`` decides which table settles it. ``row_id`` is the ledger row for a
    member, or the group-policy row for a Guest.
    """

    kind: str
    row_id: uuid.UUID

    MEMBER = "member"
    GUEST = "guest"


class QuotaService:
    """Meters natural-language conversation for Members and Guests.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        settings: Supplies the display timezone that defines "today".
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- Member quota -----------------------------------------------------
    def today(self, now: datetime | None = None) -> date:
        """The local calendar day a moment belongs to."""
        return quota_date_for(now or utcnow(), self._settings.timezone)

    def reset_hint(self, now: datetime | None = None) -> datetime:
        """When the current allowance comes back, as a UTC instant."""
        return next_reset_at(now or utcnow(), self._settings.timezone)

    @staticmethod
    def is_metered(role: Role) -> bool:
        """True when this role's natural conversation counts against a limit."""
        return role not in UNLIMITED_ROLES

    async def _ledger_for(
        self, user_id: uuid.UUID, *, quota_date: date, now: datetime
    ) -> DailyAiUsage:
        """Today's row for this member, opening one if the day just turned.

        A new day's row inherits any standing override, which is what makes
        "set their limit to 50" outlive midnight while "give them 10 more
        today" does not.
        """
        result = await self._session.execute(
            select(DailyAiUsage).where(
                DailyAiUsage.user_id == user_id, DailyAiUsage.quota_date == quota_date
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row

        override = await self.standing_override(user_id)
        row = DailyAiUsage(
            user_id=user_id,
            quota_date=quota_date,
            base_limit=MEMBER_DEFAULT_DAILY_LIMIT,
            temporary_bonus=0,
            persistent_override=override.daily_limit if override is not None else None,
            used_count=0,
            reserved_count=0,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def standing_override(self, user_id: uuid.UUID) -> UserQuotaOverride | None:
        """The member's persistent daily limit, if an owner set one."""
        result = await self._session.execute(
            select(UserQuotaOverride).where(UserQuotaOverride.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _facts(row: DailyAiUsage) -> DailyUsageFacts:
        return DailyUsageFacts(
            quota_date=row.quota_date,
            base_limit=row.base_limit,
            temporary_bonus=row.temporary_bonus,
            persistent_override=row.persistent_override,
            used_count=row.used_count,
            reserved_count=row.reserved_count,
        )

    async def inspect_member(
        self, *, user_id: uuid.UUID, role: Role, now: datetime | None = None
    ) -> QuotaVerdict:
        """Report the allowance without touching it."""
        if not self.is_metered(role):
            return QuotaVerdict(outcome=QuotaOutcome.UNLIMITED)
        moment = now or utcnow()
        row = await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)
        await self._reclaim_stale_member(row, now=moment)
        return evaluate(self._facts(row))

    async def _reclaim_stale_member(self, row: DailyAiUsage, *, now: datetime) -> None:
        """Drop reservations left behind by a process that died mid-turn."""
        if row.reserved_count <= 0:
            return
        anchor = row.oldest_reservation_at or row.updated_at
        if anchor is None or now - ensure_utc(anchor) < STALE_RESERVATION_TTL:
            return
        logger.warning(
            "quota_reservations_reclaimed",
            extra={"user_id": str(row.user_id), "reserved": row.reserved_count},
        )
        row.reserved_count = 0
        row.oldest_reservation_at = None
        row.updated_at = now
        await self._session.flush()

    async def reserve_member(
        self, *, user_id: uuid.UUID, role: Role, now: datetime | None = None
    ) -> tuple[Reservation | None, QuotaVerdict]:
        """Hold one slot for a member, or refuse.

        Returns ``(reservation, verdict)``. **The reservation is the answer**;
        the verdict is only there to describe the allowance in a message.

        That distinction matters and used to be got wrong here. On the twentieth
        message of the day the reservation succeeds and leaves the ledger at
        20/20 - so a verdict computed *after* reserving reads "exhausted" for a
        request that was in fact allowed. A caller keying off the verdict would
        refuse the twentieth message and strand its reservation, permanently
        costing the member a slot. So a successful reservation always reports
        :attr:`~meobot.domain.access.quota.QuotaOutcome.ALLOWED`, and callers
        branch on ``reservation is None``.
        """
        if not self.is_metered(role):
            return None, QuotaVerdict(outcome=QuotaOutcome.UNLIMITED)

        moment = now or utcnow()
        row = await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)
        await self._reclaim_stale_member(row, now=moment)

        # One statement: increment only if there is still room. The limit is
        # recomputed inside the UPDATE, so a concurrent writer cannot slip
        # between the check and the write.
        effective = (
            func.coalesce(DailyAiUsage.persistent_override, DailyAiUsage.base_limit)
            + DailyAiUsage.temporary_bonus
        )
        result: CursorResult[Any] = await self._session.execute(  # type: ignore[assignment]
            update(DailyAiUsage)
            .where(
                DailyAiUsage.id == row.id,
                DailyAiUsage.used_count + DailyAiUsage.reserved_count < effective,
            )
            .values(
                reserved_count=DailyAiUsage.reserved_count + 1,
                oldest_reservation_at=func.coalesce(DailyAiUsage.oldest_reservation_at, moment),
                updated_at=moment,
            )
        )
        await self._session.flush()
        self._session.expire(row)

        refreshed = await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)
        facts = self._facts(refreshed)
        if result.rowcount != 1:
            return None, evaluate(facts)

        return Reservation(kind=Reservation.MEMBER, row_id=row.id), QuotaVerdict(
            outcome=QuotaOutcome.ALLOWED,
            limit=facts.effective_limit,
            # Report the slot this call just took as used rather than reserved:
            # the caller is about to spend it, and "20 of 20" is what a member
            # would expect to be told about the message they just sent.
            used=facts.used_count + 1,
            reserved=max(0, facts.reserved_count - 1),
        )

    # --- Guest allowance --------------------------------------------------
    async def reserve_guest(
        self, *, policy_id: uuid.UUID, now: datetime | None = None
    ) -> Reservation | None:
        """Hold one Guest question, or refuse.

        The same conditional-update trick, with both Guest limits in the
        ``WHERE`` clause: the window must still be open *and* a question must
        still be unspent. The 11th message matches nothing and never reaches a
        provider.
        """
        moment = now or utcnow()
        await self._reclaim_stale_guest(policy_id, now=moment)
        result: CursorResult[Any] = await self._session.execute(  # type: ignore[assignment]
            update(GroupMemberResponsePolicy)
            .where(
                GroupMemberResponsePolicy.id == policy_id,
                GroupMemberResponsePolicy.mode == GroupPolicyMode.GUEST,
                GroupMemberResponsePolicy.revoked_at.is_(None),
                GroupMemberResponsePolicy.guest_expires_at > moment,
                GroupMemberResponsePolicy.guest_questions_used
                + GroupMemberResponsePolicy.guest_reserved_count
                < GroupMemberResponsePolicy.guest_question_limit,
            )
            .values(
                guest_reserved_count=GroupMemberResponsePolicy.guest_reserved_count + 1,
                updated_at=moment,
            )
        )
        await self._session.flush()
        if result.rowcount != 1:
            return None
        return Reservation(kind=Reservation.GUEST, row_id=policy_id)

    async def _reclaim_stale_guest(self, policy_id: uuid.UUID, *, now: datetime) -> None:
        """Clear Guest reservations stranded by a crashed turn."""
        row = await self._session.get(GroupMemberResponsePolicy, policy_id)
        if row is None or row.guest_reserved_count <= 0:
            return
        if row.updated_at is None or now - ensure_utc(row.updated_at) < STALE_RESERVATION_TTL:
            return
        logger.warning(
            "guest_reservations_reclaimed",
            extra={"policy_id": str(policy_id), "reserved": row.guest_reserved_count},
        )
        row.guest_reserved_count = 0
        await self._session.flush()

    # --- Settlement -------------------------------------------------------
    async def commit(self, reservation: Reservation | None, *, now: datetime | None = None) -> None:
        """Turn a held slot into a used one. Call only after delivery."""
        if reservation is None:
            return
        moment = now or utcnow()
        if reservation.kind == Reservation.MEMBER:
            await self._session.execute(
                update(DailyAiUsage)
                .where(DailyAiUsage.id == reservation.row_id, DailyAiUsage.reserved_count > 0)
                .values(
                    reserved_count=DailyAiUsage.reserved_count - 1,
                    used_count=DailyAiUsage.used_count + 1,
                    oldest_reservation_at=None,
                    updated_at=moment,
                )
            )
        else:
            await self._session.execute(
                update(GroupMemberResponsePolicy)
                .where(
                    GroupMemberResponsePolicy.id == reservation.row_id,
                    GroupMemberResponsePolicy.guest_reserved_count > 0,
                )
                .values(
                    guest_reserved_count=GroupMemberResponsePolicy.guest_reserved_count - 1,
                    guest_questions_used=GroupMemberResponsePolicy.guest_questions_used + 1,
                    updated_at=moment,
                )
            )
            await self._mark_guest_exhausted(reservation.row_id, now=moment)
        await self._session.flush()

    async def release(
        self, reservation: Reservation | None, *, now: datetime | None = None
    ) -> None:
        """Give a held slot back. Call on any failure before delivery."""
        if reservation is None:
            return
        moment = now or utcnow()
        if reservation.kind == Reservation.MEMBER:
            await self._session.execute(
                update(DailyAiUsage)
                .where(DailyAiUsage.id == reservation.row_id, DailyAiUsage.reserved_count > 0)
                .values(
                    reserved_count=DailyAiUsage.reserved_count - 1,
                    oldest_reservation_at=None,
                    updated_at=moment,
                )
            )
        else:
            await self._session.execute(
                update(GroupMemberResponsePolicy)
                .where(
                    GroupMemberResponsePolicy.id == reservation.row_id,
                    GroupMemberResponsePolicy.guest_reserved_count > 0,
                )
                .values(
                    guest_reserved_count=GroupMemberResponsePolicy.guest_reserved_count - 1,
                    updated_at=moment,
                )
            )
        await self._session.flush()

    async def _mark_guest_exhausted(self, policy_id: uuid.UUID, *, now: datetime) -> None:
        """Stamp the moment a Guest's last question was spent."""
        row = await self._session.get(GroupMemberResponsePolicy, policy_id)
        if row is None or row.guest_exhausted_at is not None:
            return
        if row.guest_questions_used >= row.guest_question_limit:
            row.guest_exhausted_at = now
            await self._session.flush()

    # --- Owner overrides --------------------------------------------------
    async def add_bonus(
        self, *, user_id: uuid.UUID, amount: int, now: datetime | None = None
    ) -> QuotaVerdict:
        """Grant extra messages for today only."""
        moment = now or utcnow()
        row = await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)
        row.temporary_bonus = min(MAX_DAILY_LIMIT, row.temporary_bonus + max(0, amount))
        row.updated_at = moment
        await self._session.flush()
        return evaluate(self._facts(row))

    async def reset_today(self, *, user_id: uuid.UUID, now: datetime | None = None) -> QuotaVerdict:
        """Set today's consumption back to zero, keeping the limit."""
        moment = now or utcnow()
        row = await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)
        row.used_count = 0
        row.reserved_count = 0
        row.oldest_reservation_at = None
        row.updated_at = moment
        await self._session.flush()
        return evaluate(self._facts(row))

    async def set_daily_limit(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
        actor_user_id: uuid.UUID | None,
        actor_telegram_id: int | None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> QuotaVerdict:
        """Set a standing daily limit that survives midnight."""
        bounded = max(0, min(MAX_DAILY_LIMIT, limit))
        moment = now or utcnow()
        override = await self.standing_override(user_id)
        if override is None:
            override = UserQuotaOverride(
                user_id=user_id,
                daily_limit=bounded,
                reason=reason,
                created_by_user_id=actor_user_id,
                created_by_telegram_id=actor_telegram_id,
            )
            self._session.add(override)
        else:
            override.daily_limit = bounded
            override.reason = reason
            override.created_by_user_id = actor_user_id
            override.created_by_telegram_id = actor_telegram_id
        row = await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)
        row.persistent_override = bounded
        row.updated_at = moment
        await self._session.flush()
        return evaluate(self._facts(row))

    async def clear_daily_limit(
        self, *, user_id: uuid.UUID, now: datetime | None = None
    ) -> QuotaVerdict:
        """Remove a standing limit and go back to the default."""
        moment = now or utcnow()
        override = await self.standing_override(user_id)
        if override is not None:
            await self._session.delete(override)
        row = await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)
        row.persistent_override = None
        row.updated_at = moment
        await self._session.flush()
        return evaluate(self._facts(row))

    async def usage_row(self, *, user_id: uuid.UUID, now: datetime | None = None) -> DailyAiUsage:
        """Today's ledger row, created if this is the first message of the day."""
        moment = now or utcnow()
        return await self._ledger_for(user_id, quota_date=self.today(moment), now=moment)

    async def resolve_user(self, telegram_user_id: int) -> User | None:
        """Convenience lookup used by the quota commands."""
        result = await self._session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()
