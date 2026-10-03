"""Period containers and the results reported into them.

The period-container patch. One service, because the rules are one argument:
which stream a result belongs to, who may declare one, who may count one, and
what the container's *actual* is once they have. Splitting it across the work
service, the projector and a router would put the *declaring is not being
credited* boundary in a seam - the same reason
:mod:`meobot.application.pr_work_service` is one file.

What a container is
--------------------

An ordinary :class:`~meobot.db.models.pr_work.PrWorkItem` with
``reporting_period_id`` and ``subject_user_id`` set: **one work stream, one
employee, one month.** *Tìm khách hàng - 2026-09* is one row, and "+3, +5, +2,
+7, +10" are five :class:`~meobot.db.models.pr_work_result.PrWorkResult` rows
inside it. There is never a second container for the same three keys - the
partial unique index ``uq_pr_work_items_period_container`` refuses it, and
every creation path here is a get-or-create against that index.

What the container's number is
-------------------------------

``quantity = SUM(quantity) OF results WHERE status = COUNTED``. Derived, kept
in sync by :meth:`PrWorkResultService._sync_container` after every write, and
read by M2 and M6 through the **same** contribution row those milestones already
read. A container's single contribution is ``COUNTED`` while at least one
result is, with ``counted_at`` the earliest result's validation instant; the
month it belongs to is the container's own - see
:func:`~meobot.application.pr_work_quota_service.counted_in_period`.

The KPI is read beside that number and nothing else
----------------------------------------------------

:meth:`PrWorkResultService.summary` reads the approved quota for the subject,
the type and the month, and compares through
:func:`~meobot.domain.pr.work_results.compare_to_target`. It is consulted
*after* a result exists, never before one may be declared. A stream with no
target reports ``completion_percent = None`` and is priced like any other.

Who counts a result
--------------------

M1's rule, at result grain. A manual result lands ``PENDING`` and is counted by
somebody holding ``PR_WORK_VALIDATE`` who is **not the subject**. A result
another module contributes is counted on the source's own independent
validation - the head's approval of a script - and left ``PENDING`` when the
source's only validator is the person it credits, exactly as
:meth:`~meobot.application.pr_work_service.PrWorkService.count_source_work`
decides for a one-off job.

Why an excluded result is out, and who may put it back
-------------------------------------------------------

``0041``. Three acts write ``EXCLUDED`` and they do not mean the same thing:

* a validator's *Từ chối / Không ghi nhận* (:meth:`PrWorkResultService.exclude_result`)
  is a **reviewed decision** - ``VALIDATOR_REJECTED``. No projection reverses
  it; :meth:`PrWorkResultService.reconsider_result` is the one release;
* an administrator's *Xóa kết quả* (the maintenance service) takes the
  accounting out and says nothing about the source - ``ADMIN_REMOVED``. The
  next projection re-evaluates it;
* the source withdrawing the fact (:meth:`PrWorkResultService.reverse_source_result`)
  - ``SOURCE_REVERSED``. Current source truth decides.

:meth:`PrWorkResultService.record_source_result` is the **only** place an
excluded row is restored, and it asks
:func:`~meobot.domain.pr.work_results.source_may_restore` - one policy, read
by the worker, the per-content button, the batch sync and the rebuild alike.

Source truth wins over a stale row
-----------------------------------

A source-derived result may only be counted while its source milestone is
live. The projector sweeps a ``PENDING`` row whose milestone vanished to
``SOURCE_REVERSED``; and because the projector may not have run yet, the
validator's own path asks the source first through :class:`SourceTruth` -
:meth:`PrWorkResultService.validate_results` refuses or converges a row the
source no longer supports (``work_result_source_not_eligible``), and
:meth:`PrWorkResultService.reconsider_result` will not release a rejection
into a pending state the source would not back. Manual results have no
source and are never asked.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from meobot.application.audit_service import AuditService
from meobot.application.pr_capability_service import PrCapabilityService
from meobot.application.pr_code_service import PrCodeService
from meobot.application.pr_performance_config_service import PrWorkScoringRuleService
from meobot.application.pr_performance_service import price_amount
from meobot.application.pr_support import lock_row, record_pr_event
from meobot.application.pr_work_period_service import PrWorkPeriodService
from meobot.application.pr_work_quota_service import PrWorkQuotaEligibilityService
from meobot.application.pr_work_service import MAX_TITLE, PrWorkService
from meobot.core.logging import get_logger
from meobot.core.time import ensure_utc, utcnow
from meobot.db.models.pr_performance import PrWorkScoringRule
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkContribution, PrWorkHistory, PrWorkItem, PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota
from meobot.db.models.pr_work_result import PrWorkResult
from meobot.db.models.user import User
from meobot.domain.audit.models import AuditAction
from meobot.domain.identity.models import Actor
from meobot.domain.pr.errors import (
    PrConflictError,
    PrNotFoundError,
    PrPermissionDeniedError,
    PrValidationError,
)
from meobot.domain.pr.models import PrPriority
from meobot.domain.pr.performance import PrContributionScoreStatus
from meobot.domain.pr.policy import PrCapability
from meobot.domain.pr.reporting import PrPeriodStatus
from meobot.domain.pr.work import (
    DEFAULT_CREDIT_WEIGHT,
    PrWorkContributionRole,
    PrWorkCountStatus,
    PrWorkEventType,
    PrWorkSourceType,
    PrWorkStatus,
    PrWorkUnit,
    assert_source_key,
)
from meobot.domain.pr.work_labels import work_unit_label
from meobot.domain.pr.work_quota import PrWorkPlanStatus
from meobot.domain.pr.work_results import (
    MAX_RESULT_LABEL,
    MAX_RESULT_LINK,
    MAX_RESULT_NOTE,
    PERFORMANCE_FINALIZED,
    PERIOD_CONTAINER_CANCELLED,
    PERIOD_CONTAINER_LOCKED,
    PrWorkCountOrigin,
    PrWorkExclusionKind,
    PrWorkResultSource,
    TargetComparison,
    compare_to_target,
    may_reconsider,
    require_result_quantity,
    source_may_restore,
)

logger = get_logger(__name__)

ZERO = Decimal("0.00")

#: How many results one validation request may count at once. The same order
#: of magnitude as bulk validation's bound, for the same reason: a request body
#: must not be able to ask for ten thousand row locks.
MAX_RESULTS_PER_VALIDATION = 500


@dataclass(frozen=True, slots=True)
class ContainerSummary:
    """One stream's month, as a card draws it. **Every number is the server's.**

    ``counted_quantity`` is the actual. ``declared_quantity`` is what the
    employee has reported, counted or not, so a screen can say *"27 đã ghi
    nhận · 5 chờ xác nhận"*. ``comparison`` is the KPI read beside the actual;
    ``standard_minutes`` is the actual priced at the rate in force. None of
    the four is derived from another on the client.
    """

    work_item_id: uuid.UUID
    period_id: uuid.UUID
    period_code: str
    period_status: PrPeriodStatus
    subject_user_id: uuid.UUID
    unit: PrWorkUnit
    unit_label: str
    counted_quantity: Decimal
    pending_quantity: Decimal
    declared_quantity: Decimal
    excluded_quantity: Decimal
    result_count: int
    pending_count: int
    target_quantity: Decimal | None
    work_quota_id: uuid.UUID | None
    comparison: TargetComparison
    standard_minutes: Decimal | None
    standard_minutes_per_unit: Decimal | None
    scoring_status: PrContributionScoreStatus


class SourceTruth(Protocol):
    """What the result service asks a contributing module before counting.

    The content projector implements it. ``None`` means *no opinion* - not
    this module's key, or a kind it leaves alone - and is never read as
    ineligible.
    """

    async def source_is_eligible(
        self, source_type: PrWorkResultSource, source_key: str
    ) -> bool | None: ...


#: The reason a validation is refused because the source no longer backs it.
SOURCE_NOT_ELIGIBLE = "work_result_source_not_eligible"
SOURCE_NOT_ELIGIBLE_MESSAGE = (
    "Không thể xác nhận vì Nội dung nguồn hiện không còn đủ điều kiện ghi nhận."
)


@dataclass(frozen=True, slots=True)
class ResultBatch:
    """What one validation did."""

    work_item_id: uuid.UUID
    counted: tuple[uuid.UUID, ...]
    counted_quantity: Decimal
    #: Pending source-derived rows the source no longer backed, converged to
    #: ``SOURCE_REVERSED`` instead of counted. Empty unless the source moved.
    reversed: tuple[uuid.UUID, ...] = ()


class PrWorkResultService:
    """Creates period containers, records results into them, and keeps the actual true."""

    def __init__(
        self,
        session: AsyncSession,
        audit: AuditService,
        capabilities: PrCapabilityService,
        codes: PrCodeService,
        periods: PrWorkPeriodService,
        work: PrWorkService,
        eligibility: PrWorkQuotaEligibilityService | None = None,
        scoring_rules: PrWorkScoringRuleService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._capabilities = capabilities
        self._codes = codes
        self._periods = periods
        self._work = work
        self._eligibility = eligibility
        self._rules = scoring_rules
        self._source_truth: SourceTruth | None = None

    def bind_source_truth(self, truth: SourceTruth) -> None:
        """Give the service the module that answers *is the source still live?*

        Bound by the service bundle after the projector is built, because the
        projector is built after this service and holds it. Without a binding
        source-derived rows are counted on the row alone, as before ``0041``.
        """
        self._source_truth = truth

    # =====================================================================
    # Containers
    # =====================================================================
    async def container_for(
        self, *, work_type_id: uuid.UUID, subject_user_id: uuid.UUID, period_id: uuid.UUID
    ) -> PrWorkItem | None:
        """The one container for these three keys, or ``None``."""
        return (
            (
                await self._session.execute(
                    select(PrWorkItem).where(
                        PrWorkItem.work_type_id == work_type_id,
                        PrWorkItem.subject_user_id == subject_user_id,
                        PrWorkItem.reporting_period_id == period_id,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

    async def ensure_container(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_type_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        period: PrReportingPeriod,
        source_type: PrWorkSourceType = PrWorkSourceType.MANUAL,
        source_key: str | None = None,
        recurring_occurrence_id: uuid.UUID | None = None,
        title: str | None = None,
        description: str | None = None,
        assigned_by_user_id: uuid.UUID | None = None,
    ) -> PrWorkItem:
        """Get, or create, the container for one employee, work type and month.

        **No capability check here** - every caller has made its own: a
        manager assigning a stream, an employee reporting into their own, a
        routine's activating manager, the content projector. What is checked is
        representation: an active work type, an active subject, an open month.

        Idempotent against the partial unique index. Two requests racing to
        create the same stream both insert; one loses on the index, and the
        loser re-reads the winner's row rather than failing the person who
        pressed the button second.
        """
        existing = await self.container_for(
            work_type_id=work_type_id, subject_user_id=subject_user_id, period_id=period.id
        )
        if existing is not None:
            if existing.status is PrWorkStatus.CANCELLED:
                # **Never silently record into a cancelled stream.** Its
                # contribution is ``EXCLUDED``, so results filed here would
                # show on the card and nowhere else. No application path
                # cancels a container any more (``PrWorkService.cancel``
                # refuses them); a row already in this state is repaired by
                # an administrator through *cleanup-empty-containers*, after
                # which the next report opens a live stream.
                raise PrConflictError(
                    "Luồng công việc định kỳ của kỳ này đã bị hủy trước đây. "
                    "Quản trị viên cần dọn luồng trống này trước khi ghi nhận kết quả.",
                    details={
                        "reason": PERIOD_CONTAINER_CANCELLED,
                        "work_item_id": str(existing.id),
                        "period": period.code,
                    },
                )
            return existing
        if period.status is not PrPeriodStatus.OPEN:
            raise PrValidationError(
                f"Kỳ {period.code} đã khóa nên không mở thêm công việc định kỳ.",
                details={"field": "period_id", "reason": "period_not_open", "period": period.code},
            )
        work_type = await self._session.get(PrWorkType, work_type_id)
        if work_type is None:
            raise PrNotFoundError(
                "Không tìm thấy loại công việc.", details={"work_type_id": str(work_type_id)}
            )
        if not work_type.is_active:
            raise PrValidationError(
                "Loại công việc này đã ngừng sử dụng.",
                details={"field": "work_type_id", "reason": "work_type_inactive"},
            )
        subject = await self._session.get(User, subject_user_id)
        if subject is None or not subject.active:
            raise PrValidationError(
                "Người thực hiện không còn hoạt động.",
                details={"field": "subject_user_id", "reason": "user_inactive"},
            )
        if source_key is not None:
            assert_source_key(source_key)

        lower, _ = self._periods.bounds(period)
        now = utcnow()
        item = PrWorkItem(
            code=await self._codes.allocate_work_code(at=now),
            title=_container_title(title or work_type.name, period),
            description=description,
            work_type_id=work_type.id,
            source_type=source_type,
            source_key=source_key,
            status=PrWorkStatus.ACCEPTED,
            priority=PrPriority.NORMAL,
            # The derived actual starts at zero, in the type's unit. The CHECK
            # allows zero on a container and nowhere else.
            quantity=ZERO,
            unit=work_type.default_unit,
            # The worker acts as nobody in particular - the content projector
            # runs under the system actor - so a stream it opens is filed by
            # its subject, exactly as ``create_source_work`` files source work.
            created_by_user_id=actor.user_id or subject.id,
            assigned_by_user_id=assigned_by_user_id,
            assigned_at=now if assigned_by_user_id is not None else None,
            accepted_at=now,
            # Listed in its month, first: the stream is the month's.
            execution_at=lower,
            recurring_occurrence_id=recurring_occurrence_id,
            reporting_period_id=period.id,
            subject_user_id=subject.id,
        )
        # Added **inside** the savepoint. ``begin_nested()`` flushes whatever is
        # already pending before it opens the SAVEPOINT, so a row added before
        # it was inserted outside the savepoint - and the unique conflict this
        # block exists to absorb poisoned the whole transaction instead. Found
        # by the auto-provisioning race test against PostgreSQL.
        try:
            async with self._session.begin_nested():
                self._session.add(item)
                await self._session.flush()
        except IntegrityError:
            # Somebody created the same stream a moment ago. Theirs is the
            # container; ours never existed.
            if item in self._session:
                self._session.expunge(item)
            winner = await self.container_for(
                work_type_id=work_type_id, subject_user_id=subject_user_id, period_id=period.id
            )
            if winner is None:  # pragma: no cover - the index refused for another reason
                raise
            return winner

        self._session.add(
            PrWorkContribution(
                work_item_id=item.id,
                user_id=subject.id,
                contribution_role=PrWorkContributionRole.PRIMARY,
                credit_weight=DEFAULT_CREDIT_WEIGHT,
                assigned_at=now,
            )
        )
        await self._session.flush()
        await self._work.record_history(
            item,
            event=PrWorkEventType.CREATED,
            actor=actor,
            to_status=PrWorkStatus.ACCEPTED,
            metadata={
                "period": period.code,
                "subject_user_id": str(subject.id),
                "source_type": source_type.value,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_CONTAINER_CREATED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "work_type": work_type.code,
                "period": period.code,
                "subject_user_id": str(subject.id),
                "source_type": source_type.value,
                "source_key": source_key,
            },
        )
        return item

    async def period_for_moment(
        self, moment: datetime, *, actor: Actor, request_id: uuid.UUID
    ) -> PrReportingPeriod:
        """The month an instant falls in, opened if nobody has yet."""
        return await self._periods.period_for_or_create(moment, actor=actor, request_id=request_id)

    # =====================================================================
    # Manual reporting
    # =====================================================================
    async def report_result(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        quantity: Decimal | None,
        label: str | None = None,
        link: str | None = None,
        note: str | None = None,
        work_item_id: uuid.UUID | None = None,
        work_type_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
        period_id: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> PrWorkResult:
        """Declare a result. ``PR_WORK_EXECUTE`` on your own stream, ``PR_WORK_MANAGE`` on any.

        Either names an existing container (``work_item_id``) or the stream's
        three keys, in which case the container is created on first use - a
        person reporting "+3 khách hàng" for a month nobody set a routine up
        for is still reporting real work, and the form must not send them to
        find a manager first.

        **No KPI is consulted.** Whether a target exists, whether it has been
        reached, whether this result would exceed it - none of that is a
        condition here. The result lands ``PENDING`` and is counted by somebody
        else; see :meth:`validate_results`.
        """
        actor_id = _require_user_id(actor)
        amount = require_result_quantity(quantity)
        moment = ensure_utc(occurred_at) if occurred_at is not None else utcnow()

        if work_item_id is not None:
            item = await self._require_container(work_item_id)
        else:
            if work_type_id is None:
                raise PrValidationError(
                    "Hãy chọn loại công việc.",
                    details={"field": "work_type_id", "reason": "required"},
                )
            subject = subject_user_id or actor_id
            if period_id is not None:
                period = await self._periods.require_period(period_id)
            else:
                period = await self.period_for_moment(moment, actor=actor, request_id=request_id)
            await self._require_reporter(actor, subject)
            item = await self.ensure_container(
                actor=actor,
                request_id=request_id,
                work_type_id=work_type_id,
                subject_user_id=subject,
                period=period,
                assigned_by_user_id=actor_id if subject != actor_id else None,
            )
        item = await self._lock_container(item.id)
        assert item.subject_user_id is not None
        await self._require_reporter(actor, item.subject_user_id)
        await self._require_open(item)

        result = PrWorkResult(
            work_item_id=item.id,
            user_id=item.subject_user_id,
            quantity=amount,
            label=_optional_text(label, "label", MAX_RESULT_LABEL),
            link=_optional_text(link, "link", MAX_RESULT_LINK),
            note=_optional_text(note, "note", MAX_RESULT_NOTE),
            source_type=PrWorkResultSource.MANUAL,
            status=PrWorkCountStatus.PENDING,
            reported_by_user_id=actor_id,
            reported_at=moment,
        )
        self._session.add(result)
        await self._session.flush()
        if item.status is PrWorkStatus.ACCEPTED:
            item.status = PrWorkStatus.IN_PROGRESS
            item.started_at = item.started_at or moment
        await self._sync_container(item, now=moment)
        await self._work.record_history(
            item,
            event=PrWorkEventType.RESULT_REPORTED,
            actor=actor,
            note=result.label,
            metadata={
                "result_id": str(result.id),
                "quantity": str(amount),
                "source_type": result.source_type.value,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_REPORTED,
            entity_type="pr_work_result",
            entity_id=result.id,
            after={
                "work_item_id": str(item.id),
                "code": item.code,
                "quantity": str(amount),
                "label": result.label,
                "link": result.link,
                "source_type": result.source_type.value,
            },
        )
        return result

    async def validate_results(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_item_id: uuid.UUID,
        result_ids: Sequence[uuid.UUID] | None = None,
        note: str | None = None,
    ) -> ResultBatch:
        """Count pending results. ``PR_WORK_VALIDATE``, **and not the subject.**

        Every ``PENDING`` result on the container, or the ones named. The
        self-validation rule is M1's, applied to the stream's subject: the
        person whose actual this moves cannot be the person moving it, whatever
        capability they hold.

        Nothing about a KPI target is consulted. A result past the target is
        counted like the ones before it, because the target is a comparison
        and the work happened.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_VALIDATE)
        actor_id = _require_user_id(actor)
        item = await self._lock_container(work_item_id)
        if item.subject_user_id == actor_id:
            raise PrPermissionDeniedError(
                "Đây là kết quả của chính bạn nên không thể tự xác nhận. "
                "Cần một người khác xác nhận để kết quả được ghi nhận.",
                details={"reason": "self_validation", "work_item_id": str(item.id)},
            )
        await self._require_open(item)
        if result_ids is not None and len(result_ids) > MAX_RESULTS_PER_VALIDATION:
            raise PrValidationError(
                f"Một lần xác nhận không quá {MAX_RESULTS_PER_VALIDATION} kết quả.",
                details={"field": "result_ids", "reason": "too_many"},
            )

        now = utcnow()
        pending = [
            row
            for row in await self.results_of(item.id)
            if row.status is PrWorkCountStatus.PENDING
            and (result_ids is None or row.id in set(result_ids))
        ]
        # **Source truth wins over a stale row.** A source-derived result is
        # asked about at the source before it is counted, because the
        # projector that would have swept it may not have run yet. A row the
        # source no longer backs is not counted: when the validator named it,
        # the request is refused and nothing is written; when they asked for
        # "every pending result", it is converged to ``SOURCE_REVERSED`` -
        # what the next projection would do - and the rest are counted.
        countable: list[PrWorkResult] = []
        reversed_rows: list[PrWorkResult] = []
        for row in pending:
            if await self._source_eligible(row) is False:
                if result_ids is not None:
                    raise PrConflictError(
                        SOURCE_NOT_ELIGIBLE_MESSAGE,
                        details={
                            "reason": SOURCE_NOT_ELIGIBLE,
                            "result_id": str(row.id),
                            "source_type": row.source_type.value,
                            "source_key": row.source_key,
                        },
                    )
                await self._reverse(
                    row,
                    item,
                    actor=actor,
                    request_id=request_id,
                    reason="Nguồn không còn xác nhận công việc này",
                    now=now,
                )
                reversed_rows.append(row)
                continue
            countable.append(row)
        pending = countable
        total = ZERO
        for row in pending:
            row.status = PrWorkCountStatus.COUNTED
            row.counted_at = now
            row.counted_by_user_id = actor_id
            total += row.quantity
            await self._work.record_history(
                item,
                event=PrWorkEventType.RESULT_COUNTED,
                actor=actor,
                note=_optional_text(note, "note", MAX_RESULT_NOTE),
                metadata={
                    "result_id": str(row.id),
                    "quantity": str(row.quantity),
                    "from_status": PrWorkCountStatus.PENDING.value,
                    "to_status": PrWorkCountStatus.COUNTED.value,
                    # **A Work decision.** The convergence rule reads this
                    # back so a source replay never undoes it.
                    "origin": PrWorkCountOrigin.WORK_VALIDATOR.value,
                },
            )
        await self._session.flush()
        await self._sync_container(item, now=now)
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_COUNTED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "counted_results": [str(row.id) for row in pending],
                "counted_quantity": str(total),
                "actual_quantity": str(item.quantity),
            },
        )
        logger.info(
            "pr_work_results_counted",
            extra={"pr_work_item_id": str(item.id), "pr_work_results": len(pending)},
        )
        return ResultBatch(
            work_item_id=item.id,
            counted=tuple(row.id for row in pending),
            counted_quantity=total,
            reversed=tuple(row.id for row in reversed_rows),
        )

    async def exclude_result(
        self, *, actor: Actor, request_id: uuid.UUID, result_id: uuid.UUID, reason: str
    ) -> PrWorkResult:
        """*Từ chối / Không ghi nhận*. ``PR_WORK_VALIDATE``, **not the subject.**

        A validator's reviewed decision that a result does not count, with a
        reason that is required. The row stays - ``EXCLUDED`` with
        ``exclusion_kind = VALIDATOR_REJECTED``, who decided and when - and
        **no projection puts it back**: the worker, *Đồng bộ lại từ Nội dung*,
        *Đồng bộ thiếu* and *Xây dựng lại* all leave it where the validator put
        it. :meth:`reconsider_result` is the one release.

        A ``PENDING`` result is the ordinary case. A ``COUNTED`` one may also be
        rejected - the correction flow M1 has always had at this grain - and
        the actual drops with it. An already excluded result is returned as it
        is: rejecting an administrator's removal, or a source reversal, is not
        a decision this method takes.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_VALIDATE)
        actor_id = _require_user_id(actor)
        result = await self._require_result(result_id)
        item = await self._lock_container(result.work_item_id)
        result = await self._reread(result)
        if item.subject_user_id == actor_id:
            raise PrPermissionDeniedError(
                "Đây là kết quả của chính bạn nên không thể tự từ chối.",
                details={"reason": "self_validation", "work_item_id": str(item.id)},
            )
        await self._require_open(item)
        text = _require_text(reason, "reason", MAX_RESULT_NOTE)
        if result.status is PrWorkCountStatus.EXCLUDED:
            return result
        before = result.status
        now = utcnow()
        result.status = PrWorkCountStatus.EXCLUDED
        # A row that is not counted names no counter. Who counted it before
        # stays on the timeline and in the audit trail.
        result.counted_at = None
        result.counted_by_user_id = None
        result.excluded_at = now
        result.excluded_by_user_id = actor_id
        result.excluded_reason = text
        result.exclusion_kind = PrWorkExclusionKind.VALIDATOR_REJECTED
        await self._session.flush()
        await self._sync_container(item, now=now)
        await self._work.record_history(
            item,
            event=PrWorkEventType.RESULT_REJECTED,
            actor=actor,
            note=text,
            metadata={
                "result_id": str(result.id),
                "quantity": str(result.quantity),
                "from_status": before.value,
                "to_status": result.status.value,
                "exclusion_kind": PrWorkExclusionKind.VALIDATOR_REJECTED.value,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_REJECTED,
            entity_type="pr_work_result",
            entity_id=result.id,
            before={"status": before.value, "exclusion_kind": None},
            after={
                "status": result.status.value,
                "exclusion_kind": PrWorkExclusionKind.VALIDATOR_REJECTED.value,
                **self._result_audit_context(result, item),
                "note": text,
                "actual_quantity": str(item.quantity),
            },
        )
        return result

    async def reconsider_result(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        result_id: uuid.UUID,
        note: str | None = None,
    ) -> PrWorkResult:
        """*Xem xét lại*. ``PR_WORK_VALIDATE``, not the subject. The one release.

        A validator's rejection - or a legacy exclusion whose author the row
        does not record - goes back to ``PENDING``, so *Xác nhận* and *Từ chối*
        are available again. Nothing is counted here, and nothing about the
        source is consulted or forced: if the content has since become
        invalid, the row is pending exactly as a fresh projection would have
        left it, and a validator counting it is a validator's decision.

        Any holder of the capability may release it, not only the validator
        who rejected it. The rejection stays in the timeline and the audit
        trail; this writes a later event beside it, never over it.

        An administrator's removal and a source reversal are not released
        here: the projector re-evaluates those on its own, and
        ``work_result_not_reconsiderable`` says so.
        """
        await self._capabilities.require(actor, PrCapability.PR_WORK_VALIDATE)
        actor_id = _require_user_id(actor)
        result = await self._require_result(result_id)
        item = await self._lock_container(result.work_item_id)
        result = await self._reread(result)
        if item.subject_user_id == actor_id:
            raise PrPermissionDeniedError(
                "Đây là kết quả của chính bạn nên không thể tự xem xét lại.",
                details={"reason": "self_validation", "work_item_id": str(item.id)},
            )
        await self._require_open(item)
        if result.status is not PrWorkCountStatus.EXCLUDED or not may_reconsider(
            result.exclusion_kind
        ):
            raise PrConflictError(
                "Chỉ kết quả đã bị người xác nhận từ chối mới xem xét lại được.",
                details={
                    "reason": "work_result_not_reconsiderable",
                    "result_id": str(result.id),
                    "status": result.status.value,
                    "exclusion_kind": (
                        result.exclusion_kind.value if result.exclusion_kind else None
                    ),
                },
            )
        if await self._source_eligible(result) is False:
            # Releasing it would make a pending row the source does not back,
            # and pending is where a validator counts. The rejection stands -
            # and its history with it - until the source says otherwise.
            raise PrConflictError(
                "Không thể xem xét lại vì Nội dung nguồn hiện không còn đủ điều kiện ghi nhận.",
                details={
                    "reason": SOURCE_NOT_ELIGIBLE,
                    "result_id": str(result.id),
                    "source_type": result.source_type.value,
                    "source_key": result.source_key,
                },
            )
        text = _optional_text(note, "note", MAX_RESULT_NOTE)
        before_kind = result.exclusion_kind
        before_reason = result.excluded_reason
        now = utcnow()
        result.status = PrWorkCountStatus.PENDING
        result.counted_at = None
        result.counted_by_user_id = None
        result.excluded_at = None
        result.excluded_by_user_id = None
        result.excluded_reason = None
        result.exclusion_kind = None
        await self._session.flush()
        await self._sync_container(item, now=now)
        await self._work.record_history(
            item,
            event=PrWorkEventType.RESULT_RECONSIDERED,
            actor=actor,
            note=text,
            metadata={
                "result_id": str(result.id),
                "quantity": str(result.quantity),
                "from_status": PrWorkCountStatus.EXCLUDED.value,
                "to_status": PrWorkCountStatus.PENDING.value,
                "released_exclusion_kind": before_kind.value if before_kind else None,
                "released_reason": before_reason,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_RECONSIDERED,
            entity_type="pr_work_result",
            entity_id=result.id,
            before={
                "status": PrWorkCountStatus.EXCLUDED.value,
                "exclusion_kind": before_kind.value if before_kind else None,
                "excluded_reason": before_reason,
            },
            after={
                "status": result.status.value,
                "exclusion_kind": None,
                **self._result_audit_context(result, item),
                "note": text,
                "actual_quantity": str(item.quantity),
            },
        )
        return result

    @staticmethod
    def _result_audit_context(result: PrWorkResult, item: PrWorkItem) -> dict[str, object]:
        """What every result-grain audit row names: the result, its stream, its subject."""
        return {
            "result_id": str(result.id),
            "work_item_id": str(item.id),
            "code": item.code,
            "work_type_id": str(item.work_type_id),
            "reporting_period_id": (
                str(item.reporting_period_id) if item.reporting_period_id else None
            ),
            "subject_user_id": str(result.user_id),
            "quantity": str(result.quantity),
            "source_type": result.source_type.value,
            "source_key": result.source_key,
        }

    async def withdraw_result(
        self, *, actor: Actor, request_id: uuid.UUID, result_id: uuid.UUID
    ) -> None:
        """The reporter takes back their own **pending** manual result.

        Only while nobody has counted it and only the person who declared it -
        a mistaken "+30" that should have been "+3" is withdrawn and re-entered.
        A counted result is somebody else's decision and is excluded by them.
        """
        actor_id = _require_user_id(actor)
        result = await self._require_result(result_id)
        item = await self._lock_container(result.work_item_id)
        result = await self._reread(result)
        if result.reported_by_user_id != actor_id:
            raise PrPermissionDeniedError(
                "Chỉ người báo cáo mới rút lại được kết quả của mình.",
                details={"reason": "not_reporter", "result_id": str(result.id)},
            )
        if result.source_type is not PrWorkResultSource.MANUAL:
            raise PrValidationError(
                "Kết quả do hệ thống ghi nhận không rút lại được ở đây.",
                details={"reason": "source_result", "result_id": str(result.id)},
            )
        if result.status is not PrWorkCountStatus.PENDING:
            raise PrConflictError(
                "Kết quả đã được xác nhận nên không rút lại được. Hãy nhờ người xác nhận loại bỏ.",
                details={"reason": "result_not_pending", "result_id": str(result.id)},
            )
        await self._require_open(item)
        now = utcnow()
        await self._session.execute(delete(PrWorkResult).where(PrWorkResult.id == result.id))
        await self._session.flush()
        await self._sync_container(item, now=now)
        await self._work.record_history(
            item,
            event=PrWorkEventType.RESULT_WITHDRAWN,
            actor=actor,
            metadata={"result_id": str(result.id), "quantity": str(result.quantity)},
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_WITHDRAWN,
            entity_type="pr_work_result",
            entity_id=result.id,
            before={"quantity": str(result.quantity), "work_item_id": str(item.id)},
        )

    # =====================================================================
    # Results another module contributes. Internal only.
    # =====================================================================
    async def record_source_result(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        source_type: PrWorkResultSource,
        source_key: str,
        work_type_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        occurred_at: datetime,
        validated_by_user_id: uuid.UUID | None,
        validated_at: datetime | None,
        label: str | None = None,
        link: str | None = None,
        quantity: Decimal | None = None,
    ) -> PrWorkResult:
        """Upsert one result a source contributed, and settle whether it counts.

        **Reachable from no route.** The content projector is the caller, and
        the shape is the one M3 already has for a one-off job, at result
        grain:

        * the row is found by ``(source_type, source_key)`` - the idempotency
          rule - or created on the container for the subject, the mapped work
          type and the month of the validation instant;
        * it is ``COUNTED`` on the source's instant when the source supplied a
          validator **who is not the subject**; ``PENDING`` otherwise. The
          self-validation rule is re-applied here against the source's
          validator, exactly as ``count_source_work`` does;
        * an existing row that the source has re-validated after a withdrawal
          goes back to ``COUNTED`` on the new instant. No second row;
        * an existing **uncounted** row whose mapping now points at a different
          work type is moved to that type's container. A counted one stays
          where its month reported it.
        """
        if source_type is PrWorkResultSource.MANUAL:
            raise PrValidationError(
                "Manual results have no source key",
                details={"field": "source_type", "reason": "manual_has_no_key"},
            )
        assert_source_key(source_key)
        amount = require_result_quantity(quantity)
        occurred_at = ensure_utc(occurred_at)
        validation_instant = ensure_utc(validated_at) if validated_at is not None else occurred_at
        independent = validated_by_user_id is not None and validated_by_user_id != subject_user_id

        existing = await self.result_for_source(source_type, source_key)
        if (
            existing is not None
            and existing.status is PrWorkCountStatus.EXCLUDED
            and not source_may_restore(existing.exclusion_kind)
        ):
            # **The validator's decision outranks the source's say-so.** A
            # rejected result - or a legacy exclusion nobody can attribute - is
            # not refiled, not restored and not counted, whatever the source
            # says now. ``reconsider_result`` is the only way out, and it is a
            # person's. The projector reports ``HELD_BY_VALIDATOR`` for it.
            return existing
        created = False
        if existing is None:
            period = await self.period_for_moment(
                validation_instant, actor=actor, request_id=request_id
            )
            item = await self.ensure_container(
                actor=actor,
                request_id=request_id,
                work_type_id=work_type_id,
                subject_user_id=subject_user_id,
                period=period,
            )
            item = await self._lock_container(item.id)
            candidate = PrWorkResult(
                work_item_id=item.id,
                user_id=subject_user_id,
                quantity=amount,
                label=_optional_text(label, "label", MAX_RESULT_LABEL),
                link=_optional_text(link, "link", MAX_RESULT_LINK),
                source_type=source_type,
                source_key=source_key,
                status=PrWorkCountStatus.PENDING,
                reported_by_user_id=subject_user_id,
                reported_at=occurred_at,
            )
            # Inside a savepoint, against ``uq_pr_work_results_source``: two
            # workers replaying one milestone at once both insert, one loses,
            # and the loser continues with the winner's row rather than
            # failing the projection. Sequential replays never reach here -
            # the read above finds the row.
            try:
                async with self._session.begin_nested():
                    self._session.add(candidate)
                    await self._session.flush()
            except IntegrityError:
                if candidate in self._session:
                    self._session.expunge(candidate)
                existing = await self.result_for_source(source_type, source_key)
                if existing is None:  # pragma: no cover - the index refused for another reason
                    raise
                item = await self._lock_container(existing.work_item_id)
            else:
                existing = candidate
                created = True
        if created:
            await self._work.record_history(
                item,
                event=PrWorkEventType.RESULT_REPORTED,
                actor=actor,
                note=existing.label,
                metadata={
                    "result_id": str(existing.id),
                    "quantity": str(amount),
                    "source_type": source_type.value,
                    "source_key": source_key,
                },
            )
            await record_pr_event(
                self._audit,
                request_id=request_id,
                actor=actor,
                action=AuditAction.PR_WORK_RESULT_REPORTED,
                entity_type="pr_work_result",
                entity_id=existing.id,
                after={
                    "work_item_id": str(item.id),
                    "code": item.code,
                    "quantity": str(amount),
                    "source_type": source_type.value,
                    "source_key": source_key,
                },
            )
        else:
            item = await self._lock_container(existing.work_item_id)
            if (
                item.work_type_id != work_type_id
                and existing.status is not PrWorkCountStatus.COUNTED
            ):
                item = await self._refile(
                    existing,
                    item,
                    actor=actor,
                    request_id=request_id,
                    work_type_id=work_type_id,
                    subject_user_id=subject_user_id,
                    validation_instant=validation_instant,
                )

        if existing.status is PrWorkCountStatus.COUNTED:
            return existing
        if item.reporting_period_id is not None:
            period_row = await self._session.get(PrReportingPeriod, item.reporting_period_id)
            if period_row is not None and period_row.status is not PrPeriodStatus.OPEN:
                # The caller checks the period before it gets here; this is the
                # floor, so a closed month is never rewritten by a replay.
                return existing
            if period_row is not None and await self._periods.performance_finalized(
                user_id=subject_user_id, period_id=period_row.id
            ):
                # Same floor for an agreed month: the projector reports
                # ``BLOCKED_BY_PERIOD`` before it gets here, and a replay that
                # reaches this line still moves nothing.
                return existing
        if not independent:
            # **The source is the truth, and the truth is "pending".** A row an
            # administrator removed, or a reversal excluded, whose milestone is
            # live again but has no independent validator goes back to
            # ``PENDING`` so a validator can count it - exactly the state a
            # first projection would have produced. Left ``EXCLUDED`` it could
            # never return: ``validate_results`` counts pending rows only, and
            # every later replay would report *pending validation* about a row
            # that is not pending. Nothing is counted here; the actual does not
            # move.
            if existing.status is PrWorkCountStatus.EXCLUDED:
                restored_from = existing.exclusion_kind
                existing.status = PrWorkCountStatus.PENDING
                existing.counted_at = None
                existing.excluded_at = None
                existing.excluded_by_user_id = None
                existing.excluded_reason = None
                existing.exclusion_kind = None
                await self._session.flush()
                await self._work.record_history(
                    item,
                    event=PrWorkEventType.RESULT_REPORTED,
                    actor=actor,
                    note=existing.label,
                    metadata={
                        "result_id": str(existing.id),
                        "quantity": str(existing.quantity),
                        "source_type": source_type.value,
                        "source_key": source_key,
                        "restored": True,
                        "restored_from": restored_from.value if restored_from else None,
                    },
                )
            return existing
        assert validated_by_user_id is not None
        restored_from = (
            existing.exclusion_kind if existing.status is PrWorkCountStatus.EXCLUDED else None
        )
        existing.status = PrWorkCountStatus.COUNTED
        existing.counted_at = validation_instant
        existing.counted_by_user_id = validated_by_user_id
        existing.excluded_at = None
        existing.excluded_by_user_id = None
        existing.excluded_reason = None
        existing.exclusion_kind = None
        await self._session.flush()
        await self._sync_container(item, now=validation_instant)
        await self._work.record_history(
            item,
            event=PrWorkEventType.RESULT_COUNTED,
            actor=actor,
            metadata={
                "result_id": str(existing.id),
                "quantity": str(existing.quantity),
                "source_validated_by_user_id": str(validated_by_user_id),
                "effective_validation_at": validation_instant.isoformat(),
                "restored_from": restored_from.value if restored_from else None,
                "origin": PrWorkCountOrigin.SOURCE.value,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_COUNTED,
            entity_type="pr_work_item",
            entity_id=item.id,
            after={
                "code": item.code,
                "counted_results": [str(existing.id)],
                "source_key": source_key,
                "validated_by_user_id": str(validated_by_user_id),
                "effective_validation_at": validation_instant.isoformat(),
                "actual_quantity": str(item.quantity),
            },
        )
        return existing

    async def reverse_source_result(
        self,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        source_type: PrWorkResultSource,
        source_key: str,
        reason: str,
    ) -> PrWorkResult | None:
        """The source withdrew the fact underneath a result. Internal only.

        The result goes ``EXCLUDED`` with ``exclusion_kind = SOURCE_REVERSED``
        and the reason, the container's actual drops, and M2 is recomputed. A
        redo through :meth:`record_source_result` brings the same row back to
        ``COUNTED``. The caller checks the period; this refuses nothing about
        months. An already excluded row - by a validator, an administrator or
        an earlier reversal - is left exactly as it is: it is already out, and
        overwriting *why* would turn a validator's decision into a source
        question.
        """
        existing = await self.result_for_source(source_type, source_key)
        if existing is None or existing.status is PrWorkCountStatus.EXCLUDED:
            return existing
        item = await self._lock_container(existing.work_item_id)
        existing = await self._reread(existing)
        if existing.status is PrWorkCountStatus.EXCLUDED:
            return existing
        await self._reverse(
            existing, item, actor=actor, request_id=request_id, reason=reason, now=utcnow()
        )
        return existing

    async def _reverse(
        self,
        existing: PrWorkResult,
        item: PrWorkItem,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        reason: str,
        now: datetime,
    ) -> None:
        """``SOURCE_REVERSED``, on a row the caller holds the container lock for."""
        assert existing.source_key is not None
        source_key = existing.source_key
        before = existing.status
        existing.status = PrWorkCountStatus.EXCLUDED
        existing.counted_at = None
        existing.counted_by_user_id = None
        existing.excluded_at = now
        existing.excluded_by_user_id = actor.user_id
        existing.excluded_reason = reason
        existing.exclusion_kind = PrWorkExclusionKind.SOURCE_REVERSED
        await self._session.flush()
        await self._sync_container(item, now=now)
        await self._work.record_history(
            item,
            event=PrWorkEventType.RESULT_EXCLUDED,
            actor=actor,
            note=reason,
            metadata={
                "result_id": str(existing.id),
                "source_key": source_key,
                "from_status": before.value,
                "to_status": existing.status.value,
                "exclusion_kind": PrWorkExclusionKind.SOURCE_REVERSED.value,
            },
        )
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_RESULT_EXCLUDED,
            entity_type="pr_work_result",
            entity_id=existing.id,
            before={"status": before.value, "exclusion_kind": None},
            after={
                "status": existing.status.value,
                "exclusion_kind": PrWorkExclusionKind.SOURCE_REVERSED.value,
                **self._result_audit_context(existing, item),
                "reason": reason,
                "actual_quantity": str(item.quantity),
            },
        )

    async def count_origin(self, row: PrWorkResult) -> PrWorkCountOrigin | None:
        """Who made the **current** count on a ``COUNTED`` row - the source, or a person.

        Read from the newest ``RESULT_COUNTED`` event on the row's timeline,
        which both counting paths write in the same transaction as the count
        itself: :meth:`record_source_result` stamps ``origin = SOURCE``
        (beside ``source_validated_by_user_id``, which older rows carry
        alone), :meth:`validate_results` stamps ``origin = WORK_VALIDATOR``.
        ``None`` for a row that is not counted, or one with no counting event
        on record - which the convergence rule treats as the source's, never
        as a person's.
        """
        if row.status is not PrWorkCountStatus.COUNTED:
            return None
        events = (
            (
                await self._session.execute(
                    select(PrWorkHistory)
                    .where(
                        PrWorkHistory.work_item_id == row.work_item_id,
                        PrWorkHistory.event_type == PrWorkEventType.RESULT_COUNTED,
                    )
                    .order_by(PrWorkHistory.created_at.desc(), PrWorkHistory.id.desc())
                )
            )
            .scalars()
            .all()
        )
        wanted = str(row.id)
        for event in events:
            metadata = event.event_metadata or {}
            if metadata.get("result_id") != wanted:
                continue
            origin = metadata.get("origin")
            if origin == PrWorkCountOrigin.WORK_VALIDATOR.value:
                return PrWorkCountOrigin.WORK_VALIDATOR
            if (
                origin == PrWorkCountOrigin.SOURCE.value
                or "source_validated_by_user_id" in metadata
            ):
                return PrWorkCountOrigin.SOURCE
            return PrWorkCountOrigin.WORK_VALIDATOR
        return None

    async def _source_eligible(self, row: PrWorkResult) -> bool | None:
        """Ask the row's source whether it still backs the row. ``None``: no opinion."""
        if (
            self._source_truth is None
            or row.source_type is PrWorkResultSource.MANUAL
            or row.source_key is None
        ):
            return None
        return await self._source_truth.source_is_eligible(row.source_type, row.source_key)

    async def source_eligibility(
        self, rows: Sequence[PrWorkResult]
    ) -> dict[uuid.UUID, bool | None]:
        """For the read model: which pending source-derived rows the source still backs.

        Asked only about ``PENDING`` rows - the ones a validator could act
        on - so a detail read costs one source lookup per such row and none
        for a manual result.
        """
        answers: dict[uuid.UUID, bool | None] = {}
        for row in rows:
            answers[row.id] = (
                await self._source_eligible(row)
                if row.status is PrWorkCountStatus.PENDING
                else None
            )
        return answers

    async def result_for_source(
        self, source_type: PrWorkResultSource, source_key: str
    ) -> PrWorkResult | None:
        return (
            (
                await self._session.execute(
                    select(PrWorkResult).where(
                        PrWorkResult.source_type == source_type,
                        PrWorkResult.source_key == source_key,
                    )
                )
            )
            .scalars()
            .one_or_none()
        )

    async def _refile(
        self,
        result: PrWorkResult,
        item: PrWorkItem,
        *,
        actor: Actor,
        request_id: uuid.UUID,
        work_type_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        validation_instant: datetime,
    ) -> PrWorkItem:
        """Move an uncounted source result to the container of its corrected type."""
        assert item.reporting_period_id is not None
        period = await self._session.get(PrReportingPeriod, item.reporting_period_id)
        assert period is not None
        target = await self.ensure_container(
            actor=actor,
            request_id=request_id,
            work_type_id=work_type_id,
            subject_user_id=subject_user_id,
            period=period,
        )
        target = await self._lock_container(target.id)
        result.work_item_id = target.id
        result.user_id = subject_user_id
        await self._session.flush()
        await self._sync_container(item, now=validation_instant)
        await self._sync_container(target, now=validation_instant)
        await record_pr_event(
            self._audit,
            request_id=request_id,
            actor=actor,
            action=AuditAction.PR_WORK_TYPE_UPDATED,
            entity_type="pr_work_result",
            entity_id=result.id,
            before={"work_item_id": str(item.id), "work_type_id": str(item.work_type_id)},
            after={"work_item_id": str(target.id), "work_type_id": str(work_type_id)},
        )
        return target

    # =====================================================================
    # Reading
    # =====================================================================
    async def results_of(self, work_item_id: uuid.UUID) -> list[PrWorkResult]:
        """Every result on one container, oldest first."""
        return list(
            (
                await self._session.execute(
                    select(PrWorkResult)
                    .where(PrWorkResult.work_item_id == work_item_id)
                    .order_by(PrWorkResult.reported_at.asc(), PrWorkResult.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    async def summary(self, item: PrWorkItem) -> ContainerSummary | None:
        """One container's month. ``None`` for a one-off job."""
        if not item.is_period_container:
            return None
        return (await self.summaries_for((item,))).get(item.id)

    async def summaries_for(self, items: Sequence[PrWorkItem]) -> dict[uuid.UUID, ContainerSummary]:
        """The summary of every container in ``items``, in a fixed number of queries.

        Results are summed in one grouped query, the approved quotas are read
        in two, and the rate lookups are cached per (work type, day) - the
        same caching M6's projection does - so a page of fifty streams does
        not issue fifty of each.
        """
        containers = [one for one in items if one.is_period_container]
        if not containers:
            return {}
        ids = [one.id for one in containers]
        sums = await self._result_sums(ids)
        period_ids = {one.reporting_period_id for one in containers if one.reporting_period_id}
        periods = {
            row.id: row
            for row in (
                await self._session.execute(
                    select(PrReportingPeriod).where(PrReportingPeriod.id.in_(period_ids))
                )
            )
            .scalars()
            .all()
        }
        type_ids = {one.work_type_id for one in containers}
        types = {
            row.id: row
            for row in (
                await self._session.execute(select(PrWorkType).where(PrWorkType.id.in_(type_ids)))
            )
            .scalars()
            .all()
        }
        quotas = await self._approved_quotas(
            {(one.subject_user_id, one.reporting_period_id) for one in containers}  # type: ignore[misc]
        )
        contributions = {
            row.work_item_id: row
            for row in (
                await self._session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id.in_(ids))
                )
            )
            .scalars()
            .all()
        }

        rules: dict[tuple[uuid.UUID, date], PrWorkScoringRule | None] = {}
        out: dict[uuid.UUID, ContainerSummary] = {}
        for item in containers:
            assert item.reporting_period_id is not None and item.subject_user_id is not None
            period = periods.get(item.reporting_period_id)
            work_type = types.get(item.work_type_id)
            if period is None or work_type is None:  # pragma: no cover - RESTRICT keys
                continue
            counted, pending, excluded, count, pending_count = sums.get(
                item.id, (ZERO, ZERO, ZERO, 0, 0)
            )
            quota = quotas.get((item.subject_user_id, period.id, item.work_type_id))
            target = quota.target_value if quota is not None else None
            comparison = compare_to_target(counted, target)

            # **Priced through M6's own function at M6's own rate.** The rate in
            # force on the day the stream was first counted, else the month's
            # end - the rule ``_project`` applies to the same contribution.
            contribution = contributions.get(item.id)
            counted_at = contribution.counted_at if contribution is not None else None
            on = ensure_utc(counted_at).date() if counted_at is not None else period.date_end
            minutes: Decimal | None = None
            per_unit: Decimal | None = None
            status = PrContributionScoreStatus.NO_SCORING_RULE
            if self._rules is not None:
                key = (work_type.id, on)
                if key not in rules:
                    rules[key] = await self._rules.rule_for(work_type.id, on=on)
                rule = rules[key]
                priced, status, per_unit = price_amount(counted, rule)
                minutes = (
                    priced if status is not PrContributionScoreStatus.NO_SCORING_RULE else None
                )

            unit = item.unit or work_type.default_unit
            out[item.id] = ContainerSummary(
                work_item_id=item.id,
                period_id=period.id,
                period_code=period.code,
                period_status=period.status,
                subject_user_id=item.subject_user_id,
                unit=unit,
                unit_label=work_unit_label(unit),
                counted_quantity=counted,
                pending_quantity=pending,
                declared_quantity=counted + pending,
                excluded_quantity=excluded,
                result_count=count,
                pending_count=pending_count,
                target_quantity=target,
                work_quota_id=quota.id if quota is not None else None,
                comparison=comparison,
                standard_minutes=minutes,
                standard_minutes_per_unit=per_unit,
                scoring_status=status,
            )
        return out

    # =====================================================================
    # Internals
    # =====================================================================
    async def sync_container(self, item: PrWorkItem, *, now: datetime) -> None:
        """Re-derive a container from its results. **Internal**, for the maintenance service.

        The same routine every result write here ends with; exposed so an
        administrative exclusion made in another service - which has already
        locked the container and checked the period - settles the actual, the
        contribution and the M2 handoff through the one implementation.
        """
        await self._sync_container(item, now=now)

    async def _sync_container(self, item: PrWorkItem, *, now: datetime) -> None:
        """Make the container's derived facts match its results. **The actual.**

        Three writes, all derived and none of them a decision:

        * ``item.quantity`` = the counted sum;
        * the contribution is ``COUNTED`` with the earliest counted result's
          instant while anything is counted, ``PENDING`` otherwise. A
          contribution somebody deliberately ``EXCLUDED`` - a cancelled stream
          - is left alone;
        * M2 is asked to recompute the subject's month, behind the same
          savepoint rule ``PrWorkService.approve`` uses, so a failed projection
          never takes a valid result down with it.
        """
        sums = await self._result_sums([item.id])
        counted, _pending, _excluded, _count, _pending_count = sums.get(
            item.id, (ZERO, ZERO, ZERO, 0, 0)
        )
        earliest = (
            await self._session.execute(
                select(func.min(PrWorkResult.counted_at)).where(
                    PrWorkResult.work_item_id == item.id,
                    PrWorkResult.status == PrWorkCountStatus.COUNTED,
                )
            )
        ).scalar_one_or_none()

        item.quantity = counted
        if item.unit is None:
            work_type = await self._session.get(PrWorkType, item.work_type_id)
            assert work_type is not None
            item.unit = work_type.default_unit

        contribution = (
            (
                await self._session.execute(
                    select(PrWorkContribution).where(PrWorkContribution.work_item_id == item.id)
                )
            )
            .scalars()
            .first()
        )
        moved: list[PrWorkContribution] = []
        withdrawn: list[PrWorkContribution] = []
        if contribution is not None and contribution.count_status is not PrWorkCountStatus.EXCLUDED:
            if counted > 0 and earliest is not None:
                stamp = ensure_utc(earliest)
                if contribution.count_status is not PrWorkCountStatus.COUNTED:
                    contribution.count_status = PrWorkCountStatus.COUNTED
                    contribution.counted_at = stamp
                    moved.append(contribution)
                elif contribution.counted_at != stamp:
                    contribution.counted_at = stamp
                    moved.append(contribution)
                else:
                    # The amount changed under an already-counted contribution;
                    # M2 still has to re-measure it.
                    moved.append(contribution)
            elif contribution.count_status is PrWorkCountStatus.COUNTED:
                contribution.count_status = PrWorkCountStatus.PENDING
                contribution.counted_at = None
                withdrawn.append(contribution)
        await self._session.flush()

        if self._eligibility is None or item.reporting_period_id is None:
            return
        period = await self._session.get(PrReportingPeriod, item.reporting_period_id)
        if period is None:  # pragma: no cover - RESTRICT key
            return
        try:
            async with self._session.begin_nested():
                if moved:
                    await self._eligibility.on_contributions_counted(
                        moved, now=now, period_of=period
                    )
                if withdrawn:
                    await self._eligibility.on_contributions_uncounted(
                        withdrawn, now=now, period_of=period
                    )
        except Exception:  # deliberately broad; see PrWorkService._project_eligibility
            logger.exception(
                "pr_work_container_projection_failed",
                extra={"pr_work_item_id": str(item.id)},
            )

    async def _result_sums(
        self, item_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[Decimal, Decimal, Decimal, int, int]]:
        """``(counted, pending, excluded, results, pending results)`` per container."""
        ids = list(item_ids)
        if not ids:
            return {}
        counted = func.coalesce(
            func.sum(
                case((PrWorkResult.status == PrWorkCountStatus.COUNTED, PrWorkResult.quantity))
            ),
            0,
        )
        pending = func.coalesce(
            func.sum(
                case((PrWorkResult.status == PrWorkCountStatus.PENDING, PrWorkResult.quantity))
            ),
            0,
        )
        excluded = func.coalesce(
            func.sum(
                case((PrWorkResult.status == PrWorkCountStatus.EXCLUDED, PrWorkResult.quantity))
            ),
            0,
        )
        pending_count = func.coalesce(
            func.sum(case((PrWorkResult.status == PrWorkCountStatus.PENDING, 1))), 0
        )
        rows = (
            await self._session.execute(
                select(
                    PrWorkResult.work_item_id,
                    counted,
                    pending,
                    excluded,
                    func.count(PrWorkResult.id),
                    pending_count,
                )
                .where(PrWorkResult.work_item_id.in_(ids))
                .group_by(PrWorkResult.work_item_id)
            )
        ).all()
        return {
            row[0]: (
                _decimal(row[1]),
                _decimal(row[2]),
                _decimal(row[3]),
                int(row[4] or 0),
                int(row[5] or 0),
            )
            for row in rows
        }

    async def _approved_quotas(
        self, pairs: set[tuple[uuid.UUID, uuid.UUID]]
    ) -> dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], PrWorkQuota]:
        """Approved quotas keyed ``(user, period, work type)`` for the pairs named."""
        if not pairs:
            return {}
        users = {user for user, _ in pairs}
        periods = {period for _, period in pairs}
        plans = (
            (
                await self._session.execute(
                    select(PrWorkPlan).where(
                        PrWorkPlan.user_id.in_(users),
                        PrWorkPlan.period_id.in_(periods),
                        PrWorkPlan.status == PrWorkPlanStatus.APPROVED,
                    )
                )
            )
            .scalars()
            .all()
        )
        by_plan = {plan.id: plan for plan in plans if (plan.user_id, plan.period_id) in pairs}
        if not by_plan:
            return {}
        quotas = (
            (
                await self._session.execute(
                    select(PrWorkQuota).where(PrWorkQuota.plan_id.in_(by_plan))
                )
            )
            .scalars()
            .all()
        )
        out: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], PrWorkQuota] = {}
        for quota in quotas:
            plan = by_plan[quota.plan_id]
            out[(plan.user_id, plan.period_id, quota.work_type_id)] = quota
        return out

    async def _require_container(self, work_item_id: uuid.UUID) -> PrWorkItem:
        item = await self._session.get(PrWorkItem, work_item_id)
        if item is None:
            raise PrNotFoundError(
                "Không tìm thấy công việc.", details={"work_item_id": str(work_item_id)}
            )
        if not item.is_period_container:
            raise PrValidationError(
                "Công việc này là việc một lần, không báo cáo kết quả theo kỳ được.",
                details={"reason": "not_period_container", "work_item_id": str(item.id)},
            )
        return item

    async def _lock_container(self, work_item_id: uuid.UUID) -> PrWorkItem:
        item = await lock_row(self._session, PrWorkItem, work_item_id)
        if item is None:
            raise PrNotFoundError(
                "Không tìm thấy công việc.", details={"work_item_id": str(work_item_id)}
            )
        if not item.is_period_container:
            raise PrValidationError(
                "Công việc này là việc một lần, không báo cáo kết quả theo kỳ được.",
                details={"reason": "not_period_container", "work_item_id": str(item.id)},
            )
        return item

    async def names_for(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Display names, resolved here so no client is handed a bare UUID."""
        ids = {one for one in user_ids if one is not None}
        if not ids:
            return {}
        rows = (
            await self._session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
        ).all()
        return {row[0]: row[1] for row in rows}

    async def require_result(self, result_id: uuid.UUID) -> PrWorkResult:
        """One result by id, or a not-found the caller can render."""
        return await self._require_result(result_id)

    async def _reread(self, result: PrWorkResult) -> PrWorkResult:
        """The result as it is **after** the container lock was taken.

        Every result-grain write reads the row first - it has to, to know
        which container to lock - and the row it read may be a decision old
        by the time the lock is granted: a validator counting it, an
        administrator removing it, another validator rejecting it. Acting on
        the stale copy would write ``EXCLUDED`` over a ``counted_at`` the
        session never saw (and trip ``counted_at_matches_status``), or
        rewrite an administrator's removal as a rejection. Under the lock the
        row is re-read, so every check below is against what is there now.
        """
        await self._session.refresh(result)
        return result

    async def _require_result(self, result_id: uuid.UUID) -> PrWorkResult:
        row = await self._session.get(PrWorkResult, result_id)
        if row is None:
            raise PrNotFoundError("Không tìm thấy kết quả.", details={"result_id": str(result_id)})
        return row

    async def _require_reporter(self, actor: Actor, subject_user_id: uuid.UUID) -> None:
        """Your own stream with ``PR_WORK_EXECUTE``; somebody else's with ``PR_WORK_MANAGE``."""
        if actor.user_id == subject_user_id:
            await self._capabilities.require(actor, PrCapability.PR_WORK_EXECUTE)
            return
        await self._capabilities.require(actor, PrCapability.PR_WORK_MANAGE)

    async def _require_open(self, item: PrWorkItem) -> None:
        """A cancelled stream, a shut month, or an **agreed** month takes no more results.

        The third is the finalised-performance guard: once somebody has
        finalised the subject's month, no result on it may be reported,
        counted, rejected, released or withdrawn - the stored figure would
        otherwise drift from the live one. Period close is a separate
        lifecycle; this refuses the mutation and says why.
        """
        if item.status is PrWorkStatus.CANCELLED:
            raise PrConflictError(
                "Công việc định kỳ này đã hủy.",
                details={"reason": PERIOD_CONTAINER_LOCKED, "work_item_id": str(item.id)},
            )
        assert item.reporting_period_id is not None
        period = await self._session.get(PrReportingPeriod, item.reporting_period_id)
        if period is not None and period.status is not PrPeriodStatus.OPEN:
            raise PrConflictError(
                f"Kỳ {period.code} đã khóa nên không ghi nhận thêm kết quả.",
                details={"reason": "period_not_open", "period": period.code},
            )
        if period is not None and item.subject_user_id is not None:
            await self._require_not_finalized(period, user_id=item.subject_user_id)

    async def _require_not_finalized(
        self, period: PrReportingPeriod, *, user_id: uuid.UUID
    ) -> None:
        if await self._periods.performance_finalized(user_id=user_id, period_id=period.id):
            raise PrConflictError(
                f"Hiệu suất kỳ {period.code} của nhân sự này đã được chốt nên không thay đổi "
                "được kết quả công việc.",
                details={
                    "reason": PERFORMANCE_FINALIZED,
                    "period": period.code,
                    "subject_user_id": str(user_id),
                },
            )


def _container_title(name: str, period: PrReportingPeriod) -> str:
    """*"Tìm khách hàng — 2026-09"*, bounded to the title column."""
    suffix = f" — {period.code}"
    return f"{name[: MAX_TITLE - len(suffix)]}{suffix}"


def _decimal(value: object) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _require_user_id(actor: Actor) -> uuid.UUID:
    if actor.user_id is None:
        raise PrPermissionDeniedError(
            "Cần đăng nhập để thực hiện thao tác này.", details={"reason": "no_user"}
        )
    return actor.user_id


def _require_text(value: str | None, field: str, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise PrValidationError(
            "Trường này không được để trống.", details={"field": field, "reason": "required"}
        )
    if len(text) > max_length:
        raise PrValidationError(
            f"Tối đa {max_length} ký tự.", details={"field": field, "reason": "too_long"}
        )
    return text


def _optional_text(value: str | None, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise PrValidationError(
            f"Tối đa {max_length} ký tự.", details={"field": field, "reason": "too_long"}
        )
    return text


__all__: list[str] = [
    "MAX_RESULTS_PER_VALIDATION",
    "ContainerSummary",
    "PrWorkResultService",
    "ResultBatch",
]
