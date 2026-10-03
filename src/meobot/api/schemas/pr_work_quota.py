"""What the KPI-plan and eligibility API sends and accepts. M2.

Its own module rather than more of ``schemas/pr_work.py``, which is M1's and
whose readers are the work board rather than the plan screen.

Three rules run through every model here
-----------------------------------------

**No score, anywhere.** No ``base_score``, no ``awarded_score``, no ``points``,
no ``quality_multiplier``, no ``bonus`` and no field called ``scored``.
``quota_status`` is as far as M2 goes, and its four values say whether counted
work is inside an approved quota - never what that work is worth. The Vietnamese
labels say the same thing: *"Đủ điều kiện tính KPI"*, never *"Đã được tính
điểm"*.

**No status arrives from a client.** There is no ``status`` field on any request
body and no ``PATCH`` that can set one. Approving a plan, revising it and
discarding it are three endpoints calling three service methods, so there is no
request through which a plan could be filed as already approved - which is the
difference between an immutability rule and a suggestion. The one ``PATCH``
here edits a **draft** quota's two numbers and can reach nothing else.

**No amount is a float.** Every quota and allocation figure is a
``Decimal``, and Pydantic is told so, because eligibility capacity is
money-adjacent arithmetic and binary floating point does not add up.

Labels
------

Every ``*_label`` is composed here from
:mod:`meobot.domain.pr.work_quota_labels`, so there is one Vietnamese wording
per value and the browser holds no second copy of the table.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from meobot.application.pr_plan_workload import PlanWorkload, QuotaWorkload
from meobot.application.pr_work_plan_service import (
    EmployeePlanSummary,
    PlanDetail,
    PlanPage,
)
from meobot.application.pr_work_quota_service import (
    ContributionEligibility,
    QuotaPeriodSummary,
    QuotaTypeProgress,
    ReconcileOutcome,
)
from meobot.db.models.pr_reporting import PrReportingPeriod
from meobot.db.models.pr_work import PrWorkType
from meobot.db.models.pr_work_quota import PrWorkPlan, PrWorkQuota
from meobot.domain.pr.work import PrWorkUnit
from meobot.domain.pr.work_labels import work_unit_label
from meobot.domain.pr.work_quota import PrWorkQuotaBasis, plan_review_state
from meobot.domain.pr.work_quota_labels import (
    plan_readiness_blocker_label,
    plan_review_state_label,
    target_unresolved_label,
    work_plan_status_label,
    work_quota_basis_hint,
    work_quota_basis_label,
    work_quota_status_hint,
    work_quota_status_label,
    work_unmeasurable_reason_label,
)


class _Body(BaseModel):
    """Request bodies refuse unknown fields.

    ``extra="forbid"`` rather than ``ignore``, and it is load-bearing: a body
    carrying ``status``, ``approved_at`` or ``quota_status`` is **refused**
    rather than silently dropped, so an attempt to set one is an error somebody
    sees rather than a no-op they might believe worked.
    """

    model_config = ConfigDict(extra="forbid")


#: What a target or a cap may be, on the wire.
#:
#: ``gt=0`` here as well as in the domain and in the database, and the
#: repetition is deliberate: the browser gets a field-level validation error
#: instead of a 422 with a domain payload, and the two deeper checks still stand
#: for every other caller.
_Amount = Field(gt=0, le=Decimal("1000000"), max_digits=12, decimal_places=2)


# --- Reporting periods ------------------------------------------------------


class ReportingPeriodResponse(BaseModel):
    """One month a plan may be written for.

    ``status`` is the whole of M2's immutability story on one field: an ``OPEN``
    period may have its eligibility recomputed, and a ``CLOSED`` or ``LOCKED``
    one may not. The client draws the difference; the server refuses either way.
    """

    id: uuid.UUID
    #: ``2026-09``. What a person says and a report prints.
    code: str
    period_type: str
    date_start: date
    date_end: date
    #: ``OPEN`` | ``CLOSED`` | ``LOCKED``.
    status: str
    closed_at: datetime | None = None
    locked_at: datetime | None = None

    @classmethod
    def from_row(cls, row: PrReportingPeriod) -> ReportingPeriodResponse:
        return cls(
            id=row.id,
            code=row.code,
            period_type=row.period_type.value,
            date_start=row.date_start,
            date_end=row.date_end,
            status=row.status.value,
            closed_at=row.closed_at,
            locked_at=row.locked_at,
        )


class EnsurePeriodRequest(_Body):
    """Open a month for reporting. ``PR_WORK_CONFIGURE``, and **idempotent**.

    A year and a month rather than a pair of dates, because the dates of
    September are not a decision anybody should be able to get wrong, and a
    request that could name 1-29 September would be inventing a calendar beside
    the one the module already has.

    Asking for a month that exists returns it **unchanged**, including its
    status: a retried request does not reopen a closed period.
    """

    year: int = Field(ge=2024, le=2100)
    month: int = Field(ge=1, le=12)


# --- Quotas -----------------------------------------------------------------


class WorkQuotaResponse(BaseModel):
    """One work type's target and cap inside one plan version.

    ``target_value`` and ``eligibility_cap`` are **two numbers on purpose**: 20
    is what the plan asks for and 25 is how much may be eligible at all, so
    21-25 is real extra work that stays eligible without pretending the target
    was 25. Neither is ever null, and an absent cap is never read as unlimited.
    """

    id: uuid.UUID
    plan_id: uuid.UUID
    work_type_id: uuid.UUID
    work_type_code: str | None = None
    work_type_name: str | None = None
    #: ``ITEM_COUNT`` | ``QUANTITY``.
    basis: str
    basis_label: str
    basis_hint: str
    target_value: Decimal
    eligibility_cap: Decimal
    #: Present for ``QUANTITY`` and null for ``ITEM_COUNT``, whose amounts are
    #: counts of contributions rather than a quantity of anything.
    unit: str | None = None
    unit_label: str | None = None
    note: str | None = None

    @classmethod
    def from_row(
        cls, row: PrWorkQuota, *, work_types: dict[uuid.UUID, PrWorkType] | None = None
    ) -> WorkQuotaResponse:
        work_type = (work_types or {}).get(row.work_type_id)
        return cls(
            id=row.id,
            plan_id=row.plan_id,
            work_type_id=row.work_type_id,
            work_type_code=work_type.code if work_type else None,
            work_type_name=work_type.name if work_type else None,
            basis=row.basis.value,
            basis_label=work_quota_basis_label(row.basis),
            basis_hint=work_quota_basis_hint(row.basis),
            target_value=row.target_value,
            eligibility_cap=row.eligibility_cap,
            unit=row.unit.value if row.unit else None,
            unit_label=work_unit_label(row.unit) if row.unit else None,
            note=row.note,
        )


class AddQuotaRequest(_Body):
    """Put one work type's target and cap into a **draft** plan.

    ``basis`` and ``unit`` are optional and default to the work type's own,
    which is the case that cannot be got wrong. Sending them explicitly is
    allowed and is **validated against the type** rather than trusted: a
    ``QUANTITY`` cap in ``VIDEO`` on a type that counts comments is refused,
    because the work type is the semantic authority and a quota in units the
    work is never recorded in would silently never fill.
    """

    work_type_id: uuid.UUID
    target_value: Decimal = _Amount
    eligibility_cap: Decimal = _Amount
    basis: PrWorkQuotaBasis | None = None
    unit: PrWorkUnit | None = None
    note: str | None = Field(default=None, max_length=2000)


class UpdateQuotaRequest(_Body):
    """Change a **draft** quota's two numbers, or move it to another work type.

    ``basis`` and ``unit`` are **absent on purpose**: they are derived from the
    work type, and when ``work_type_id`` moves the quota to another type the
    service re-derives both from that type and refuses a type the plan already
    has a quota for. The numbers stay; what the plan implies in workload is
    recomputed when it is read.

    This route cannot reach an approved plan. The service refuses anything that
    is not a ``DRAFT`` and says so, with ``next: "revise"``.
    """

    work_type_id: uuid.UUID | None = None
    target_value: Decimal | None = Field(
        default=None, gt=0, le=Decimal("1000000"), max_digits=12, decimal_places=2
    )
    eligibility_cap: Decimal | None = Field(
        default=None, gt=0, le=Decimal("1000000"), max_digits=12, decimal_places=2
    )
    note: str | None = Field(default=None, max_length=2000)


# --- Plans ------------------------------------------------------------------


class QuotaWorkloadResponse(BaseModel):
    """One quota's target, the rule that priced it, and the product.

    Enough to print *"28 đầu việc · 30 phút/đầu việc → 840 phút"* without the
    browser multiplying anything: ``contribution_minutes`` **is** the product,
    ``rule_label`` **is** the rule, and both come from the one calculator. A
    quota no approved rule prices has ``is_priced: false``, a ``status`` that
    says why, and no contribution - never a silent zero.
    """

    quota_id: uuid.UUID
    work_type_id: uuid.UUID
    work_type_code: str | None = None
    work_type_name: str | None = None
    #: ``ITEM_COUNT`` | ``QUANTITY`` - how the quota measures.
    measurement_mode: str
    measurement_mode_label: str
    target_value: Decimal
    #: "đầu việc" for a count; the work type's unit for a quantity.
    target_unit_label: str
    #: Minutes per **one** ``target_unit_label``. The rule model's own figure.
    standard_minutes_per_unit: Decimal | None = None
    #: ``"30 phút / đầu việc"``. Server-worded; the client prints it.
    rule_label: str | None = None
    rule_version_no: int | None = None
    rule_effective_from: date | None = None
    contribution_minutes: Decimal | None = None
    is_priced: bool
    #: ``PRICED`` | ``NO_SCORING_RULE`` | ``EXCLUDED_FROM_PERFORMANCE``.
    status: str
    status_label: str

    @classmethod
    def from_domain(cls, row: QuotaWorkload) -> QuotaWorkloadResponse:
        return cls(
            quota_id=row.quota_id,
            work_type_id=row.work_type_id,
            work_type_code=row.work_type_code,
            work_type_name=row.work_type_name,
            measurement_mode=row.basis.value,
            measurement_mode_label=row.measurement_mode_label,
            target_value=row.target_value,
            target_unit_label=row.target_unit_label,
            standard_minutes_per_unit=row.standard_minutes_per_unit,
            rule_label=row.rule_label,
            rule_version_no=row.rule_version_no,
            rule_effective_from=row.rule_effective_from,
            contribution_minutes=row.contribution_minutes,
            is_priced=row.is_priced,
            status=row.status.value,
            status_label=row.status_label,
        )


class UnpricedWorkTypeResponse(BaseModel):
    work_type_id: uuid.UUID
    work_type_name: str | None = None


class PlanWorkloadResponse(BaseModel):
    """**Tải KPI** - what a plan asks for, in standard minutes. Advisory.

    Priced through M6's own readers by the one calculator: approved scoring
    rules in force on the period's last day, the approved policy's daily
    minutes, the person's resolved workdays. Nothing here is a score, a band or
    a gate; the manager decides.

    Two things the client must read before printing a percentage:

    * ``percent`` is ``null`` when the calendar could not resolve a target
      (``target_unresolved_label`` says why) **or** when any quota is unpriced
      (``is_complete: false``, ``unpriced_work_types`` names them). Four priced
      quotas and one unpriced one are not "90%";
    * ``projected_minutes`` is then a floor - what *has* been priced - and the
      screen says so.

    ``quotas`` carries the per-quota breakdown on detail responses and is empty
    on list rows, where twenty employees' breakdowns would be weight nobody
    reads until they open a row.
    """

    projected_minutes: Decimal
    target_minutes: Decimal | None = None
    percent: Decimal | None = None
    #: Work types in the plan that no approved scoring rule prices yet. Kept
    #: for the screens that only ever wanted a count.
    unscored_work_type_ids: list[uuid.UUID] = Field(default_factory=list)
    priced_quota_count: int = 0
    unpriced_quota_count: int = 0
    excluded_quota_count: int = 0
    is_complete: bool = True
    unpriced_work_types: list[UnpricedWorkTypeResponse] = Field(default_factory=list)
    #: Why there is no target, when there is none. Null when there is one.
    target_unresolved_reason: str | None = None
    target_unresolved_label: str | None = None
    #: The arithmetic behind ``target_minutes``, so a card can say
    #: "22 ngày công x 300 phút" rather than only "6 600".
    calendar_workdays: Decimal | None = None
    approved_leave_days: Decimal | None = None
    eligible_workdays: Decimal | None = None
    daily_target_minutes: int | None = None
    target_is_overridden: bool = False
    #: The day whose approved rules priced the plan - the period's last day.
    rules_effective_on: date | None = None
    quotas: list[QuotaWorkloadResponse] = Field(default_factory=list)

    @classmethod
    def from_workload(
        cls, workload: PlanWorkload | None, *, include_quotas: bool = True
    ) -> PlanWorkloadResponse | None:
        if workload is None:
            return None
        target = workload.target
        return cls(
            projected_minutes=workload.projected_minutes,
            target_minutes=workload.target_minutes,
            percent=workload.percent,
            unscored_work_type_ids=list(workload.unscored_work_type_ids),
            priced_quota_count=workload.priced_quota_count,
            unpriced_quota_count=workload.unpriced_quota_count,
            excluded_quota_count=workload.excluded_quota_count,
            is_complete=workload.is_complete,
            unpriced_work_types=[
                UnpricedWorkTypeResponse(work_type_id=type_id, work_type_name=name)
                for type_id, name in workload.unpriced_work_types
            ],
            target_unresolved_reason=workload.target_unresolved_reason,
            target_unresolved_label=target_unresolved_label(workload.target_unresolved_reason),
            calendar_workdays=target.calendar_workdays if target and target.resolved else None,
            approved_leave_days=target.approved_leave_days if target and target.resolved else None,
            eligible_workdays=target.eligible_workdays if target and target.resolved else None,
            daily_target_minutes=target.daily_target_minutes if target else None,
            target_is_overridden=bool(target and target.is_overridden),
            rules_effective_on=workload.rules_effective_on,
            quotas=(
                [QuotaWorkloadResponse.from_domain(row) for row in workload.quotas]
                if include_quotas
                else []
            ),
        )


def _review_state(row: PrWorkPlan | None) -> tuple[str | None, str | None]:
    if row is None:
        return None, None
    state = plan_review_state(
        row.status, submitted_at=row.submitted_at, returned_at=row.returned_at
    )
    if state is None:
        return None, None
    return state.value, plan_review_state_label(state)


class EmployeePlanSummaryResponse(BaseModel):
    """One employee's KPI standing for one month. **The main KPI screen's row.**

    The top-level object is the **employee**, not a plan version. An employee on
    their third revision is one row here; their v1 and v2 are history inside the
    detail screen. The list used to be built from plan rows, which made the same
    person appear three times as three management entities - and, worse, made an
    employee with no plan appear not at all, when "who has no plan" is half of
    what the screen is for.

    Every field a row draws is resolved here. The browser decides nothing about
    which version is current: see
    :func:`~meobot.application.pr_work_plan_service.current_plan_of`.
    """

    user_id: uuid.UUID
    user_name: str
    period_id: uuid.UUID
    #: Whether there is a plan to open at all. False for an employee nobody has
    #: written one for, which is a row the screen must still show.
    has_plan: bool
    #: The plan a manager acts on - approved, else draft, else absent.
    current_plan_id: uuid.UUID | None = None
    current_version_no: int | None = None
    #: ``DRAFT`` | ``APPROVED``. Never ``SUPERSEDED`` or ``DISCARDED``: those are
    #: history and are never current, however recent.
    current_status: str | None = None
    current_status_label: str | None = None
    quota_count: int = 0
    approved_at: datetime | None = None
    updated_at: datetime | None = None
    #: The draft in flight. **Distinct from the current plan** when a manager is
    #: revising one that is already in force, which is the case the screen has
    #: to offer two different controls for. Equal to ``current_plan_id`` when no
    #: approved version exists yet and the draft *is* what a manager acts on.
    latest_draft_id: uuid.UUID | None = None
    #: The draft's own version and size, so a row can say "v4 · 0 hạn mức"
    #: without fetching the plan. Absent when there is no draft.
    draft_version_no: int | None = None
    draft_quota_count: int = 0
    #: When the draft was last touched. What *"Cập nhật …"* prints on the draft
    #: panel - and deliberately the row's own ``updated_at``, which is the only
    #: timestamp a draft actually has.
    draft_updated_at: datetime | None = None
    #: Every version for this employee and month - the current plan and any
    #: active draft included. What a row prints as "N phiên bản".
    history_count: int = 0
    #: The versions that are **over**: superseded and discarded. What
    #: *"Lịch sử thay đổi (N)"* counts. Separate from ``history_count`` because
    #: a draft being written is not history, and counting it as history is how
    #: it became unreachable.
    terminal_count: int = 0
    #: KPI self-service. Where the draft stands: ``EDITING`` | ``SUBMITTED`` |
    #: ``RETURNED``, with its label, and the submission facts. ``null`` without
    #: a draft. A manager's queue is the rows whose state is ``SUBMITTED`` -
    #: an unsubmitted draft is the employee's, not a request.
    draft_review_state: str | None = None
    draft_review_state_label: str | None = None
    draft_is_submitted: bool = False
    draft_submitted_at: datetime | None = None
    draft_submitted_by_user_id: uuid.UUID | None = None
    draft_returned_at: datetime | None = None
    draft_return_note: str | None = None
    draft_workload: PlanWorkloadResponse | None = None
    current_workload: PlanWorkloadResponse | None = None

    @classmethod
    def from_summary(cls, summary: EmployeePlanSummary) -> EmployeePlanSummaryResponse:
        current = summary.current_plan
        draft = summary.latest_draft
        state, state_label = _review_state(draft)
        return cls(
            draft_review_state=state,
            draft_review_state_label=state_label,
            draft_is_submitted=draft.is_submitted if draft else False,
            draft_submitted_at=draft.submitted_at if draft else None,
            draft_submitted_by_user_id=draft.submitted_by_user_id if draft else None,
            draft_returned_at=draft.returned_at if draft else None,
            draft_return_note=draft.return_note if draft else None,
            # Summary only. The breakdown is the detail response's, on request.
            draft_workload=PlanWorkloadResponse.from_workload(
                summary.draft_workload, include_quotas=False
            ),
            current_workload=PlanWorkloadResponse.from_workload(
                summary.current_workload, include_quotas=False
            ),
            user_id=summary.user_id,
            user_name=summary.user_name,
            period_id=summary.period_id,
            has_plan=summary.has_plan,
            current_plan_id=current.id if current else None,
            current_version_no=current.version_no if current else None,
            current_status=current.status.value if current else None,
            current_status_label=work_plan_status_label(current.status) if current else None,
            quota_count=summary.quota_count,
            approved_at=current.approved_at if current else None,
            updated_at=current.updated_at if current else None,
            latest_draft_id=summary.latest_draft.id if summary.latest_draft else None,
            draft_version_no=summary.latest_draft.version_no if summary.latest_draft else None,
            draft_quota_count=summary.draft_quota_count,
            draft_updated_at=summary.latest_draft.updated_at if summary.latest_draft else None,
            history_count=summary.history_count,
            terminal_count=summary.terminal_count,
        )


class PlanReviewCountsResponse(BaseModel):
    """**Chờ duyệt N** and its neighbours - the manager's figures for one month.

    Counted from the same rows the list draws, on the server, so the tab a
    manager filters by and the number on it cannot disagree. ``pending_review``
    is submitted drafts only.
    """

    pending_review: int = 0
    approved: int = 0
    drafting: int = 0
    without_plan: int = 0


class EmployeePlanSummaryListResponse(BaseModel):
    """Every active employee's KPI standing for one month, by name."""

    period_id: uuid.UUID
    items: list[EmployeePlanSummaryResponse]
    counts: PlanReviewCountsResponse = Field(default_factory=PlanReviewCountsResponse)

    @classmethod
    def from_summaries(
        cls, period_id: uuid.UUID, summaries: tuple[EmployeePlanSummary, ...]
    ) -> EmployeePlanSummaryListResponse:
        items = [EmployeePlanSummaryResponse.from_summary(one) for one in summaries]
        counts = PlanReviewCountsResponse(
            pending_review=sum(1 for one in items if one.draft_is_submitted),
            approved=sum(1 for one in items if one.current_status == "APPROVED"),
            drafting=sum(
                1 for one in items if one.latest_draft_id is not None and not one.draft_is_submitted
            ),
            without_plan=sum(1 for one in items if not one.has_plan),
        )
        return cls(period_id=period_id, items=items, counts=counts)


class PlanHistoryEntryResponse(BaseModel):
    """One past or present version, as the history list draws it.

    Ordered by ``version_no`` descending by the service, which is the only
    ordering that cannot tie - numbers are per employee-month and never reused.
    """

    id: uuid.UUID
    version_no: int
    status: str
    status_label: str
    quota_count: int
    created_at: datetime
    approved_at: datetime | None = None
    superseded_at: datetime | None = None
    discarded_at: datetime | None = None
    note: str | None = None
    #: True for the version currently in force or being written. Exactly one
    #: entry carries it, or none when every version is history.
    is_current: bool = False
    #: True for the **one** revision being written, when there is one. Decided
    #: here rather than by a browser comparing statuses: at most one ``DRAFT``
    #: exists per employee-month - ``uq_pr_work_plans_draft`` - and a screen that
    #: re-derived it would be re-implementing the index.
    #:
    #: Distinct from ``is_current``: with an approved v3 and a draft v4, v3 is
    #: current and v4 is the active draft. **Neither is history**, and a list
    #: that filtered only on ``is_current`` buried v4 with no way to continue,
    #: approve or discard it.
    is_active_draft: bool = False


class PlanHistoryResponse(BaseModel):
    """One employee's whole plan history for one month, newest first."""

    user_id: uuid.UUID
    period_id: uuid.UUID
    items: list[PlanHistoryEntryResponse]


class WorkPlanResponse(BaseModel):
    """One plan version, as a list row draws it."""

    id: uuid.UUID
    user_id: uuid.UUID
    #: Resolved server-side. A browser is never handed a bare UUID to print.
    user_name: str | None = None
    period_id: uuid.UUID
    period_code: str | None = None
    period_status: str | None = None
    version_no: int
    #: ``DRAFT`` | ``APPROVED`` | ``SUPERSEDED`` | ``DISCARDED``. Only
    #: ``APPROVED`` decides anything.
    status: str
    status_label: str
    supersedes_plan_id: uuid.UUID | None = None
    note: str | None = None
    created_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None = None
    approved_at: datetime | None = None
    superseded_at: datetime | None = None
    discarded_at: datetime | None = None
    created_at: datetime
    #: When the row last changed. Meaningful on a ``DRAFT``, where it is the
    #: only timestamp there is and answers *"when did somebody last touch this
    #: revision"*. On a terminal version prefer the lifecycle instant beside it -
    #: ``approved_at``, ``superseded_at``, ``discarded_at`` - which says *what*
    #: happened as well as when.
    updated_at: datetime | None = None
    quota_count: int = 0
    #: KPI self-service. Present on a draft; ``null`` on anything else.
    review_state: str | None = None
    review_state_label: str | None = None
    submitted_at: datetime | None = None
    submitted_by_user_id: uuid.UUID | None = None
    returned_at: datetime | None = None
    returned_by_user_id: uuid.UUID | None = None
    return_note: str | None = None

    @classmethod
    def from_row(
        cls,
        row: PrWorkPlan,
        *,
        periods: dict[uuid.UUID, PrReportingPeriod] | None = None,
        user_names: dict[uuid.UUID, str] | None = None,
        quota_count: int = 0,
    ) -> WorkPlanResponse:
        period = (periods or {}).get(row.period_id)
        state, state_label = _review_state(row)
        return cls(
            review_state=state,
            review_state_label=state_label,
            submitted_at=row.submitted_at,
            submitted_by_user_id=row.submitted_by_user_id,
            returned_at=row.returned_at,
            returned_by_user_id=row.returned_by_user_id,
            return_note=row.return_note,
            id=row.id,
            user_id=row.user_id,
            user_name=(user_names or {}).get(row.user_id),
            period_id=row.period_id,
            period_code=period.code if period else None,
            period_status=period.status.value if period else None,
            version_no=row.version_no,
            status=row.status.value,
            status_label=work_plan_status_label(row.status),
            supersedes_plan_id=row.supersedes_plan_id,
            note=row.note,
            created_by_user_id=row.created_by_user_id,
            approved_by_user_id=row.approved_by_user_id,
            approved_at=row.approved_at,
            superseded_at=row.superseded_at,
            discarded_at=row.discarded_at,
            updated_at=row.updated_at,
            created_at=row.created_at,
            quota_count=quota_count,
        )


class WorkPlanDetailResponse(BaseModel):
    """One plan version in full, with the controls the server would accept.

    ``can_edit`` is **false for every approved plan**, whoever is asking. That
    is not a permission check that happened to fail - it is the immutability
    rule rendered as a flag, so the client offers *"Tạo bản điều chỉnh"* rather
    than a disabled form. Calling the edit route anyway gets the identical
    refusal.
    """

    plan: WorkPlanResponse
    period: ReportingPeriodResponse
    quotas: list[WorkQuotaResponse] = Field(default_factory=list)
    created_by_name: str | None = None
    approved_by_name: str | None = None
    can_edit: bool = False
    can_approve: bool = False
    can_revise: bool = False
    can_discard: bool = False
    #: KPI self-service. Whether the caller is the plan's subject, whether they
    #: may submit it, whether a manager may return it, and why it is not ready
    #: - as stable codes with their Vietnamese sentences, the **same** list
    #: submission and approval are held to.
    is_subject: bool = False
    can_submit: bool = False
    can_return: bool = False
    readiness_blockers: list[str] = Field(default_factory=list)
    readiness_blocker_labels: list[str] = Field(default_factory=list)
    submitted_by_name: str | None = None
    returned_by_name: str | None = None
    workload: PlanWorkloadResponse | None = None

    @classmethod
    def from_detail(cls, detail: PlanDetail) -> WorkPlanDetailResponse:
        types = {row.id: row for row in detail.work_types}
        return cls(
            is_subject=detail.is_subject,
            can_submit=detail.can_submit,
            can_return=detail.can_return,
            readiness_blockers=list(detail.readiness_blockers),
            readiness_blocker_labels=[
                plan_readiness_blocker_label(code) for code in detail.readiness_blockers
            ],
            submitted_by_name=detail.submitted_by_name,
            returned_by_name=detail.returned_by_name,
            workload=PlanWorkloadResponse.from_workload(detail.workload),
            plan=WorkPlanResponse.from_row(
                detail.plan,
                periods={detail.period.id: detail.period},
                user_names={detail.plan.user_id: detail.user_name} if detail.user_name else None,
                quota_count=len(detail.quotas),
            ),
            period=ReportingPeriodResponse.from_row(detail.period),
            quotas=[WorkQuotaResponse.from_row(row, work_types=types) for row in detail.quotas],
            created_by_name=detail.created_by_name,
            approved_by_name=detail.approved_by_name,
            can_edit=detail.can_edit,
            can_approve=detail.can_approve,
            can_revise=detail.can_revise,
            can_discard=detail.can_discard,
        )


class WorkPlanPageResponse(BaseModel):
    """A page of plans."""

    plans: list[WorkPlanResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0

    @classmethod
    def from_page(cls, page: PlanPage) -> WorkPlanPageResponse:
        periods = {row.id: row for row in page.periods}
        return cls(
            plans=[
                WorkPlanResponse.from_row(
                    row,
                    periods=periods,
                    user_names=page.user_names,
                    quota_count=page.quota_counts.get(row.id, 0),
                )
                for row in page.plans
            ],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )


class CreatePlanRequest(_Body):
    """Start a draft plan for one employee and one month.

    No ``status`` and no ``version_no``: the first lands at ``DRAFT`` because
    that is the only place a plan can start, and the second is allocated by the
    server so that "v2" means one thing for ever.
    """

    user_id: uuid.UUID
    period_id: uuid.UUID
    note: str | None = Field(default=None, max_length=2000)


class SelfCreatePlanRequest(_Body):
    """**Tạo KPI của tôi.** The period, and nothing about whose plan it is.

    There is deliberately no ``user_id``: the subject is the session. A client
    cannot start somebody else's plan through this body whatever it sends.
    """

    period_id: uuid.UUID
    note: str | None = Field(default=None, max_length=2000)


class PlanNoteRequest(_Body):
    """A note carried by approve, revise and discard.

    One body for three endpoints because the three take the same optional
    sentence - *"tăng hạn mức seeding sau khi mở thêm kênh"* - and what the
    endpoint **does** is which endpoint it is, never a field in here.
    """

    note: str | None = Field(default=None, max_length=2000)


# --- Eligibility ------------------------------------------------------------


class ContributionEligibilityResponse(BaseModel):
    """One counted contribution and what the quota engine says about it.

    ``basis_amount = eligible_amount + over_quota_amount`` whenever a quota
    measured the row, so the three figures on a card reconcile. Two statuses are
    different, and each ``null`` below says something a zero could not:

    * ``NO_QUOTA`` is ``n, 0, 0`` - the work is real and none of it is eligible,
      because nobody has decided anything about it. ``basis_amount`` is null when
      the contribution has no measurable quantity at all;
    * ``UNMEASURABLE`` has **no ``basis_amount``** and a ``reason_code``. There is
      a quota; what is missing is a number on the work item;
    * ``PENDING_EVALUATION`` has **no amounts at all**. Nothing has decided, and
      a zero would be a decision.
    """

    contribution_id: uuid.UUID
    work_item_id: uuid.UUID
    work_item_code: str
    work_item_title: str
    work_type_id: uuid.UUID
    work_type_code: str
    work_type_name: str
    counted_at: datetime
    #: ``NO_QUOTA`` | ``UNMEASURABLE`` | ``ELIGIBLE`` | ``PARTIALLY_ELIGIBLE`` |
    #: ``OVER_QUOTA`` | ``PENDING_EVALUATION``.
    #:
    #: **Never ``SCORED``** - no milestone has awarded a point. The last of the
    #: six is read-only: no stored row holds it, and it means *an approved quota
    #: covers this and nothing has evaluated it yet*.
    quota_status: str
    quota_status_label: str
    quota_status_hint: str
    basis: str
    unit: str | None = None
    unit_label: str | None = None
    #: Null when the contribution cannot be measured, or has not been evaluated.
    #: **Not zero** - zero is a measurement, and this is the absence of one.
    basis_amount: Decimal | None = None
    #: Null only for ``PENDING_EVALUATION``. Zero is a real answer for
    #: ``NO_QUOTA`` and ``UNMEASURABLE``: nothing is eligible either way.
    eligible_amount: Decimal | None = None
    over_quota_amount: Decimal | None = None
    #: **Why it could not be measured.** Set only for ``UNMEASURABLE``, and
    #: always set for it. ``MISSING_QUANTITY`` | ``INVALID_QUANTITY`` |
    #: ``UNIT_MISMATCH`` - a stable machine code, never an exception message, so
    #: no internal detail reaches a screen.
    reason_code: str | None = None
    #: The one-line Vietnamese sentence for ``reason_code``, composed here so the
    #: browser holds no second copy of the table.
    reason_label: str | None = None
    #: False when nothing has evaluated this contribution yet and the row was
    #: derived on read. It changes nothing about what the figures mean; it tells
    #: an operator whether a reconcile would do anything.
    is_materialised: bool = True
    work_plan_id: uuid.UUID | None = None
    work_quota_id: uuid.UUID | None = None
    evaluated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: ContributionEligibility) -> ContributionEligibilityResponse:
        return cls(
            contribution_id=row.contribution_id,
            work_item_id=row.work_item_id,
            work_item_code=row.work_item_code,
            work_item_title=row.work_item_title,
            work_type_id=row.work_type_id,
            work_type_code=row.work_type_code,
            work_type_name=row.work_type_name,
            counted_at=row.counted_at,
            quota_status=row.quota_status.value,
            quota_status_label=work_quota_status_label(row.quota_status),
            quota_status_hint=work_quota_status_hint(row.quota_status),
            basis=row.basis.value,
            unit=row.unit.value if row.unit else None,
            unit_label=work_unit_label(row.unit) if row.unit else None,
            basis_amount=row.basis_amount,
            eligible_amount=row.eligible_amount,
            over_quota_amount=row.over_quota_amount,
            reason_code=row.reason_code.value if row.reason_code else None,
            reason_label=work_unmeasurable_reason_label(row.reason_code),
            is_materialised=row.is_materialised,
            work_plan_id=row.work_plan_id,
            work_quota_id=row.work_quota_id,
            evaluated_at=row.evaluated_at,
        )


class EligibilityListResponse(BaseModel):
    """One person's counted contributions in one period, each with its decision."""

    period: ReportingPeriodResponse
    user_id: uuid.UUID
    contributions: list[ContributionEligibilityResponse] = Field(default_factory=list)


class QuotaTypeProgressResponse(BaseModel):
    """One work type's KPI figures. **Five numbers that are never merged.**

    ``counted_amount = eligible_amount + over_quota_amount + no_quota_amount``.

    ``target_progress`` is capped at the target, because "20 / 20" is what a met
    target looks like; work beyond it that is still inside the cap is
    ``extra_eligible_above_target``, said separately. Neither is a point total.
    """

    work_type_id: uuid.UUID
    work_type_code: str
    work_type_name: str
    basis: str
    basis_label: str
    unit: str | None = None
    unit_label: str | None = None
    #: Null when no approved quota covers this work type in this period.
    #: **Not unlimited** - it means ``NO_QUOTA``.
    target_value: Decimal | None = None
    eligibility_cap: Decimal | None = None
    work_quota_id: uuid.UUID | None = None
    #: How many of this person's shares were validated. M1's figure.
    counted_contributions: int = 0
    #: How many of those had an amount the quota engine could state. When it is
    #: smaller than ``counted_contributions``, the amounts below understate the
    #: period by exactly that many rows - which is the honest way to say it,
    #: because the alternative is adding a missing quantity in as zero.
    measured_contributions: int = 0
    #: What those shares are worth on this type's basis: the same number for
    #: ``ITEM_COUNT``, and 3 150 comments from 30 items for ``QUANTITY``.
    #: **Over the measurable rows only.**
    counted_amount: Decimal
    eligible_amount: Decimal
    over_quota_amount: Decimal
    no_quota_amount: Decimal
    no_quota_contributions: int = 0
    #: An approved quota covers this type and these contributions cannot be
    #: measured against it. **A count, never an amount** - there is no amount.
    unmeasurable_contributions: int = 0
    #: An approved quota covers this type and nothing has evaluated these yet.
    #: Reconciling the period resolves them.
    pending_contributions: int = 0
    target_progress: Decimal
    extra_eligible_above_target: Decimal
    #: **The KPI comparison.** Period-container patch. The whole counted
    #: amount against the target: ``completion_percent`` is uncapped (135.0 for
    #: 27 against 20) and ``null`` when there is no target;
    #: ``over_target_amount`` is what was done beyond it. Neither caps the
    #: actual and neither is a point total.
    completion_percent: Decimal | None = None
    over_target_amount: Decimal = Decimal("0")
    remaining_amount: Decimal = Decimal("0")
    is_target_met: bool = False

    @classmethod
    def from_row(cls, row: QuotaTypeProgress) -> QuotaTypeProgressResponse:
        comparison = row.comparison
        return cls(
            work_type_id=row.work_type_id,
            work_type_code=row.work_type_code,
            work_type_name=row.work_type_name,
            basis=row.basis.value,
            basis_label=work_quota_basis_label(row.basis),
            unit=row.unit.value if row.unit else None,
            unit_label=work_unit_label(row.unit) if row.unit else None,
            target_value=row.target_value,
            eligibility_cap=row.eligibility_cap,
            work_quota_id=row.work_quota_id,
            counted_contributions=row.counted_contributions,
            measured_contributions=row.measured_contributions,
            counted_amount=row.counted_amount,
            eligible_amount=row.eligible_amount,
            over_quota_amount=row.over_quota_amount,
            no_quota_amount=row.no_quota_amount,
            no_quota_contributions=row.no_quota_contributions,
            unmeasurable_contributions=row.unmeasurable_contributions,
            pending_contributions=row.pending_contributions,
            target_progress=row.target_progress,
            extra_eligible_above_target=row.extra_eligible_above_target,
            completion_percent=comparison.completion_percent,
            over_target_amount=comparison.over_target,
            remaining_amount=comparison.remaining,
            is_target_met=comparison.is_met,
        )


class EligibilitySummaryResponse(BaseModel):
    """One person's KPI eligibility for one period, per work type.

    The cross-type figures are **counts of contributions only**. There is no
    total quantity and there is nowhere to put one: summing ``COMMENT`` +
    ``VIDEO`` + ``DAY`` would produce a number that is not a quantity of
    anything.
    """

    user_id: uuid.UUID
    period: ReportingPeriodResponse
    #: The plan in force. Null when the employee has none for this period, which
    #: is the ``NO_QUOTA`` case and the screen says so in words.
    plan_id: uuid.UUID | None = None
    plan_version_no: int | None = None
    plan_approved_at: datetime | None = None
    types: list[QuotaTypeProgressResponse] = Field(default_factory=list)
    #: How many contributions are in each quota status. A count, never a
    #: quantity.
    contributions_by_status: dict[str, int] = Field(default_factory=dict)
    counted_contributions: int = 0
    #: Distinct jobs behind those contributions. Not derived from the figure
    #: above: a three-person shoot is one work item and three contributions, and
    #: both are true.
    counted_work_items: int = 0

    @classmethod
    def from_summary(
        cls, summary: QuotaPeriodSummary, *, period: PrReportingPeriod
    ) -> EligibilitySummaryResponse:
        return cls(
            user_id=summary.user_id,
            period=ReportingPeriodResponse.from_row(period),
            plan_id=summary.plan_id,
            plan_version_no=summary.plan_version_no,
            plan_approved_at=summary.plan_approved_at,
            types=[QuotaTypeProgressResponse.from_row(row) for row in summary.types],
            contributions_by_status=summary.contributions_by_status,
            counted_contributions=summary.counted_contributions,
            counted_work_items=summary.counted_work_items,
        )


class ReconcileRequest(_Body):
    """Recompute an **open** period's eligibility.

    ``user_ids`` narrows the sweep; omitting it reconciles everybody with
    counted work in that period, so nobody is missed because their name was not
    on the list.

    There is deliberately **no ``force`` field**. A ``CLOSED`` or ``LOCKED``
    period is refused, and getting past that is a correction workflow with its
    own audit trail rather than a boolean.
    """

    period_id: uuid.UUID
    user_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class ReconcileResponse(BaseModel):
    """What one reconciliation did.

    ``unmeasurable`` is the operational figure worth watching: contributions an
    approved quota could not measure, because the work item has no quantity, or
    an invalid one, or one in the wrong unit. They are **not** silently counted
    as one, and no longer silently skipped either - each gets an allocation row
    with ``quota_status = UNMEASURABLE`` and a reason code, so a screen can name
    the field somebody has to fix.
    """

    period_id: uuid.UUID
    period_code: str
    users: int = 0
    evaluated: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    unmeasurable: int = 0

    @classmethod
    def from_outcome(cls, outcome: ReconcileOutcome) -> ReconcileResponse:
        return cls(
            period_id=outcome.period_id,
            period_code=outcome.period_code,
            users=outcome.users,
            evaluated=outcome.evaluated,
            created=outcome.created,
            updated=outcome.updated,
            removed=outcome.removed,
            unmeasurable=outcome.unmeasurable,
        )


__all__: list[str] = [
    "AddQuotaRequest",
    "ContributionEligibilityResponse",
    "CreatePlanRequest",
    "EligibilityListResponse",
    "EligibilitySummaryResponse",
    "EnsurePeriodRequest",
    "PlanNoteRequest",
    "QuotaTypeProgressResponse",
    "ReconcileRequest",
    "ReconcileResponse",
    "ReportingPeriodResponse",
    "UpdateQuotaRequest",
    "WorkPlanDetailResponse",
    "WorkPlanPageResponse",
    "WorkPlanResponse",
    "WorkQuotaResponse",
]
