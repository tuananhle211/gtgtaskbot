"""Daily chat-quota arithmetic.

The quota counts one thing: a natural-language answer that MeoBot actually
delivered. Not a slash command, not a tool run, not a refusal, not a provider
failure - those are all work MeoBot does for a member that costs them nothing.

Two decisions are worth stating because they are the ones that go wrong:

**The day is local.** "20 lượt hôm nay" means the calendar day in
``Asia/Ho_Chi_Minh``, so the reset happens at local midnight, which is 17:00 UTC
the day before. Storing the date the usage belongs to - rather than filtering a
timestamp range at read time - means the reset needs no scheduled task: at
00:00 local the key simply changes and the next lookup finds no row.

**The counter is global.** One member has one allowance across private chat and
every group. Scoping it per chat would make "switch to another group" a way to
get twenty more.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

#: What a Member gets each local day when nothing has been overridden.
MEMBER_DEFAULT_DAILY_LIMIT = 20

#: The grant size behind the owner's "add 10 for today" button.
QUOTA_BONUS_STEP = 10

#: Highest limit an owner may set for one person, as a guard against a typo
#: turning into an unbounded spend.
MAX_DAILY_LIMIT = 500


class QuotaOutcome(StrEnum):
    """Why a quota check said what it said."""

    ALLOWED = "allowed"
    EXHAUSTED = "exhausted"
    #: Roles that are not metered at all in this release.
    UNLIMITED = "unlimited"


class QuotaVerdict(BaseModel):
    """The answer to "may this person send one more chat message?"."""

    model_config = ConfigDict(frozen=True)

    outcome: QuotaOutcome
    limit: int = 0
    used: int = 0
    reserved: int = 0

    @property
    def allowed(self) -> bool:
        return self.outcome is not QuotaOutcome.EXHAUSTED

    @property
    def remaining(self) -> int:
        if self.outcome is QuotaOutcome.UNLIMITED:
            return -1
        return max(0, self.limit - self.used - self.reserved)


class DailyUsageFacts(BaseModel):
    """One member's ledger row for one local day, as the domain sees it."""

    model_config = ConfigDict(frozen=True)

    quota_date: date
    base_limit: int = Field(default=MEMBER_DEFAULT_DAILY_LIMIT, ge=0)
    #: Granted by the owner for this day only; gone tomorrow.
    temporary_bonus: int = Field(default=0, ge=0)
    #: A standing replacement for ``base_limit``. ``None`` means "use the base".
    persistent_override: int | None = Field(default=None, ge=0)
    used_count: int = Field(default=0, ge=0)
    reserved_count: int = Field(default=0, ge=0)

    @property
    def effective_limit(self) -> int:
        """The number that actually applies today.

        A persistent override *replaces* the default; a temporary bonus *adds*
        to whichever of those is in force. So "set their limit to 50" and "give
        them 10 more today" compose instead of fighting.
        """
        base = self.base_limit if self.persistent_override is None else self.persistent_override
        return base + self.temporary_bonus

    @property
    def committed(self) -> int:
        """Slots that are spent or about to be."""
        return self.used_count + self.reserved_count

    def has_room(self) -> bool:
        """The eligibility rule, in one place."""
        return self.committed < self.effective_limit


def quota_date_for(moment: datetime, timezone: ZoneInfo) -> date:
    """The local calendar day ``moment`` belongs to.

    ``moment`` is UTC (everything stored is), ``timezone`` is the display zone
    from settings. This function is the only place that conversion happens, so
    "which day is it" cannot be answered two different ways.
    """
    return moment.astimezone(timezone).date()


def next_reset_at(moment: datetime, timezone: ZoneInfo) -> datetime:
    """The next local midnight after ``moment``, as a UTC instant.

    Used to tell a member when their allowance comes back, in their own
    timezone rather than the server's.
    """
    local = moment.astimezone(timezone)
    tomorrow = local.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=timezone)


def evaluate(facts: DailyUsageFacts) -> QuotaVerdict:
    """Decide whether one more chat message fits inside today's allowance."""
    if facts.has_room():
        return QuotaVerdict(
            outcome=QuotaOutcome.ALLOWED,
            limit=facts.effective_limit,
            used=facts.used_count,
            reserved=facts.reserved_count,
        )
    return QuotaVerdict(
        outcome=QuotaOutcome.EXHAUSTED,
        limit=facts.effective_limit,
        used=facts.used_count,
        reserved=facts.reserved_count,
    )
