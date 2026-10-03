"""Which reporting period a KPI plan is for, and which one counted work lands in.

M2. Small on purpose: it exists so that **no second calendar exists**.

``pr_reporting_periods`` has been in the schema since Step 1B and, until this
milestone, nothing created a row in it or read one - the table was master data
waiting for a reader. M2 is that reader, and the temptation it has to refuse is
inventing a parallel notion of "September" out of :func:`day_bounds` and a
plan's own pair of dates. Two calendars would disagree the first time somebody
closed a period, and the disagreement would be invisible.

So: a plan names a **period row**, the period row owns the dates, and this
service is the only place a date becomes a period.

Month periods only, in M2
--------------------------

``PrPeriodType`` has ``WEEK`` and ``MONTH``, and M2 accepts only ``MONTH``.

The reason is not preference. A contribution counted on 15 September falls
inside *both* ``2026-W38`` and ``2026-09``, so if plans could target either
type, one contribution could be claimed by two approved quotas at once - and
:class:`~meobot.db.models.pr_work_quota.PrWorkQuotaAllocation` allows exactly
one current decision per contribution, deliberately. Rather than resolve that
with a precedence rule nobody asked for, M2 refuses ``WEEK`` plans with a stable
error and documents it as a limitation. Weekly quotas are a later milestone's,
and the thing it will need - a second allocation dimension - is a column, not a
rewrite.

Periods are not generated on a timer
-------------------------------------

Step 1B's rule, kept: *"Periods are not generated. There is no calendar, no
counter and no job that creates next week's row."* An administrator asks for
``2026-09`` and gets it, idempotently. Nothing here runs on a schedule, and a
counted contribution that falls in a month nobody has created is **not** given
one silently - it is reported as unmapped, which is an operational fact somebody
should see rather than a row this service invents on their behalf.
"""

from __future__ import annotations

import calendar
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_content_query import day_bounds
from meobot.application.pr_support import record_pr_event
from meobot.db.models.pr_performance import PrPerformanceResult
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import PrNotFoundError, PrValidationError
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus, PrPeriodType

#: The earliest and latest month a plan may be written for.
#:
#: Bounded because the year is a number somebody types, and a plan for the year
#: 3000 is a typo that would otherwise sit in a picker for ever.
MIN_PERIOD_YEAR = 2024
MAX_PERIOD_YEAR = 2100


def month_period_code(year: int, month: int) -> str:
    """``2026-09``. The code a person says and a report prints.

    The same shape ``PrReportingPeriod`` documents - ``2026-W32`` for a week,
    ``2026-08`` for a month - so a period this service creates is
    indistinguishable from one somebody entered by hand.
    """
    return f"{year:04d}-{month:02d}"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """The first and last **calendar day** of one month, inclusive.

    Both inclusive, matching ``PrReportingPeriod.date_start`` /
    ``date_end`` and its ``dates_ordered`` check. Turning them into instants is
    :func:`day_bounds`' job and happens in one place - see
    :meth:`PrWorkPeriodService.bounds`.
    """
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


class PrWorkPeriodService:
    """Creates, lists and resolves the reporting periods M2 plans are written for.

    Args:
        session: Unit of work. The caller owns the transaction boundary.
        audit: Event writer sharing that session.
        capabilities: Gates creation on ``PR_WORK_CONFIGURE`` and listing on the
            module's ordinary read right.
        timezone: The business timezone. **Load-bearing**: the stored columns
            are UTC instants and a period is a range of Vietnamese calendar
            days, so 2026-09-01 begins at 2026-08-31T17:00Z. Comparing
            ``counted_at >= 2026-09-01T00:00Z`` would push the first seven hours
            of every Vietnamese working day into the previous month.
    """

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        *,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._timezone = timezone or ZoneInfo("UTC")

    # =====================================================================
    # Reading
    # =====================================================================
    async def list_periods(
        self, *, actor: Actor, limit: int = 24, include_non_month: bool = False
    ) -> Sequence[PrReportingPeriod]:
        """Reporting periods, newest first, for a picker.

        Month periods only unless asked otherwise, because those are the only
        ones a plan may target - offering a client a period the approval would
        refuse is a picker that produces errors.

        Any holder of ``PR_WORK_EXECUTE`` may read them: a period is master
        data, like the work taxonomy beside it, and an employee has to be able
        to see which month their own plan is for.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
        statement = select(PrReportingPeriod)
        if not include_non_month:
            statement = statement.where(PrReportingPeriod.period_type == PrPeriodType.MONTH)
        statement = statement.order_by(PrReportingPeriod.date_start.desc()).limit(
            max(1, min(limit, 120))
        )
        return (await self._session.execute(statement)).scalars().all()

    async def require_period(self, period_id: uuid.UUID) -> PrReportingPeriod:
        """One period by id, or a not-found the caller can render."""
        row = await self._session.get(PrReportingPeriod, period_id)
        if row is None:
            raise PrNotFoundError(
                "No such reporting period",
                details={"entity": "pr_reporting_period", "id": str(period_id)},
            )
        return row

    def bounds(self, period: PrReportingPeriod) -> tuple[datetime, datetime]:
        """The half-open UTC instant range one period's calendar days name.

        Resolved through the **existing** :func:`day_bounds`, which is the one
        place in MeoBot where a Vietnamese calendar day becomes a pair of UTC
        instants. A second implementation here is how "September" would come to
        mean two different things on two screens.
        """
        lower, upper = day_bounds(period.date_start, period.date_end, tz=self._timezone)
        # ``day_bounds`` returns ``None`` only for a ``None`` date, and both
        # columns are ``NOT NULL``.
        assert lower is not None and upper is not None
        return lower, upper

    async def period_for(self, moment: datetime) -> PrReportingPeriod | None:
        """The month period one instant falls in, or ``None``.

        ``None`` is a real answer and **not** an invitation to create one. A
        contribution counted in a month nobody has opened is an operational fact
        - somebody has not set the period up - and inventing the row here would
        hide it, then quietly attach a KPI decision to a period nobody agreed
        to. Callers report it; see
        :meth:`~meobot.application.pr_work_quota_service.PrWorkQuotaEligibilityService.reconcile_period`.

        The instant is converted to a **Vietnamese calendar day** before it is
        compared, for the reason in the class docstring.
        """
        local_day = moment.astimezone(self._timezone).date()
        statement = (
            select(PrReportingPeriod)
            .where(
                PrReportingPeriod.period_type == PrPeriodType.MONTH,
                PrReportingPeriod.date_start <= local_day,
                PrReportingPeriod.date_end >= local_day,
            )
            .order_by(PrReportingPeriod.date_start.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalars().one_or_none()

    async def performance_finalized(self, *, user_id: uuid.UUID, period_id: uuid.UUID) -> bool:
        """Has this person's month been **agreed** - a finalised performance row?

        The accounting guard every result and contribution mutation asks
        before it moves a counted amount. A finalised
        :class:`~meobot.db.models.pr_performance.PrPerformanceResult` is a
        figure somebody signed; a validation, rejection, reversal or admin
        removal landing under it would leave the stored month saying one
        number and the live screen another. Period close is a separate
        lifecycle and is **not** implemented here - this is the smallest
        guard that keeps an agreed figure from drifting.
        """
        statement = (
            select(PrPerformanceResult.id)
            .where(
                PrPerformanceResult.user_id == user_id,
                PrPerformanceResult.reporting_period_id == period_id,
                PrPerformanceResult.finalized_at.is_not(None),
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    # =====================================================================
    # Writing
    # =====================================================================
    async def ensure_month_period(
        self, *, actor: Actor, request_id: uuid.UUID, year: int, month: int
    ) -> PrReportingPeriod:
        """Get or create the month period for ``year``/``month``. Idempotent.

        ``PR_WORK_CONFIGURE``. Returns the existing row untouched when there is
        one - including its ``status``, so asking for a closed month does not
        reopen it. That is what makes a retried HTTP request harmless.

        The new row is linked to the month before it through
        ``previous_period_id`` when that one exists, because a report comparing
        September with August needs to know which August it means and an
        explicit link survives a reporting gap that arithmetic would not.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_CONFIGURE)
        return await self.get_or_create_month_period(
            actor=actor, request_id=request_id, year=year, month=month
        )

    async def period_for_or_create(
        self, moment: datetime, *, actor: Actor, request_id: uuid.UUID
    ) -> PrReportingPeriod:
        """The month period an instant falls in, created if nobody has opened it.

        **Internal, and only for period containers.** :meth:`period_for`'s
        ``None`` is the right answer when a contribution is being *evaluated* -
        the absence of a month is an operational fact to report. A period
        container is different: it *is* the month, and a stream somebody is
        reporting into cannot be refused because an administrator has not yet
        pressed a button. The row created here is the same row
        :meth:`ensure_month_period` would create, with the same audit event.
        """
        existing = await self.period_for(moment)
        if existing is not None:
            return existing
        local_day = moment.astimezone(self._timezone).date()
        return await self.get_or_create_month_period(
            actor=actor, request_id=request_id, year=local_day.year, month=local_day.month
        )

    async def get_or_create_month_period(
        self, *, actor: Actor, request_id: uuid.UUID, year: int, month: int
    ) -> PrReportingPeriod:
        """The capability-free half of :meth:`ensure_month_period`. One implementation.

        Reached by the configuration route through its capability check, and by
        the period-container path through :meth:`period_for_or_create`. Both
        create the identical row, so there is one place a month is born.
        """
        if not MIN_PERIOD_YEAR <= year <= MAX_PERIOD_YEAR:
            raise PrValidationError(
                "That year is outside the range a reporting period may cover",
                details={
                    "field": "year",
                    "reason": "period_year_out_of_range",
                    "minimum": MIN_PERIOD_YEAR,
                    "maximum": MAX_PERIOD_YEAR,
                },
            )
        if not 1 <= month <= 12:
            raise PrValidationError(
                "That is not a month",
                details={"field": "month", "reason": "period_month_out_of_range"},
            )

        code = month_period_code(year, month)
        existing = (
            (
                await self._session.execute(
                    select(PrReportingPeriod).where(PrReportingPeriod.code == code)
                )
            )
            .scalars()
            .one_or_none()
        )
        if existing is not None:
            if existing.period_type is not PrPeriodType.MONTH:
                raise PrValidationError(
                    "A reporting period with that code already exists and is not a month",
                    details={
                        "field": "code",
                        "reason": "period_code_is_not_a_month",
                        "code": code,
                    },
                )
            return existing

        start, end = month_bounds(year, month)
        previous = (
            (
                await self._session.execute(
                    select(PrReportingPeriod)
                    .where(
                        PrReportingPeriod.period_type == PrPeriodType.MONTH,
                        PrReportingPeriod.date_end < start,
                    )
                    .order_by(PrReportingPeriod.date_end.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one_or_none()
        )

        row = PrReportingPeriod(
            code=code,
            period_type=PrPeriodType.MONTH,
            date_start=start,
            date_end=end,
            previous_period_id=previous.id if previous is not None else None,
            status=PrPeriodStatus.OPEN,
        )
        # Inside a savepoint, against the unique period code: two workers whose
        # first result of the month lands at the same moment both try to open
        # it, one loses, and the loser continues with the winner's row rather
        # than failing its projection. ``begin_nested()`` flushes what is
        # already pending before it opens the SAVEPOINT, so the row is added
        # inside the block and not before it.
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            if row in self._session:
                self._session.expunge(row)
            winner = (
                (
                    await self._session.execute(
                        select(PrReportingPeriod).where(PrReportingPeriod.code == code)
                    )
                )
                .scalars()
                .one_or_none()
            )
            if winner is None:  # pragma: no cover - the index refused for another reason
                raise
            return winner
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_PERIOD_CREATED,
            entity_type="pr_reporting_period",
            entity_id=row.id,
            after={
                "code": row.code,
                "period_type": row.period_type.value,
                "date_start": row.date_start.isoformat(),
                "date_end": row.date_end.isoformat(),
                "status": row.status.value,
            },
        )
        return row


def assert_plan_period_type(period: PrReportingPeriod) -> None:
    """Raise unless this period is one a KPI plan may target.

    Month only in M2 - see the module docstring on why a week and a month
    covering the same day would let two approved quotas claim one contribution.
    """
    if period.period_type is not PrPeriodType.MONTH:
        raise PrValidationError(
            "A KPI plan is written for a month",
            details={
                "field": "period_id",
                "reason": "period_type_not_supported",
                "period_type": period.period_type.value,
                "supported": [PrPeriodType.MONTH.value],
            },
        )


__all__: list[str] = [
    "MAX_PERIOD_YEAR",
    "MIN_PERIOD_YEAR",
    "PrWorkPeriodService",
    "assert_plan_period_type",
    "month_bounds",
    "month_period_code",
]
